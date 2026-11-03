"""Explicit shared inputs for independently produced strategy candidates."""
from contextlib import closing
import json

from db_common import get_connection
from research_foundation_contracts import ResearchObservation, timestamp
from .compact_storage import encode_snapshot
from .foundation_store import canonical, digest, insert_record, read_record, record_data, verified_envelope
from .bootstrap import initialize_database


SOURCE_TIME_KEYS = {
    "timestamp", "published_at", "available_at", "as_of", "quote_at",
    "first_public_time", "first_seen_time", "first_observed_at", "observed_at",
    "captured_at", "ingested_at", "created_at", "information_cutoff_time",
}


def validate_source_times(value, cutoff):
    if isinstance(value, dict):
        for name, item in value.items():
            # Serializers write an unknown time as "", which states nothing to check.
            if name in SOURCE_TIME_KEYS and item not in (None, ''):
                if timestamp(item) > cutoff:
                    raise ValueError("Observation includes evidence after its cutoff")
            elif isinstance(item, (dict, list)):
                validate_source_times(item, cutoff)
    elif isinstance(value, list):
        for item in value:
            validate_source_times(item, cutoff)


WITNESS_VERSION = "observation-input-v2"


def source_witness(observation):
    return {"source_snapshot_hash": digest(observation["source_snapshot"])}


def record_research_observation(observation: ResearchObservation, *, db_path=None):
    initialize_database(db_path=db_path)
    data = record_data(observation)
    with closing(get_connection(db_path)) as conn, conn:
        company = conn.execute("SELECT symbol FROM companies WHERE id=?", (observation.company_id,)).fetchone()
        if company is None or company["symbol"] != observation.symbol:
            raise ValueError("Observation company and symbol do not match")
        if not data["source_snapshot"].get("catalysts"):
            catalyst = conn.execute("SELECT state_json FROM catalysts WHERE catalyst_id=?",
                                    (observation.catalyst_id,)).fetchone()
            if catalyst is None:
                raise ValueError("Unknown observation catalyst")
            data["source_snapshot"]["catalysts"] = [json.loads(catalyst["state_json"])]
        if observation.catalyst_id not in {row.get("catalyst_id") for row in data["source_snapshot"]["catalysts"]}:
            raise ValueError("Observation source omits its catalyst")
        validate_source_times(data["source_snapshot"], data["information_cutoff_time"])
        observation_id = "observation:" + digest(data)
        data["observation_id"] = observation_id
        insert_record(conn, "research_observations", "observation_id", data, {
            "company_id": observation.company_id, "symbol": observation.symbol,
            "catalyst_id": observation.catalyst_id, "event_revision": observation.event_revision,
            "information_cutoff_time": data["information_cutoff_time"],
            "protocol_version": observation.protocol_version,
            "source_hash": digest(data["source_snapshot"]),
        })
    return observation_id


def read_observation(conn, observation_id):
    data = read_record(conn, "research_observations", "observation_id", observation_id)
    identity = {key: value for key, value in data.items() if key != "observation_id"}
    if "observation:" + digest(identity) != observation_id:
        raise ValueError("Observation identity does not match its frozen inputs")
    return data


def get_research_observation(observation_id, *, db_path=None):
    with closing(get_connection(db_path)) as conn:
        return read_observation(conn, observation_id)


def bind_candidate_observation(conn, candidate_id, observation_id):
    """Bind at candidate creation, never infer a match from historical outputs."""
    observation = read_observation(conn, observation_id)
    candidate = conn.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if candidate is None or candidate["symbol"] != observation["symbol"]:
        raise ValueError("Candidate and observation symbols differ")
    if timestamp(candidate["created_at"]) < observation["information_cutoff_time"]:
        raise ValueError("Candidate predates its observation")
    if conn.execute("SELECT 1 FROM candidate_snapshots WHERE candidate_id=?", (candidate_id,)).fetchone():
        raise ValueError("Cannot attach an observation after candidate evaluation has begun")
    conn.execute("INSERT INTO candidate_observations VALUES (?,?)", (candidate_id, observation_id))
    # The observation is immutable and hash-checked, so its digest witnesses the binding without a
    # second full copy of the frozen inputs for every candidate that shares them.
    envelope = {"candidate_id": candidate_id, "observation_id": observation_id,
                "stage": "observation_input", "snapshot_version": WITNESS_VERSION,
                "information_cutoff_time": observation["information_cutoff_time"],
                "payload": source_witness(observation)}
    manifest = json.loads(candidate["pipeline_version_json"])
    stored, encoding = encode_snapshot(conn, canonical(envelope))
    conn.execute("""INSERT INTO candidate_snapshots
        (candidate_id,stage,captured_at,information_cutoff_time,pipeline_version,
         snapshot_version,snapshot_hash,snapshot_json,snapshot_encoding) VALUES (?,?,?,?,?,?,?,?,?)""",
        (candidate_id, "observation_input", candidate["created_at"], observation["information_cutoff_time"],
         manifest["pipeline_version"], WITNESS_VERSION, digest(envelope), stored, encoding))
    return observation


def candidate_observation(conn, candidate_id):
    link = conn.execute("SELECT observation_id FROM candidate_observations WHERE candidate_id=?",
                        (candidate_id,)).fetchone()
    return read_observation(conn, link["observation_id"]) if link else None


def validate_candidate_observation(conn, candidate_id, observation):
    snapshots = conn.execute("SELECT * FROM candidate_snapshots WHERE candidate_id=?", (candidate_id,)).fetchall()
    witness_found = False
    for row in snapshots:
        envelope = verified_envelope(row, conn)
        if envelope.get("observation_id") != observation["observation_id"]:
            raise ValueError("Candidate snapshot lacks its shared observation identity")
        if timestamp(row["information_cutoff_time"]) != observation["information_cutoff_time"]:
            raise ValueError("Candidate input cutoff differs from its shared observation")
        source = observation["source_snapshot"]
        payload = envelope.get("payload")
        if isinstance(payload, dict):
            for key in ("market_context", "supporting_articles", "grounding", "company", "opportunist_rollup", "company_evidence"):
                if key in source and key in payload and source[key] != payload[key]:
                    raise ValueError("Candidate inputs differ from the shared source snapshot")
        if row["stage"] == "observation_input":
            # Earlier candidates carry the full source as their witness.
            witness_found = envelope["payload"] in (source_witness(observation), observation["source_snapshot"])
    if not witness_found:
        raise ValueError("Candidate lacks the original shared source snapshot")


def enroll_strategy_evaluation(conn, run, candidate_id, inputs, observation):
    decision = inputs["features"].get("manager_direction")
    status = "evaluated" if decision in {"call", "put"} else "abstained" if decision == "neither" else "unavailable"
    evaluation_id = "evaluation:" + digest({"run_id": run["run_id"], "observation_id": observation["observation_id"]})
    conn.execute("""INSERT INTO strategy_evaluations
        (evaluation_id,run_id,observation_id,candidate_id,strategy,manifest_hash,input_hash,output_hash,status)
        VALUES (?,?,?,?,?,?,?,?,?)""", (evaluation_id, run["run_id"], observation["observation_id"],
        candidate_id, "full_v2", digest(run["manifest"]), digest(inputs),
        digest({"features": inputs["features"], "selected_contract": inputs["selected_contract"]}), status))


def validate_strategy_evaluation(conn, run, saved, inputs):
    row = conn.execute("SELECT * FROM strategy_evaluations WHERE run_id=? AND candidate_id=?",
                       (run["run_id"], saved["candidate_id"])).fetchone()
    if row is None:
        if inputs.get("observation_id"):
            raise ValueError("Shared observation evaluation is missing")
        return None
    expected_output = digest({"features": inputs["features"], "selected_contract": inputs["selected_contract"]})
    if (row["input_hash"] != saved["input_hash"] or row["manifest_hash"] != digest(run["manifest"])
            or row["output_hash"] != expected_output or row["observation_id"] != inputs.get("observation_id")):
        raise ValueError("Strategy evaluation differs from its frozen inputs or manifest")
    return row


def paired_champion_results(incumbent_run, incumbent, challenger_run, challenger_report):
    """Compare frozen report cohorts, never later mutable collection results."""
    if digest({key: value for key, value in incumbent.items() if key != "report_id"}) != incumbent.get("report_id"):
        raise ValueError("Incumbent report hash mismatch")
    old_version, new_version = incumbent_run.get("evaluation_version"), challenger_run.get("evaluation_version")
    if not old_version or not new_version or any(old_version[key] != new_version[key]
                                               for key in ("evaluator_hash", "protocol_version")):
        raise ValueError("Replacement requires the same evaluator and observation protocol")
    if incumbent_run["policy"] != challenger_run["policy"]:
        raise ValueError("Replacement requires the same evaluation policy")
    old = {row["observation_id"]: row for row in incumbent.get("observation_results", [])}
    new = {row["observation_id"]: row for row in challenger_report.get("observation_results", [])}
    if (not old or old.keys() != new.keys() or len(new) != challenger_report["n_observed"]
            or len(old) != incumbent["n_observed"]):
        raise ValueError("Replacement requires the same complete shared observation cohort")
    comparison = []
    for identity, result in new.items():
        prior = old[identity]
        if any(row["status"] not in {"complete", "cash"} for row in (prior, result)):
            raise ValueError("Replacement has unavailable observation outcomes")
        comparison.append(result["hypothetical_pnl"] - prior["hypothetical_pnl"])
    return comparison
