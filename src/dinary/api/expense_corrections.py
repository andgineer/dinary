"""PATCH /api/expenses/{id}/category — category correction with batch rule propagation."""

import asyncio
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dinary.services.classification_rules import RuleSpec, create_or_update_rule
from dinary.services.storage import get_db, transaction

router = APIRouter()


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


@router.patch("/api/expenses/{expense_id}/category", response_model=CategoryCorrectionResponse)
async def correct_expense_category(
    expense_id: int,
    req: CategoryCorrectionRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryCorrectionResponse:
    return await asyncio.to_thread(_correct_category_sync, expense_id, req, con)


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


def _correct_category_sync(
    expense_id: int,
    req: CategoryCorrectionRequest,
    con: sqlite3.Connection,
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
        items_by_expense: defaultdict[int, list[tuple[int, int, float]]] = defaultdict(list)
        for name_norm in set(item_names):
            _upsert_rule_in_tx(con, store_id, name_norm, req.category_id)

            if req.scope == CorrectionScope.single:
                continue

            other_items = _query_other_items(con, name_norm, store_id, expense_id, since)
            for other_item_id, other_receipt_id, old_exp_id, total_price in other_items:
                con.execute(
                    "UPDATE receipt_items SET category_id = ?, confidence_level = 4 WHERE id = ?",
                    [req.category_id, other_item_id],
                )
                items_by_expense[int(old_exp_id)].append(
                    (int(other_item_id), int(other_receipt_id), float(total_price)),
                )

        batch_count = _split_merge_expenses(con, req.category_id, expense_id, items_by_expense)

    return CategoryCorrectionResponse(
        corrected_expense_id=expense_id,
        batch_updated_count=batch_count,
    )


def _split_merge_expenses(
    con: sqlite3.Connection,
    new_category_id: int,
    primary_expense_id: int,
    items_by_expense: "defaultdict[int, list[tuple[int, int, float]]]",
) -> int:
    """Move batch items to the correct expense, splitting source expenses as needed."""
    moved_count = 0
    for old_exp_id, moved_items in items_by_expense.items():
        moved_ids = [item_id for item_id, _, _ in moved_items]
        moved_total = round(sum(tp for _, _, tp in moved_items), 2)
        receipt_id_for_exp = moved_items[0][1]

        placeholders = ",".join("?" * len(moved_ids))
        remaining = con.execute(
            f"SELECT COUNT(*) FROM receipt_items"  # noqa: S608
            f" WHERE expense_id = ? AND id NOT IN ({placeholders})",
            [old_exp_id, *moved_ids],
        ).fetchone()[0]

        if remaining == 0:
            con.execute(
                "UPDATE expenses SET category_id = ?, confidence_level = 4 WHERE id = ?",
                [new_category_id, old_exp_id],
            )
        else:
            con.execute(
                "UPDATE expenses"
                " SET amount = amount - ?, amount_original = amount_original - ?"
                " WHERE id = ?",
                [moved_total, moved_total, old_exp_id],
            )
            old_exp = con.execute(
                "SELECT datetime, store_id, currency_original FROM expenses WHERE id = ?",
                [old_exp_id],
            ).fetchone()
            exp_dt = old_exp[0] if old_exp else datetime.now(UTC).isoformat()
            exp_store_id = old_exp[1] if old_exp else None
            exp_currency = old_exp[2] if old_exp else "RSD"

            target = con.execute(
                "SELECT id FROM expenses WHERE receipt_id = ? AND category_id = ? AND id != ?",
                [receipt_id_for_exp, new_category_id, primary_expense_id],
            ).fetchone()
            if target:
                new_exp_id = int(target[0])
                con.execute(
                    "UPDATE expenses"
                    " SET amount = amount + ?, amount_original = amount_original + ?,"
                    "     confidence_level = 4"
                    " WHERE id = ?",
                    [moved_total, moved_total, new_exp_id],
                )
            else:
                con.execute(
                    "INSERT INTO expenses"
                    " (datetime, amount, amount_original, currency_original,"
                    "  category_id, confidence_level, receipt_id, store_id)"
                    " VALUES (?, ?, ?, ?, ?, 4, ?, ?)",
                    [
                        exp_dt,
                        moved_total,
                        moved_total,
                        exp_currency,
                        new_category_id,
                        receipt_id_for_exp,
                        exp_store_id,
                    ],
                )
                new_exp_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

            for item_id, _, _ in moved_items:
                con.execute(
                    "UPDATE receipt_items SET expense_id = ? WHERE id = ?",
                    [new_exp_id, item_id],
                )

        moved_count += len(moved_items)
    return moved_count


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
