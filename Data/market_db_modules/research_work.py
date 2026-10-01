"""One research owner per event, purpose and source snapshot across intake routes."""
from .foundation_store import digest, read_record


def work_identity(conn, company_id, catalyst_id, purpose, package_id, articles, watchlist=None, predecessor=None):
    documents, facts = [], []
    if package_id:
        package = read_record(conn, 'evidence_packages', 'package_id', package_id)
        if package['company_id'] != company_id:
            raise ValueError('Research work package belongs to another company')
        documents = sorted(package['document_ids'])
        facts = sorted(identity for identity in package['fact_ids']
                       if read_record(conn, 'company_facts', 'fact_id', identity)['kind'] != 'derived')
    news = sorted(({'id': a.get('article_id', a.get('id')), 'title': a.get('title'),
                    'body': a.get('body'), 'root': a.get('root_event_id')} for a in articles), key=digest)
    inputs = digest({'documents': documents, 'facts': facts, 'news': news, 'watchlist': watchlist or {}})
    key = digest([company_id, catalyst_id, purpose, predecessor, inputs])
    return key, inputs


def claim_research_work(conn, candidate, company_id, catalyst_id, session_id, *, at):
    payload = candidate['research_input']
    key, inputs = work_identity(conn, company_id, catalyst_id, candidate['purpose'],
                               payload['company_evidence'].get('package_id'), payload.get('evidence', []),
                               payload.get('watchlist_context'), payload.get('research_predecessor_revision'))
    conn.execute('INSERT OR IGNORE INTO research_work_owners VALUES (?,?,?,?,?,?,?)',
                 (key, session_id, company_id, catalyst_id, candidate['purpose'], inputs, at))
    return conn.execute('SELECT session_id FROM research_work_owners WHERE work_key=?', (key,)).fetchone()[0]


def session_covers_event(conn, session_id, event, purpose):
    key, _ = work_identity(conn, event['company']['id'], event['catalyst_id'], purpose,
                           event.get('package_id'), event.get('articles', []), event.get('watchlist'), event.get('predecessor_revision'))
    return conn.execute('SELECT 1 FROM research_work_owners WHERE work_key=? AND session_id=?',
                        (key, session_id)).fetchone() is not None
