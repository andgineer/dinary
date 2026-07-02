"""Not promoted to conftest.py: doing so would leak this per-test DB override into sibling non-admin suites."""

import shutil

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
            " VALUES (1, 'Food', 1, TRUE)",
        )
    finally:
        con.close()


__all__ = ["db"]
