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
