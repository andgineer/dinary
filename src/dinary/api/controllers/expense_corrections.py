"""Category correction business logic."""

import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from fastapi import HTTPException
from pydantic import BaseModel

from dinary.db.classification_rules import RuleSpec, create_or_update_rule
from dinary.db.storage import transaction


class CorrectionScope(StrEnum):
    single = "single"
    month = "month"
    year = "year"
    all = "all"


class CategoryCorrectionRequest(BaseModel):
    category_id: int
    scope: CorrectionScope = CorrectionScope.all


class CategoryCorrectionResponse(BaseModel):
    corrected_expense_id: int
    batch_updated_count: int


def _query_other_items(
    con: sqlite3.Connection,
    name_norm: str,
    store_id: int | None,
    expense_id: int,
    since: str | None,
) -> list:
    if since is not None:
        return con.execute(
            """
            SELECT ri.id, ri.receipt_id, ri.expense_id, ri.total_price
              FROM receipt_items ri
              JOIN receipts rec ON rec.id = ri.receipt_id
             WHERE ri.name_normalized = ?
               AND rec.store_id IS ?
               AND ri.expense_id != ?
               AND ri.expense_id IS NOT NULL
               AND rec.created_at >= ?
            """,
            [name_norm, store_id, expense_id, since],
        ).fetchall()
    return con.execute(
        """
        SELECT ri.id, ri.receipt_id, ri.expense_id, ri.total_price
          FROM receipt_items ri
          JOIN receipts rec ON rec.id = ri.receipt_id
         WHERE ri.name_normalized = ?
           AND rec.store_id IS ?
           AND ri.expense_id != ?
           AND ri.expense_id IS NOT NULL
        """,
        [name_norm, store_id, expense_id],
    ).fetchall()


def _since_for_scope(scope: CorrectionScope) -> str | None:
    now = datetime.now(UTC)
    if scope == CorrectionScope.month:
        return (now - timedelta(days=30)).isoformat()
    if scope == CorrectionScope.year:
        return datetime(now.year, 1, 1, tzinfo=UTC).isoformat()
    return None


def _upsert_rule_in_tx(
    con: sqlite3.Connection,
    store_id: int | None,
    item_name_normalized: str,
    category_id: int,
) -> None:
    create_or_update_rule(
        con,
        store_id,
        item_name_normalized,
        RuleSpec(category_id, 4, "user_correction"),
    )


def correct_category_sync(
    expense_id: int,
    req: CategoryCorrectionRequest,
    con: sqlite3.Connection,
    skip_rule: bool = False,
) -> CategoryCorrectionResponse:
    row = con.execute(
        "SELECT receipt_id, store_id FROM expenses WHERE id = ?",
        [expense_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Expense not found")

    cat_row = con.execute(
        "SELECT id FROM categories WHERE id = ? AND is_active = 1",
        [req.category_id],
    ).fetchone()
    if cat_row is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or inactive category_id: {req.category_id}",
        )

    receipt_id = row[0]
    store_id = row[1]

    with transaction(con):
        con.execute(
            "UPDATE expenses SET category_id = ?, confidence_level = 4 WHERE id = ?",
            [req.category_id, expense_id],
        )

        item_names: list[str] = []
        if receipt_id is not None:
            items = con.execute(
                "SELECT id, name_normalized FROM receipt_items WHERE expense_id = ?",
                [expense_id],
            ).fetchall()
            for item_id, name_norm in items:
                if name_norm:
                    item_names.append(name_norm)
                con.execute(
                    "UPDATE receipt_items SET category_id = ?, confidence_level = 4 WHERE id = ?",
                    [req.category_id, item_id],
                )

        since = _since_for_scope(req.scope)
        batch_count = 0
        for name_norm in set(item_names):
            if not skip_rule:
                _upsert_rule_in_tx(con, store_id, name_norm, req.category_id)

            if req.scope == CorrectionScope.single:
                continue

            other_items = _query_other_items(con, name_norm, store_id, expense_id, since)
            for other_item_id, _other_receipt_id, old_exp_id, _total_price in other_items:
                con.execute(
                    "UPDATE receipt_items SET category_id = ?, confidence_level = 4 WHERE id = ?",
                    [req.category_id, other_item_id],
                )
                con.execute(
                    "UPDATE expenses SET category_id = ?, confidence_level = 4 WHERE id = ?",
                    [req.category_id, old_exp_id],
                )
                batch_count += 1

    return CategoryCorrectionResponse(
        corrected_expense_id=expense_id,
        batch_updated_count=batch_count,
    )
