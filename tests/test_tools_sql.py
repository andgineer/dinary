"""Unit tests for ``dinary.tools.sql`` — the ``inv sql`` runner.

Covers the three render shapes (rich / csv / json), the JSON
round-trip that backs ``inv sql --remote``, and the safety contract
(read-only connection refuses writes).
"""

import io
import json as _json
from decimal import Decimal

import allure
import duckdb
import pytest

from dinary.config import settings
from dinary.tools import sql as sql_tool


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Point the runner at a tmp DB with a tiny expenses-like table.

    Avoids bringing up the real schema: the SQL tool is schema-agnostic
    (it just executes whatever SQL the operator types), so a 3-column
    synthetic table is enough to exercise the column / row / value
    coercion paths without coupling these tests to every migration.
    Only ``settings.data_path`` needs monkeypatching because
    ``sql._execute`` reads that directly rather than going through
    ``duckdb_repo.DB_PATH`` (the stored-SQL runner is not relevant to
    this ad-hoc tool).
    """
    db_path = tmp_path / "dinary.duckdb"
    con = duckdb.connect(str(db_path))
    try:
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
    finally:
        con.close()

    monkeypatch.setattr(settings, "data_path", str(db_path))
    return db_path


@allure.epic("CLI tools")
@allure.feature("inv sql — rich renderer")
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


@allure.epic("CLI tools")
@allure.feature("inv sql — csv renderer")
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


@allure.epic("CLI tools")
@allure.feature("inv sql — json envelope")
class TestRenderJson:
    def test_envelope_shape(self):
        """The JSON envelope is the SSH wire format for ``inv sql
        --remote`` — its shape (``columns`` / ``rows`` / ``row_count``)
        is a contract, not an implementation detail. Verify ASCII
        non-escape so Cyrillic survives ``jq`` without ``--raw-output``.
        """
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
        """``render_json`` -> ``rows_from_json`` must produce the same
        columns and tuple-typed rows the caller fed in. This backs the
        remote render path: the local rich/csv renderers take tuples
        and must get equivalent input from the deserialised envelope.
        """
        buf = io.StringIO()
        original_rows = [(1, "а"), (2, None)]
        sql_tool.render_json(["id", "label"], original_rows, stream=buf)
        payload = _json.loads(buf.getvalue())

        columns, rows = sql_tool.rows_from_json(payload)

        assert columns == ["id", "label"]
        assert rows == original_rows
        assert all(isinstance(r, tuple) for r in rows)


@allure.epic("CLI tools")
@allure.feature("inv sql — read-only contract")
class TestReadOnlySafety:
    def test_select_returns_rows(self, seeded_db):
        """End-to-end happy path: ``_execute`` opens the seeded DB and
        returns the expected columns + row count. The tuple shape is
        what ``render_*`` consume downstream.
        """
        columns, rows = sql_tool._execute("SELECT id, label FROM sample ORDER BY id")

        assert columns == ["id", "label"]
        assert rows == [(1, "еда"), (2, None), (3, "транспорт")]

    def test_writes_are_blocked(self, seeded_db):
        """The whole point of the tool: a typo'd ``UPDATE`` can't
        clobber the ledger because the connection is opened
        ``read_only=True``. DuckDB surfaces this as an error from the
        engine — the runner does not need any extra guard of its own.
        """
        with pytest.raises(duckdb.Error):
            sql_tool._execute("UPDATE sample SET label = 'x' WHERE id = 1")

        # And the row is actually untouched, belt and suspenders.
        _, rows = sql_tool._execute("SELECT label FROM sample WHERE id = 1")
        assert rows == [("еда",)]


@allure.epic("CLI tools")
@allure.feature("inv sql — argparse")
class TestArgparse:
    def test_requires_query_or_file(self, capsys):
        """``inv sql`` with neither ``--query`` nor ``--file`` must
        exit 2 — argparse's "required" error — instead of silently
        running an empty statement that would crash DuckDB later.
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
