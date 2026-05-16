"""Receipt review API.

GET /api/receipts/review/feed?page=N&page_size=20
    Unified list of classification_rules sorted by:
      1. Doubtful first (confidence_level < 4), by amount at stake DESC
      2. Certain rules (confidence_level >= 4), by last receipt date DESC

GET /api/receipts/review/counts
    {doubtful_rules: int} — count of unique rules with conf < 4, for PWA badge.
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from dinary.services import ledger_repo

router = APIRouter()


def _count_doubtful(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT cr.id
              FROM classification_rules cr
              JOIN receipt_items ri ON ri.name_normalized = cr.item_name_normalized
              JOIN receipts rec ON rec.id = ri.receipt_id
                 AND (cr.store_id IS NULL OR rec.store_id = cr.store_id)
             WHERE cr.confidence_level < 4
             GROUP BY cr.id
        )
        """,
    ).fetchone()[0]


def _count_total(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT cr.id
              FROM classification_rules cr
              JOIN receipt_items ri ON ri.name_normalized = cr.item_name_normalized
              JOIN receipts rec ON rec.id = ri.receipt_id
                 AND (cr.store_id IS NULL OR rec.store_id = cr.store_id)
             GROUP BY cr.id
        )
        """,
    ).fetchone()[0]


def _query_rules(conn: sqlite3.Connection, limit: int, offset: int) -> list[dict]:
    rows = conn.execute(
        """
        WITH rule_stats AS (
            SELECT
                cr.id,
                cr.item_name_normalized,
                cr.category_id,
                cr.confidence_level,
                s.chain_name                AS store_chain,
                c.name                      AS category_name,
                SUM(ri.total_price)         AS amount_at_stake,
                COUNT(ri.id)                AS occurrence_count,
                MAX(ri.expense_id)          AS expense_id,
                MAX(e.currency_original)    AS currency,
                MAX(rec.created_at)         AS last_receipt_date
              FROM classification_rules cr
              JOIN categories c   ON c.id = cr.category_id
              LEFT JOIN stores s  ON s.id = cr.store_id
              JOIN receipt_items ri ON ri.name_normalized = cr.item_name_normalized
              JOIN receipts rec   ON rec.id = ri.receipt_id
                   AND (cr.store_id IS NULL OR rec.store_id = cr.store_id)
              LEFT JOIN expenses e ON e.id = ri.expense_id
             GROUP BY cr.id
        )
        SELECT *
          FROM rule_stats
         ORDER BY
             (confidence_level < 4) DESC,
             CASE WHEN confidence_level < 4 THEN amount_at_stake ELSE 0 END DESC,
             CASE WHEN confidence_level >= 4 THEN last_receipt_date ELSE '' END DESC
         LIMIT ? OFFSET ?
        """,
        [limit, offset],
    ).fetchall()
    return [
        {
            "is_doubtful": bool(r["confidence_level"] < 4),
            "id": int(r["id"]),
            "name": str(r["item_name_normalized"]) if r["item_name_normalized"] else None,
            "store": str(r["store_chain"]) if r["store_chain"] else None,
            "total": float(r["amount_at_stake"] or 0),
            "count": int(r["occurrence_count"]),
            "currency": str(r["currency"]) if r["currency"] else None,
            "confidence_level": int(r["confidence_level"]),
            "category_id": int(r["category_id"]),
            "category_name": str(r["category_name"]),
            "expense_id": int(r["expense_id"]) if r["expense_id"] is not None else None,
            "datetime": str(r["last_receipt_date"]) if r["last_receipt_date"] else None,
        }
        for r in rows
    ]


def _build_feed(conn: sqlite3.Connection, page: int, page_size: int) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    offset = (page - 1) * page_size

    d_total = _count_doubtful(conn)
    total = _count_total(conn)
    rows = _query_rules(conn, page_size, offset) if total > 0 else []

    return {
        "doubtful_count": d_total,
        "items": rows,
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
