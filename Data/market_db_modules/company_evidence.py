"""Persist evidence without performing provider retrieval or changing trading policy."""
from contextlib import closing
from hashlib import sha256

from db_common import get_connection
from research_foundation_contracts import CompanyFact, EvidenceDocument, EvidencePackage, timestamp
from .bootstrap import initialize_database
from .foundation_store import insert_record, load_record, read_record, record_data


def record_evidence_document(document: EvidenceDocument, *, db_path=None):
    data = record_data(document)
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        if document.supersedes_document_id:
            prior = read_record(conn, "evidence_documents", "document_id", document.supersedes_document_id)
            if data["first_observed_at"] < prior["first_observed_at"]:
                raise ValueError("Document revision predates the original observation")
        return insert_record(conn, "evidence_documents", "document_id", data, {
            "provider": document.provider, "first_observed_at": data["first_observed_at"],
            "supersedes_document_id": document.supersedes_document_id,
            "content_hash": sha256(document.text.encode()).hexdigest(),
        })


def record_company_fact(fact: CompanyFact, *, db_path=None):
    data = record_data(fact)
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        document = read_record(conn, "evidence_documents", "document_id", fact.document_id)
        if fact.source_end > len(document["text"]):
            raise ValueError("Fact source span is outside the document")
        if data["available_at"] < document["ingested_at"]:
            raise ValueError("Fact was not available before its source was ingested")
        if fact.supersedes_fact_id:
            prior = read_record(conn, "company_facts", "fact_id", fact.supersedes_fact_id)
            if (prior["company_id"], prior["metric"], prior["fiscal_period"]) != (
                    fact.company_id, fact.metric, fact.fiscal_period):
                raise ValueError("Fact revision changes company, metric or fiscal period")
            if data["available_at"] < prior["available_at"]:
                raise ValueError("Fact revision predates the original fact")
        return insert_record(conn, "company_facts", "fact_id", data, {
            "company_id": fact.company_id, "document_id": fact.document_id,
            "metric": fact.metric, "fiscal_period": fact.fiscal_period,
            "available_at": data["available_at"], "supersedes_fact_id": fact.supersedes_fact_id,
        })


def record_evidence_package(package: EvidencePackage, *, db_path=None):
    data = record_data(package)
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        for event in package.scheduled_events:
            if event.get('document_id') not in package.document_ids:
                raise ValueError('Scheduled event references an omitted document')
        for claim in (*package.supporting_claims, *package.opposing_claims):
            if not set(claim.get('fact_ids', ())).issubset(package.fact_ids):
                raise ValueError('Evidence claim references an omitted fact')
        for identity in package.document_ids:
            document = read_record(conn, "evidence_documents", "document_id", identity)
            if document["ingested_at"] > data["information_cutoff_time"]:
                raise ValueError("Package includes a future document")
        for identity in package.fact_ids:
            fact = read_record(conn, "company_facts", "fact_id", identity)
            if fact["company_id"] != package.company_id or fact["document_id"] not in package.document_ids:
                raise ValueError("Package fact belongs to another company or an omitted document")
            if fact["available_at"] > data["information_cutoff_time"]:
                raise ValueError("Package includes a future fact")
        insert_record(conn, "evidence_packages", "package_id", data, {
            "company_id": package.company_id, "catalyst_id": package.catalyst_id,
            "event_revision": package.event_revision,
            "information_cutoff_time": data["information_cutoff_time"],
        })
        conn.executemany("INSERT INTO evidence_package_documents VALUES (?,?) ON CONFLICT DO NOTHING",
                         [(package.package_id, identity) for identity in package.document_ids])
        conn.executemany("INSERT INTO evidence_package_facts VALUES (?,?) ON CONFLICT DO NOTHING",
                         [(package.package_id, identity) for identity in package.fact_ids])
    return package.package_id


def get_evidence_package(package_id, *, db_path=None):
    return load_record(db_path, "evidence_packages", "package_id", package_id)


def get_evidence_document(document_id, *, db_path=None):
    return load_record(db_path, "evidence_documents", "document_id", document_id)


def get_company_fact(fact_id, *, db_path=None):
    return load_record(db_path, "company_facts", "fact_id", fact_id)


def latest_evidence_package(company_id, *, as_of, db_path=None):
    with closing(get_connection(db_path)) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_packages' AND type='table'").fetchone() is None:
            return None
        # The reserved package:research* namespace is shadow-only.
        row = conn.execute('''SELECT package_id FROM evidence_packages WHERE company_id=?
            AND package_id NOT GLOB 'package:research*'
            AND julianday(information_cutoff_time)<=julianday(?)
            ORDER BY julianday(information_cutoff_time) DESC,rowid DESC LIMIT 1''',
                           (company_id, timestamp(as_of))).fetchone()
        return read_record(conn, 'evidence_packages', 'package_id', row['package_id']) if row else None


def list_company_documents(company_id, *, as_of, db_path=None):
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT DISTINCT d.document_id FROM evidence_documents d
            JOIN evidence_package_documents pd USING(document_id)
            JOIN evidence_packages p USING(package_id) WHERE p.company_id=?
            AND p.package_id NOT GLOB 'package:research*'
            AND julianday(p.information_cutoff_time)<=julianday(?)''', (company_id, timestamp(as_of)))
        return [read_record(conn, 'evidence_documents', 'document_id', row['document_id']) for row in rows]


def list_company_facts(company_id, *, as_of, db_path=None):
    cutoff = timestamp(as_of)
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute("""SELECT fact_id FROM company_facts WHERE company_id=?
            AND julianday(available_at)<=julianday(?) ORDER BY available_at,fact_id""", (company_id, cutoff))
        return [read_record(conn, "company_facts", "fact_id", row["fact_id"]) for row in rows]
