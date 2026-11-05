"""Dated, replayable packages and transparent calculations over compatible facts."""
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime

from market_db_modules.company_evidence import (
    get_evidence_document, list_company_documents, list_company_facts,
    record_company_fact, record_evidence_package,
)
from market_db_modules.foundation_store import digest
from research_foundation_contracts import CompanyFact, EvidencePackage
from services.discovery.company_evidence_extraction import comparison_key, guidance_midpoint, report_date


MAX_DOCUMENTS = 24
MAX_FACTS = 240
REQUIRED_METRICS = {'revenue', 'eps_diluted', 'operating_cash_flow', 'cash'}


def _retained_consensus(consensus_documents, facts, at):
    """The newest estimate, plus the last one captured for the most recent report already due by `at`.

    Yahoo rolls on to the next earnings date once a company reports, so without the second document
    the estimate the reported quarter is judged against would be gone. `consensus_documents` is in
    publication order; older captures of either report are dropped.
    """
    if not consensus_documents:
        return set()
    reports = {}
    for fact in facts:
        reported = report_date(fact)
        if reported is not None:
            reports.setdefault(fact['document_id'], reported)
    kept = {consensus_documents[-1]['document_id']}
    cutoff = datetime.fromisoformat(at).date()
    due = [doc for doc in consensus_documents if reports.get(doc['document_id']) and reports[doc['document_id']] <= cutoff]
    if due:
        last_report = max(reports[doc['document_id']] for doc in due)
        kept.add([doc for doc in due if reports[doc['document_id']] == last_report][-1]['document_id'])
    return kept


def build_company_package(company, document_ids, at, policy, *, db_path, gaps=()):
    documents = {doc['document_id']: doc for doc in list_company_documents(company['id'], as_of=at, db_path=db_path)}
    documents.update({identity: get_evidence_document(identity, db_path=db_path) for identity in document_ids})
    ordered = sorted(documents.values(), key=lambda doc: (doc['published_at'], doc['ingested_at'], doc['document_id']))
    gaps = list(gaps)
    # Consensus is re-fetched every refresh with published_at=now; keep only the latest so it neither
    # crowds primary documents out of the window nor stands in for them as the newest disclosure.
    primary = [doc for doc in ordered if doc['document_type'] != 'consensus']
    if len(primary) > MAX_DOCUMENTS:
        gaps.append('document_history_truncated')
    kept = {doc['document_id'] for doc in primary[-MAX_DOCUMENTS:]}
    company_facts = list_company_facts(company['id'], as_of=at, db_path=db_path)
    kept.update(_retained_consensus([doc for doc in ordered if doc['document_type'] == 'consensus'],
                                    company_facts, at))
    ordered = [doc for doc in ordered if doc['document_id'] in kept]
    documents = {doc['document_id']: doc for doc in ordered}
    facts = [f for f in company_facts if f['document_id'] in documents and f['kind'] != 'derived']
    # Preserve the newest economic periods first; comparative history has lower priority.
    facts.sort(key=lambda fact: (fact['fiscal_period'], fact['available_at'], fact['fact_id']))
    if len(facts) > MAX_FACTS:
        gaps.append('fact_history_truncated')
    facts = facts[-MAX_FACTS:]
    active = _active_facts(facts)
    gaps.extend(_coverage_gaps(active))
    claims, opposing, derived = _comparisons(facts, active, at, db_path)
    facts.extend(derived)
    for doc in ordered:
        gaps.extend(doc.get('metadata', {}).get('gaps', []))
    if not primary:
        return None
    newest = primary[-1]
    event_key = newest.get('metadata', {}).get('event_key', newest['document_id'])
    catalyst_id = _ensure_catalyst(company, event_key, newest, at, db_path)
    superseded_documents = {doc.get('supersedes_document_id') for doc in ordered}
    scheduled = tuple({**event, 'document_id': doc['document_id'],
                       'superseded': doc['document_id'] in superseded_documents} for doc in ordered
                      for event in doc.get('metadata', {}).get('scheduled_events', []))
    if not scheduled:
        gaps.append('scheduled_earnings_event_unavailable')
    revision = digest({'documents': sorted(documents), 'facts': sorted(f['fact_id'] for f in facts)})
    age = (datetime.fromisoformat(at) - datetime.fromisoformat(newest['published_at'])).total_seconds()
    fields = dict(company_id=company['id'], catalyst_id=catalyst_id, event_revision=revision,
                  information_cutoff_time=at, document_ids=tuple(sorted(documents)),
                  fact_ids=tuple(sorted(f['fact_id'] for f in facts)), supporting_claims=tuple(claims),
                  opposing_claims=tuple(opposing), gaps=tuple(sorted(set(gaps))), scheduled_events=scheduled,
                  coverage_status='partial' if gaps else 'complete',
                  freshness_status=('unknown' if newest.get('metadata', {}).get('publication_precision') == 'unknown'
                                    else 'fresh' if age <= policy.stale_seconds else 'stale'))
    package = EvidencePackage(package_id='package:' + digest(fields), **fields)
    record_evidence_package(package, db_path=db_path)
    from contextlib import closing
    from db_common import get_connection
    from market_db_modules.company_interest import signal_discovery
    with closing(get_connection(db_path)) as conn, conn:
        signal_discovery(conn, company['id'], 'primary_evidence', revision, at=at)
    return package.package_id


def _active_facts(facts):
    superseded = {f['supersedes_fact_id'] for f in facts if f.get('supersedes_fact_id')}
    return [fact for fact in facts if fact['fact_id'] not in superseded]


def _coverage_gaps(facts):
    gaps = [f'{metric}_unavailable' for metric in sorted(REQUIRED_METRICS - {f['metric'] for f in facts})]
    if not any(f['kind'] == 'consensus' for f in facts):
        gaps.append('consensus_unavailable; market_surprise_unknown')
    if not any(f['kind'] == 'guidance' for f in facts):
        gaps.append('current_guidance_unavailable')
    return gaps


def _comparisons(facts, active, at, db_path):
    claims, opposing, derived = [], [], []
    groups = defaultdict(list)
    for fact in active:
        groups[(fact['kind'], comparison_key(fact))].append(fact)
    for (kind, key), values in groups.items():
        distinct = {(f['value'], f.get('range_high')) for f in values}
        if len(distinct) > 1:
            opposing.append({'type': 'conflicting_values', 'metric': key[0], 'fiscal_period': key[1],
                             'fact_ids': [f['fact_id'] for f in values]})
    for current in active:
        if current['kind'] != 'guidance' or current['value'] is None:
            continue
        compatible = [f for f in facts if comparison_key(f) == comparison_key(current)
                      and f['fact_id'] != current['fact_id'] and f['kind'] in ('prior_guidance', 'guidance')
                      and (f['kind'] == 'prior_guidance' or f['fact_id'] == current.get('supersedes_fact_id'))
                      and f['value'] is not None]
        if not compatible:
            opposing.append({'type': 'missing_comparable_prior_guidance', 'fact_ids': [current['fact_id']],
                             'reason': 'No verified baseline with identical period, basis, currency and unit'})
            continue
        current_values = groups[(current['kind'], comparison_key(current))]
        if (len({(f['value'], f.get('range_high')) for f in compatible}) != 1
                or len({(f['value'], f.get('range_high')) for f in current_values}) != 1):
            opposing.append({'type': 'ambiguous_guidance_comparison',
                             'fact_ids': [current['fact_id']] + [f['fact_id'] for f in compatible]})
            continue
        prior = sorted(compatible, key=lambda f: (f['available_at'], f['fact_id']))[-1]
        difference = guidance_midpoint(current) - guidance_midpoint(prior)
        computed = _derived(current, current['metric'] + '_guidance_midpoint_change', difference,
                            '(current_low + current_high)/2 - (prior_low + prior_high)/2',
                            [current, prior], at, db_path)
        derived.append(computed)
        claim = {'type': 'guidance_change', 'direction': 'increase' if difference > 0 else 'decrease' if difference < 0 else 'unchanged',
                 'fact_ids': [current['fact_id'], prior['fact_id'], computed['fact_id']],
                 'market_surprise': 'unknown_without_comparable_consensus'}
        (opposing if difference < 0 else claims).append(claim)
    derived.extend(_margins(active, at, db_path))
    return claims, opposing, derived


def _margins(facts, at, db_path):
    derived = []
    for numerator in facts:
        metric = {'gross_profit': 'gross_margin', 'operating_income': 'operating_margin'}.get(numerator['metric'])
        if not metric or numerator['kind'] != 'actual' or numerator['value'] is None:
            continue
        revenue = [f for f in facts if f['metric'] == 'revenue' and f['kind'] == 'actual'
                   and f['document_id'] == numerator['document_id'] and f['value'] not in (None, 0)
                   and comparison_key(f)[1:] == comparison_key(numerator)[1:]]
        if len({f['value'] for f in revenue}) != 1:
            continue
        derived.append(_derived(numerator, metric, numerator['value'] / revenue[0]['value'] * 100,
                                'numerator / revenue * 100', [numerator, revenue[0]], at, db_path,
                                unit='percent', currency=None, scale=1))
    return derived


def _derived(base, metric, value, formula, inputs, at, db_path, **overrides):
    fields = {key: base.get(key) for key in ('company_id', 'unit', 'currency', 'scale', 'fiscal_period',
                                           'period_type', 'accounting_basis', 'document_id', 'source_start', 'source_end')}
    fields.update(metric=metric, value=value, kind='derived', extraction_version='evidence-comparisons-v1',
                  calculation={'formula': formula, 'input_fact_ids': [f['fact_id'] for f in inputs],
                               'inputs': [{'value': f['value'], 'range_high': f.get('range_high')} for f in inputs]}, **overrides)
    identity = 'fact:' + digest(fields)
    from market_db_modules.company_evidence import get_company_fact
    try:
        return get_company_fact(identity, db_path=db_path)
    except ValueError as exc:
        if not str(exc).startswith('Unknown '):
            raise
    fact = CompanyFact(fact_id=identity, available_at=at, **fields)
    record_company_fact(fact, db_path=db_path)
    return asdict(fact)


def _ensure_catalyst(company, event_key, document, at, db_path):
    from contextlib import closing
    from db_common import get_connection
    from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
    identity = 'catalyst:company-evidence:' + digest([company['id'], event_key])
    with closing(get_connection(db_path)) as conn, conn:
        if conn.execute('SELECT 1 FROM catalysts WHERE catalyst_id=?', (identity,)).fetchone() is None:
            state = build_catalyst_state(identity, [], ticker=company['symbol'])
            state.update(catalyst_type='company_disclosure', catalyst_subtype='earnings_guidance',
                         catalyst_scope='company', mechanism='Primary company disclosure; direction unclassified',
                         first_public_time=document['published_at'], first_seen_time=document['first_observed_at'],
                         latest_evidence_time=at, primary_source_present=True)
            store_catalyst_state(state, conn=conn)
    return identity
