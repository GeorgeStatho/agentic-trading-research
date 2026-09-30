"""Approved, dated archive retrieval. Model arguments never become URLs or SQL."""
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import math

from db_common import get_connection
from market_db_modules.company_evidence import get_company_fact, get_evidence_document
from market_db_modules.foundation_store import digest, read_record
from research_foundation_contracts import CompanyFact, EvidenceDocument, timestamp
from services.company_evidence_extraction import comparison_key, guidance_midpoint, validate_extracted_fact
from dataclasses import asdict

MAX_RESULT_FACTS = 240
MAX_RESULT_DOCUMENTS = 24
MAX_DOCUMENT_EXCERPT = 6000
OPERATIONS = {
    'get_event_documents': ('event_id',),
    'get_company_facts': ('metrics', 'fiscal_periods'),
    'compare_guidance': ('metric', 'fiscal_period'),
    'get_available_consensus': ('metric', 'fiscal_period'),
    'find_contradictory_evidence': ('event_id',),
    'get_event_price_response': ('event_id',),
}


@dataclass(frozen=True)
class ResearchRequest:
    question: str
    operation: str
    arguments: dict
    critical: bool

    @property
    def key(self):
        return digest([self.operation, self.arguments])

    @classmethod
    def parse(cls, data, *, company_id, event_id, cutoff):
        if not isinstance(data, dict) or set(data) != {'question', 'operation', 'arguments', 'critical'}:
            raise ValueError('Invalid research request fields')
        operation, args = data['operation'], data['arguments']
        if operation not in OPERATIONS or not isinstance(args, dict):
            raise ValueError('Operation is not approved')
        if set(args) != {'company_id', 'as_of', *OPERATIONS[operation]}:
            raise ValueError('Unexpected operation arguments')
        if type(args['company_id']) is not int or args['company_id'] != company_id:
            raise ValueError('Research company mismatch')
        if timestamp(args['as_of']) != timestamp(cutoff):
            raise ValueError('Research must use its frozen information cutoff')
        if 'event_id' in args and args['event_id'] != event_id:
            raise ValueError('Research event mismatch')
        for name in ('metric', 'fiscal_period'):
            if name in args and (not isinstance(args[name], str) or not 1 <= len(args[name]) <= 80):
                raise ValueError('Invalid metric or fiscal period')
        args = {**args, 'as_of': timestamp(args['as_of'])}
        for name in ('metrics', 'fiscal_periods'):
            if name in args:
                values = args[name]
                if (not isinstance(values, list) or not 1 <= len(values) <= 12
                        or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in values)):
                    raise ValueError('Invalid research filter')
                args[name] = sorted(set(values))
        if (type(data['critical']) is not bool or not isinstance(data['question'], str)
                or not 1 <= len(data['question'].strip()) <= 1000):
            raise ValueError('Invalid research question')
        return cls(data['question'], operation, args, data['critical'])


def active_facts(facts):
    replaced = {f.get('supersedes_fact_id') for f in facts}
    active = [f for f in facts if f['fact_id'] not in replaced and f['value'] is not None]
    # Historical backfills may arrive after the current forecast and lack a supersedes link.
    newest = {}
    for fact in active:
        if fact['kind'] == 'guidance' and fact.get('source_published_at'):
            key = comparison_key(fact)
            newest[key] = max(newest.get(key, ''), fact['source_published_at'])
    return [f for f in active if f['kind'] != 'guidance' or not f.get('source_published_at')
            or f['source_published_at'] == newest[comparison_key(f)]]


def evidence_conflicts(facts):
    groups = defaultdict(list)
    for fact in active_facts(facts):
        groups[(fact['kind'], comparison_key(fact))].append(fact)
    return [{'type': 'conflicting_values', 'fact_ids': sorted(f['fact_id'] for f in group)}
            for group in groups.values() if len({(f['value'], f.get('range_high')) for f in group}) > 1]


def guidance_baselines(current, facts):
    compatible = [f for f in facts if comparison_key(f) == comparison_key(current) and f['value'] is not None]
    explicit = [f for f in active_facts(compatible) if f['kind'] == 'prior_guidance']
    if explicit:
        return explicit
    # A superseded forecast remains usable as a historical baseline, never as current guidance.
    published = current.get('source_published_at')
    earlier = [f for f in compatible if f['kind'] == 'guidance' and published
               and f.get('source_published_at') and f['source_published_at'] < published]
    latest = max((f['source_published_at'] for f in earlier), default=None)
    return [f for f in earlier if f['source_published_at'] == latest]


def guidance_comparisons(facts):
    """Compatible, dated baselines only; simultaneous conflicting forecasts stay ambiguous."""
    active = active_facts(facts)
    comparisons = []
    for current in active:
        if current['kind'] != 'guidance':
            continue
        prior = guidance_baselines(current, facts)
        if len({(f['value'], f.get('range_high')) for f in prior}) != 1:
            continue
        baseline = prior[0]
        change = guidance_midpoint(current) - guidance_midpoint(baseline)
        if not math.isfinite(change):
            raise ValueError('Guidance comparison is not finite')
        comparisons.append({'type': 'guidance_midpoint_change', 'fact_ids': [baseline['fact_id'], current['fact_id']],
                            'value': change, 'unit': current['unit'],
                            'scale': current['scale'], 'formula': 'current_midpoint - prior_midpoint'})
    return comparisons


class ResearchTools:
    """Free archive adapter. Providers populate this archive through Phase 1 ingestion."""
    cost_per_call = 0
    provider = 'evidence_archive'

    def __init__(self, *, company_id, event_id, cutoff, db_path):
        self.company_id, self.event_id = company_id, event_id
        self.cutoff, self.db_path = timestamp(cutoff), db_path

    def parse(self, data):
        return ResearchRequest.parse(data, company_id=self.company_id, event_id=self.event_id, cutoff=self.cutoff)

    def _facts(self, request):
        args = request.arguments
        query = '''SELECT fact_id FROM company_facts WHERE company_id=?
                   AND julianday(available_at)<=julianday(?)'''
        values = [self.company_id, self.cutoff]
        filters = {'metric': args.get('metrics', [args['metric']] if 'metric' in args else []),
                   'fiscal_period': args.get('fiscal_periods', [args['fiscal_period']] if 'fiscal_period' in args else [])}
        for column, selected in filters.items():
            if selected:
                query += f' AND {column} IN ({",".join("?" for _ in selected)})'
                values.extend(selected)
        query += ' ORDER BY available_at DESC,fact_id LIMIT ?'
        with closing(get_connection(self.db_path)) as conn:
            rows = conn.execute(query, (*values, MAX_RESULT_FACTS + 1)).fetchall()
            facts = [read_record(conn, 'company_facts', 'fact_id', row['fact_id']) for row in rows]
        return [f for f in facts[:MAX_RESULT_FACTS] if f['kind'] != 'derived'], len(rows) > MAX_RESULT_FACTS

    def execute(self, request, *, timeout):
        if timeout <= 0:
            raise TimeoutError('Research deadline')
        # Revalidate even programmatically constructed requests at the adapter boundary.
        request = self.parse(asdict(request))
        if request.operation == 'get_event_price_response':
            return self._prices()
        facts, truncated = self._facts(request)
        documents = {}
        if request.operation == 'get_event_documents':
            with closing(get_connection(self.db_path)) as conn:
                rows = conn.execute('''SELECT DISTINCT d.document_id FROM evidence_package_documents d
                    JOIN evidence_packages p USING(package_id) WHERE p.company_id=? AND p.catalyst_id=?
                    AND julianday(p.information_cutoff_time)<=julianday(?) LIMIT ?''',
                    (self.company_id, self.event_id, self.cutoff, MAX_RESULT_DOCUMENTS + 1)).fetchall()
            truncated = truncated or len(rows) > MAX_RESULT_DOCUMENTS
            documents = {r['document_id']: get_evidence_document(r['document_id'], db_path=self.db_path)
                         for r in rows[:MAX_RESULT_DOCUMENTS]}
            facts = [f for f in facts if f['document_id'] in documents]
        elif request.operation == 'get_available_consensus':
            facts = [f for f in facts if f['kind'] == 'consensus']
        elif request.operation == 'compare_guidance':
            facts = [f for f in facts if f['kind'] in {'guidance', 'prior_guidance'}]
        for fact in facts:
            documents.setdefault(fact['document_id'], get_evidence_document(fact['document_id'], db_path=self.db_path))
        result = {'provider': self.provider, 'facts': facts, 'documents': list(documents.values()),
                  'gaps': ['archive_result_truncated'] if truncated else [], 'price_responses': []}
        return self.validate_result(request, result)

    def validate_result(self, request, result):
        """Recheck stored source support instead of trusting an adapter's asserted numbers."""
        args = request.arguments
        documents = {}
        for proposal in result['documents']:
            document = get_evidence_document(proposal['document_id'], db_path=self.db_path)
            if document != proposal:
                raise ValueError('Retrieved document differs from recorded source')
            EvidenceDocument(**document)
            if document.get('metadata', {}).get('company_id') != self.company_id:
                raise ValueError('Retrieved source company mismatch')
            if timestamp(document['ingested_at']) > self.cutoff:
                raise ValueError('Retrieved source is after cutoff')
            documents[document['document_id']] = document
        for fact in result['facts']:
            if fact != get_company_fact(fact['fact_id'], db_path=self.db_path):
                raise ValueError('Retrieved fact differs from recorded source')
            CompanyFact(**fact)
            if fact['company_id'] != self.company_id or timestamp(fact['available_at']) > self.cutoff:
                raise ValueError('Retrieved fact company/time mismatch')
            if 'metric' in args and fact['metric'] != args['metric']:
                raise ValueError('Retrieved metric mismatch')
            if 'fiscal_period' in args and fact['fiscal_period'] != args['fiscal_period']:
                raise ValueError('Retrieved period mismatch')
            for field, plural in (('metric', 'metrics'), ('fiscal_period', 'fiscal_periods')):
                if plural in args and fact[field] not in args[plural]:
                    raise ValueError('Retrieved fact filter mismatch')
            document = documents[fact['document_id']]
            validate_extracted_fact(fact, document['text'], {'id': self.company_id})
        facts = [{**f, 'source_published_at': documents[f['document_id']]['published_at']} for f in result['facts']]
        comparisons = guidance_comparisons(facts)
        available = bool(facts or documents)
        if request.operation == 'get_company_facts':
            known = {(f['metric'], f['fiscal_period']) for f in active_facts(facts)}
            available = all((metric, period) in known for metric in args['metrics'] for period in args['fiscal_periods'])
        if request.operation == 'compare_guidance':
            available = bool(comparisons)
        elif request.operation == 'get_available_consensus':
            available = any(f['kind'] == 'consensus' and f['value'] is not None for f in facts)
        if not available:
            result['gaps'].append('requested_evidence_unavailable')
        # Raw documents remain in immutable storage; persist a bounded, replayable view.
        excerpts = [{**d, 'text': d['text'][:MAX_DOCUMENT_EXCERPT],
                     'excerpt_truncated': len(d['text']) > MAX_DOCUMENT_EXCERPT} for d in documents.values()]
        visible = [{**f, 'source_excerpt': documents[f['document_id']]['text'][f['source_start']:f['source_end']][:MAX_DOCUMENT_EXCERPT],
                         'source_published_at': documents[f['document_id']]['published_at']}
                   for f in facts]
        return {**result, 'documents': excerpts, 'facts': visible, 'comparisons': comparisons,
                'conflicts': evidence_conflicts(facts), 'available': available and not result['gaps']}

    def _prices(self):
        with closing(get_connection(self.db_path)) as conn:
            rows = conn.execute('''SELECT r.horizon_days,r.outcome_at,r.recorded_at,r.residual_return,r.status
                FROM catalyst_responses r JOIN catalyst_response_events e USING(catalyst_id)
                JOIN companies c ON c.symbol=e.ticker
                WHERE c.id=? AND e.catalyst_id=? AND r.status='complete' AND julianday(r.recorded_at)<=julianday(?)
                AND julianday(r.outcome_at)<=julianday(?) AND julianday(e.event_at)<=julianday(?)
                AND julianday(e.registered_at)<=julianday(?) ORDER BY r.horizon_days LIMIT 24''',
                (self.company_id, self.event_id, self.cutoff, self.cutoff, self.cutoff, self.cutoff)).fetchall()
        prices = [dict(row) for row in rows if row['residual_return'] is not None
                  and math.isfinite(row['residual_return'])]
        return {'provider': self.provider, 'facts': [], 'documents': [], 'price_responses': prices,
                'comparisons': [], 'conflicts': [], 'available': bool(prices),
                'gaps': [] if prices else ['event_price_response_unavailable']}
