from pathlib import Path
import logging
import sys

from db_common import DB_PATH, get_connection
from market_db import initialize_database as initialize_market_database
from market_db import (
    add_company_price_snapshot,
    list_companies_by_industry,
    list_company_price_snapshots,
    list_industry_company_rankings,
    load_sector_definitions_from_json,
    load_sector_tree,
    load_sector_tree_from_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_DATA_DIR = PROJECT_ROOT / "Python Scripts" / "Webscraping" / "market_data"
if str(MARKET_DATA_DIR) not in sys.path:
    sys.path.append(str(MARKET_DATA_DIR))

try:
    from yFinanceData import load_industry_from_yfinance, load_sector_from_yfinance
except ImportError:  # pragma: no cover - optional runtime integration
    load_industry_from_yfinance = None
    load_sector_from_yfinance = None

LOGGER = logging.getLogger("market_bootstrap")


def _ensure_sector_definitions_seeded() -> None:
    initialize_market_database(db_path=DB_PATH)
    with get_connection(DB_PATH) as conn:
        sector_count = conn.execute("SELECT COUNT(*) AS count FROM sectors").fetchone()["count"]

    if int(sector_count or 0) > 0:
        return

    try:
        load_sector_definitions_from_json(db_path=DB_PATH)
    except FileNotFoundError:  # pragma: no cover - local setup may omit the seed file
        return


def _find_sector_row(sector_identifier: str) -> dict | None:
    _ensure_sector_definitions_seeded()
    needle = " ".join(str(sector_identifier or "").split()).strip().lower()
    if not needle:
        return None

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, sector_key, name
            FROM sectors
            ORDER BY name
            """
        ).fetchall()

    for row in rows:
        if str(row["sector_key"] or "").strip().lower() == needle:
            return dict(row)
    for row in rows:
        if str(row["name"] or "").strip().lower() == needle:
            return dict(row)
    return None


def _find_industry_row(industry_identifier: str) -> dict | None:
    _ensure_sector_definitions_seeded()
    needle = " ".join(str(industry_identifier or "").split()).strip().lower()
    if not needle:
        return None

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.industry_key, i.name, i.sector_id, s.sector_key, s.name AS sector_name
            FROM industries AS i
            JOIN sectors AS s ON s.id = i.sector_id
            ORDER BY s.name, i.name
            """
        ).fetchall()

    for row in rows:
        if str(row["industry_key"] or "").strip().lower() == needle:
            return dict(row)
    for row in rows:
        if str(row["name"] or "").strip().lower() == needle:
            return dict(row)
    return None


def ensure_sector_market_data(sector_identifier: str) -> dict | None:
    sector = _find_sector_row(sector_identifier)
    if sector is None:
        return None

    with get_connection(DB_PATH) as conn:
        industry_row = conn.execute(
            """
            SELECT 1
            FROM industries
            WHERE sector_id = ?
            LIMIT 1
            """,
            (int(sector["id"]),),
        ).fetchone()

    if industry_row is not None:
        LOGGER.info(
            "Market data already present for sector %s (%s)",
            sector.get("name"),
            sector.get("sector_key"),
        )
        return sector

    if load_sector_from_yfinance is None:
        LOGGER.warning(
            "Skipping yfinance hydration for sector %s (%s) because the loader is unavailable",
            sector.get("name"),
            sector.get("sector_key"),
        )
        return sector

    LOGGER.info(
        "Hydrating market data for sector %s (%s) from yfinance",
        sector.get("name"),
        sector.get("sector_key"),
    )
    load_sector_from_yfinance(str(sector["sector_key"]))
    LOGGER.info(
        "Finished market data hydration for sector %s (%s)",
        sector.get("name"),
        sector.get("sector_key"),
    )
    return _find_sector_row(str(sector["sector_key"]))


def ensure_all_sector_market_data() -> list[dict]:
    _ensure_sector_definitions_seeded()
    with get_connection(DB_PATH) as conn:
        sector_rows = conn.execute(
            """
            SELECT id, sector_key, name
            FROM sectors
            ORDER BY name
            """
        ).fetchall()

    hydrated_sectors: list[dict] = []
    total = len(sector_rows)
    for index, row in enumerate(sector_rows, start=1):
        sector_key = str(row["sector_key"] or "").strip()
        if not sector_key:
            continue
        LOGGER.info(
            "Bootstrapping market sector %s/%s: %s (%s)",
            index,
            total,
            row["name"],
            sector_key,
        )
        hydrated_sector = ensure_sector_market_data(sector_key)
        if hydrated_sector is not None:
            hydrated_sectors.append(hydrated_sector)
            LOGGER.info(
                "Completed market sector %s/%s: %s (%s)",
                index,
                total,
                hydrated_sector.get("name"),
                hydrated_sector.get("sector_key"),
            )
        else:
            LOGGER.warning(
                "Failed to resolve market sector %s/%s for key %s",
                index,
                total,
                sector_key,
            )
    return hydrated_sectors


def ensure_industry_market_data(industry_identifier: str) -> dict | None:
    industry = _find_industry_row(industry_identifier)
    if industry is not None:
        with get_connection(DB_PATH) as conn:
            company_row = conn.execute(
                """
                SELECT 1
                FROM companies
                WHERE industry_id = ?
                LIMIT 1
                """,
                (int(industry["id"]),),
            ).fetchone()

        if company_row is not None:
            return industry

    if load_industry_from_yfinance is not None:
        load_industry_from_yfinance(str(industry_identifier))
        industry = _find_industry_row(industry_identifier)
        if industry is not None:
            return industry

    if industry is not None:
        return industry

    sector = ensure_sector_market_data(str(industry_identifier))
    if sector is None:
        return None

    return _find_industry_row(industry_identifier)
