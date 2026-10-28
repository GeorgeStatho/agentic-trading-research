"""Plan C slice C1: decision attribution (evidence, direction, expression), its persistence, runner and collector hook.

Only the collector's other stages are mocked. Persistence and the runner use real temporary databases.
New modules are imported on use so each test fails on its own while they do not exist yet.
"""
from __future__ import annotations

from contextlib import ExitStack, closing
from datetime import date, datetime, timedelta, timezone
import importlib
import inspect
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from db_common import get_connection
from market_db_modules.evidence_thesis import record_evidence_thesis

from tests.test_llm_backtest import DECISION_AT, DatabaseFixture

DECIDED_AT = datetime.fromisoformat(DECISION_AT)
NEW_YORK = ZoneInfo('America/New_York')
TRADING_DAYS = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(14)
                if (date(2026, 3, 2) + timedelta(days=offset)).weekday() < 5]


def attribution():
    return importlib.import_module('services.decision_attribution')


def store():
    return importlib.import_module('market_db_modules.decision_attribution')


def runner():
    return importlib.import_module('services.decision_attribution_runner')


# --- inputs for the pure layers -------------------------------------------------------------------------------------

def claim(claim_type, *article_ids):
    return {'claim_type': claim_type, 'claim': 'fixture claim', 'evidence_article_ids': list(article_ids)}


def strategist(*claims, **self_ratings):
    return {'decision': 'trade_candidate', 'evidence_claims': list(claims),
            'evidence_quality': 'moderate', 'contradictions_present': False, **self_ratings}


def article(article_id, hours_before=1.0):
    return {'article_id': article_id, 'published_at': (DECIDED_AT - timedelta(hours=hours_before)).isoformat()}


def catalyst(roots=2, primary=False):
    return {'catalyst_id': 'catalyst:1', 'independent_root_count': roots, 'primary_source_present': primary}


def good_evidence(count=10, hours_before=1.0):
    """Inputs for a `strong` proxy: a catalyst claim citing `count` resolvable, fresh articles and two roots."""
    ids = list(range(1, count + 1))
    return {'strategist': strategist(claim('catalyst', *ids)), 'catalysts': [catalyst()],
            'articles': [article(i, hours_before) for i in ids]}


def grade(**overrides):
    inputs = {'thesis_check': None, **good_evidence(), **overrides}
    return attribution().evidence_layer(inputs['strategist'], inputs['thesis_check'], inputs['catalysts'],
                                        inputs['articles'], DECIDED_AT)


def closes_after(*prices, start=0):
    """Daily closes stamped 16:00 New York on consecutive trading days from the decision day."""
    return [(datetime.combine(TRADING_DAYS[start + index], datetime.min.time().replace(hour=16), NEW_YORK), price)
            for index, price in enumerate(prices)]


def closes_with(fifth, tenth):
    return closes_after(*[100.0] * 4, fifth, *[100.0] * 4, tenth)


def order(decision, **fields):
    return {'id': 7, 'symbol': 'MSFT', 'decision_run_at': DECISION_AT, 'manager_decision': decision,
            'trade_executed': 0, 'realized_pnl_pct': None, 'latest_trade_pnl_pct': None,
            'resolved_outcome_label': None, **fields}


def payload(reference=100.0, **frozen):
    return {'company': {'symbol': 'MSFT'}, 'market_context': {'current_stock_price': {'price': reference}}, **frozen}


def direction(decision, closes, *, now=None, **fields):
    return attribution().direction_layer(order(decision, **fields), payload(), closes,
                                         **({'now': now} if now else {}))


# --- evidence layer -------------------------------------------------------------------------------------------------

class EvidenceLayerThesisCheckTests(unittest.TestCase):
    def test_a_phase_4_check_decides_the_quality(self) -> None:
        cases = (
            ('validated', [], 'strong'),
            ('validated', ['central_claim_failed'], 'weak'),
            ('validated_with_warnings', [], 'adequate'),
            ('validated_with_warnings', ['central_claim_failed'], 'weak'),
            ('validated_with_warnings', ['package_opposing_evidence_unaddressed'], 'weak'),
            ('deferred', [], 'weak'),
            ('invalid_output', [], 'weak'),
        )
        for status, failures, quality in cases:
            with self.subTest(status=status, failures=failures):
                # The proxy inputs would grade `strong`: the check overrides them.
                result = grade(thesis_check={'status': status, 'failures': failures, 'claims': []})
                self.assertEqual((result['quality'], result['source']), (quality, 'thesis_check'))

    def test_a_check_that_was_never_requested_falls_back_to_the_proxy(self) -> None:
        result = grade(thesis_check={'status': 'not_requested', 'failures': ['not_selected'], 'claims': []})
        self.assertEqual((result['quality'], result['source']), ('strong', 'grounding_proxy'))


class EvidenceLayerProxyTests(unittest.TestCase):
    def test_every_condition_met_is_strong_and_reports_its_signals(self) -> None:
        result = grade()
        self.assertEqual((result['quality'], result['source']), ('strong', 'grounding_proxy'))
        signals = result['signals']
        self.assertEqual((signals['cited_articles'], signals['cited_articles_resolvable'],
                          signals['catalyst_claim_present'], signals['independent_roots'],
                          signals['primary_source_present']), (10, 1.0, True, 2, False))
        self.assertAlmostEqual(signals['freshest_evidence_age_hours'], 1.0)

    def test_one_primary_source_stands_in_for_two_roots(self) -> None:
        self.assertEqual(grade(catalysts=[catalyst(roots=1, primary=True)])['quality'], 'strong')

    def test_a_single_secondary_root_is_only_adequate(self) -> None:
        self.assertEqual(grade(catalysts=[catalyst(roots=1)])['quality'], 'adequate')

    def test_the_primary_catalyst_state_is_the_first_one(self) -> None:
        self.assertEqual(grade(catalysts=[catalyst(roots=2), catalyst(roots=0)])['signals']['independent_roots'], 2)

    def test_no_catalyst_state_means_no_independent_roots(self) -> None:
        result = grade(catalysts=[])
        self.assertEqual((result['quality'], result['signals']['independent_roots']), ('adequate', 0))

    def test_evidence_up_to_72_hours_old_is_fresh_enough(self) -> None:
        self.assertEqual(grade(**good_evidence(hours_before=72.0))['quality'], 'strong')
        self.assertEqual(grade(**good_evidence(hours_before=73.0))['quality'], 'adequate')

    def test_freshness_is_the_newest_resolvable_cited_article(self) -> None:
        evidence = good_evidence(3)
        evidence['articles'] = [article(1, 90.0), article(2, 5.0), article(3, 30.0)]
        self.assertAlmostEqual(grade(**evidence)['signals']['freshest_evidence_age_hours'], 5.0)

    def test_resolvability_below_90_percent_is_adequate_and_below_50_is_weak(self) -> None:
        cases = ((9, 0.9, 'strong'), (8, 0.8, 'adequate'), (5, 0.5, 'adequate'), (4, 0.4, 'weak'))
        for resolvable, share, quality in cases:
            with self.subTest(share=share):
                evidence = good_evidence()
                evidence['articles'] = evidence['articles'][:resolvable]       # the rest are not in the input
                result = grade(**evidence)
                self.assertEqual(result['quality'], quality)
                self.assertAlmostEqual(result['signals']['cited_articles_resolvable'], share)

    def test_an_article_published_after_the_decision_is_not_resolvable(self) -> None:
        evidence = good_evidence(5)
        evidence['articles'][0] = article(1, hours_before=-2.0)                # two hours after decided_at
        result = grade(**evidence)
        self.assertAlmostEqual(result['signals']['cited_articles_resolvable'], 0.8)
        self.assertEqual(result['quality'], 'adequate')

    def test_cited_articles_count_each_id_once_across_claims(self) -> None:
        evidence = good_evidence(3)
        evidence['strategist'] = strategist(claim('catalyst', 1, 2), claim('thesis', 2, 3))
        self.assertEqual(grade(**evidence)['signals']['cited_articles'], 3)

    def test_no_catalyst_claim_is_weak_however_good_the_rest(self) -> None:
        evidence = good_evidence()
        evidence['strategist'] = strategist(claim('thesis', *range(1, 11)))
        result = grade(**evidence)
        self.assertEqual((result['quality'], result['signals']['catalyst_claim_present']), ('weak', False))

    def test_no_cited_articles_is_weak(self) -> None:
        for claims in ((), (claim('catalyst'),)):
            with self.subTest(claims=len(claims)):
                evidence = good_evidence()
                evidence['strategist'] = strategist(*claims)
                self.assertEqual(grade(**evidence)['quality'], 'weak')

    def test_without_a_strategist_recommendation_it_is_unchecked(self) -> None:
        self.assertEqual(grade(strategist=None)['quality'], 'unchecked')


class SelfRatingTests(unittest.TestCase):
    def test_self_ratings_are_recorded_but_never_change_the_quality(self) -> None:
        for name, evidence in (('strong inputs', good_evidence()), ('weak inputs', {**good_evidence(), 'catalysts': []})):
            for rating, contradictions in (('strong', False), ('weak', True)):
                with self.subTest(name, rating=rating):
                    plain = grade(**evidence)
                    rated = grade(**{**evidence, 'strategist': {**evidence['strategist'], 'evidence_quality': rating,
                                                                'contradictions_present': contradictions}})
                    self.assertEqual(rated['quality'], plain['quality'])
                    self.assertEqual(rated['signals']['model_self_rating'],
                                     {'evidence_quality': rating, 'contradictions_present': contradictions})


# --- direction layer ------------------------------------------------------------------------------------------------

class DirectionLayerTests(unittest.TestCase):
    def test_a_call_is_graded_at_both_horizons_with_five_days_primary(self) -> None:
        result = direction('call', closes_with(102.0, 98.0))
        self.assertEqual((result['outcome'], result['primary_horizon']), ('correct', 5))
        self.assertEqual((result['horizon_outcomes']['5'], result['horizon_outcomes']['10']), ('correct', 'wrong'))
        self.assertAlmostEqual(result['horizon_returns']['5'], 0.02)
        self.assertAlmostEqual(result['horizon_returns']['10'], -0.02)

    def test_a_put_scores_the_negative_of_the_return(self) -> None:
        self.assertEqual(direction('put', closes_with(99.0, 99.0))['outcome'], 'correct')
        self.assertEqual(direction('put', closes_with(101.0, 101.0))['outcome'], 'wrong')

    def test_a_move_inside_half_a_percent_is_flat(self) -> None:
        self.assertEqual(direction('call', closes_with(100.4, 99.6))['horizon_outcomes'], {'5': 'flat', '10': 'flat'})
        self.assertEqual(direction('call', closes_with(100.6, 99.4))['horizon_outcomes'], {'5': 'correct', '10': 'wrong'})

    def test_neither_is_abstained_and_records_how_far_the_stock_moved(self) -> None:
        for decision in ('neither', 'do_not_buy'):
            with self.subTest(decision):
                result = direction(decision, closes_with(97.0, 105.0))
                self.assertEqual(result['outcome'], 'abstained')
                self.assertAlmostEqual(result['abs_move']['5'], 0.03)
                self.assertAlmostEqual(result['abs_move']['10'], 0.05)

    def test_too_few_closes_are_pending_before_the_horizon_and_unavailable_after(self) -> None:
        closes = closes_after(101.0, 101.0)
        early = direction('call', closes, now=DECIDED_AT + timedelta(days=1))
        late = direction('call', closes, now=DECIDED_AT + timedelta(days=90))
        self.assertEqual((early['outcome'], late['outcome']), ('pending', 'unavailable'))
        self.assertEqual(direction('call', [], now=DECIDED_AT + timedelta(days=1))['outcome'], 'pending')
        self.assertEqual(direction('call', [], now=DECIDED_AT + timedelta(days=90))['outcome'], 'unavailable')

    def test_the_five_day_horizon_elapses_after_eleven_calendar_days(self) -> None:
        for closes in ([], closes_after(101.0, 101.0)):
            with self.subTest(closes=len(closes)):
                self.assertEqual(direction('call', closes, now=DECIDED_AT + timedelta(days=11))['outcome'], 'pending')
                self.assertEqual(direction('call', closes, now=DECIDED_AT + timedelta(days=12))['outcome'],
                                 'unavailable')

    def test_the_horizon_keys_are_strings(self) -> None:
        result = direction('neither', closes_with(97.0, 105.0))
        self.assertEqual((set(result['horizon_returns']), set(result['horizon_outcomes']), set(result['abs_move'])),
                         ({'5', '10'},) * 3)

    def test_a_horizon_with_enough_closes_is_graded_while_the_longer_one_waits(self) -> None:
        closes = closes_after(*[100.0] * 4, 103.0)
        early = direction('call', closes, now=DECIDED_AT + timedelta(days=8))
        late = direction('call', closes, now=DECIDED_AT + timedelta(days=90))
        self.assertEqual((early['outcome'], early['horizon_outcomes']['10']), ('correct', 'pending'))
        self.assertEqual((late['outcome'], late['horizon_outcomes']['10']), ('correct', 'unavailable'))

    def test_the_beta_adjusted_forward_residual_return_is_recorded_when_it_exists(self) -> None:
        self.assertEqual(direction('call', closes_with(102.0, 102.0), forward_residual_return=0.013)
                         ['forward_residual_return'], 0.013)
        self.assertIsNone(direction('call', closes_with(102.0, 102.0))['forward_residual_return'])


# --- expression layer and primary failure ---------------------------------------------------------------------------

class ExpressionLayerTests(unittest.TestCase):
    def expression(self, direction_outcome, **fields):
        return attribution().expression_layer(order('call', **fields), {'outcome': direction_outcome})['outcome']

    def test_a_right_call_that_made_money_is_captured(self) -> None:
        self.assertEqual(self.expression('correct', trade_executed=1, realized_pnl_pct=0.4), 'captured')

    def test_a_right_call_that_lost_or_broke_even_was_lost_despite_direction(self) -> None:
        for pnl in (-0.2, 0.0):
            with self.subTest(pnl=pnl):
                self.assertEqual(self.expression('correct', trade_executed=1, realized_pnl_pct=pnl),
                                 'lost_despite_direction')

    def test_a_wrong_call_that_lost_was_lost_with_direction(self) -> None:
        self.assertEqual(self.expression('wrong', trade_executed=1, realized_pnl_pct=-0.5), 'lost_with_direction')

    def test_an_untraded_decision_is_not_traded(self) -> None:
        self.assertEqual(self.expression('correct'), 'not_traded')

    def test_an_open_position_is_pending(self) -> None:
        self.assertEqual(self.expression('correct', trade_executed=1, latest_trade_pnl_pct=0.1,
                                         resolved_outcome_label='open_profit'), 'pending')

    def test_a_position_with_a_submitted_exit_counts_by_its_latest_pnl(self) -> None:
        self.assertEqual(self.expression('correct', trade_executed=1, latest_trade_pnl_pct=0.2,
                                         resolved_outcome_label='managed_exit_submitted_profit'), 'captured')

    def test_a_trade_is_pending_until_the_direction_is_known(self) -> None:
        self.assertEqual(self.expression('pending', trade_executed=1, realized_pnl_pct=0.4), 'pending')


class PrimaryFailureTests(unittest.TestCase):
    LOST = {'trade_executed': 1, 'realized_pnl_pct': -0.3}
    WON = {'trade_executed': 1, 'realized_pnl_pct': 0.3}
    CHECKS = {'strong': {'status': 'validated', 'failures': [], 'claims': []},
              'adequate': {'status': 'validated_with_warnings', 'failures': [], 'claims': []}}

    def attribute(self, decision, closes, *, evidence='weak', **fields):
        """`weak` evidence is a strategist citing nothing; the others come from a Phase 4 check."""
        return attribution().attribute(order(decision, **fields), payload(strategist_recommendation=strategist()),
                                       self.CHECKS.get(evidence), [], closes)

    def test_the_record_carries_the_ids_the_layers_and_the_version(self) -> None:
        record = self.attribute('call', closes_with(102.0, 102.0), **self.WON)
        self.assertEqual((record['manager_decision_history_id'], record['attribution_version']),
                         (7, 'decision-attribution-v1'))
        self.assertEqual((record['evidence']['quality'], record['direction']['outcome'],
                          record['expression']['outcome']), ('weak', 'correct', 'captured'))

    def test_weak_evidence_and_a_wrong_direction_blame_the_evidence(self) -> None:
        self.assertEqual(self.attribute('call', closes_with(97.0, 97.0))['primary_failure'], 'evidence')

    def test_adequate_or_strong_evidence_and_a_wrong_direction_blame_the_forecast(self) -> None:
        for evidence in ('adequate', 'strong'):
            with self.subTest(evidence):
                record = self.attribute('call', closes_with(97.0, 97.0), evidence=evidence)
                self.assertEqual(record['primary_failure'], 'forecast')

    def test_a_right_direction_and_a_lost_trade_blame_the_expression(self) -> None:
        record = self.attribute('call', closes_with(102.0, 102.0), evidence='strong', **self.LOST)
        self.assertEqual(record['primary_failure'], 'expression')

    def test_a_right_direction_that_made_money_has_no_failure(self) -> None:
        self.assertEqual(self.attribute('call', closes_with(102.0, 102.0), **self.WON)['primary_failure'], 'none')

    def test_a_flat_move_or_an_abstention_has_no_failure(self) -> None:
        self.assertEqual(self.attribute('call', closes_with(100.2, 100.2))['primary_failure'], 'none')
        self.assertEqual(self.attribute('neither', closes_with(97.0, 97.0))['primary_failure'], 'none')

    def test_an_undecided_direction_or_open_trade_is_pending(self) -> None:
        undecided = attribution().attribute(order('call'), payload(strategist_recommendation=strategist()), None, [],
                                            closes_after(101.0), now=DECIDED_AT + timedelta(days=1))
        self.assertEqual(undecided['primary_failure'], 'pending')
        open_trade = self.attribute('call', closes_with(102.0, 102.0), trade_executed=1, latest_trade_pnl_pct=0.1,
                                    resolved_outcome_label='open_profit')
        self.assertEqual(open_trade['primary_failure'], 'pending')

    def test_a_wrong_direction_without_checked_evidence_blames_the_forecast(self) -> None:
        record = attribution().attribute(order('call'), payload(), None, [], closes_with(97.0, 97.0))
        self.assertEqual((record['evidence']['quality'], record['primary_failure']), ('unchecked', 'forecast'))

    def test_an_unavailable_direction_has_no_failure(self) -> None:
        record = attribution().attribute(order('call'), payload(strategist_recommendation=strategist()), None, [], [],
                                         now=DECIDED_AT + timedelta(days=90))
        self.assertEqual((record['direction']['outcome'], record['primary_failure']), ('unavailable', 'none'))

    def test_a_flat_direction_with_a_losing_trade_has_no_failure(self) -> None:
        record = self.attribute('call', closes_with(100.2, 100.2), **self.LOST)
        self.assertEqual((record['direction']['outcome'], record['primary_failure']), ('flat', 'none'))

    def test_a_wrong_direction_is_settled_even_while_the_trade_is_open(self) -> None:
        record = self.attribute('call', closes_with(97.0, 97.0), trade_executed=1, latest_trade_pnl_pct=-0.1,
                                resolved_outcome_label='open_loss')
        self.assertEqual(record['primary_failure'], 'evidence')


class BootstrapTests(unittest.TestCase):
    OUTCOMES = [1, 0] * 50

    def test_the_standard_error_is_deterministic_under_the_seed(self) -> None:
        bootstrap = attribution().bootstrap_se
        self.assertEqual(bootstrap(self.OUTCOMES, seed=3), bootstrap(self.OUTCOMES, seed=3))
        self.assertNotEqual(bootstrap(self.OUTCOMES, seed=3), bootstrap(self.OUTCOMES, seed=4))

    def test_the_standard_error_of_a_fair_hit_rate_is_near_the_binomial_one(self) -> None:
        self.assertTrue(0.04 < attribution().bootstrap_se(self.OUTCOMES, seed=1) < 0.06)

    def test_identical_outcomes_have_no_spread_and_no_outcomes_have_no_error(self) -> None:
        self.assertEqual(attribution().bootstrap_se([1, 1, 1, 1], seed=1), 0.0)
        self.assertIsNone(attribution().bootstrap_se([], seed=1))

    def test_it_defaults_to_a_thousand_resamples_and_takes_a_seed(self) -> None:
        parameters = inspect.signature(attribution().bootstrap_se).parameters
        self.assertEqual(parameters['n'].default, 1000)
        self.assertIn('seed', parameters)


# --- persistence, runner and hook -----------------------------------------------------------------------------------

def record(decision_id, *, failure='none', version=None, evidence='adequate', direction_outcome='correct'):
    return {'manager_decision_history_id': decision_id,
            'attribution_version': version or attribution().ATTRIBUTION_VERSION,
            'evidence': {'quality': evidence, 'source': 'grounding_proxy', 'signals': {}},
            'direction': {'outcome': direction_outcome, 'horizon_returns': {}},
            'expression': {'outcome': 'not_traded'}, 'primary_failure': failure}


class AttributionDatabase(DatabaseFixture):
    def setUp(self) -> None:
        super().setUp()
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute('DELETE FROM manager_decision_history WHERE id = ?', (self.decision_id,))

    def add_decision(self, decision='call', at=DECISION_AT, *, reference=400.0, strategist_recommendation=None,
                     articles=(), **history_columns) -> int:
        frozen = {'company': {'symbol': 'MSFT'}, 'market_context': {'current_stock_price': {'price': reference}},
                  'strategist_recommendation': strategist_recommendation or {},
                  'supporting_articles': {'article_summaries': list(articles), 'full_articles': []}}
        columns = ['symbol', 'decision_run_at', 'manager_decision', *history_columns]
        with closing(get_connection(self.db_path)) as conn, conn:
            decision_id = conn.execute(
                f"INSERT INTO manager_decision_history ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                ('MSFT', at, decision, *history_columns.values())).lastrowid
            conn.execute('INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, '
                         'manager_output_json) VALUES (?, ?, ?)',
                         (decision_id, json.dumps({'replay_input': frozen}), json.dumps({'recommendation': {}})))
        return decision_id

    def add_primary_catalyst(self, candidate_id, *, roots=2, primary_source=False) -> None:
        state = catalyst(roots, primary_source)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO catalysts (catalyst_id, ticker, catalyst_type, catalyst_subtype, catalyst_family, "
                "catalyst_scope, direction, mechanism, expected_horizon, article_count, source_count, "
                "independent_root_count, primary_source_present, classification_version, classification_status, "
                "state_json, updated_at) VALUES ('catalyst:1', 'MSFT', 'other', 'other', 'other', 'company', "
                "'unknown', 'none', 'days', 1, 1, ?, ?, 'fixture', 'classified', ?, ?)",
                (roots, int(primary_source), json.dumps(state), DECISION_AT))
            conn.execute("INSERT INTO candidate_catalysts VALUES (?, 'catalyst:1', 'other', 1, ?)",
                         (candidate_id, json.dumps(state)))

    def add_thesis_check(self, candidate_id, status='validated') -> None:
        record_evidence_thesis({'route': 'legacy_strategist', 'company_id': 1, 'status': status, 'failures': [],
                                'claims': [], 'candidate_id': candidate_id, 'created_at': DECISION_AT},
                               db_path=self.db_path)

    def rows(self) -> dict[int, dict]:
        return {row['manager_decision_history_id']: row for row in store().list_attributions(db_path=self.db_path)}

    def needing(self, limit=100) -> list[int]:
        return [row['id'] for row in store().decisions_needing_attribution(limit, db_path=self.db_path)]


class SchemaTests(AttributionDatabase):
    def test_the_table_and_its_failure_index_exist(self) -> None:
        with closing(get_connection(self.db_path)) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%decision_attributions%'")}
        self.assertEqual(names, {'decision_attributions', 'idx_decision_attributions_failure'})


class PersistenceTests(AttributionDatabase):
    def test_a_stored_attribution_lists_with_its_labels_and_full_record(self) -> None:
        decision_id = self.add_decision()
        store().upsert_attribution(record(decision_id, failure='forecast', evidence='weak', direction_outcome='wrong'),
                                   db_path=self.db_path)
        row = self.rows()[decision_id]
        self.assertEqual((row['attribution_version'], row['evidence_quality'], row['evidence_source'],
                          row['direction_outcome'], row['expression_outcome'], row['primary_failure']),
                         ('decision-attribution-v1', 'weak', 'grounding_proxy', 'wrong', 'not_traded', 'forecast'))
        self.assertTrue(row['computed_at'])
        self.assertEqual(row['record']['evidence']['quality'], 'weak')

    def test_listing_can_be_limited_to_rows_computed_since(self) -> None:
        store().upsert_attribution(record(self.add_decision()), db_path=self.db_path)
        self.assertEqual(len(store().list_attributions(since='2000-01-01T00:00:00+00:00', db_path=self.db_path)), 1)
        self.assertEqual(store().list_attributions(since='2999-01-01T00:00:00+00:00', db_path=self.db_path), [])

    def test_a_pending_row_is_recomputed(self) -> None:
        decision_id = self.add_decision()
        store().upsert_attribution(record(decision_id, failure='pending', direction_outcome='pending'),
                                   db_path=self.db_path)
        store().upsert_attribution(record(decision_id, failure='forecast', direction_outcome='wrong'),
                                   db_path=self.db_path)
        self.assertEqual((self.rows()[decision_id]['primary_failure'], self.rows()[decision_id]['direction_outcome']),
                         ('forecast', 'wrong'))

    def test_a_settled_row_of_the_current_version_is_left_alone(self) -> None:
        decision_id = self.add_decision()
        store().upsert_attribution(record(decision_id, failure='forecast'), db_path=self.db_path)
        store().upsert_attribution(record(decision_id, failure='evidence'), db_path=self.db_path)
        self.assertEqual(self.rows()[decision_id]['primary_failure'], 'forecast')

    def test_a_row_from_an_older_version_is_recomputed(self) -> None:
        decision_id = self.add_decision()
        store().upsert_attribution(record(decision_id, failure='forecast', version='decision-attribution-v0'),
                                   db_path=self.db_path)
        store().upsert_attribution(record(decision_id, failure='evidence'), db_path=self.db_path)
        self.assertEqual((self.rows()[decision_id]['primary_failure'], self.rows()[decision_id]['attribution_version']),
                         ('evidence', 'decision-attribution-v1'))


class DecisionsNeedingAttributionTests(AttributionDatabase):
    def test_unattributed_pending_and_stale_decisions_are_selected_oldest_first(self) -> None:
        settled = self.add_decision(at='2026-03-01T15:00:00+00:00')
        stale = self.add_decision(at='2026-03-03T15:00:00+00:00')
        pending = self.add_decision(at='2026-03-04T15:00:00+00:00')
        fresh = self.add_decision(at='2026-03-02T15:00:00+00:00')
        store().upsert_attribution(record(settled), db_path=self.db_path)
        store().upsert_attribution(record(stale, version='decision-attribution-v0'), db_path=self.db_path)
        store().upsert_attribution(record(pending, failure='pending', direction_outcome='pending'), db_path=self.db_path)
        self.assertEqual(self.needing(), [fresh, stale, pending])
        self.assertEqual(self.needing(limit=2), [fresh, stale])

    def test_a_row_carries_the_frozen_input_thesis_check_primary_catalyst_and_residual(self) -> None:
        with_candidate = self.add_decision()
        self.add_candidate('candidate-1', with_candidate)
        self.add_thesis_check('candidate-1', status='validated_with_warnings')
        self.add_primary_catalyst('candidate-1', roots=3, primary_source=True)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO candidate_price_observations (candidate_id, catalyst_id, observed_at, bucket, "
                         "snapshot_json, forward_residual_return) VALUES ('candidate-1', 'catalyst:1', ?, 'b', '{}', 0.02)",
                         (DECISION_AT,))
        bare = self.add_decision(at='2026-03-03T15:00:00+00:00')
        rows = {row['id']: row for row in store().decisions_needing_attribution(10, db_path=self.db_path)}
        row, plain = rows[with_candidate], rows[bare]
        self.assertEqual(json.loads(row['manager_input_json'])['replay_input']['company'], {'symbol': 'MSFT'})
        self.assertEqual(row['thesis_check']['status'], 'validated_with_warnings')
        self.assertEqual((row['catalyst_state']['independent_root_count'], row['catalyst_state']['primary_source_present']),
                         (3, True))
        self.assertEqual(row['forward_residual_return'], 0.02)
        self.assertEqual((plain['thesis_check'], plain['catalyst_state'], plain['forward_residual_return']),
                         (None, None, None))


class RunnerTests(AttributionDatabase):
    def attribute(self, **kwargs) -> dict:
        return runner().attribute_decisions(db_path=self.db_path, **kwargs)

    def test_a_decision_with_closes_is_scored_from_the_stored_daily_bars(self) -> None:
        decision_id = self.add_decision('call', reference=400.0)
        self.add_closes(*[(day.isoformat(), 410.0) for day in TRADING_DAYS[:5]])
        result = self.attribute()
        row = self.rows()[decision_id]
        self.assertEqual((result['selected'], result['written']), (1, 1))
        self.assertEqual((row['direction_outcome'], row['expression_outcome'], row['primary_failure']),
                         ('correct', 'not_traded', 'none'))

    def test_a_decision_long_after_its_horizon_without_closes_is_unavailable(self) -> None:
        decision_id = self.add_decision('call')
        self.attribute()
        self.assertEqual(self.rows()[decision_id]['direction_outcome'], 'unavailable')

    def test_a_decision_made_moments_ago_stays_pending_and_is_picked_up_again(self) -> None:
        decision_id = self.add_decision('call', at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
        first, second = self.attribute(), self.attribute()
        self.assertEqual((self.rows()[decision_id]['primary_failure'], first['pending'], second['selected']),
                         ('pending', 1, 1))

    def test_settled_decisions_are_not_selected_again(self) -> None:
        self.add_decision('neither')
        self.attribute()
        self.assertEqual(self.attribute()['selected'], 0)

    def test_the_limit_takes_the_oldest_decisions_first(self) -> None:
        ids = [self.add_decision('neither', at=f'2026-03-0{day}T15:00:00+00:00') for day in (3, 1, 2)]
        result = self.attribute(limit=2)
        self.assertEqual((result['selected'], sorted(self.rows())), (2, sorted([ids[1], ids[2]])))

    def test_a_phase_4_check_on_the_decisions_candidate_sets_the_evidence(self) -> None:
        decision_id = self.add_decision('call')
        self.add_candidate('candidate-1', decision_id)
        self.add_thesis_check('candidate-1', status='validated')
        self.attribute()
        row = self.rows()[decision_id]
        self.assertEqual((row['evidence_quality'], row['evidence_source']), ('strong', 'thesis_check'))

    def test_without_a_check_the_frozen_strategist_claims_and_catalyst_grade_the_proxy(self) -> None:
        ids = list(range(1, 11))
        decision_id = self.add_decision('call', strategist_recommendation=strategist(claim('catalyst', *ids)),
                                        articles=[article(i) for i in ids])
        self.add_candidate('candidate-1', decision_id)
        self.add_primary_catalyst('candidate-1')
        self.attribute()
        row = self.rows()[decision_id]
        self.assertEqual((row['evidence_quality'], row['evidence_source']), ('strong', 'grounding_proxy'))
        self.assertEqual(row['record']['evidence']['signals']['independent_roots'], 2)

    def test_a_traded_decision_gets_its_expression_from_the_realized_pnl(self) -> None:
        decision_id = self.add_decision('call', reference=400.0, trade_executed=1, realized_pnl_pct=-0.3)
        self.add_closes(*[(day.isoformat(), 410.0) for day in TRADING_DAYS[:5]])
        self.attribute()
        row = self.rows()[decision_id]
        self.assertEqual((row['expression_outcome'], row['primary_failure']), ('lost_despite_direction', 'expression'))


class CollectorHookTests(unittest.TestCase):
    def cycle(self, attribute_decisions):
        collector = importlib.import_module('news_collector_main')
        calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(collector, 'run_news_collection_pipeline', return_value={}))
            stack.enter_context(patch.object(collector.JsonFileWriter, 'write'))
            for target in ('services.market_regime.build_market_regime_snapshot',
                           'services.forward_evaluation.run_forward_evaluation_cycle',
                           'services.company_evidence_service.collect_company_evidence',
                           'services.event_discovery.discover_company_events'):
                stack.enter_context(patch(target, return_value={}))
            stack.enter_context(patch('services.catalyst_response_collector.collect_catalyst_responses',
                                      side_effect=lambda *a, **k: calls.append('responses') or {}))
            stack.enter_context(patch('services.decision_attribution_runner.attribute_decisions',
                                      side_effect=lambda *a, **k: calls.append('attribution') or attribute_decisions()))
            return collector.run_news_collection_cycle(), calls

    def test_attribution_runs_after_response_collection_and_its_counts_are_in_the_result(self) -> None:
        result, calls = self.cycle(lambda: {'selected': 3})
        self.assertEqual((calls, result['decision_attribution']), (['responses', 'attribution'], {'selected': 3}))

    def test_an_attribution_failure_does_not_stop_the_cycle(self) -> None:
        def explode():
            raise RuntimeError('database is locked')

        with self.assertLogs(level='ERROR'):
            result, calls = self.cycle(explode)
        self.assertIsNone(result['decision_attribution'])
        self.assertIn('event_discovery', result)


if __name__ == '__main__':
    unittest.main()
