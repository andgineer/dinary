"""Shared fixtures + helpers for the split ``test_sheet_logging_*.py``
files.

Underscore prefix keeps pytest from collecting this as a test module.
The autouse fixtures stay scoped to the sheet-logging suite (imported
into each split file rather than promoted to ``conftest.py``) so the
per-test DB-path override and the circuit-breaker reset do not leak
into sibling tests.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from dinary.config import settings
from dinary.services import ledger_repo, sheet_logging


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", tmp_path / "dinary.db")
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "test-spreadsheet-id")


@pytest.fixture(autouse=True)
def _reset_backoff():
    # Circuit breaker state is module-level; clear it between tests so
    # a prior "transient error" test doesn't stall the next drain with
    # ``{backoff_active: True}``.
    sheet_logging._reset_backoff()
    yield
    sheet_logging._reset_backoff()


@pytest.fixture
def setup() -> int:
    """Seed the unified DB with one expense and its queue row.

    Returns the integer PK of that expense — the sheet-logging layer
    now keys queue rows on ``expenses.id`` rather than on a legacy
    string id.
    """
    ledger_repo.init_db()
    con = ledger_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
            " sheet_category, sheet_group) VALUES (1, 1, NULL, 'Food', 'Essentials')",
        )
    finally:
        con.close()

    con = ledger_repo.get_connection()
    try:
        ledger_repo.insert_expense(
            con,
            client_expense_id="exp1-client-key",
            expense_datetime=datetime(2026, 4, 14, 10),
            amount=12.0,
            amount_original=1500.0,
            currency_original="RSD",
            category_id=1,
            event_id=None,
            comment="lunch",
            sheet_category=None,
            sheet_group=None,
            tag_ids=[],
            enqueue_logging=True,
        )
        pk_row = con.execute(
            "SELECT id FROM expenses WHERE client_expense_id = 'exp1-client-key'",
        ).fetchone()
    finally:
        con.close()
    assert pk_row is not None
    return int(pk_row[0])


def _expense_row(
    *,
    amount: Decimal,
    amount_original: Decimal,
    currency_original: str,
) -> ledger_repo.ExpenseRow:
    """Minimal ``ExpenseRow`` factory for pure-helper tests.

    ``_derive_app_currency_amount_for_sheet`` only reads ``amount``, ``amount_original``
    and ``currency_original``; the rest exists solely to satisfy the
    dataclass slots.
    """
    return ledger_repo.ExpenseRow(
        id=1,
        client_expense_id="x",
        datetime=datetime(2026, 4, 14, 10),
        amount=amount,
        amount_original=amount_original,
        currency_original=currency_original,
        category_id=1,
        event_id=None,
        comment=None,
        sheet_category=None,
        sheet_group=None,
    )


__all__ = ["_expense_row", "_reset_backoff", "_tmp_data_dir", "setup"]
