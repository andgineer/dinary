"""Shared autouse fixture for the split ``test_admin_catalog_*.py``
files.

Each test gets a per-call temporary SQLite DB pre-seeded with a
single category group (id=1) so the split files can ``POST`` new
categories under it without first issuing a setup request. Pytest
auto-discovers this module via the per-test ``noqa: F401`` import
in each split file (we deliberately avoid promoting it to
``conftest.py`` so the override does not bleed into sibling
non-admin suites).
"""

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
            " VALUES (1, 'Food', 1, TRUE)",
        )
    finally:
        con.close()


__all__ = ["_tmp_db"]
