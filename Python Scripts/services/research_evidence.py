"""Deterministic coverage and immutable package assembly for the research loop."""
from market_db_modules.company_evidence import get_company_fact, get_evidence_document, record_evidence_package
from market_db_modules.foundation_store import digest
from research_foundation_contracts import EvidencePackage
from services.research_tools import active_facts, evidence_conflicts, guidance_comparisons, guidance_baselines


def initial_evidence(package, tools):
    request = tools.parse({'question': 'Validate initial archived evidence', 'critical': True,
                           'operation': 'find_contradictory_evidence',
                           'arguments': {'company_id': tools.company_id, 'event_id': tools.event_id, 'as_of': tools.cutoff}})
    facts = [get_company_fact(identity, db_path=tools.db_path) for identity in package['fact_ids']]
    facts = [f for f in facts if f['kind'] != 'derived']
    result = tools.validate_result(request, {
        'facts': facts, 'documents': [get_evidence_document(identity, db_path=tools.db_path) for identity in package['document_ids']],
        'price_responses': [], 'gaps': [], 'provider': tools.provider})
    return {**result, 'gaps': sorted(set(result['gaps'] + package['gaps']))}


def merge_evidence(evidence, result):
    merged = {**evidence}
    for field, key in (('facts', 'fact_id'), ('documents', 'document_id')):
        by_id = {v[key]: v for v in evidence[field]}
        by_id.update({v[key]: v for v in result[field]})
        merged[field] = sorted(by_id.values(), key=lambda v: v[key])
    merged['price_responses'] = list({digest(v): v for v in evidence['price_responses'] + result['price_responses']}.values())
    known = active_facts(merged['facts'])
    resolved = {fact['metric'] + '_unavailable' for fact in known}
    if any(f['kind'] == 'guidance' for f in known):
        resolved.add('current_guidance_unavailable')
    if any(f['kind'] == 'consensus' for f in known):
        resolved.add('consensus_unavailable; market_surprise_unknown')
    if merged['documents']:
        resolved.add('primary_package_unavailable')
    merged['gaps'] = sorted(set(evidence['gaps'] + result['gaps']) - resolved)
    merged['comparisons'] = guidance_comparisons(merged['facts'])
    merged['conflicts'] = evidence_conflicts(merged['facts'])
    return merged


def evidence_identity(evidence):
    return digest({key: evidence[key] for key in ('facts', 'documents', 'price_responses', 'comparisons', 'conflicts')})


def missing_guidance(facts):
    active = active_facts(facts)
    return [f for f in active if f['kind'] == 'guidance'
            and len({(p['value'], p.get('range_high')) for p in guidance_baselines(f, facts)}) != 1]


def required_requests(evidence, tools, completed):
    requests = []
    for fact in missing_guidance(evidence['facts']):
        request = tools.parse({'question': 'What was prior guidance for this same metric, fiscal period and basis?',
                               'operation': 'compare_guidance', 'critical': True,
                               'arguments': {'company_id': tools.company_id, 'metric': fact['metric'],
                                             'fiscal_period': fact['fiscal_period'], 'as_of': tools.cutoff}})
        requests.append(request)
    requests.append(tools.parse({'question': 'Does archived evidence contradict this company event?',
                                'operation': 'find_contradictory_evidence', 'critical': False,
                                'arguments': {'company_id': tools.company_id, 'event_id': tools.event_id, 'as_of': tools.cutoff}}))
    return list({r.key: r for r in requests if r.key not in completed}.values())


def coverage_outcome(progress):
    evidence = progress['evidence']
    if evidence['conflicts']:
        return 'conflicting_evidence', 'validated_sources_disagree'
    if progress['unresolved'] or missing_guidance(evidence['facts']):
        return 'deferred_missing_evidence', 'critical_evidence_unavailable'
    if not any(f['kind'] in {'actual', 'guidance'} for f in active_facts(evidence['facts'])):
        return 'deferred_missing_evidence', 'verified_company_facts_unavailable'
    if any('truncated' in gap for gap in evidence['gaps']):
        return 'deferred_missing_evidence', 'evidence_coverage_truncated'
    return 'ready', 'sufficient_verified_evidence_for_thesis_analysis'


def final_package(initial, progress, *, db_path):
    evidence = progress['evidence']
    gaps = sorted(set(evidence['gaps'] + ['unresolved:' + q['question'] for q in progress['unresolved']]))
    fields = {key: initial[key] for key in ('company_id', 'catalyst_id', 'event_revision', 'information_cutoff_time',
                                           'freshness_status', 'scheduled_events')}
    fields.update(document_ids=tuple(sorted(d['document_id'] for d in evidence['documents'])),
                  fact_ids=tuple(sorted(f['fact_id'] for f in evidence['facts'])),
                  supporting_claims=tuple(c for c in evidence['comparisons'] if c['value'] >= 0),
                  opposing_claims=tuple(evidence['conflicts'] + [c for c in evidence['comparisons'] if c['value'] < 0]),
                  coverage_status='partial' if gaps else 'complete', gaps=tuple(gaps))
    identity = 'package:research:' + digest(fields)
    record_evidence_package(EvidencePackage(package_id=identity, **fields), db_path=db_path)
    return identity
