"""Unit tests for ``dinary.tools.sql`` — the ``inv sql`` runner.

Covers the three render shapes (rich / csv / json), the JSON
round-trip that backs ``inv sql --remote``, and the safety contract
(read-only connection refuses writes).
"""

import io
import json as _json
import sqlite3
from decimal import Decimal

import allure
import pytest

from dinary.config import settings
from tasks import sql as sql_tool


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """The SQL tool is schema-agnostic, so a synthetic 3-column table exercises
    the coercion paths without coupling to every migration. Only
    ``settings.data_path`` is patched — ``sql._execute`` reads that directly."""
    db_path = tmp_path / "dinary.db"
    con = sqlite3.connect(str(db_path))
    try:
        # WAL to match runtime shape; otherwise read-only open may see "database is locked".
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE sample (
                id    INTEGER PRIMARY KEY,
                label TEXT,
                amt   DECIMAL(12,2)
            )
        """)
        con.execute(
            "INSERT INTO sample VALUES (1, 'еда', 12.34), (2, NULL, -5.00), (3, 'транспорт', 0)",
        )
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(settings, "data_path", str(db_path))
    return db_path


@allure.epic("Infrastructure")
@allure.feature("CLI tools")
class TestRenderRich:
    def test_header_and_rows(self):
        """The rich renderer emits both the column header and each row's
        ``str(value)`` into the captured stream; ``None`` renders as an
        empty cell so the operator doesn't see the literal ``"None"``.
        """
        buf = io.StringIO()
        sql_tool.render_rich(
            ["id", "label"],
            [(1, "a"), (2, None)],
            stream=buf,
        )
        out = buf.getvalue()
        assert "id" in out
        assert "label" in out
        assert "1 " in out or "1 " in out  # box-drawing padding varies
        assert "a" in out
        assert "2 row(s)" in out

    def test_empty_result_set_message(self):
        """``CREATE TABLE`` / ``PRAGMA`` / ``SET`` return no result
        set — the runner must say so rather than emitting an empty
        table with blank header and 0 rows, which is visually noisy.
        """
        buf = io.StringIO()
        sql_tool.render_rich([], [], stream=buf)
        assert "no result set" in buf.getvalue()


@allure.epic("Infrastructure")
@allure.feature("CLI tools")
class TestRenderCsv:
    def test_header_plus_rows_with_nulls(self):
        """CSV output has a header row and ``None`` serialises to an
        empty field — matches standard CSV convention for SQL nulls so
        a downstream ``csvkit`` pipeline sees expected ``"",<next>``.
        """
        buf = io.StringIO()
        sql_tool.render_csv(
            ["id", "label"],
            [(1, "eda"), (2, None)],
            stream=buf,
        )
        lines = buf.getvalue().strip().split("\r\n")
        assert lines[0] == "id,label"
        assert lines[1] == "1,eda"
        assert lines[2] == "2,"


@allure.epic("Infrastructure")
@allure.feature("CLI tools")
class TestRenderJson:
    def test_envelope_shape(self):
        """The envelope shape is the SSH wire format contract; ASCII non-escape
        so Cyrillic survives ``jq`` without ``--raw-output``."""
        buf = io.StringIO()
        sql_tool.render_json(
            ["id", "label"],
            [(1, "еда"), (2, None)],
            stream=buf,
        )
        payload = _json.loads(buf.getvalue())
        assert payload["columns"] == ["id", "label"]
        assert payload["rows"] == [[1, "еда"], [2, None]]
        assert payload["row_count"] == 2
        assert "еда" in buf.getvalue()  # not \u0435\u0434\u0430

    def test_decimal_stringifies(self):
        """``DECIMAL`` columns return Python ``Decimal``s that ``json``
        cannot serialise natively — the runner coerces to string so
        precision isn't silently lost to a lossy float conversion.
        """
        buf = io.StringIO()
        sql_tool.render_json(
            ["amt"],
            [(Decimal("12.34"),)],
            stream=buf,
        )
        assert _json.loads(buf.getvalue())["rows"] == [["12.34"]]

    def test_rows_from_json_roundtrip(self):
        """Backs the remote render path — local rich/csv renderers need tuples
        from the deserialised envelope, same as the original input."""
        buf = io.StringIO()
        original_rows = [(1, "а"), (2, None)]
        sql_tool.render_json(["id", "label"], original_rows, stream=buf)
        payload = _json.loads(buf.getvalue())

        columns, rows = sql_tool.rows_from_json(payload)

        assert columns == ["id", "label"]
        assert rows == original_rows
        assert all(isinstance(r, tuple) for r in rows)


@allure.epic("Infrastructure")
@allure.feature("CLI tools")
class TestReadOnlySafety:
    def test_select_returns_rows(self, seeded_db):
        """End-to-end happy path: ``_execute`` opens the seeded DB and
        returns the expected columns + row count. The tuple shape is
        what ``render_*`` consume downstream.
        """
        columns, rows = sql_tool._execute("SELECT id, label FROM sample ORDER BY id")

        assert columns == ["id", "label"]
        assert rows == [(1, "еда"), (2, None), (3, "транспорт")]

    def test_writes_are_blocked_by_default(self, seeded_db):
        """A typo'd UPDATE can't clobber the ledger — the read-only URI makes
        SQLite itself raise, so the runner needs no extra guard."""
        with pytest.raises(sqlite3.OperationalError):
            sql_tool._execute("UPDATE sample SET label = 'x' WHERE id = 1")

        # And the row is actually untouched, belt and suspenders.
        _, rows = sql_tool._execute("SELECT label FROM sample WHERE id = 1")
        assert rows == [("еда",)]

    def test_write_flag_permits_mutation(self, seeded_db):
        """The mutation must persist across a follow-up read-only open."""
        sql_tool._execute("UPDATE sample SET label = 'X' WHERE id = 1", write=True)
        _, rows = sql_tool._execute("SELECT label FROM sample WHERE id = 1")
        assert rows == [("X",)]


@allure.epic("Infrastructure")
@allure.feature("CLI tools")
class TestWriteFlag:
    def test_main_without_write_refuses_update(self, capsys, seeded_db):
        """End-to-end CLI: running ``--query "UPDATE ..."`` without
        ``--write`` must bubble up the SQLite read-only error rather
        than silently succeed.
        """
        with pytest.raises(sqlite3.OperationalError):
            sql_tool.main(["--query", "UPDATE sample SET label='x' WHERE id=1"])

    def test_main_with_write_applies_update(self, capsys, seeded_db):
        """End-to-end CLI: ``--write`` lets the same UPDATE land.
        The follow-up SELECT (without ``--write``) still works and
        sees the new value, proving the write persisted to disk.
        """
        rc = sql_tool.main(
            [
                "--query",
                "UPDATE sample SET label='xxx' WHERE id=1",
                "--write",
            ],
        )
        assert rc == 0

        rc = sql_tool.main(
            ["--query", "SELECT label FROM sample WHERE id=1", "--csv"],
        )
        assert rc == 0
        assert "xxx" in capsys.readouterr().out


@allure.epic("Infrastructure")
@allure.feature("CLI tools")
class TestArgparse:
    def test_requires_query_or_file(self, capsys):
        """``inv sql`` with neither ``--query`` nor ``--file`` must
        exit 2 — argparse's "required" error — instead of silently
        running an empty statement that would crash SQLite later.
        """
        with pytest.raises(SystemExit):
            sql_tool.main([])
        assert "one of" in capsys.readouterr().err

    def test_csv_and_json_mutex(self, capsys, seeded_db):
        """``--csv`` and ``--json`` are two terminal formats — asking
        for both at once is meaningless, argparse rejects.
        """
        with pytest.raises(SystemExit):
            sql_tool.main(["--query", "SELECT 1", "--csv", "--json"])
        assert "not allowed with" in capsys.readouterr().err

    def test_query_csv_roundtrip(self, capsys, seeded_db):
        """End-to-end CLI: ``main`` runs the query and prints CSV to
        stdout. Verify both the header and a cell with a non-ASCII
        label come back intact.
        """
        rc = sql_tool.main(
            ["--query", "SELECT id, label FROM sample ORDER BY id LIMIT 1", "--csv"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert out.splitlines()[0] == "id,label"
        assert out.splitlines()[1] == "1,еда"
