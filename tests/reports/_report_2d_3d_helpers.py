"""Shared fixtures + catalog seeder for the split ``test_report_2d_3d_*.py``
files.

Underscore prefix keeps pytest from collecting this as a test module.
The autouse fixtures stay scoped to the report-2d-3d suite (imported
into each split file rather than promoted to ``conftest.py``) so the
per-test DB-path override and the ``read_import_sources`` stub do not
leak into sibling tests.
"""

import pytest

from dinary import config
from dinary.config import ImportSourceRow
from dinary.services import ledger_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", tmp_path / "dinary.db")


@pytest.fixture(autouse=True)
def _stub_import_sources(monkeypatch):
    """Stand in for ``.deploy/import_sources.json`` with a single 2024 row.

    ``report_2d_3d._get_import_years`` calls ``read_import_sources``
    to enumerate years in scope; without the stub it would see the
    operator's real file (or an empty list) and break hermeticity.
    """
    rows = [
        ImportSourceRow(
            year=2024,
            spreadsheet_id="sid",
            worksheet_name="",
            layout_key="default",
        ),
    ]
    monkeypatch.setattr(config, "read_import_sources", lambda: list(rows))


def _seed_catalog():
    """Seed a minimal catalog into ``dinary.db`` for resolution tests."""
    ledger_repo.init_db()
    con = ledger_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        for cid, name in [
            (1, "еда"),
            (2, "мобильник"),
            (3, "кафе"),
            (4, "аренда"),
            (5, "коммунальные"),
            (6, "бытовая техника"),
            (7, "транспорт"),
            (8, "электроника"),
            (9, "подарки"),
            (10, "сервисы"),
            (11, "инструменты"),
            (12, "гаджеты"),
        ]:
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active) VALUES (?, ?, 1, TRUE)",
                [cid, name],
            )
        for tid, name in [(1, "собака"), (2, "Аня"), (3, "релокация")]:
            con.execute(
                "INSERT INTO tags (id, name, is_active) VALUES (?, ?, TRUE)",
                [tid, name],
            )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (1, 'отпуск-2024', '2024-01-01', '2024-12-31', TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (2, 'релокация-в-Сербию', '2022-04-01', '2030-12-31', FALSE)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute("INSERT INTO import_mapping_tags VALUES (1, 1)")
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'мобильник', '', 2, NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 0, 'кафе', 'путешествия', 3, 1)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (4, 0, 'аренда', 'релокация', 4, NULL)",
        )
        con.execute("INSERT INTO import_mapping_tags VALUES (4, 3)")
    finally:
        con.close()


__all__ = ["_seed_catalog", "_stub_import_sources", "_tmp_data_dir"]
