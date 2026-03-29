from db_common import get_connection


def get_all_industries() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                i.id,
                i.industry_key,
                i.name,
                i.symbol,
                i.market_weight,
                s.sector_key,
                s.name AS sector_name
            FROM industries AS i
            JOIN sectors AS s ON s.id = i.sector_id
            ORDER BY s.name, i.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_companies() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.symbol,
                c.name,
                c.rating,
                c.market_weight,
                c.raw_json,
                i.id AS industry_id,
                i.industry_key,
                i.name AS industry_name,
                s.id AS sector_id,
                s.sector_key,
                s.name AS sector_name
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            JOIN sectors AS s ON s.id = i.sector_id
            ORDER BY s.name, i.name, c.name
            """
        ).fetchall()
    return [dict(row) for row in rows]
