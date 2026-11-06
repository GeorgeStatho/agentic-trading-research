"""Companies with an admitted discovery entry join the collector's news cycle next to the ranked companies."""
from contextlib import closing
from dataclasses import replace
import unittest
from unittest.mock import call, patch

from db_common import get_connection
from tests.research_foundation_fixtures import ENTRY, START
from tests.integration.test_discovery_production import EnforceFixture


def company(symbol, sector='technology', industry='semiconductors', company_id=2):
    return {'symbol': symbol, 'company_id': company_id, 'sector_key': sector, 'industry_key': industry}


class NewsRefreshCompanyTests(EnforceFixture):
    def setUp(self):
        super().setUp()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM discovery_news_refreshes')

    def companies(self, mode='shadow', **overrides):
        from services.discovery.discovery_routing import news_refresh_companies
        policy = replace(self.policy, mode=mode, **overrides)
        return news_refresh_companies(policy=policy, at=ENTRY, db_path=self.db)

    def test_shadow_returns_the_admitted_company_with_its_sector_and_industry(self):
        with closing(get_connection(self.db)) as conn:
            sector, industry = conn.execute('''SELECT se.sector_key, i.industry_key FROM companies c
                JOIN industries i ON i.id=c.industry_id JOIN sectors se ON se.id=i.sector_id WHERE c.id=2''').fetchone()
        self.assertEqual(self.companies(), [company('OTHER', sector, industry)])

    def test_enforce_also_refreshes_and_disabled_does_not(self):
        self.assertEqual([c['symbol'] for c in self.companies('enforce')], ['OTHER'])
        self.assertEqual(self.companies('disabled'), [])

    def test_ranked_symbols_are_excluded_and_the_limit_caps_the_list(self):
        from services.discovery.discovery_routing import news_refresh_companies
        policy = replace(self.policy, mode='shadow')
        self.assertEqual(news_refresh_companies(policy=policy, at=ENTRY, exclude_symbols=['other'], db_path=self.db), [])
        self.assertEqual(self.companies(production_limit=0), [])

    def test_a_company_refreshed_since_its_admission_is_not_refreshed_again(self):
        from services.discovery.discovery_routing import record_news_refresh
        record_news_refresh(2, START, db_path=self.db)
        self.assertEqual([c['symbol'] for c in self.companies()], ['OTHER'])
        record_news_refresh(2, ENTRY, db_path=self.db)
        self.assertEqual(self.companies(), [])


class RefreshDiscoveryCompanyNewsTests(unittest.TestCase):
    def run_refresh(self, companies, *, refreshed_sectors=(), refreshed_industries=(), company_failure=None):
        from agent_pipeline import news_refresh
        patches = {name: patch.object(news_refresh, name, return_value=1) for name in (
            '_scrape_sector_news', 'classify_sector_articles', 'classify_sector_articles_to_industries',
            '_scrape_industry_news', '_scrape_company_news', 'classify_company_articles', 'record_news_refresh')}
        mocks = {name: patcher.start() for name, patcher in patches.items()}
        self.addCleanup(patch.stopall)
        if company_failure:
            def classify(symbol):
                if symbol == company_failure:
                    raise RuntimeError('model down')
                return 1
            mocks['classify_company_articles'].side_effect = classify
        with patch.object(news_refresh, 'news_refresh_companies', return_value=companies) as selection:
            result = news_refresh.refresh_discovery_company_news(
                ranked_symbols={'NVDA'}, refreshed_sectors=set(refreshed_sectors),
                refreshed_industries=set(refreshed_industries))
        self.assertEqual(selection.call_args.kwargs['exclude_symbols'], {'NVDA'})
        return result, mocks

    def test_an_unrefreshed_sector_and_industry_are_scraped_and_classified_with_the_company(self):
        result, mocks = self.run_refresh([company('OTHER')])
        mocks['_scrape_sector_news'].assert_called_once_with('technology')
        mocks['classify_sector_articles'].assert_called_once_with('technology')
        mocks['classify_sector_articles_to_industries'].assert_called_once_with('technology')
        mocks['_scrape_industry_news'].assert_called_once_with('semiconductors')
        mocks['_scrape_company_news'].assert_called_once_with('OTHER')
        mocks['classify_company_articles'].assert_called_once_with('OTHER')
        self.assertEqual(mocks['record_news_refresh'].call_args.args[0], 2)
        self.assertEqual([row['symbol'] for row in result['companies']], ['OTHER'])

    def test_a_sector_and_industry_the_ranked_loop_refreshed_are_skipped(self):
        _, mocks = self.run_refresh([company('OTHER')], refreshed_sectors={'technology'},
                                    refreshed_industries={'semiconductors'})
        for name in ('_scrape_sector_news', 'classify_sector_articles', 'classify_sector_articles_to_industries',
                     '_scrape_industry_news'):
            mocks[name].assert_not_called()
        mocks['classify_company_articles'].assert_called_once_with('OTHER')

    def test_two_companies_in_one_sector_share_one_sector_refresh(self):
        _, mocks = self.run_refresh([company('OTHER'), company('THIRD', industry='software', company_id=3)])
        mocks['_scrape_sector_news'].assert_called_once_with('technology')
        self.assertEqual(mocks['_scrape_industry_news'].call_args_list, [call('semiconductors'), call('software')])

    def test_a_failed_company_is_not_recorded_and_the_next_one_still_runs(self):
        result, mocks = self.run_refresh([company('OTHER'), company('THIRD', company_id=3)], company_failure='OTHER')
        self.assertEqual([c.args[0] for c in mocks['record_news_refresh'].call_args_list], [3])
        self.assertEqual([row['symbol'] for row in result['failed']], ['OTHER'])


class CollectionCycleWiringTests(unittest.TestCase):
    def test_the_news_collection_cycle_refreshes_discovery_companies_after_the_ranked_loop(self):
        from agent_pipeline import news_refresh
        rankings = {'top_sectors': [{'sector_key': 'technology'}]}
        with patch.object(news_refresh, '_run_scrape_subprocess', return_value=0), \
                patch.object(news_refresh, 'classify_macro_news_to_sectors', return_value=[]), \
                patch.object(news_refresh, 'get_current_rankings', return_value=rankings), \
                patch.object(news_refresh, 'classify_sector_articles', return_value={}), \
                patch.object(news_refresh, 'classify_sector_articles_to_industries', return_value={}), \
                patch.object(news_refresh, '_get_ranked_industries_for_sector', return_value=[]), \
                patch.object(news_refresh, 'refresh_discovery_company_news',
                             return_value={'companies': [], 'failed': []}) as refresh:
            result = news_refresh.run_news_collection_pipeline()
        self.assertEqual(refresh.call_args.kwargs, {'ranked_symbols': set(), 'refreshed_sectors': {'technology'},
                                                     'refreshed_industries': set()})
        self.assertEqual(result['discovery_news'], {'companies': [], 'failed': []})


if __name__ == '__main__':
    unittest.main()
