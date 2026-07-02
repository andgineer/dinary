"""Underscore prefix keeps pytest from collecting this as a test module."""

import shutil
from unittest.mock import MagicMock

import pytest

from dinary.db import storage


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", dst)
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'food', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'car', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (1, 'vacation-2026', '2026-01-01', '2026-04-20', TRUE, TRUE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'dog', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'anna', TRUE)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (3, 'travel', TRUE)",
        )
    finally:
        con.close()


def _catalog():
    return (
        {"food": 1, "car": 2},
        {"vacation-2026": 1},
        {"dog": 1, "anna": 2, "travel": 3},
    )


def _fake_worksheet(raw_rows_including_header):
    ws = MagicMock()
    ws.get_all_values.return_value = raw_rows_including_header
    return ws


def _fake_sheet(ws):
    sh = MagicMock()
    sh.worksheet.return_value = ws
    return sh


__all__ = ["_catalog", "_fake_sheet", "_fake_worksheet", "db"]
