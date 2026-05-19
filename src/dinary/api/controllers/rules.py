"""Rules feed business logic."""

import json
import sqlite3
from typing import Any

from dinary.db.receipts import count_pending_classification_jobs


def count_doubtful(con: sqlite3.Connection) -> int:
    return con.execute(
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


def count_total(con: sqlite3.Connection) -> int:
    return con.execute(
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


def _resolve_ids_to_names(
    con: sqlite3.Connection,
    table: str,
    ids_json: str | None,
) -> list[dict[str, Any]]:
    """Parse a JSON id-array and resolve each to {id, name} via active rows in table."""
    if not ids_json:
        return []
    try:
        ids = [int(i) for i in json.loads(ids_json) if isinstance(i, (int, float))]
    except Exception:  # noqa: BLE001
        return []
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, name FROM {table} WHERE id IN ({placeholders}) AND is_active = 1",  # noqa: S608
        ids,
    ).fetchall()
    name_by_id = {int(r[0]): str(r[1]) for r in rows}
    return [{"id": i, "name": name_by_id[i]} for i in ids if i in name_by_id]


def query_rules(
    con: sqlite3.Connection,
    limit: int,
    offset: int,
    *,
    doubtful_only: bool = False,
) -> list[dict[str, Any]]:
    base_query = """
        WITH rule_stats AS (
            SELECT
                cr.id,
                cr.item_name_normalized,
                cr.category_id,
                cr.confidence_level,
                cr.alternative_category_ids,
                cr.tag_ids,
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
        """
    order_clause = """
         ORDER BY
             (confidence_level < 4) DESC,
             CASE WHEN confidence_level < 4 THEN amount_at_stake ELSE 0 END DESC,
             CASE WHEN confidence_level >= 4 THEN last_receipt_date ELSE '' END DESC
         LIMIT ? OFFSET ?
        """
    if doubtful_only:
        sql = base_query + " WHERE confidence_level < 4 " + order_clause
    else:
        sql = base_query + order_clause
    rows = con.execute(sql, [limit, offset]).fetchall()  # noqa: S608
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
            "alternative_categories": _resolve_ids_to_names(
                con,
                "categories",
                r["alternative_category_ids"],
            ),
            "tags": _resolve_ids_to_names(con, "tags", r["tag_ids"]),
        }
        for r in rows
    ]


def confirm_rules_bulk(con: sqlite3.Connection, rule_ids: list[int]) -> int:
    if not rule_ids:
        return 0
    placeholders = ",".join("?" * len(rule_ids))
    with con:
        con.execute(
            f"UPDATE classification_rules SET confidence_level=4, source='user_correction'"  # noqa: S608
            f" WHERE id IN ({placeholders})",
            rule_ids,
        )
        item_rows = con.execute(
            f"SELECT item_name_normalized, store_id FROM classification_rules"  # noqa: S608
            f" WHERE id IN ({placeholders})",
            rule_ids,
        ).fetchall()
        for item_name, store_id in item_rows:
            if store_id is None:
                con.execute(
                    """
                    UPDATE receipt_items SET confidence_level=4
                     WHERE name_normalized=?
                       AND receipt_id IN (SELECT id FROM receipts WHERE store_id IS NULL)
                    """,
                    [item_name],
                )
                con.execute(
                    """
                    UPDATE expenses SET confidence_level=4
                     WHERE id IN (
                         SELECT ri.expense_id FROM receipt_items ri
                          JOIN receipts rec ON rec.id = ri.receipt_id
                         WHERE ri.name_normalized=? AND rec.store_id IS NULL
                     )
                    """,
                    [item_name],
                )
            else:
                con.execute(
                    """
                    UPDATE receipt_items SET confidence_level=4
                     WHERE name_normalized=?
                       AND receipt_id IN (SELECT id FROM receipts WHERE store_id=?)
                    """,
                    [item_name, store_id],
                )
                con.execute(
                    """
                    UPDATE expenses SET confidence_level=4
                     WHERE id IN (
                         SELECT ri.expense_id FROM receipt_items ri
                          JOIN receipts rec ON rec.id = ri.receipt_id
                         WHERE ri.name_normalized=? AND rec.store_id=?
                     )
                    """,
                    [item_name, store_id],
                )
    return len(rule_ids)


def build_rules_feed(
    con: sqlite3.Connection,
    page: int,
    page_size: int,
    *,
    doubtful_only: bool = True,
) -> dict[str, Any]:
    con.row_factory = sqlite3.Row
    offset = (page - 1) * page_size
    d_total = count_doubtful(con)
    total = count_total(con)
    effective_total = d_total if doubtful_only else total
    rows = (
        query_rules(con, page_size, offset, doubtful_only=doubtful_only)
        if effective_total > 0
        else []
    )
    return {
        "doubtful_count": d_total,
        "items": rows,
        "has_more": offset + page_size < effective_total,
        "pending_receipts": count_pending_classification_jobs(con),
    }


def build_rules_counts(con: sqlite3.Connection) -> dict[str, Any]:
    count = con.execute(
        """
        SELECT COUNT(DISTINCT cr.id)
          FROM classification_rules cr
          JOIN receipt_items ri ON ri.name_normalized = cr.item_name_normalized
          JOIN receipts rec ON rec.id = ri.receipt_id
             AND (cr.store_id IS NULL OR rec.store_id = cr.store_id)
         WHERE cr.confidence_level < 4
        """,
    ).fetchone()[0]
    return {
        "doubtful_rules": int(count),
        "pending_receipts": count_pending_classification_jobs(con),
    }
