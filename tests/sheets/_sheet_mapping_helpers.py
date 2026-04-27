"""Shared fixtures + helpers for the split ``test_sheet_mapping_*.py``
files.

Underscore prefix keeps pytest from collecting this as a test module.
The autouse fixture stays scoped to the sheet-mapping suite (imported
into each split file rather than promoted to ``conftest.py``) so the
per-test DB-path override does not leak into sibling tests.
"""

from unittest.mock import MagicMock

import pytest

from dinary.services import ledger_repo


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", tmp_path / "dinary.db")
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
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'машина', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (1, 'отпуск-2026', '2026-01-01', '2026-04-20', TRUE, TRUE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'аня', TRUE)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (3, 'путешествия', TRUE)",
        )
    finally:
        con.close()


def _catalog():
    return (
        {"еда": 1, "машина": 2},
        {"отпуск-2026": 1},
        {"собака": 1, "аня": 2, "путешествия": 3},
    )


def _fake_worksheet(raw_rows_including_header):
    ws = MagicMock()
    ws.get_all_values.return_value = raw_rows_including_header
    return ws


def _fake_sheet(ws):
    sh = MagicMock()
    sh.worksheet.return_value = ws
    return sh


__all__ = ["_catalog", "_fake_sheet", "_fake_worksheet", "_tmp_db"]
