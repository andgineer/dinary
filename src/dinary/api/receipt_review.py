"""Receipt review API.

GET /api/receipts/review/feed?page=N&page_size=20
    Block 1: doubtful (conf < 4), deduplicated by (store_id, item_name_normalized),
             sorted by total amount at stake DESC.
    Block 2: certain (conf = 4), all expenses from receipts, sorted by receipt datetime DESC.

GET /api/receipts/review/counts
    {doubtful_rules: int} — count of unique rules with conf < 4, for PWA badge.
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from dinary.services import ledger_repo

router = APIRouter()


def _build_feed(conn: sqlite3.Connection, page: int, page_size: int) -> dict[str, Any]:
    offset = (page - 1) * page_size

    doubtful = conn.execute(
        """
        SELECT
            cr.id              AS rule_id,
            cr.item_name_normalized,
            s.chain_name       AS store_chain,
            cr.category_id,
            c.name             AS category_name,
            cr.confidence_level,
            SUM(ri.total_price) AS amount_at_stake,
            COUNT(ri.id)        AS occurrence_count
          FROM classification_rules cr
          JOIN categories c ON c.id = cr.category_id
          LEFT JOIN stores s ON s.id = cr.store_id
          JOIN receipt_items ri ON ri.name_normalized = cr.item_name_normalized
          JOIN receipts rec2 ON rec2.id = ri.receipt_id
             AND (cr.store_id IS NULL OR rec2.store_id = cr.store_id)
         WHERE cr.confidence_level < 4
         GROUP BY cr.id
         ORDER BY amount_at_stake DESC
        """,
    ).fetchall()

    doubtful_rows = [
        {
            "is_doubtful": True,
            "rule_id": int(r[0]),
            "item_name_normalized": str(r[1]),
            "store_chain": str(r[2]) if r[2] else None,
            "category_id": int(r[3]),
            "category_name": str(r[4]),
            "confidence_level": int(r[5]),
            "amount_at_stake": float(r[6] or 0),
            "occurrence_count": int(r[7]),
        }
        for r in doubtful
    ]

    certain = conn.execute(
        """
        SELECT
            e.id            AS expense_id,
            e.amount_original,
            e.currency_original,
            c.name          AS category_name,
            e.category_id,
            e.confidence_level,
            rec.created_at  AS receipt_datetime,
            s.chain_name    AS store_chain
          FROM expenses e
          JOIN categories c ON c.id = e.category_id
          JOIN receipts rec ON rec.id = e.receipt_id
          LEFT JOIN stores s ON s.id = e.store_id
         WHERE e.receipt_id IS NOT NULL
           AND e.confidence_level = 4
         ORDER BY rec.created_at DESC
        """,
    ).fetchall()

    certain_rows = [
        {
            "is_doubtful": False,
            "expense_id": int(r[0]),
            "amount": float(r[1]),
            "currency": str(r[2]),
            "category_name": str(r[3]),
            "category_id": int(r[4]),
            "confidence_level": int(r[5]) if r[5] is not None else None,
            "receipt_datetime": str(r[6]) if r[6] else None,
            "store_chain": str(r[7]) if r[7] else None,
        }
        for r in certain
    ]

    all_items = doubtful_rows + certain_rows
    total = len(all_items)
    page_items = all_items[offset : offset + page_size]

    return {
        "doubtful_count": len(doubtful_rows),
        "items": page_items,
        "has_more": offset + page_size < total,
    }


@router.get("/api/receipts/review/feed")
def review_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    conn = ledger_repo.get_connection()
    try:
        return _build_feed(conn, page, page_size)
    finally:
        conn.close()


@router.get("/api/receipts/review/counts")
def review_counts() -> dict:
    conn = ledger_repo.get_connection()
    try:
        count = conn.execute(
            """
            SELECT COUNT(DISTINCT cr.id)
              FROM classification_rules cr
              JOIN receipt_items ri ON ri.name_normalized = cr.item_name_normalized
              JOIN receipts rec ON rec.id = ri.receipt_id
                 AND (cr.store_id IS NULL OR rec.store_id = cr.store_id)
             WHERE cr.confidence_level < 4
            """,
        ).fetchone()[0]
        return {"doubtful_rules": int(count)}
    finally:
        conn.close()
