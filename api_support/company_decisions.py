"""Latest strategist and manager decision per company, and the market regime, for the dashboard."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from market_db_modules.market_regime import get_latest_market_regime_snapshot
from db_common import get_connection
from api_support.request_utils import safe_json_loads
from services.core.numbers import safe_float


def _build_empty_company_decisions_payload() -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": "",
        "company_count": 0,
        "companies": [],
    }


def _empty_company_decision_entry(*, symbol: str, name: str, last_updated_at: str) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "last_updated_at": last_updated_at,
        "strategist": {
            "decision": "",
            "confidence": "",
            "summary": "",
            "thesis": [],
            "risks": [],
        },
        "manager": {
            "decision": "",
            "confidence": "",
            "reason": "",
            "target_dte_bucket": "",
            "manager_decision_history_id": None,
            "selected_option_id": "",
            "selected_expiration_date": "",
            "selected_strike_price": None,
            "selected_option_source": "",
            "entry_gate": {},
            "market_regime_gate": {},
        },
        "diagnostics": {
            "historical_bars": {},
            "strategist_validation": {},
        },
        "manager_history": [],
    }


def _extract_article_references_from_manager_input(
    manager_input_json: object,
    *,
    limit: int = 3,
) -> list[dict]:
    if isinstance(manager_input_json, str):
        try:
            manager_input_json = json.loads(manager_input_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(manager_input_json, dict):
        return []

    article_references = manager_input_json.get("article_references")
    if not isinstance(article_references, list):
        return []

    compact: list[dict] = []
    for item in article_references[: max(1, int(limit))]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "article_id": int(item.get("article_id") or 0),
                "title": str(item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "published_at": str(item.get("published_at") or "").strip(),
                "article_scope": str(item.get("article_scope") or "").strip(),
                "evidence_layers": [
                    str(layer or "").strip()
                    for layer in item.get("evidence_layers", [])
                    if str(layer or "").strip()
                ],
            }
        )
    return compact


def _normalize_history_company_identity(item: dict, manager_input_json: object) -> tuple[str, str]:
    manager_input = manager_input_json if isinstance(manager_input_json, dict) else {}
    company = manager_input.get("company")
    if not isinstance(company, dict):
        company = {}

    symbol = str(item.get("symbol") or company.get("symbol") or "").strip().upper()
    name = str(item.get("company_name") or company.get("name") or "").strip()
    return symbol, name


def _extract_strategist_snapshot_from_manager_input(
    manager_input_json: object,
    *,
    fallback_decision: object = None,
) -> dict:
    manager_input = manager_input_json if isinstance(manager_input_json, dict) else {}
    strategist = manager_input.get("strategist_recommendation")
    if not isinstance(strategist, dict):
        strategist = {}

    return {
        "decision": str(strategist.get("decision") or fallback_decision or "").strip(),
        "confidence": str(strategist.get("confidence") or "").strip(),
        "summary": str(strategist.get("summary") or "").strip(),
        "thesis": [
            str(item).strip()
            for item in strategist.get("thesis", [])
            if str(item).strip()
        ]
        if isinstance(strategist.get("thesis"), list)
        else [],
        "risks": [
            str(item).strip()
            for item in strategist.get("risks", [])
            if str(item).strip()
        ]
        if isinstance(strategist.get("risks"), list)
        else [],
    }


def _extract_manager_snapshot_from_history_row(
    item: dict,
    manager_output_json: object,
) -> dict:
    manager_output = manager_output_json if isinstance(manager_output_json, dict) else {}
    recommendation = manager_output.get("recommendation")
    if not isinstance(recommendation, dict):
        recommendation = {}
    entry_gate = manager_output.get("entry_gate") if isinstance(manager_output.get("entry_gate"), dict) else {}
    market_regime_gate = manager_output.get("market_regime_gate") if isinstance(manager_output.get("market_regime_gate"), dict) else {}

    return {
        "decision": str(item.get("manager_decision") or recommendation.get("decision") or "").strip(),
        "confidence": str(item.get("manager_confidence") or recommendation.get("confidence") or "").strip(),
        "reason": str(item.get("manager_reason") or recommendation.get("reason") or "").strip(),
        "target_dte_bucket": str(item.get("target_dte_bucket") or recommendation.get("target_dte_bucket") or "").strip(),
        "manager_decision_history_id": item.get("id"),
        "selected_option_id": str(item.get("selected_option_id") or recommendation.get("selected_option_id") or "").strip(),
        "selected_expiration_date": str(
            item.get("selected_expiration_date") or recommendation.get("selected_expiration_date") or ""
        ).strip(),
        "selected_strike_price": safe_float(
            item.get("selected_strike_price")
            if item.get("selected_strike_price") not in (None, "")
            else recommendation.get("selected_strike_price")
        ),
        "selected_option_source": str(
            item.get("selected_option_source") or recommendation.get("selected_option_source") or ""
        ).strip(),
        "entry_gate": entry_gate,
        "market_regime_gate": market_regime_gate,
    }


def _extract_historical_bars_diagnostic(manager_input_json: object) -> dict:
    manager_input = manager_input_json if isinstance(manager_input_json, dict) else {}
    market_context = manager_input.get("market_context")
    if not isinstance(market_context, dict):
        market_context = {}
    underlying = market_context.get("underlying_price_history")
    if not isinstance(underlying, dict):
        return {}

    return {
        "available": bool(underlying.get("available")),
        "source": str(underlying.get("source") or "").strip(),
        "feed": str(underlying.get("feed") or "").strip(),
        "close_count": int(underlying.get("close_count") or 0),
        "error": str(underlying.get("error") or "").strip(),
    }


def _load_latest_strategist_run_statuses() -> dict[int, dict]:
    """Return the most recent persisted strategist status for each company.

    A failed strategist response has no manager-decision row because the cycle
    intentionally stops before manager evaluation.  The dashboard therefore
    reads the already-persisted strategist run directly to show that failure.
    """
    try:
        with closing(get_connection()) as conn, conn:
            # status follows the large payload columns, so only the latest run per
            # company is read; the subquery resolves it from the index alone.
            rows = conn.execute(
                """
                SELECT
                    sr.company_id,
                    c.symbol,
                    c.name AS company_name,
                    sr.status,
                    sr.error_message,
                    sr.input_article_ids_json,
                    sr.created_at
                FROM companies AS c
                JOIN strategist_runs AS sr ON sr.id = (
                    SELECT latest.id
                    FROM strategist_runs AS latest
                    WHERE latest.company_id = c.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                """
            ).fetchall()
    except Exception:
        return {}

    latest_by_company: dict[int, dict] = {}
    for row in rows:
        item = dict(row)
        company_id = int(item.get("company_id") or 0)
        if company_id <= 0:
            continue
        article_ids = safe_json_loads(item.get("input_article_ids_json"))
        latest_by_company[company_id] = {
            "company_id": company_id,
            "symbol": str(item.get("symbol") or "").strip().upper(),
            "company_name": str(item.get("company_name") or "").strip(),
            "status": str(item.get("status") or "").strip().lower(),
            "error_message": str(item.get("error_message") or "").strip(),
            "input_article_count": len(article_ids) if isinstance(article_ids, list) else 0,
            "created_at": str(item.get("created_at") or "").strip(),
        }
    return latest_by_company


def _strategist_validation_diagnostic(status: dict | None) -> dict:
    if not isinstance(status, dict) or status.get("status") != "failed":
        return {}
    return {
        "status": "failed",
        "error_message": str(status.get("error_message") or "").strip(),
        "input_article_count": int(status.get("input_article_count") or 0),
        "created_at": str(status.get("created_at") or "").strip(),
    }


def _serialize_manager_history_row(item: dict) -> dict:
    return {
        "id": int(item.get("id") or 0),
        "decision_run_at": str(item.get("decision_run_at") or "").strip(),
        "manager_decision": str(item.get("manager_decision") or "").strip(),
        "manager_confidence": str(item.get("manager_confidence") or "").strip(),
        "manager_reason": str(item.get("manager_reason") or "").strip(),
        "target_dte_bucket": str(item.get("target_dte_bucket") or "").strip(),
        "selected_option_id": str(item.get("selected_option_id") or "").strip(),
        "selected_option_symbol": str(item.get("selected_option_symbol") or "").strip(),
        "selected_expiration_date": str(item.get("selected_expiration_date") or "").strip(),
        "selected_strike_price": safe_float(item.get("selected_strike_price")),
        "selected_option_source": str(item.get("selected_option_source") or "").strip(),
        "trade_executed": bool(item.get("trade_executed")),
        "trade_execution_order_id": str(item.get("trade_execution_order_id") or "").strip(),
        "trade_execution_record_id": item.get("trade_execution_record_id"),
        "latest_trade_pnl_pct": safe_float(item.get("latest_trade_pnl_pct")),
        "latest_trade_pnl_updated_at": str(item.get("latest_trade_pnl_updated_at") or "").strip(),
        "pnl_expires_at": str(item.get("pnl_expires_at") or "").strip(),
        "resolved_outcome_label": str(item.get("resolved_outcome_label") or "").strip(),
        "article_references": _extract_article_references_from_manager_input(
            item.get("manager_input_json"),
            limit=3,
        ),
    }


COMPANY_DECISION_WINDOW_DAYS = 30


COMPANY_DECISION_ROW_LIMIT = 250


MANAGER_HISTORY_PER_COMPANY = 3


_PRICE_HISTORY_DIAGNOSTIC_FIELDS = ("available", "source", "feed", "close_count", "error")


_RECOMMENDATION_FIELDS = (
    "decision",
    "confidence",
    "reason",
    "target_dte_bucket",
    "selected_option_id",
    "selected_expiration_date",
    "selected_strike_price",
    "selected_option_source",
)


def _json_fields_sql(column: str, path: str, fields: tuple[str, ...]) -> str:
    pairs = ", ".join(f"'{field}', json_extract({column}, '{path}.{field}')" for field in fields)
    return f"json_object({pairs})"


# The manager payload holds the whole model input (articles, option chain, bars), so
# SQLite trims it to the fields this page shows, and only for the rows it shows.
_COMPANY_DECISION_ROWS_SQL = f"""
    WITH recent AS (
        SELECT
            history.id,
            history.decision_run_at,
            UPPER(TRIM(COALESCE(
                NULLIF(TRIM(history.symbol), ''),
                CASE WHEN json_valid(payload.manager_input_json)
                    THEN json_extract(payload.manager_input_json, '$.company.symbol') END,
                ''
            ))) AS symbol_key
        FROM manager_decision_history AS history
        LEFT JOIN manager_decision_payloads AS payload ON payload.manager_decision_history_id = history.id
        WHERE history.decision_run_at >= :cutoff
        ORDER BY history.decision_run_at DESC, history.id DESC
        LIMIT :row_limit
    ),
    ranked AS (
        SELECT
            id,
            symbol_key,
            ROW_NUMBER() OVER (
                PARTITION BY symbol_key ORDER BY decision_run_at DESC, id DESC
            ) AS symbol_rank
        FROM recent
        WHERE symbol_key <> ''
    )
    SELECT
        history.id,
        history.company_id,
        ranked.symbol_key AS symbol,
        history.company_name,
        history.decision_run_at,
        history.strategist_decision,
        history.manager_decision,
        history.manager_confidence,
        history.manager_reason,
        history.target_dte_bucket,
        history.selected_option_id,
        history.selected_option_symbol,
        history.selected_expiration_date,
        history.selected_strike_price,
        history.selected_option_source,
        history.trade_executed,
        history.trade_execution_order_id,
        history.trade_execution_record_id,
        CASE WHEN history.pnl_expires_at IS NULL OR history.pnl_expires_at > :now
            THEN history.latest_trade_pnl_pct END AS latest_trade_pnl_pct,
        CASE WHEN history.pnl_expires_at IS NULL OR history.pnl_expires_at > :now
            THEN history.latest_trade_pnl_updated_at END AS latest_trade_pnl_updated_at,
        history.pnl_expires_at,
        CASE WHEN history.pnl_expires_at IS NULL OR history.pnl_expires_at > :now
            THEN history.resolved_outcome_label END AS resolved_outcome_label,
        CASE WHEN json_valid(payload.manager_input_json) THEN json_object(
            'company', json_object(
                'name', json_extract(payload.manager_input_json, '$.company.name')
            ),
            'strategist_recommendation',
                json_extract(payload.manager_input_json, '$.strategist_recommendation'),
            'article_references', json_extract(payload.manager_input_json, '$.article_references'),
            'market_context', json_object(
                'underlying_price_history',
                CASE WHEN json_type(
                    payload.manager_input_json, '$.market_context.underlying_price_history'
                ) = 'object' THEN {_json_fields_sql(
                    "payload.manager_input_json",
                    "$.market_context.underlying_price_history",
                    _PRICE_HISTORY_DIAGNOSTIC_FIELDS,
                )} END
            )
        ) END AS manager_input_json,
        CASE WHEN json_valid(payload.manager_output_json) THEN json_object(
            'recommendation', {_json_fields_sql(
                "payload.manager_output_json", "$.recommendation", _RECOMMENDATION_FIELDS
            )},
            'entry_gate', json_extract(payload.manager_output_json, '$.entry_gate'),
            'market_regime_gate', json_extract(payload.manager_output_json, '$.market_regime_gate')
        ) END AS manager_output_json
    FROM ranked
    JOIN manager_decision_history AS history ON history.id = ranked.id
    LEFT JOIN manager_decision_payloads AS payload ON payload.manager_decision_history_id = history.id
    WHERE ranked.symbol_rank <= :per_company
    ORDER BY history.decision_run_at DESC, history.id DESC
"""


def _load_recent_company_decision_rows() -> list[dict]:
    """Return each company's latest manager decisions with trimmed manager JSON."""
    now = datetime.now(timezone.utc)
    with closing(get_connection()) as conn, conn:
        rows = conn.execute(
            _COMPANY_DECISION_ROWS_SQL,
            {
                "cutoff": (now - timedelta(days=COMPANY_DECISION_WINDOW_DAYS)).isoformat(),
                "row_limit": COMPANY_DECISION_ROW_LIMIT,
                "now": now.isoformat(),
                "per_company": MANAGER_HISTORY_PER_COMPANY,
            },
        ).fetchall()
    return [dict(row) for row in rows]


def _build_company_decisions_payload_from_history() -> dict:
    try:
        rows = _load_recent_company_decision_rows()
    except Exception:
        return _build_empty_company_decisions_payload()

    strategist_run_statuses = _load_latest_strategist_run_statuses()
    if not rows and not strategist_run_statuses:
        return _build_empty_company_decisions_payload()

    companies_by_symbol: dict[str, dict] = {}
    history_by_symbol: dict[str, list[dict]] = {}

    for row in rows:
        item = dict(row or {})
        item["manager_input_json"] = safe_json_loads(item.get("manager_input_json"))
        item["manager_output_json"] = safe_json_loads(item.get("manager_output_json"))
        symbol, name = _normalize_history_company_identity(item, item.get("manager_input_json"))
        if not symbol:
            continue

        history_by_symbol.setdefault(symbol, []).append(item)

        if symbol in companies_by_symbol:
            if name and not companies_by_symbol[symbol].get("name"):
                companies_by_symbol[symbol]["name"] = name
            continue

        companies_by_symbol[symbol] = _empty_company_decision_entry(
            symbol=symbol,
            name=name,
            last_updated_at=str(item.get("decision_run_at") or "").strip(),
        )
        companies_by_symbol[symbol]["strategist"] = _extract_strategist_snapshot_from_manager_input(
            item.get("manager_input_json"),
            fallback_decision=item.get("strategist_decision"),
        )
        companies_by_symbol[symbol]["manager"] = _extract_manager_snapshot_from_history_row(
            item,
            item.get("manager_output_json"),
        )
        company_id = int(item.get("company_id") or 0)
        companies_by_symbol[symbol]["diagnostics"] = {
            "historical_bars": _extract_historical_bars_diagnostic(item.get("manager_input_json")),
            "strategist_validation": _strategist_validation_diagnostic(
                strategist_run_statuses.get(company_id)
            ),
        }

    for company_id, strategist_status in strategist_run_statuses.items():
        validation = _strategist_validation_diagnostic(strategist_status)
        if not validation:
            continue
        symbol = str(strategist_status.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if symbol not in companies_by_symbol:
            companies_by_symbol[symbol] = _empty_company_decision_entry(
                symbol=symbol,
                name=str(strategist_status.get("company_name") or "").strip(),
                last_updated_at=str(strategist_status.get("created_at") or "").strip(),
            )
        companies_by_symbol[symbol]["diagnostics"]["strategist_validation"] = validation

    for symbol, company_entry in companies_by_symbol.items():
        company_entry["manager_history"] = [
            _serialize_manager_history_row(item)
            for item in history_by_symbol.get(symbol, [])
        ]

    companies = sorted(
        companies_by_symbol.values(),
        key=lambda entry: (
            str(entry.get("last_updated_at") or ""),
            str(entry.get("symbol") or ""),
        ),
        reverse=True,
    )
    last_updated_at = str(companies[0].get("last_updated_at") or "").strip() if companies else ""
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": last_updated_at,
        "company_count": len(companies),
        "companies": companies,
    }


def build_company_decisions_payload() -> dict:
    return _build_company_decisions_payload_from_history()


def build_market_regime_payload() -> dict:
    snapshot = get_latest_market_regime_snapshot()
    if not isinstance(snapshot, dict):
        return {
            "available": False,
            "state": "unknown",
            "reason_codes": ["no_market_regime_snapshot_recorded"],
            "input_freshness": {},
            "macro_event_window": {},
        }
    return {"available": True, **snapshot}
