"""Analyst consensus is matched to the quarter it was an estimate for.

Yahoo consensus is labelled `next_report:<D>` (the earnings date D) with basis
`analyst_consensus`; SEC actuals carry dated periods and basis `US-GAAP`, so an exact
comparison key never matched and a consensus baseline could never be cited. The estimate for
report D is for the latest quarter that ended before D, so it matches that quarter's actual:
revenue across bases, other metrics only on an identical basis (analyst EPS is usually adjusted,
GAAP EPS is not). The package must also keep the estimate captured before the report, which is
lost today once Yahoo rolls on to the next earnings date.
"""
from datetime import date, datetime
from types import SimpleNamespace
import unittest

from tests.research_foundation_fixtures import database, START, ENTRY
from tests.test_company_evidence import COMPANY, actuals, guidance
from tests.test_yahoo_consensus import EVENT, consensus_doc
from agent_builders.company_evidence_payload import build_company_evidence_payload
from market_db_modules.company_evidence import get_evidence_package
from services.discovery.analyst_consensus import (
    CURRENCY_NOT_USD, CURRENCY_UNKNOWN, consensus_matches_actual, report_date, report_label,
)
from services.discovery.company_evidence_extraction import ingest_source
from services.discovery.company_evidence_packages import build_company_package
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.thesis.evidence_thesis_checks import _expectation_failures, _surprise_claimed_without_consensus
from services.thesis.evidence_thesis_numbers import incompatible_calculation_facts
from Webscraping.company_evidence.models import SourceDocument

REPORT = '2026-10-28'
REPORTED_QUARTER = '2026-07-01/2026-09-30'


def fact(fact_id, kind, period, *, metric='revenue', basis=None, period_type='quarter', unit='USD',
         currency='USD', scale=1, value=100.0):
    basis = basis or ('analyst_consensus' if kind == 'consensus' else 'US-GAAP')
    return {'fact_id': fact_id, 'kind': kind, 'value': value, 'metric': metric, 'fiscal_period': period,
            'period_type': period_type, 'accounting_basis': basis, 'unit': unit, 'currency': currency, 'scale': scale}


def consensus(period=f'next_report:{REPORT}', **kwargs):
    return fact('c', 'consensus', period, **kwargs)


def actual(period=REPORTED_QUARTER, **kwargs):
    return fact('a', 'actual', period, **kwargs)


class MatchTests(unittest.TestCase):
    def test_revenue_estimate_matches_the_quarter_reported_on_that_date(self):
        self.assertTrue(consensus_matches_actual(consensus(), actual()))

    def test_previous_quarter_does_not_match(self):
        self.assertFalse(consensus_matches_actual(consensus(), actual('2026-04-01/2026-06-30')))

    def test_prior_year_comparative_does_not_match(self):
        # A 10-Q also reports the same quarter a year earlier; that column is not what was estimated.
        self.assertFalse(consensus_matches_actual(consensus(), actual('2025-07-01/2025-09-30')))

    def test_quarter_ending_on_or_after_the_report_date_does_not_match(self):
        self.assertFalse(consensus_matches_actual(consensus(), actual('2026-10-01/2026-12-31')))
        self.assertFalse(consensus_matches_actual(consensus('next_report:2026-09-30'), actual()))

    def test_gaap_eps_does_not_match_an_analyst_eps_estimate(self):
        self.assertFalse(consensus_matches_actual(consensus(metric='eps_diluted', unit='USD/shares'),
                                                  actual(metric='eps_diluted', unit='USD/shares')))

    def test_non_revenue_metric_matches_only_on_an_identical_basis(self):
        same = 'adjusted'
        self.assertTrue(consensus_matches_actual(consensus(metric='eps_diluted', unit='USD/shares', basis=same),
                                                 actual(metric='eps_diluted', unit='USD/shares', basis=same)))

    def test_unknown_report_date_does_not_match(self):
        self.assertFalse(consensus_matches_actual(consensus('next_report:unknown'), actual()))

    def test_year_to_date_or_annual_actuals_do_not_match(self):
        self.assertFalse(consensus_matches_actual(consensus(), actual('2026-01-01/2026-09-30', period_type='duration')))
        self.assertFalse(consensus_matches_actual(consensus(), actual('2025-10-01/2026-09-30', period_type='annual')))

    def test_units_currency_and_scale_must_agree(self):
        self.assertFalse(consensus_matches_actual(consensus(), actual(currency='EUR', unit='EUR')))
        self.assertFalse(consensus_matches_actual(consensus(), actual(scale=1000)))

    def test_metric_must_agree(self):
        self.assertFalse(consensus_matches_actual(consensus(), actual(metric='net_income')))

    # 2026-11-09, ANET: Yahoo kept the passed earnings date while its estimates moved to the next quarter, so
    # snapshots taken after the report carried next quarter's estimate under the reported quarter's label.
    def test_estimate_captured_after_its_report_date_does_not_match(self):
        stale = {**consensus(), 'available_at': at('2026-10-29', 4)}
        self.assertFalse(consensus_matches_actual(stale, actual()))

    # 2026-11-09 review: on a report date Yahoo had often rolled already (one EPS estimate went 1.58 -> 3.51 that day).
    def test_estimate_captured_on_its_report_date_does_not_match(self):
        for captured in (at('2026-10-28', 7), at('2026-10-28', 17)):
            with self.subTest(captured=captured):
                self.assertFalse(consensus_matches_actual({**consensus(), 'available_at': captured}, actual()))

    def test_estimate_captured_before_its_report_date_still_matches(self):
        self.assertTrue(consensus_matches_actual({**consensus(), 'available_at': at('2026-10-27', 23)}, actual()))

    def test_the_report_date_is_a_new_york_date(self):
        # 01:00 UTC on the 28th is 21:00 ET on the 27th, the evening before the report.
        self.assertTrue(consensus_matches_actual({**consensus(), 'available_at': at('2026-10-28', 1)}, actual()))

    def test_a_report_label_reads_back_as_its_report_date(self):
        self.assertEqual(report_date({'fiscal_period': report_label(date(2026, 10, 28))}), date(2026, 10, 28))
        self.assertIsNone(report_date({'fiscal_period': report_label(None)}))

    def test_requires_a_consensus_and_an_actual(self):
        self.assertFalse(consensus_matches_actual(actual(), actual()))
        self.assertFalse(consensus_matches_actual(consensus(), fact('g', 'guidance', REPORTED_QUARTER)))


class ThesisCheckTests(unittest.TestCase):
    BEAT = 'Q3 revenue beat analyst estimates'

    def baseline_failures(self, actual_fact):
        facts = {'c': consensus(), 'a': actual_fact}
        change = {'assessment': 'supported_comparison', 'baseline_type': 'consensus',
                  'statement': 'Revenue came in above the baseline', 'claim_ids': ['S1']}
        statement = {'claim_type': 'observed_fact', 'fact_ids': ['c', 'a']}
        return _expectation_failures({'change_vs_prior_expectations': change}, {'S1': statement},
                                     {'S1': {'status': 'verified'}}, SimpleNamespace(facts=facts))

    def test_matched_consensus_cites_the_expectation_baseline(self):
        self.assertNotIn('expectation_baseline_not_cited', self.baseline_failures(actual()))

    def test_prior_year_comparative_still_does_not_cite_the_baseline(self):
        self.assertIn('expectation_baseline_not_cited', self.baseline_failures(actual('2025-07-01/2025-09-30')))

    def test_gaap_eps_still_does_not_cite_an_analyst_eps_baseline(self):
        facts = {'c': consensus(metric='eps_diluted', unit='USD/shares'),
                 'a': actual(metric='eps_diluted', unit='USD/shares')}
        change = {'assessment': 'supported_comparison', 'baseline_type': 'consensus',
                  'statement': 'EPS came in above the baseline', 'claim_ids': ['S1']}
        failures = _expectation_failures({'change_vs_prior_expectations': change},
                                         {'S1': {'claim_type': 'observed_fact', 'fact_ids': ['c', 'a']}},
                                         {'S1': {'status': 'verified'}}, SimpleNamespace(facts=facts))
        self.assertIn('expectation_baseline_not_cited', failures)

    def test_matched_pair_licenses_a_beat_claim(self):
        self.assertFalse(_surprise_claimed_without_consensus(self.BEAT, [consensus(), actual()], []))

    def test_unmatched_pair_still_does_not_license_a_beat_claim(self):
        self.assertTrue(_surprise_claimed_without_consensus(self.BEAT, [consensus(), actual('2025-07-01/2025-09-30')], []))

    def test_matched_pair_is_a_valid_calculation_input(self):
        self.assertEqual(incompatible_calculation_facts([consensus(), actual()]), [])

    def test_unmatched_pair_is_still_an_invalid_calculation_input(self):
        self.assertTrue(incompatible_calculation_facts([consensus(), actual('2025-07-01/2025-09-30')]))


def at(day, hour=12):
    return datetime.fromisoformat(f'{day}T{hour:02d}:00:00+00:00').isoformat()


def two_metric_consensus_doc(captured_at, report_date, revenue=110.0, eps=4.72):
    """Like the Yahoo adapter: one document per refresh carrying both the revenue and EPS estimates."""
    parts = [f'USD analyst consensus revenue next_report:{report_date} average {revenue}',
             f'USD analyst consensus eps_diluted next_report:{report_date} average {eps}']
    text = '\n'.join(parts)
    records = []
    for part, metric, value, unit in ((parts[0], 'revenue', revenue, 'USD'), (parts[1], 'eps_diluted', eps, 'USD/shares')):
        start = text.index(part)
        records.append({'metric': metric, 'kind': 'consensus', 'value': value, 'unit': unit, 'currency': 'USD',
                        'scale': 1, 'fiscal_period': f'next_report:{report_date}', 'period_type': 'quarter',
                        'accounting_basis': 'analyst_consensus', 'source_start': start, 'source_end': start + len(part),
                        'extraction_version': 'yahoo-calendar-consensus-v1'})
    return SourceDocument('yahoo_consensus', 'https://finance.yahoo.com/quote/NVDA/analysis', 'Yahoo Finance',
                          'consensus', text, captured_at, captured_at, f'yahoo_consensus:NVDA:{report_date}',
                          {'cik': COMPANY['cik'], 'publication_precision': 'timestamp'}, tuple(records),
                          ({**EVENT, 'scheduled_at': report_date},))


class PackageRetentionTests(unittest.TestCase):
    """ENTRY is 2026-09-02: a report on 2026-08-26 has happened, the next one (2026-11-25) has not."""

    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy()

    def ingest(self, source, observed=None):
        return ingest_source(COMPANY, source, observed or source.observed_at, db_path=self.db)[0]

    def build(self, ids, cutoff=ENTRY):
        identity = build_company_package(COMPANY, ids, cutoff, self.policy, db_path=self.db)
        return set(get_evidence_package(identity, db_path=self.db)['document_ids'])

    def test_keeps_the_estimate_from_before_the_last_report_and_the_newest_estimate(self):
        primary = self.ingest(guidance(at=START))
        pre_report_early = self.ingest(consensus_doc(at('2026-08-20'), period='2026-08-26', value=4.60))
        pre_report_last = self.ingest(consensus_doc(at('2026-08-25'), period='2026-08-26', value=4.65))
        next_early = self.ingest(consensus_doc(at('2026-08-27'), period='2026-11-25', value=5.00))
        next_latest = self.ingest(consensus_doc(at('2026-09-01'), period='2026-11-25', value=5.10))
        kept = self.build([primary, pre_report_early, pre_report_last, next_early, next_latest])
        self.assertIn(primary, kept)
        self.assertIn(pre_report_last, kept)
        self.assertIn(next_latest, kept)
        self.assertNotIn(pre_report_early, kept)
        self.assertNotIn(next_early, kept)

    def test_only_the_newest_estimate_when_no_report_has_happened(self):
        primary = self.ingest(guidance(at=START))
        older = self.ingest(consensus_doc(at('2026-08-27'), period='2026-11-25', value=5.00))
        newest = self.ingest(consensus_doc(at('2026-09-01'), period='2026-11-25', value=5.10))
        kept = self.build([primary, older, newest])
        self.assertIn(newest, kept)
        self.assertNotIn(older, kept)

    def test_older_reports_than_the_last_one_are_not_kept(self):
        primary = self.ingest(guidance(at=START))
        two_reports_ago = self.ingest(consensus_doc(at('2026-05-20'), period='2026-05-27', value=4.20))
        last_report = self.ingest(consensus_doc(at('2026-08-25'), period='2026-08-26', value=4.65))
        newest = self.ingest(consensus_doc(at('2026-09-01'), period='2026-11-25', value=5.10))
        kept = self.build([primary, two_reports_ago, last_report, newest])
        self.assertNotIn(two_reports_ago, kept)
        self.assertEqual({last_report, newest} & kept, {last_report, newest})

    def test_the_kept_pre_report_estimate_does_not_reschedule_the_past_report(self):
        # The pre-report estimate is kept only as a baseline; its earnings date has passed and must not
        # appear among the package's scheduled events next to the real upcoming one.
        primary = self.ingest(guidance(at=START))
        last_report = self.ingest(two_metric_consensus_doc(at('2026-08-25'), '2026-08-26'))
        newest = self.ingest(two_metric_consensus_doc(at('2026-09-01'), '2026-11-25'))
        identity = build_company_package(COMPANY, [primary, last_report, newest], ENTRY, self.policy, db_path=self.db)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertIn(last_report, package['document_ids'])
        self.assertEqual([event['scheduled_at'] for event in package['scheduled_events']], ['2026-11-25'])

    def test_snapshots_still_labelled_with_a_passed_report_are_not_kept(self):
        # ANET shape: after the 2026-08-26 report Yahoo still shows that date, but its estimate is next quarter's.
        primary = self.ingest(guidance(at=START))
        pre_report = self.ingest(two_metric_consensus_doc(at('2026-08-25', 17), '2026-08-26', revenue=3.33))
        stale_early = self.ingest(two_metric_consensus_doc(at('2026-08-28'), '2026-08-26', revenue=3.60))
        stale_latest = self.ingest(two_metric_consensus_doc(at('2026-09-01'), '2026-08-26', revenue=3.98))
        identity = build_company_package(COMPANY, [primary, pre_report, stale_early, stale_latest], ENTRY,
                                         self.policy, db_path=self.db)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertIn(pre_report, package['document_ids'])
        self.assertNotIn(stale_early, package['document_ids'])
        self.assertNotIn(stale_latest, package['document_ids'])
        self.assertEqual(package['scheduled_events'], [])

    def test_a_capture_on_the_report_date_gives_way_to_the_day_before(self):
        primary = self.ingest(guidance(at=START))
        day_before = self.ingest(two_metric_consensus_doc(at('2026-08-25', 20), '2026-08-26', revenue=3.33))
        on_the_day = self.ingest(two_metric_consensus_doc(at('2026-08-26', 8), '2026-08-26', revenue=3.60))
        kept = self.build([primary, day_before, on_the_day])
        self.assertIn(day_before, kept)
        self.assertNotIn(on_the_day, kept)

    def test_an_evening_refresh_the_day_before_a_report_keeps_the_last_reports_baseline(self):
        # 2026-11-10 review: 01:30 UTC on the 25th is 20:30 ET on the 24th, so the 11-25 report is still upcoming.
        # A UTC cutoff counted it as due too, and its estimate pushed out the 08-26 report's baseline.
        primary = self.ingest(guidance(at=START))
        last_report = self.ingest(consensus_doc(at('2026-08-25'), period='2026-08-26', value=4.65))
        upcoming = self.ingest(consensus_doc(at('2026-11-20'), period='2026-11-25', value=5.10))
        kept = self.build([primary, last_report, upcoming], cutoff='2026-11-25T01:30:00+00:00')
        self.assertEqual({last_report, upcoming} & kept, {last_report, upcoming})

    def consensus_kept(self, gap):
        primary = self.ingest(guidance(at=START))
        earlier = self.ingest(two_metric_consensus_doc(at('2026-09-01'), '2026-11-25'))
        identity = build_company_package(COMPANY, [primary, earlier], ENTRY, self.policy, db_path=self.db, gaps=(gap,))
        return earlier in get_evidence_package(identity, db_path=self.db)['document_ids']

    def test_a_reporter_in_another_currency_keeps_no_earlier_consensus(self):
        # SK hynix: won estimates stored as USD six times before the currency check existed.
        self.assertFalse(self.consensus_kept(CURRENCY_NOT_USD))

    def test_one_failed_currency_lookup_keeps_the_earlier_consensus(self):
        self.assertTrue(self.consensus_kept(CURRENCY_UNKNOWN))


class PayloadHintTests(unittest.TestCase):
    """The strategist sees which actual each consensus fact can be compared with."""

    def test_consensus_fact_lists_the_actuals_it_matches(self):
        db = database(self)
        # actuals() reports the quarter 2026-04-01/2026-06-30; its earnings date is 2026-07-29.
        ids = [ingest_source(COMPANY, actuals(at=START), START, db_path=db)[0],
               ingest_source(COMPANY, two_metric_consensus_doc(at('2026-07-28'), '2026-07-29'), at('2026-07-28'),
                             db_path=db)[0]]
        identity = build_company_package(COMPANY, ids, ENTRY, CompanyEvidencePolicy(), db_path=db)
        payload = build_company_evidence_payload(COMPANY['id'], package_id=identity, as_of=ENTRY, db_path=db)
        by_kind = {(f['kind'], f['metric']): f for f in payload['facts']}
        revenue_actual = by_kind[('actual', 'revenue')]
        self.assertEqual(by_kind[('consensus', 'revenue')]['matches_actual_fact_ids'], [revenue_actual['fact_id']])
        self.assertEqual(by_kind[('consensus', 'eps_diluted')]['matches_actual_fact_ids'], [])
        self.assertNotIn('matches_actual_fact_ids', revenue_actual)

    def test_a_snapshot_taken_after_the_report_is_not_offered_as_its_estimate(self):
        db = database(self)
        ids = [ingest_source(COMPANY, actuals(at=START), START, db_path=db)[0],
               ingest_source(COMPANY, two_metric_consensus_doc(at('2026-08-05'), '2026-07-29'), at('2026-08-05'),
                             db_path=db)[0]]
        identity = build_company_package(COMPANY, ids, ENTRY, CompanyEvidencePolicy(), db_path=db)
        payload = build_company_evidence_payload(COMPANY['id'], package_id=identity, as_of=ENTRY, db_path=db)
        self.assertEqual([f for f in payload['facts'] if f.get('matches_actual_fact_ids')], [])


class ConsensusNumberTests(unittest.TestCase):
    # 2026-11-09: a consensus range_high (top analyst estimate) was averaged into the mean, so every beat/miss failed.
    def test_beat_and_miss_figures_use_the_mean_estimate_not_the_high(self):
        from services.thesis.evidence_thesis_numbers import NumberSupport, stated_numbers
        estimate = {**consensus(value=8192019370.0), 'range_high': 9459000000.0}
        support = NumberSupport([estimate, actual(value=8549000000.0)], [], [])
        statement = 'Revenue beat consensus by $356,980,630, or 4.36%; the top estimate was $9.459 billion.'
        self.assertEqual([(n.text, support.supports(n)) for n in stated_numbers(statement)],
                         [('$356,980,630', True), ('4.36%', True), ('$9.459 billion', True)])


class StatedNumberTests(unittest.TestCase):
    def test_days_and_year_to_date_labels_are_not_figures(self):
        from services.thesis.evidence_thesis_numbers import stated_numbers
        texts = lambda s: [n.text for n in stated_numbers(s)]
        self.assertEqual(texts('Oil fell below $90 on Nov. 4 and Dec 9, 2026; OXY reports 2026-11-09.'), ['$90'])
        self.assertEqual(texts('9M 2026 revenue was $91.788B.'), ['$91.788B'])
        # Still checked: a $-amount in millions, and a bare number that only looks like a day.
        self.assertEqual(texts('Costs were $9M in 2026 and 4 plants closed.'), ['$9M', '4'])
        # Words that merely start like a month must not hide the figure after them.
        self.assertEqual(texts('Operating margin 48.76%; revenue declined 12%, decreased 3 and market 7.'),
                         ['48.76%', '12%', '3', '7'])
        self.assertEqual(texts('Nov 45 units and May 5% growth.'), ['45', '5%'])


class CrossMetricTests(unittest.TestCase):
    # 2026-11-09 answers: a margin and net debt from two metrics of the same statement.
    def test_margin_and_net_debt_from_one_statement_are_supported(self):
        from services.thesis.evidence_thesis_numbers import NumberSupport, stated_numbers
        income = fact('i', 'actual', REPORTED_QUARTER, metric='operating_income', value=2064000000.0)
        revenue = fact('r', 'actual', REPORTED_QUARTER, value=4233000000.0)
        debt = fact('d', 'actual', '2026-09-26', metric='debt_noncurrent', period_type='instant', value=46317000000.0)
        cash = fact('c', 'actual', '2026-09-26', metric='cash', period_type='instant', value=21846000000.0)
        for facts, statement in (([income, revenue], 'Operating margin was 48.76%.'),
                                 ([debt, cash], 'Net debt was about $24.471B.')):
            with self.subTest(statement=statement):
                self.assertEqual(incompatible_calculation_facts(facts), [])
                support = NumberSupport(facts, [], [])
                self.assertTrue(all(support.supports(n) for n in stated_numbers(statement)))

    # NVDA, 2026-11-02: gross margin 74.98% from gross profit $72.142B over revenue $96.221B.
    GROSS_PROFIT = fact('g', 'actual', REPORTED_QUARTER, metric='gross_profit', value=72142000000.0)
    REVENUE = fact('r', 'actual', REPORTED_QUARTER, value=96221000000.0)
    OPERATING_INCOME = fact('o', 'actual', REPORTED_QUARTER, metric='operating_income', value=63734000000.0)

    def margin_supported(self, facts):
        from services.thesis.evidence_thesis_numbers import NumberSupport, stated_numbers
        (margin,) = stated_numbers('Gross margin was 74.98%.')
        return NumberSupport(facts, [], []).supports(margin)

    def test_a_ratio_of_exactly_its_two_cited_facts_is_supported(self):
        self.assertEqual(incompatible_calculation_facts([self.GROSS_PROFIT, self.REVENUE]), [])
        self.assertTrue(self.margin_supported([self.GROSS_PROFIT, self.REVENUE]))

    def test_a_third_cited_metric_rules_out_cross_metric_ratios(self):
        # Every extra fact multiplies the ratios a made-up percentage could match.
        facts = [self.GROSS_PROFIT, self.REVENUE, self.OPERATING_INCOME]
        self.assertNotEqual(incompatible_calculation_facts(facts), [])
        self.assertFalse(self.margin_supported(facts))

    def test_the_checker_policy_version_records_the_stricter_rule(self):
        from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy
        self.assertEqual(EvidenceThesisPolicy().policy_version, 'evidence-thesis-v4')

    def test_other_quarter_or_basis_is_still_incompatible(self):
        revenue = fact('r', 'actual', REPORTED_QUARTER)
        for other in (fact('i', 'actual', '2026-04-01/2026-06-30', metric='operating_income'),
                      fact('i', 'actual', REPORTED_QUARTER, metric='operating_income', basis='non-GAAP'),
                      fact('i', 'guidance', REPORTED_QUARTER, metric='operating_income')):
            with self.subTest(other=other):
                self.assertNotEqual(incompatible_calculation_facts([revenue, other]), [])


if __name__ == '__main__':
    unittest.main()
