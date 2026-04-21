"""Tests for the DuckDB repository layer after the single-file refactor.

All tests point ``duckdb_repo.DATA_DIR`` / ``DB_PATH`` at a per-test
``tmp_path`` and run ``init_db()`` up-front so the process-wide
singleton connection operates on a clean ``dinary.duckdb`` file.
"""

from datetime import datetime, timedelta

import allure
import duckdb
import pytest

from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


@pytest.fixture
def fresh_db():
    duckdb_repo.init_db()


@pytest.fixture
def populated_catalog(fresh_db):
    """Seed the catalog with a minimal 3D dataset."""
    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'кафе', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to,"
            " auto_attach_enabled, is_active)"
            " VALUES (10, 'отпуск-2026', '2026-01-01', '2026-12-31', TRUE, TRUE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (2, 'релокация', TRUE)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping_tags (mapping_id, tag_id) VALUES (1, 1)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'кафе', 'путешествия', 2, 10)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 2026, 'еда', 'собака', 2, 10)",
        )
    finally:
        con.close()


@allure.epic("DuckDB")
@allure.feature("Connection lifecycle")
class TestConnectionLifecycle:
    def test_init_creates_file(self, tmp_path):
        assert not duckdb_repo.DB_PATH.exists()
        duckdb_repo.init_db()
        assert duckdb_repo.DB_PATH.exists()

    def test_get_connection_before_init_autocreates(self, tmp_path):
        """``get_connection`` opens the file even without explicit init."""
        con = duckdb_repo.get_connection()
        try:
            row = con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        assert row == (1,)

    def test_close_releases_singleton(self, fresh_db):
        duckdb_repo.close_connection()
        # After close, a subsequent get_connection re-opens.
        con = duckdb_repo.get_connection()
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()

    def test_multiple_cursors_share_engine(self, fresh_db):
        """``get_connection`` hands back cursors on one shared engine."""
        c1 = duckdb_repo.get_connection()
        c2 = duckdb_repo.get_connection()
        try:
            c1.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (42, 'g42', 99)",
            )
            # The second cursor sees the first cursor's committed write.
            row = c2.execute(
                "SELECT name FROM category_groups WHERE id = 42",
            ).fetchone()
        finally:
            c1.close()
            c2.close()
        assert row == ("g42",)


@allure.epic("DuckDB")
@allure.feature("Catalog version")
class TestCatalogVersion:
    def test_initial_version_is_one(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.get_catalog_version(con) == 1
        finally:
            con.close()

    def test_set_then_get(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo._set_catalog_version(con, 42)  # noqa: SLF001
            assert duckdb_repo.get_catalog_version(con) == 42
        finally:
            con.close()

    def test_missing_key_raises(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute("DELETE FROM app_metadata WHERE key = 'catalog_version'")
            with pytest.raises(RuntimeError, match="catalog_version"):
                duckdb_repo.get_catalog_version(con)
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("List categories (is_active)")
class TestListCategories:
    def test_filters_inactive_rows(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'Food', 1, TRUE), (2, 'Gone', 2, FALSE)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'active', 1, TRUE),"
                " (2, 'retired', 1, FALSE),"
                " (3, 'orphan-group', 2, TRUE)",
            )
            rows = duckdb_repo.list_categories(con)
        finally:
            con.close()
        names = {r.name for r in rows}
        # 'retired' filtered out (is_active=false on category); 'orphan-group'
        # filtered out (group is_active=false).
        assert names == {"active"}

    def test_ordering(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'Z', 2, TRUE), (2, 'A', 1, TRUE)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'b', 1, TRUE),"
                " (2, 'a', 1, TRUE),"
                " (3, 'c', 2, TRUE)",
            )
            rows = duckdb_repo.list_categories(con)
        finally:
            con.close()
        # Groups ordered by sort_order: 'A' (1) before 'Z' (2); within each
        # group, categories ordered by name ascending.
        assert [(r.name, r.group_name) for r in rows] == [
            ("c", "A"),
            ("a", "Z"),
            ("b", "Z"),
        ]


@allure.epic("DuckDB")
@allure.feature("Sheet mapping (3D)")
class TestSheetMapping:
    def test_resolve_year_zero_default(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            row = duckdb_repo.resolve_mapping(con, "еда", "собака")
            assert row is not None
            assert row.category_id == 1
            assert row.event_id is None
        finally:
            con.close()

    def test_year_specific_overrides_default(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            row = duckdb_repo.resolve_mapping_for_year(con, "еда", "собака", 2026)
            assert row is not None
            assert row.category_id == 2
            assert row.event_id == 10
        finally:
            con.close()

    def test_year_falls_back_to_zero(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            row = duckdb_repo.resolve_mapping_for_year(con, "еда", "собака", 2024)
            assert row is not None
            assert row.category_id == 1
        finally:
            con.close()

    def test_unknown_returns_none(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.resolve_mapping(con, "missing", "?") is None
        finally:
            con.close()

    def test_get_mapping_tag_ids(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.get_mapping_tag_ids(con, 1) == [1]
            assert duckdb_repo.get_mapping_tag_ids(con, 2) == []
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Logging projection (sheet_mapping)")
class TestLoggingProjection:
    @pytest.fixture
    def logging_setup(self, fresh_db):
        """Seed one category with three ``sheet_mapping`` rows exercising
        the "first non-``*`` wins per column" resolver:

        row_order=1: category=еда, tag ``tag1`` required, Расходы=``*``,
                     Конверт=``WithTag``
        row_order=2: category=еда, event=``evt``, Расходы=``*``,
                     Конверт=``WithEvt``
        row_order=3: category=еда (catch-all), Расходы=``CatA``,
                     Конверт=``*``
        """
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute("INSERT INTO tags (id, name) VALUES (1, 'tag1')")
            con.execute("INSERT INTO tags (id, name) VALUES (2, 'tag2')")
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
                " VALUES (1, 'evt', '2026-01-01', '2026-12-31', TRUE)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (1, 1, NULL, '*', 'WithTag')",
            )
            con.execute("INSERT INTO sheet_mapping_tags VALUES (1, 1)")
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (2, 1, 1, '*', 'WithEvt')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (3, 1, NULL, 'CatA', '*')",
            )
        finally:
            con.close()

    def test_event_row_wins_конверт(self, logging_setup):
        con = duckdb_repo.get_connection()
        try:
            # No tags: row_order=1 skipped; row_order=2 matches on event_id
            # and fills Конверт=WithEvt; row_order=3 supplies Расходы=CatA.
            result = duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            )
            assert result == ("CatA", "WithEvt")
        finally:
            con.close()

    def test_tag_row_wins_конверт(self, logging_setup):
        con = duckdb_repo.get_connection()
        try:
            # No event, tag 'tag1' present: row_order=1 fills Конверт=WithTag;
            # row_order=3 supplies Расходы=CatA.
            result = duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[1],
            )
            assert result == ("CatA", "WithTag")
        finally:
            con.close()

    def test_partial_resolution_keeps_resolved_column(self, logging_setup):
        con = duckdb_repo.get_connection()
        try:
            # tag 'tag2' is not required by any row, no event: rows 1 and 2
            # are skipped, row 3 fills Расходы=CatA but leaves Конверт as
            # ``*``. The resolver keeps the partial match and fills the
            # missing ``sheet_group`` with the empty-string fallback —
            # dropping CatA would be strictly worse since we already
            # picked a better-than-default value for that column.
            result = duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[2],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_unknown_category_returns_none(self, logging_setup):
        con = duckdb_repo.get_connection()
        try:
            # category_id=999 has no rows at all and no canonical name —
            # the projection returns None so the drain worker can poison
            # the queued row rather than logging to a bogus target.
            result = duckdb_repo.logging_projection(
                con,
                category_id=999,
                event_id=None,
                tag_ids=[],
            )
            assert result is None
        finally:
            con.close()

    def test_no_event_no_tags_fills_envelope_with_empty_string(self, logging_setup):
        con = duckdb_repo.get_connection()
        try:
            # Same shape as ``test_partial_resolution_keeps_resolved_column``
            # but with no tags at all; rows 1 and 2 require tag/event and
            # are skipped, row 3 assigns Расходы and leaves Конверт as
            # ``*``. The resolver fills Конверт with the empty-string
            # fallback while keeping the explicit ``CatA`` mapping.
            result = duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_no_mapping_rule_falls_back_to_category_name(self, fresh_db):
        """When no sheet_mapping rule fires at all, both columns fall
        back: ``sheet_category`` = categories.name, ``sheet_group`` = ''.
        This replaces the old "return None and let the caller fall
        back" contract with an in-helper default so partial matches
        never get discarded.
        """
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            assert duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("еда", "")
        finally:
            con.close()

    def test_both_columns_resolved_when_wildcard_row_fills_конверт(self, fresh_db):
        """A dedicated envelope-fill row + a category row together resolve
        both columns and produce a non-None result."""
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (1, NULL, NULL, '*', 'envelope')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (2, 1, NULL, 'CatA', '*')",
            )
            assert duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("CatA", "envelope")
        finally:
            con.close()

    def test_event_wildcard_row_matches_specific_event(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to,"
                " auto_attach_enabled) VALUES (1, 'отпуск-2026',"
                " '2026-01-01', '2026-04-20', TRUE)",
            )
            # Row 1 (specific event) must win over the wildcard row for
            # event_id=1; row 2 must still fire for event_id=None.
            # ``sheet_group`` is left wildcard and therefore falls back
            # to the empty-string sentinel in both cases.
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (1, 1, 1, 'Trips', '*')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (2, 1, NULL, 'Default', '*')",
            )
            assert duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            ) == ("Trips", "")
            assert duckdb_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("Default", "")
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("get_category_name")
class TestGetCategoryName:
    def test_existing(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            assert duckdb_repo.get_category_name(con, 1) == "еда"
        finally:
            con.close()

    def test_missing(self, fresh_db):
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.get_category_name(con, 999) is None
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Insert expense (client_expense_id)")
class TestInsertExpense:
    def test_insert_then_duplicate(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            r1 = duckdb_repo.insert_expense(
                con,
                client_expense_id="x1",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="hi",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            r2 = duckdb_repo.insert_expense(
                con,
                client_expense_id="x1",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="hi",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            assert r1 == "created"
            assert r2 == "duplicate"
        finally:
            con.close()

    def test_conflict_on_changed_amount(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
                con,
                client_expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=False,
            )
            r = duckdb_repo.insert_expense(
                con,
                client_expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=99.0,
                amount_original=99.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=False,
            )
            assert r == "conflict"
        finally:
            con.close()

    def test_invalid_category_raises(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            with pytest.raises(ValueError, match="category_id"):
                duckdb_repo.insert_expense(
                    con,
                    client_expense_id="x3",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=9999,
                    comment="",
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_invalid_provenance_pair_raises(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            with pytest.raises(ValueError, match="sheet_category"):
                duckdb_repo.insert_expense(
                    con,
                    client_expense_id="x4",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=1,
                    comment="",
                    sheet_category="X",
                    sheet_group=None,
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_enqueue_logging_creates_pending_row(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
                con,
                client_expense_id="x5",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=True,
            )
            jobs = duckdb_repo.list_logging_jobs(con)
        finally:
            con.close()
        # Integer expense PKs in the queue.
        assert len(jobs) == 1
        assert isinstance(jobs[0], int)

    def test_null_client_id_allows_multiple(self, populated_catalog):
        """Bootstrap rows use ``client_expense_id=None`` and must coexist."""
        con = duckdb_repo.get_connection()
        try:
            r1 = duckdb_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2026, 1, 1, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="legacy-1",
                sheet_category="cat",
                sheet_group="grp",
                tag_ids=[],
                enqueue_logging=False,
            )
            r2 = duckdb_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2026, 1, 2, 12),
                amount=2.0,
                amount_original=2.0,
                currency_original="EUR",
                category_id=1,
                comment="legacy-2",
                sheet_category="cat",
                sheet_group="grp",
                tag_ids=[],
                enqueue_logging=False,
            )
            assert r1 == "created"
            assert r2 == "created"
            # Both rows end up in the table.
            count = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            assert count == 2
        finally:
            con.close()

    def test_unique_client_id_raises_constraint(self, populated_catalog):
        """Direct SQL violation — the wrapper catches and returns
        duplicate/conflict, but the DB enforces UNIQUE regardless."""
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ["u1", datetime(2026, 1, 1, 12), 1, 1, "EUR", 1],
            )
            with pytest.raises(duckdb.ConstraintException):
                con.execute(
                    "INSERT INTO expenses (client_expense_id, datetime, amount,"
                    " amount_original, currency_original, category_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ["u1", datetime(2026, 1, 1, 12), 1, 1, "EUR", 1],
                )
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Race-recovery exception classification")
class TestIsUniqueViolationOfClientExpenseId:
    """Pin the classifier behind ``insert_expense``'s race recovery.

    ``_is_unique_violation_of_client_expense_id`` is the *only* gate
    between "fall through to the compare path" (serve
    duplicate/conflict) and "propagate as 500" (unknown DB failure)
    for the concurrent-POST paths. It decides by pattern-matching
    DuckDB's English error messages, so a quiet diagnostic-text change
    in a future DuckDB release could silently flip every unique race
    into a 500. These tests freeze the real DuckDB error strings from
    the version this refactor targeted so the classifier's contract
    stays observable in CI.

    Paired with
    ``TestInsertExpense::test_unique_client_id_raises_constraint``
    (which pins that the *exception classes* DuckDB raises stay the
    set we catch), this covers the full input surface of the recovery
    path without needing to fabricate synthetic exceptions.
    """

    def _real_duplicate_key_exception(self) -> duckdb.Error:
        """Provoke a real ``ConstraintException`` from DuckDB so we
        freeze its actual wording, not a hand-crafted message we
        invented. Uses a throwaway in-memory DB so it doesn't touch
        the per-test tmp DB at all.
        """
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE t (k VARCHAR UNIQUE, v INTEGER)",
            )
            con.execute("INSERT INTO t VALUES ('k1', 1)")
            try:
                con.execute("INSERT INTO t VALUES ('k1', 2)")
            except duckdb.Error as exc:
                return exc
            msg = "DuckDB unexpectedly accepted a duplicate key"
            raise AssertionError(msg)
        finally:
            con.close()

    def _real_fk_violation_exception(self) -> duckdb.Error:
        """Same idea for the FK path — catch the real DuckDB wording
        so we're classifier-against-truth, not
        classifier-against-stub. The classifier's ``"foreign"``
        keyword carve-out is the discriminator: today's FK messages
        carry the word "foreign" and none of the duplicate-key
        positive keywords, but the carve-out is kept as a forward-
        compat hedge against future DuckDB releases that might
        reword FK diagnostics to mention "primary key" or "unique".
        """
        con = duckdb.connect(":memory:")
        try:
            con.execute("CREATE TABLE p (id INTEGER PRIMARY KEY)")
            con.execute(
                "CREATE TABLE c (pid INTEGER REFERENCES p(id))",
            )
            try:
                con.execute("INSERT INTO c VALUES (42)")
            except duckdb.Error as exc:
                return exc
            msg = "DuckDB unexpectedly accepted an FK violation"
            raise AssertionError(msg)
        finally:
            con.close()

    def test_classifies_real_duplicate_key_as_race(self):
        exc = self._real_duplicate_key_exception()
        assert duckdb_repo._is_unique_violation_of_client_expense_id(exc), (
            f"DuckDB dup-key message no longer matches classifier keywords: {exc!s}"
        )

    def test_classifies_real_fk_violation_as_not_a_race(self):
        exc = self._real_fk_violation_exception()
        assert not duckdb_repo._is_unique_violation_of_client_expense_id(exc), (
            f"FK violation was misclassified as a client_expense_id "
            f"UNIQUE race; would be swallowed as a duplicate/conflict "
            f"response instead of surfacing as 500. Message: {exc!s}"
        )

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            # Legacy wording variants seen across DuckDB versions.
            ("Duplicate key 'k1: abc' violates unique constraint", True),
            ("violates primary key constraint", True),
            ("Constraint Error: unique constraint", True),
            # FK variants must not match even when they contain
            # "primary key" (which references the parent table).
            (
                "Violates foreign key constraint because key"
                " does not exist in the referenced table primary key",
                False,
            ),
            # Unrelated failures must pass through untouched.
            ("IO Error: disk full", False),
            ("Out of Memory Error: heap exhausted", False),
        ],
    )
    def test_fixed_message_matrix(self, message: str, expected: bool):
        """Explicit matrix pinning the classifier against a mix of
        historical duplicate-key / FK / unrelated-failure wordings
        across DuckDB versions. Any keyword rearrangement in the
        classifier (``"unique"`` vs ``"unique constraint"``, dropping
        the ``"foreign"`` carve-out, etc.) flips at least one row
        here.
        """
        fake = RuntimeError(message)
        assert duckdb_repo._is_unique_violation_of_client_expense_id(fake) is expected, message


@allure.epic("DuckDB")
@allure.feature("lookup_existing_expense")
class TestLookupExistingExpense:
    def test_found(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
                con,
                client_expense_id="L1",
                expense_datetime=datetime(2026, 4, 15, 12),
                amount=42.0,
                amount_original=42.0,
                currency_original="EUR",
                category_id=1,
                comment="lunch",
                tag_ids=[],
                enqueue_logging=False,
            )
        finally:
            con.close()

        row = duckdb_repo.lookup_existing_expense("L1")
        assert row is not None
        assert row.currency_original == "EUR"
        assert row.category_id == 1
        assert row.comment == "lunch"

    def test_not_found(self, populated_catalog):
        assert duckdb_repo.lookup_existing_expense("missing") is None


@allure.epic("DuckDB")
@allure.feature("sheet_logging_jobs queue")
class TestLoggingQueue:
    def _insert_one_expense(self) -> int:
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
                con,
                client_expense_id="job1",
                expense_datetime=datetime(2026, 1, 1, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=True,
            )
            pk_row = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'job1'",
            ).fetchone()
        finally:
            con.close()
        return int(pk_row[0])

    def test_claim_then_clear(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            token = duckdb_repo.claim_logging_job(con, pk)
            assert token is not None
            assert duckdb_repo.clear_logging_job(con, pk, token) is True
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    def test_clear_with_wrong_token_returns_false(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.claim_logging_job(con, pk)
            assert duckdb_repo.clear_logging_job(con, pk, "wrongtoken") is False
        finally:
            con.close()

    def test_release_returns_to_pending(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            token = duckdb_repo.claim_logging_job(con, pk)
            assert duckdb_repo.release_logging_claim(con, pk, token) is True
            row = con.execute(
                "SELECT status, claim_token FROM sheet_logging_jobs WHERE expense_id = ?",
                [pk],
            ).fetchone()
            assert row[0] == "pending"
            assert row[1] is None
        finally:
            con.close()

    def test_double_claim_blocked(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            t1 = duckdb_repo.claim_logging_job(con, pk)
            t2 = duckdb_repo.claim_logging_job(con, pk)
            assert t1 is not None
            assert t2 is None
        finally:
            con.close()

    def test_stale_claim_recoverable(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            now = datetime(2026, 1, 1, 12)
            t1 = duckdb_repo.claim_logging_job(con, pk, now=now)
            assert t1 is not None
            future = now + timedelta(hours=1)
            t2 = duckdb_repo.claim_logging_job(
                con,
                pk,
                now=future,
                stale_before=future - timedelta(minutes=5),
            )
            assert t2 is not None
            assert t2 != t1
        finally:
            con.close()

    def test_list_filters_fresh_in_progress_but_resurfaces_stale(
        self,
        populated_catalog,
    ):
        """``list_logging_jobs`` must:
        - include ``pending`` rows
        - exclude ``in_progress`` rows whose claim is newer than
          ``stale_before`` (a recently-claimed row belongs to the
          drain that claimed it; skipping avoids burning a
          BEGIN/COMMIT on every iteration for the same row)
        - include ``in_progress`` rows whose claim is older than
          ``stale_before`` (that drain died; the row must surface
          again so the next drain can recover it)
        """
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            now = datetime(2026, 1, 1, 12)
            token = duckdb_repo.claim_logging_job(con, pk, now=now)
            assert token is not None

            # Fresh claim (1 minute old) with a 5-minute cutoff → filtered out.
            fresh_now = now + timedelta(minutes=1)
            fresh_cutoff = fresh_now - timedelta(minutes=5)
            assert (
                duckdb_repo.list_logging_jobs(
                    con,
                    now=fresh_now,
                    stale_before=fresh_cutoff,
                )
                == []
            )

            # Stale claim (10 minutes old) with a 5-minute cutoff → resurfaces.
            stale_now = now + timedelta(minutes=10)
            stale_cutoff = stale_now - timedelta(minutes=5)
            assert duckdb_repo.list_logging_jobs(
                con,
                now=stale_now,
                stale_before=stale_cutoff,
            ) == [pk]
        finally:
            con.close()

    def test_poison_marks_row(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.poison_logging_job(con, pk, "boom")
            row = con.execute(
                "SELECT status, last_error FROM sheet_logging_jobs WHERE expense_id = ?",
                [pk],
            ).fetchone()
            # Poisoned rows are excluded from list_logging_jobs().
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()
        assert row == ("poisoned", "boom")

    def test_force_clear_wipes_row(self, populated_catalog):
        pk = self._insert_one_expense()
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.force_clear_logging_job(con, pk) is True
            assert duckdb_repo.count_logging_jobs(con) == 0
            # Already gone — idempotent false on re-delete.
            assert duckdb_repo.force_clear_logging_job(con, pk) is False
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("get_expense_by_id")
class TestGetExpenseById:
    def test_roundtrip(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
                con,
                client_expense_id="E1",
                expense_datetime=datetime(2026, 3, 3, 12),
                amount=1.5,
                amount_original=1.5,
                currency_original="EUR",
                category_id=1,
                comment="c",
                tag_ids=[],
                enqueue_logging=False,
            )
            pk = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'E1'",
            ).fetchone()[0]
            row = duckdb_repo.get_expense_by_id(con, int(pk))
        finally:
            con.close()
        assert row is not None
        assert row.category_id == 1
        assert row.currency_original == "EUR"
        assert row.comment == "c"

    def test_missing_returns_none(self, populated_catalog):
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.get_expense_by_id(con, 99999) is None
        finally:
            con.close()
