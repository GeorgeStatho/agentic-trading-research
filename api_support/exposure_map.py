from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any

from agent_helpers.shared import ask_llm_model, extract_json_object, get_model_client
from api_support.context import DB_PATH, get_connection, initialize_market_database
from db_helpers import initialize_news_database


PROMPT_VERSION = "exposure-map-v1"
VALID_DIRECTIONS = {"positive", "negative", "unclear"}
VALID_MAGNITUDES = {"low", "medium", "high"}
VALID_CONFIDENCES = {"low", "medium", "high"}
MAGNITUDE_WEIGHTS = {"low": 0.45, "medium": 0.70, "high": 1.0}
CONFIDENCE_WEIGHTS = {"low": 0.50, "medium": 0.75, "high": 1.0}

EXPOSURE_MAP_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["article_summary", "edges", "position_impacts", "uncertainties"],
    "properties": {
        "article_summary": {"type": "string"},
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "from",
                    "to",
                    "direction",
                    "magnitude",
                    "confidence",
                    "mechanism",
                    "evidence_article_ids",
                ],
                "properties": {
                    "from": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "id"],
                        "properties": {"type": {"type": "string"}, "id": {}},
                    },
                    "to": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "id"],
                        "properties": {"type": {"type": "string"}, "id": {}},
                    },
                    "direction": {"type": "string"},
                    "magnitude": {"type": "string"},
                    "confidence": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "evidence_article_ids": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
        "position_impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["option_symbol", "risk_level", "reason"],
                "properties": {
                    "option_symbol": {"type": "string"},
                    "risk_level": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
}


def resolve_exposure_map_model(model: Any = None) -> str:
    requested_model = str(model or "").strip()
    if requested_model:
        return requested_model

    configured_model = str(os.getenv("EXPOSURE_MAP_MODEL") or "").strip()
    if configured_model:
        return configured_model

    provider = str(os.getenv("LLM_PROVIDER", "ollama") or "ollama").strip().lower()
    if provider == "openai":
        return "gpt-5.6-terra"
    if provider == "vertex":
        return str(os.getenv("VERTEX_DEFAULT_MODEL", "gemini-2.5-flash")).strip()
    return str(
        os.getenv(
            "EXPOSURE_MAP_OLLAMA_MODEL",
            os.getenv("RESEARCHER_MODEL", "llama3.1"),
        )
    ).strip()


def _initialize_databases() -> None:
    initialize_market_database(db_path=DB_PATH)
    initialize_news_database(db_path=DB_PATH)


def _normalize_level(value: Any, valid_values: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in valid_values else fallback


def _entity_key(entity_type: str, entity_id: Any) -> tuple[str, str]:
    return (str(entity_type or "").strip().lower(), str(entity_id or "").strip().upper())


def _parse_expiration(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_json_loads(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _first_number(value: Any, *keys: str) -> float | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        number = _number(value.get(key), float("nan"))
        if number == number:
            return number
    return None


def _extract_implied_volatility(raw_json: Any) -> float | None:
    if not isinstance(raw_json, dict):
        return None
    candidates = [raw_json]
    for key in ("selected_option", "option", "contract"):
        nested = raw_json.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        value = _first_number(candidate, "implied_volatility", "impliedVolatility", "iv")
        if value is not None:
            return value / 100.0 if value > 3 else value
    return None


def calculate_position_risk_score(
    *,
    quantity: float,
    direction: str,
    magnitude: str,
    confidence: str,
    dte: int | None,
    implied_volatility: float | None,
    contract_type: str,
    order_side: str,
) -> tuple[float, dict[str, Any]]:
    """Calculate an auditable 0-100 exposure score without using model output."""
    normalized_direction = _normalize_level(direction, VALID_DIRECTIONS, "unclear")
    normalized_magnitude = _normalize_level(magnitude, VALID_MAGNITUDES, "medium")
    normalized_confidence = _normalize_level(confidence, VALID_CONFIDENCES, "medium")
    normalized_contract_type = str(contract_type or "").strip().lower()
    normalized_order_side = str(order_side or "buy").strip().lower()
    normalized_dte = max(0, int(dte or 0))

    if normalized_dte <= 7:
        dte_multiplier = 1.50
    elif normalized_dte <= 14:
        dte_multiplier = 1.35
    elif normalized_dte <= 30:
        dte_multiplier = 1.20
    elif normalized_dte <= 60:
        dte_multiplier = 1.00
    else:
        dte_multiplier = 0.85

    volatility = _number(implied_volatility, 0.0)
    volatility_multiplier = min(1.60, max(0.80, 1.0 + (max(0.0, volatility) * 0.5)))
    position_exposure = min(10.0, max(1.0, abs(_number(quantity, 0.0))))
    impact_weight = MAGNITUDE_WEIGHTS[normalized_magnitude]
    confidence_weight = CONFIDENCE_WEIGHTS[normalized_confidence]

    option_direction = -1 if normalized_contract_type == "put" else 1
    if normalized_order_side in {"sell", "sell_to_open", "sell_short"}:
        option_direction *= -1
    impact_direction = {"positive": 1, "negative": -1, "unclear": 0}[normalized_direction]
    is_adverse = impact_direction != 0 and impact_direction * option_direction < 0
    alignment_multiplier = 1.0 if is_adverse else 0.25 if impact_direction else 0.5
    raw_score = (
        position_exposure
        * impact_weight
        * confidence_weight
        * dte_multiplier
        * volatility_multiplier
        * alignment_multiplier
        * 100.0
    )
    score = round(min(100.0, raw_score), 2)
    return score, {
        "position_exposure": position_exposure,
        "impact_magnitude_weight": impact_weight,
        "confidence_weight": confidence_weight,
        "dte": normalized_dte,
        "dte_multiplier": dte_multiplier,
        "implied_volatility": round(volatility, 4) if volatility else None,
        "volatility_multiplier": volatility_multiplier,
        "is_adverse": is_adverse,
        "alignment_multiplier": alignment_multiplier,
        "formula": "position_exposure × impact_magnitude × confidence_weight × DTE/volatility_multiplier",
    }


def _risk_level(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def _load_article(conn, article_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, title, summary, body, source, source_url, published_at, evidence_score,
               recency_score, factuality_score, section
        FROM news_articles
        WHERE id = ?
        """,
        (article_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _load_entity_relationships(conn, article_id: int) -> list[dict[str, Any]]:
    relationships: dict[tuple[str, str], dict[str, Any]] = {}

    def add_rows(query: str, entity_type: str, source_name: str, default_direction: str = "unclear") -> None:
        try:
            rows = conn.execute(query, (article_id,)).fetchall()
        except Exception:
            return
        for row in rows:
            item = dict(row)
            entity_id = item.get("entity_id")
            if entity_id is None:
                continue
            key = _entity_key(entity_type, entity_id)
            if key in relationships:
                continue
            relationships[key] = {
                "type": entity_type,
                "id": str(entity_id),
                "label": str(item.get("label") or "").strip(),
                "symbol": str(item.get("symbol") or "").strip().upper(),
                "direction": _normalize_level(item.get("impact_direction"), VALID_DIRECTIONS, default_direction),
                "magnitude": _normalize_level(item.get("impact_magnitude"), VALID_MAGNITUDES, "medium"),
                "confidence": _normalize_level(item.get("confidence"), VALID_CONFIDENCES, "medium"),
                "reason": str(item.get("reason") or "").strip(),
                "source": source_name,
            }

    add_rows(
        """
        SELECT soi.sector_id AS entity_id, s.name AS label, s.sector_key AS symbol,
               soi.impact_direction, soi.impact_magnitude, soi.confidence, soi.reason
        FROM sector_opportunist_impacts AS soi
        JOIN sectors AS s ON s.id = soi.sector_id
        WHERE soi.article_id = ?
        """,
        "sector",
        "sector_opportunist_impacts",
    )
    add_rows(
        """
        SELECT isi.industry_id AS entity_id, i.name AS label, i.industry_key AS symbol,
               isi.impact_direction, isi.impact_magnitude, isi.confidence, isi.reason
        FROM industry_opportunist_impacts AS isi
        JOIN industries AS i ON i.id = isi.industry_id
        WHERE isi.article_id = ?
        """,
        "industry",
        "industry_opportunist_impacts",
    )
    add_rows(
        """
        SELECT coi.company_id AS entity_id, c.name AS label, c.symbol,
               coi.impact_direction, coi.impact_magnitude, coi.confidence, coi.reason
        FROM company_opportunist_impacts AS coi
        JOIN companies AS c ON c.id = coi.company_id
        WHERE coi.article_id = ?
        """,
        "company",
        "company_opportunist_impacts",
    )
    add_rows(
        """
        SELECT sna.sector_id AS entity_id, s.name AS label, s.sector_key AS symbol,
               NULL AS impact_direction, NULL AS impact_magnitude, NULL AS confidence,
               '' AS reason
        FROM sector_news_articles AS sna
        JOIN sectors AS s ON s.id = sna.sector_id
        WHERE sna.article_id = ?
        """,
        "sector",
        "sector_news_articles",
    )
    add_rows(
        """
        SELECT ina.industry_id AS entity_id, i.name AS label, i.industry_key AS symbol,
               NULL AS impact_direction, NULL AS impact_magnitude, NULL AS confidence,
               '' AS reason
        FROM industry_news_articles AS ina
        JOIN industries AS i ON i.id = ina.industry_id
        WHERE ina.article_id = ?
        """,
        "industry",
        "industry_news_articles",
    )
    add_rows(
        """
        SELECT cna.company_id AS entity_id, c.name AS label, c.symbol,
               NULL AS impact_direction, NULL AS impact_magnitude, NULL AS confidence,
               '' AS reason
        FROM company_news_articles AS cna
        JOIN companies AS c ON c.id = cna.company_id
        WHERE cna.article_id = ?
        """,
        "company",
        "company_news_articles",
    )
    return list(relationships.values())


def _load_paper_positions(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT option_symbol, underlying_symbol, expiration_date, strike_price, order_qty,
               estimated_order_cost, order_side, decision, company_id, paper, order_status,
               recorded_at, raw_json
        FROM option_trade_executions
        WHERE paper = 1
        ORDER BY recorded_at ASC, id ASC
        """
    ).fetchall()
    positions: dict[str, dict[str, Any]] = {}
    excluded_statuses = {"canceled", "cancelled", "rejected", "failed", "expired"}
    for row in rows:
        item = dict(row)
        symbol = str(item.get("option_symbol") or "").strip().upper()
        if not symbol or str(item.get("order_status") or "").strip().lower() in excluded_statuses:
            continue
        quantity = abs(_number(item.get("order_qty"), 0.0))
        if quantity <= 0:
            continue
        side = str(item.get("order_side") or "buy").strip().lower()
        signed_quantity = -quantity if side in {"sell", "sell_to_close", "sell_to_open", "sell_short"} else quantity
        position = positions.setdefault(symbol, {"quantity": 0.0})
        position.update(item)
        position["option_symbol"] = symbol
        position["quantity"] = _number(position.get("quantity")) + signed_quantity

    output: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    for position in positions.values():
        quantity = _number(position.get("quantity"))
        if quantity == 0:
            continue
        expiration = _parse_expiration(position.get("expiration_date"))
        dte = max(0, (expiration - today).days) if expiration else None
        raw_json = _safe_json_loads(position.get("raw_json"))
        decision = str(position.get("decision") or "").strip().lower()
        output.append(
            {
                "option_symbol": str(position["option_symbol"]),
                "underlying_symbol": str(position.get("underlying_symbol") or "").strip().upper(),
                "company_id": position.get("company_id"),
                "expiration_date": str(position.get("expiration_date") or ""),
                "strike_price": _number(position.get("strike_price"), 0.0) or None,
                "quantity": quantity,
                "order_side": str(position.get("order_side") or "buy").strip().lower(),
                "contract_type": "put" if decision == "put" else "call" if decision == "call" else "option",
                "dte": dte,
                "implied_volatility": _extract_implied_volatility(raw_json),
                "paper": True,
            }
        )
    return sorted(output, key=lambda position: position["option_symbol"])


def _build_grounding_payload(article: dict[str, Any], relationships: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "article": article,
        "existing_relationships": relationships,
        "open_paper_positions": positions,
        "constraints": {
            "allowed_edge_from": {"type": "article", "id": article["id"]},
            "only_existing_relationship_targets": True,
            "only_open_paper_position_symbols": [position["option_symbol"] for position in positions],
        },
    }


def _terra_prompts(grounding_payload: dict[str, Any]) -> tuple[str, str]:
    system_prompt = (
        "You are Terra, a grounded evidence-map explainer. Return only JSON matching the schema. "
        "This is not a prediction or trading system. Do not recommend, place, or imply trades. "
        "Use only the article, stored relationships, and current paper positions supplied. "
        "Every edge must originate at the supplied article and terminate at an existing relationship target. "
        "Use unclear when the supplied evidence does not establish direction."
    )
    return system_prompt, json.dumps(grounding_payload, sort_keys=True, default=str)


def validate_terra_response(
    payload: Any,
    *,
    article_id: int,
    relationships: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Terra returned invalid JSON: expected an object.")

    allowed_targets = {
        _entity_key(item["type"], item["symbol"] if item["type"] == "company" else item["id"]): item
        for item in relationships
    }
    allowed_positions = {str(position["option_symbol"]).upper() for position in positions}
    article_summary = str(payload.get("article_summary") or "").strip()
    if not article_summary:
        raise ValueError("Terra returned invalid JSON: article_summary is required.")

    normalized_edges: list[dict[str, Any]] = []
    raw_edges = payload.get("edges")
    if not isinstance(raw_edges, list):
        raise ValueError("Terra returned invalid JSON: edges must be an array.")
    for edge in raw_edges:
        if not isinstance(edge, dict):
            raise ValueError("Terra returned invalid JSON: edge must be an object.")
        from_node = edge.get("from")
        to_node = edge.get("to")
        if not isinstance(from_node, dict) or not isinstance(to_node, dict):
            raise ValueError("Terra returned invalid JSON: edge endpoints are required.")
        if _entity_key(from_node.get("type"), from_node.get("id")) != _entity_key("article", article_id):
            raise ValueError("Terra referenced an unknown article in an edge.")
        to_type = str(to_node.get("type") or "").strip().lower()
        to_identifier = to_node.get("id")
        target_key = _entity_key(to_type, to_identifier)
        target = allowed_targets.get(target_key)
        if target is None:
            raise ValueError("Terra referenced an unknown entity in an edge.")
        evidence_ids = edge.get("evidence_article_ids")
        if not isinstance(evidence_ids, list) or any(not isinstance(value, int) for value in evidence_ids):
            raise ValueError("Terra returned invalid JSON: evidence_article_ids must be integer IDs.")
        if any(value != article_id for value in evidence_ids):
            raise ValueError("Terra referenced an article outside the grounded payload.")
        mechanism = str(edge.get("mechanism") or "").strip()
        if not mechanism:
            raise ValueError("Terra returned invalid JSON: edge mechanism is required.")
        normalized_edges.append(
            {
                "from_type": "article",
                "from_id": str(article_id),
                "to_type": to_type,
                "to_id": str(target["symbol"] if to_type == "company" else target["id"]),
                "direction": _normalize_level(edge.get("direction"), VALID_DIRECTIONS, "unclear"),
                "magnitude": _normalize_level(edge.get("magnitude"), VALID_MAGNITUDES, "medium"),
                "confidence": _normalize_level(edge.get("confidence"), VALID_CONFIDENCES, "medium"),
                "mechanism": mechanism,
                "evidence_article_ids": evidence_ids,
            }
        )

    normalized_position_impacts: dict[str, dict[str, str]] = {}
    raw_position_impacts = payload.get("position_impacts")
    if not isinstance(raw_position_impacts, list):
        raise ValueError("Terra returned invalid JSON: position_impacts must be an array.")
    for impact in raw_position_impacts:
        if not isinstance(impact, dict):
            raise ValueError("Terra returned invalid JSON: position impact must be an object.")
        option_symbol = str(impact.get("option_symbol") or "").strip().upper()
        if option_symbol not in allowed_positions:
            raise ValueError("Terra referenced an unknown paper position.")
        reason = str(impact.get("reason") or "").strip()
        if not reason:
            raise ValueError("Terra returned invalid JSON: position impact reason is required.")
        normalized_position_impacts[option_symbol] = {
            "risk_level": str(impact.get("risk_level") or "").strip().lower() or "low",
            "reason": reason,
        }

    uncertainties = payload.get("uncertainties")
    if not isinstance(uncertainties, list) or any(not isinstance(value, str) for value in uncertainties):
        raise ValueError("Terra returned invalid JSON: uncertainties must be a string array.")
    return {
        "article_summary": article_summary,
        "edges": normalized_edges,
        "position_impacts": normalized_position_impacts,
        "uncertainties": [value.strip() for value in uncertainties if value.strip()],
    }


def _find_position_edge(position: dict[str, Any], edges: list[dict[str, Any]], conn) -> dict[str, Any] | None:
    underlying_symbol = str(position.get("underlying_symbol") or "").upper()
    direct_edge = next(
        (edge for edge in edges if edge["to_type"] == "company" and edge["to_id"] == underlying_symbol),
        None,
    )
    if direct_edge is not None:
        return direct_edge
    company = conn.execute(
        """
        SELECT c.id AS company_id, c.symbol, i.id AS industry_id, s.id AS sector_id
        FROM companies AS c
        JOIN industries AS i ON i.id = c.industry_id
        JOIN sectors AS s ON s.id = i.sector_id
        WHERE c.symbol = ?
        """,
        (underlying_symbol,),
    ).fetchone()
    if company is None:
        return None
    values = dict(company)
    for entity_type, entity_id in (("industry", values["industry_id"]), ("sector", values["sector_id"])):
        matching_edge = next(
            (edge for edge in edges if edge["to_type"] == entity_type and edge["to_id"] == str(entity_id)),
            None,
        )
        if matching_edge is not None:
            return matching_edge
    return None


def _store_run(
    conn,
    *,
    article_id: int,
    model: str,
    response: dict[str, Any],
    positions: list[dict[str, Any]],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO news_exposure_runs (
            article_id, model, prompt_version, input_article_ids_json, article_summary,
            uncertainties_json, raw_response_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')
        """,
        (
            article_id,
            model,
            PROMPT_VERSION,
            json.dumps([article_id]),
            response["article_summary"],
            json.dumps(response["uncertainties"]),
            json.dumps(response),
        ),
    )
    run_id = int(cursor.lastrowid)
    for edge in response["edges"]:
        conn.execute(
            """
            INSERT INTO news_exposure_edges (
                run_id, from_type, from_id, to_type, to_id, direction, magnitude,
                confidence, mechanism, evidence_article_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                edge["from_type"], edge["from_id"], edge["to_type"], edge["to_id"],
                edge["direction"], edge["magnitude"], edge["confidence"], edge["mechanism"],
                json.dumps(edge["evidence_article_ids"]),
            ),
        )

    for position in positions:
        model_impact = response["position_impacts"].get(position["option_symbol"])
        selected_edge = _find_position_edge(position, response["edges"], conn)
        if model_impact is None or selected_edge is None:
            continue
        score, score_inputs = calculate_position_risk_score(
            quantity=_number(position["quantity"]),
            direction=selected_edge["direction"],
            magnitude=selected_edge["magnitude"],
            confidence=selected_edge["confidence"],
            dte=position.get("dte"),
            implied_volatility=position.get("implied_volatility"),
            contract_type=position["contract_type"],
            order_side=position["order_side"],
        )
        conn.execute(
            """
            INSERT INTO news_exposure_position_impacts (
                run_id, option_symbol, risk_level, reason, risk_score, score_inputs_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                position["option_symbol"],
                _risk_level(score),
                model_impact["reason"],
                score,
                json.dumps(score_inputs),
            ),
        )

    edge_rows = conn.execute(
        "SELECT id, to_type, to_id FROM news_exposure_edges WHERE run_id = ?", (run_id,)).fetchall()
    position_rows = conn.execute(
        "SELECT option_symbol, risk_score, score_inputs_json FROM news_exposure_position_impacts WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    position_by_symbol = {row["option_symbol"]: dict(row) for row in position_rows}
    for edge_row in edge_rows:
        linked_scores: list[float] = []
        for position in positions:
            selected_edge = _find_position_edge(position, response["edges"], conn)
            if selected_edge is None:
                continue
            if selected_edge["to_type"] == edge_row["to_type"] and selected_edge["to_id"] == edge_row["to_id"]:
                stored_position = position_by_symbol.get(position["option_symbol"])
                if stored_position is not None:
                    linked_scores.append(_number(stored_position["risk_score"]))
        score = max(linked_scores, default=0.0)
        conn.execute(
            "UPDATE news_exposure_edges SET exposure_score = ?, score_inputs_json = ? WHERE id = ?",
            (score, json.dumps({"linked_position_risk_scores": linked_scores}), edge_row["id"]),
        )
    return run_id


def _store_failed_run(conn, *, article_id: int, model: str, raw_response: str | None, error: Exception) -> None:
    conn.execute(
        """
        INSERT INTO news_exposure_runs (
            article_id, model, prompt_version, input_article_ids_json, raw_response_json,
            status, error_message
        ) VALUES (?, ?, ?, ?, ?, 'failed', ?)
        """,
        (
            article_id,
            model,
            PROMPT_VERSION,
            json.dumps([article_id]),
            json.dumps({"raw_response": raw_response}) if raw_response is not None else None,
            str(error),
        ),
    )


def _latest_cached_run(conn, article_id: int, model: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM news_exposure_runs
        WHERE article_id = ? AND model = ? AND prompt_version = ? AND status = 'completed'
        ORDER BY id DESC LIMIT 1
        """,
        (article_id, model, PROMPT_VERSION),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def create_exposure_run(article_id: int, *, force: bool = False, model: str | None = None) -> dict[str, Any]:
    _initialize_databases()
    normalized_article_id = int(article_id)
    selected_model = resolve_exposure_map_model(model)
    if not selected_model:
        raise ValueError("An exposure-map model is required.")
    with get_connection(DB_PATH) as conn:
        article = _load_article(conn, normalized_article_id)
        if article is None:
            raise LookupError("Article not found.")
        cached_run_id = _latest_cached_run(conn, normalized_article_id, selected_model)
        if cached_run_id is not None and not force:
            return {"cached": True, **build_exposure_map_payload(normalized_article_id, run_id=cached_run_id, conn=conn)}
        relationships = _load_entity_relationships(conn, normalized_article_id)
        positions = _load_paper_positions(conn)

    grounding_payload = _build_grounding_payload(article, relationships, positions)
    system_prompt, user_prompt = _terra_prompts(grounding_payload)
    raw_response: str | None = None
    try:
        raw_response = ask_llm_model(
            get_model_client(),
            selected_model,
            system_prompt,
            user_prompt,
            temperature=0,
        response_schema=EXPOSURE_MAP_RESPONSE_SCHEMA,
        )
        parsed_response = extract_json_object(raw_response)
        response = validate_terra_response(
            parsed_response,
            article_id=normalized_article_id,
            relationships=relationships,
            positions=positions,
        )
    except Exception as exc:
        with get_connection(DB_PATH) as conn:
            _store_failed_run(
                conn,
                article_id=normalized_article_id,
                model=selected_model,
                raw_response=raw_response,
                error=exc,
            )
        raise
    with get_connection(DB_PATH) as conn:
        run_id = _store_run(
            conn,
            article_id=normalized_article_id,
            model=selected_model,
            response=response,
            positions=positions,
        )
        payload = build_exposure_map_payload(normalized_article_id, run_id=run_id, conn=conn)
    return {"cached": False, **payload}


def _load_entity_node(conn, entity_type: str, entity_id: str) -> dict[str, Any] | None:
    if entity_type == "sector":
        row = conn.execute("SELECT id, name, sector_key FROM sectors WHERE id = ?", (entity_id,)).fetchone()
        if row is not None:
            return {"id": f"sector:{row['id']}", "type": "sector", "label": row["name"], "subtitle": row["sector_key"]}
    if entity_type == "industry":
        row = conn.execute("SELECT id, name, industry_key, sector_id FROM industries WHERE id = ?", (entity_id,)).fetchone()
        if row is not None:
            return {"id": f"industry:{row['id']}", "type": "industry", "label": row["name"], "subtitle": row["industry_key"], "parent_sector_id": row["sector_id"]}
    if entity_type == "company":
        row = conn.execute(
            "SELECT c.id, c.symbol, c.name, c.industry_id, i.sector_id FROM companies AS c JOIN industries AS i ON i.id = c.industry_id WHERE c.symbol = ?",
            (entity_id,),
        ).fetchone()
        if row is not None:
            return {"id": f"company:{row['symbol']}", "type": "company", "label": row["name"], "subtitle": row["symbol"], "parent_industry_id": row["industry_id"], "parent_sector_id": row["sector_id"]}
    return None


def _add_ancestor_nodes(conn, nodes: dict[str, dict[str, Any]], entity_node: dict[str, Any], graph_edges: list[dict[str, Any]]) -> None:
    if entity_node["type"] == "company":
        industry = _load_entity_node(conn, "industry", str(entity_node["parent_industry_id"]))
        if industry is not None:
            nodes.setdefault(industry["id"], industry)
            graph_edges.append({"id": f"hierarchy:{industry['id']}:{entity_node['id']}", "source": industry["id"], "target": entity_node["id"], "kind": "hierarchy", "label": "Industry membership", "score": 0})
            _add_ancestor_nodes(conn, nodes, industry, graph_edges)
    elif entity_node["type"] == "industry":
        sector = _load_entity_node(conn, "sector", str(entity_node["parent_sector_id"]))
        if sector is not None:
            nodes.setdefault(sector["id"], sector)
            graph_edges.append({"id": f"hierarchy:{sector['id']}:{entity_node['id']}", "source": sector["id"], "target": entity_node["id"], "kind": "hierarchy", "label": "Sector membership", "score": 0})


def build_exposure_map_payload(article_id: int, *, run_id: int | None = None, conn=None) -> dict[str, Any]:
    owns_connection = conn is None
    if owns_connection:
        _initialize_databases()
    active_conn = conn or get_connection(DB_PATH)
    try:
        article = _load_article(active_conn, int(article_id))
        if article is None:
            raise LookupError("Article not found.")
        if run_id is None:
            run = active_conn.execute(
                "SELECT * FROM news_exposure_runs WHERE article_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1",
                (article_id,),
            ).fetchone()
        else:
            run = active_conn.execute(
                "SELECT * FROM news_exposure_runs WHERE id = ? AND article_id = ?", (run_id, article_id)
            ).fetchone()
        if run is None:
            return {
                "article_id": int(article_id), "has_run": False, "article": article, "nodes": [], "edges": [],
                "position_impacts": [], "uncertainties": [], "no_trade_executed": True,
            }
        run_data = dict(run)
        edge_rows = [dict(row) for row in active_conn.execute("SELECT * FROM news_exposure_edges WHERE run_id = ? ORDER BY id", (run_data["id"],)).fetchall()]
        position_impacts = [dict(row) for row in active_conn.execute("SELECT * FROM news_exposure_position_impacts WHERE run_id = ? ORDER BY risk_score DESC", (run_data["id"],)).fetchall()]
        current_positions = {position["option_symbol"]: position for position in _load_paper_positions(active_conn)}
        nodes: dict[str, dict[str, Any]] = {
            f"article:{article['id']}": {"id": f"article:{article['id']}", "type": "article", "label": article["title"], "subtitle": article["source"], "evidence_score": article["evidence_score"]}
        }
        graph_edges: list[dict[str, Any]] = []
        for edge in edge_rows:
            entity_node = _load_entity_node(active_conn, edge["to_type"], edge["to_id"])
            if entity_node is None:
                continue
            entity_node["direction"] = edge["direction"]
            nodes[entity_node["id"]] = entity_node
            graph_edges.append({
                "id": f"evidence:{edge['id']}", "source": f"article:{article['id']}", "target": entity_node["id"], "kind": "evidence",
                "label": edge["mechanism"], "score": _number(edge["exposure_score"]), "direction": edge["direction"],
                "confidence": edge["confidence"], "magnitude": edge["magnitude"], "score_inputs": _safe_json_loads(edge["score_inputs_json"]),
            })
            _add_ancestor_nodes(active_conn, nodes, entity_node, graph_edges)
        for impact in position_impacts:
            position = current_positions.get(impact["option_symbol"])
            if position is None:
                continue
            node_id = f"position:{position['option_symbol']}"
            nodes[node_id] = {
                "id": node_id, "type": "position", "label": position["option_symbol"], "subtitle": position["underlying_symbol"],
                "dte": position["dte"], "quantity": position["quantity"], "paper": True, "risk_score": impact["risk_score"],
                "risk_level": impact["risk_level"], "reason": impact["reason"], "score_inputs": _safe_json_loads(impact["score_inputs_json"]),
            }
            company_node_id = f"company:{position['underlying_symbol']}"
            if company_node_id not in nodes:
                company = _load_entity_node(active_conn, "company", position["underlying_symbol"])
                if company is not None:
                    company["direction"] = "unclear"
                    nodes[company_node_id] = company
                    _add_ancestor_nodes(active_conn, nodes, company, graph_edges)
            if company_node_id in nodes:
                graph_edges.append({"id": f"position:{position['option_symbol']}", "source": company_node_id, "target": node_id, "kind": "position", "label": impact["reason"], "score": _number(impact["risk_score"]), "direction": "negative" if impact["risk_level"] == "high" else "unclear", "score_inputs": _safe_json_loads(impact["score_inputs_json"])})
        return {
            "article_id": int(article_id), "has_run": True, "run": {"id": run_data["id"], "model": run_data["model"], "prompt_version": run_data["prompt_version"], "created_at": run_data["created_at"], "article_summary": run_data["article_summary"]},
            "article": article, "nodes": list(nodes.values()), "edges": graph_edges,
            "position_impacts": [{**impact, "score_inputs": _safe_json_loads(impact["score_inputs_json"])} for impact in position_impacts],
            "uncertainties": _safe_json_loads(run_data["uncertainties_json"]) or [], "no_trade_executed": True,
        }
    finally:
        if owns_connection:
            active_conn.close()


def list_recent_exposure_articles(limit: int = 50) -> dict[str, Any]:
    _initialize_databases()
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, title, summary, source, source_url, published_at, evidence_score
            FROM news_articles
            ORDER BY COALESCE(evidence_score, 0) DESC, COALESCE(published_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return {"articles": [dict(row) for row in rows], "no_trade_executed": True}
