"""Company-opportunist batching: several articles per request, attributed by article_id, none silently dropped.

Free Gemini tiers cap requests per day long before tokens, so one article per request wastes the allowance.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import agent_stages.company_opportunist as stage
from agent_helpers.llm_client import Client, ModelAnswer
from agent_helpers.opportunist_support import classifier_batch_token_limit
from services.settings import OpportunistSettings, get_settings, use_settings

COMPANY = {'company_id': 7, 'symbol': 'NVDA', 'name': 'NVIDIA', 'industry_key': 'semis', 'sector_key': 'tech'}
IMPACT = {'confidence': 'high', 'impact_direction': 'positive', 'impact_magnitude': 'moderate', 'materiality': 'high',
          'time_horizon': 'short_term', 'effect_type': 'direct', 'relative_positioning': 'better_than_peers',
          'reason': 'Demand for its chips rises.'}


def article(article_id, body_chars=200):
    return {'article_id': article_id, 'title': f'Title {article_id}', 'summary': 'Summary', 'body': 'x' * body_chars,
            'source': 'wire', 'source_url': f'https://example.com/{article_id}', 'source_page_url': '',
            'published_at': '2026-10-28T15:00:00Z', 'article_scope': 'company_news'}


def results(*entries):
    return json.dumps({'articles': [{'article_id': article_id, 'impacts': impacts} for article_id, impacts in entries]})


def opportunists(**overrides):
    return replace(OpportunistSettings.from_environ({}), **overrides)


class BatchTokenLimitTests(unittest.TestCase):
    def test_unset_means_provider_default(self):
        self.assertIsNone(OpportunistSettings.from_environ({}).classifier_batch_tokens)
        self.assertIsNone(OpportunistSettings.from_environ({'CLASSIFIER_BATCH_TOKENS': ' '}).classifier_batch_tokens)
        self.assertEqual(OpportunistSettings.from_environ({'CLASSIFIER_BATCH_TOKENS': '24000'}).classifier_batch_tokens,
                         24000)

    def test_hosted_providers_batch_far_more_than_local_models_unless_configured(self):
        with use_settings(opportunists=opportunists(classifier_batch_tokens=None)):
            self.assertEqual(classifier_batch_token_limit('gemini'), 16000)
            # Small local context windows keep the old budget.
            self.assertEqual(classifier_batch_token_limit('llamacpp'), 4096)
        with use_settings(opportunists=opportunists(classifier_batch_tokens=8000)):
            self.assertEqual(classifier_batch_token_limit('gemini'), 8000)
            self.assertEqual(classifier_batch_token_limit('llamacpp'), 8000)



class CompanyBatchTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.saved = []
        self.replies = []
        self.client = Client(provider='gemini', raw_client=None)
        patches = [
            patch.object(stage, 'ask_model', side_effect=self._ask),
            patch.object(stage, 'save_company_opportunist_batch_results', side_effect=self._save),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _ask(self, *, client, model, system_prompt, user_prompt):
        self.calls.append([item['article_id'] for item in json.loads(user_prompt)['articles']])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ModelAnswer(text=reply, provider='gemini', model=model)

    def _save(self, article_batch, impacts, *, company_id, answer):
        self.saved.append(([item['article_id'] for item in article_batch],
                           sorted((impact['article_id'], impact['impact_direction']) for impact in impacts)))

    def collect(self, articles):
        return stage._collect_cleaned_impacts(
            [articles], company=COMPANY, peer_groups={}, client=self.client, model='gemini-3.1-flash-lite',
            valid_article_ids={item['article_id'] for item in articles}, valid_company_id=7, valid_symbol='NVDA')

    def test_one_request_classifies_the_whole_batch_attributed_by_article_id(self):
        self.replies = [results((1, [IMPACT]), (2, []), (3, [{**IMPACT, 'impact_direction': 'negative'}]))]
        impacts = self.collect([article(1), article(2), article(3)])
        self.assertEqual(self.calls, [[1, 2, 3]])
        self.assertEqual(self.saved, [([1, 2, 3], [(1, 'positive'), (3, 'negative')])])
        self.assertEqual(sorted(impact['article_id'] for impact in impacts), [1, 3])
        self.assertTrue(all(impact['company_id'] == 7 and impact['symbol'] == 'NVDA' for impact in impacts))

    def test_articles_the_model_leaves_out_are_retried_one_by_one(self):
        self.replies = [results((1, [IMPACT])), results((2, [])), results((3, [IMPACT]))]
        self.collect([article(1), article(2), article(3)])
        self.assertEqual(self.calls, [[1, 2, 3], [2], [3]])
        self.assertEqual(self.saved, [([1], [(1, 'positive')]), ([2], []), ([3], [(3, 'positive')])])

    def test_an_article_still_missing_alone_is_recorded_without_impacts(self):
        # One article per request is today's behaviour: an answer that names nothing counts as no impact.
        self.replies = [results((1, [])), json.dumps({'articles': []})]
        self.collect([article(1), article(2)])
        self.assertEqual(self.calls, [[1, 2], [2]])
        self.assertEqual(self.saved, [([1], []), ([2], [])])

    def test_a_single_article_answer_in_the_flat_impacts_shape_is_attributed_to_it(self):
        self.replies = [json.dumps({'impacts': [IMPACT]})]
        self.collect([article(5)])
        self.assertEqual(self.saved, [([5], [(5, 'positive')])])

    def test_a_batch_that_hits_max_tokens_is_retried_one_by_one(self):
        self.replies = [RuntimeError('Model returned empty content (finish_reason MAX_TOKENS)'),
                        results((1, [])), results((2, [IMPACT]))]
        self.collect([article(1), article(2)])
        self.assertEqual(self.calls, [[1, 2], [1], [2]])
        self.assertEqual(self.saved, [([1], []), ([2], [(2, 'positive')])])



class CompanyPromptTests(unittest.TestCase):
    def test_schema_requires_article_id_per_result(self):
        schema = stage.COMPANY_IMPACTS_SCHEMA
        result = schema['properties']['articles']['items']
        self.assertEqual(schema['required'], ['articles'])
        self.assertEqual(result['required'], ['article_id', 'impacts'])
        self.assertEqual(result['properties']['article_id'], {'type': 'integer'})


class ProviderBatchSizeTests(unittest.TestCase):
    """classify_company_articles sizes batches by provider and by the body text actually sent (capped bodies)."""

    def run_stage(self, provider, articles):
        calls = []

        def ask(*, client, model, system_prompt, user_prompt):
            ids = [item['article_id'] for item in json.loads(user_prompt)['articles']]
            calls.append(ids)
            return ModelAnswer(text=results(*((article_id, []) for article_id in ids)), provider=provider, model=model)

        with use_settings(opportunists=opportunists(classifier_batch_tokens=None)), \
                patch.object(stage, 'build_company_opportunist_articles', return_value=(COMPANY, {}, articles)), \
                patch.object(stage, 'ask_model', side_effect=ask), \
                patch.object(stage, 'save_company_opportunist_batch_results'):
            stage.classify_company_articles('NVDA', client=Client(provider=provider, raw_client=None), model='m')
        return calls

    def test_gemini_sends_a_dozen_long_articles_in_one_request(self):
        body_limit = get_settings().opportunists.company_article_body_char_limit
        self.assertLessEqual(body_limit, 4000)
        self.assertEqual(len(self.run_stage('gemini', [article(index, body_chars=40_000) for index in range(1, 13)])), 1)



if __name__ == '__main__':
    unittest.main()
