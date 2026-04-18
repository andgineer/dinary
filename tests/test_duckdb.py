"""Tests for the DuckDB repository layer (3D schema)."""

from datetime import datetime, timedelta

import allure
import pytest

from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


@pytest.fixture
def config_db():
    duckdb_repo.init_config_db()


@pytest.fixture
def populated_config(config_db):
    """Seed config.duckdb with a minimal 3D dataset."""
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, 'Food', 1)")
        con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
        con.execute("INSERT INTO categories VALUES (2, 'кафе', 1)")
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (10, 'отпуск-2026', '2026-01-01', '2026-12-31', true)",
        )
        con.execute("INSERT INTO tags VALUES (1, 'собака')")
        con.execute("INSERT INTO tags VALUES (2, 'релокация')")
        con.execute(
            "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute(
            "INSERT INTO sheet_mapping_tags (mapping_id, tag_id) VALUES (1, 1)",
        )
        con.execute(
            "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'кафе', 'путешествия', 2, 10)",
        )
        con.execute(
            "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 2026, 'еда', 'собака', 2, 10)",
        )
    finally:
        con.close()


@allure.epic("DuckDB")
@allure.feature("Catalog version")
class TestCatalogVersion:
    def test_initial_version_is_one(self, config_db):
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            assert duckdb_repo.get_catalog_version(con) == 1
        finally:
            con.close()

    def test_set_then_get(self, config_db):
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            duckdb_repo._set_catalog_version(con, 42)
            assert duckdb_repo.get_catalog_version(con) == 42
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Sheet mapping (3D)")
class TestSheetMapping:
    def test_resolve_year_zero_default(self, populated_config):
        con = duckdb_repo.get_budget_connection(2025)
        try:
            row = duckdb_repo.resolve_mapping(con, "еда", "собака")
            assert row is not None
            assert row.category_id == 1
            assert row.event_id is None
        finally:
            con.close()

    def test_year_specific_overrides_default(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            row = duckdb_repo.resolve_mapping_for_year(con, "еда", "собака", 2026)
            assert row is not None
            assert row.category_id == 2
            assert row.event_id == 10
        finally:
            con.close()

    def test_year_falls_back_to_zero(self, populated_config):
        con = duckdb_repo.get_budget_connection(2024)
        try:
            row = duckdb_repo.resolve_mapping_for_year(con, "еда", "собака", 2024)
            assert row is not None
            assert row.category_id == 1
        finally:
            con.close()

    def test_unknown_returns_none(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.resolve_mapping(con, "missing", "?") is None
        finally:
            con.close()

    def test_get_mapping_tag_ids(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.get_mapping_tag_ids(con, 1) == [1]
            assert duckdb_repo.get_mapping_tag_ids(con, 2) == []
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Forward projection")
class TestForwardProjection:
    @pytest.fixture
    def projection_setup(self, config_db):
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            con.execute("INSERT INTO category_groups VALUES (1, 'g', 1)")
            con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
            con.execute("INSERT INTO tags VALUES (1, 'tag1')")
            con.execute("INSERT INTO tags VALUES (2, 'tag2')")
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
                " VALUES (1, 'evt', '2026-01-01', '2026-12-31', true)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
                " category_id, event_id) VALUES (1, 2026, 'CatA', '', 1, NULL)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
                " category_id, event_id) VALUES (2, 2026, 'CatA', 'WithEvt', 1, 1)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
                " category_id, event_id) VALUES (3, 2026, 'CatA', 'WithTag', 1, NULL)",
            )
            con.execute("INSERT INTO sheet_mapping_tags VALUES (3, 1)")
        finally:
            con.close()

    def test_exact_match_with_event(self, projection_setup):
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            target = duckdb_repo.forward_projection(
                con,
                latest_sheet_year=2026,
                category_id=1,
                event_id=1,
                tag_ids=[],
            )
            assert target == ("CatA", "WithEvt")
        finally:
            con.close()

    def test_exact_match_with_tags(self, projection_setup):
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            target = duckdb_repo.forward_projection(
                con,
                latest_sheet_year=2026,
                category_id=1,
                event_id=None,
                tag_ids=[1],
            )
            assert target == ("CatA", "WithTag")
        finally:
            con.close()

    def test_category_only_fallback(self, projection_setup):
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            target = duckdb_repo.forward_projection(
                con,
                latest_sheet_year=2026,
                category_id=1,
                event_id=None,
                tag_ids=[2],
            )
            # First-by-id fallback wins: row id=1.
            assert target == ("CatA", "")
        finally:
            con.close()

    def test_unknown_category_returns_none(self, projection_setup):
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            target = duckdb_repo.forward_projection(
                con,
                latest_sheet_year=2026,
                category_id=999,
                event_id=None,
                tag_ids=[],
            )
            assert target is None
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("expense_id_registry")
class TestExpenseIdRegistry:
    def test_first_reservation_inserts_and_returns_year(self, config_db):
        stored, newly_inserted = duckdb_repo.reserve_expense_id_year("e1", 2026)
        assert stored == 2026
        assert newly_inserted is True
        assert duckdb_repo.get_registered_expense_year("e1") == 2026

    def test_second_reservation_returns_existing_year(self, config_db):
        duckdb_repo.reserve_expense_id_year("e1", 2026)
        stored, newly_inserted = duckdb_repo.reserve_expense_id_year("e1", 2027)
        assert stored == 2026
        assert newly_inserted is False

    def test_release_removes_row(self, config_db):
        duckdb_repo.reserve_expense_id_year("e1", 2026)
        duckdb_repo.release_expense_id_year("e1")
        assert duckdb_repo.get_registered_expense_year("e1") is None

    def test_pk_violation_recovers_into_lookup(self, config_db, monkeypatch):
        """Concurrency regression: if two callers race past the SELECT and
        both INSERT, the loser used to surface a 5xx ConstraintException.
        Now the loser catches the PK violation, re-reads, and returns the
        winner's `(stored_year, False)`.

        We simulate the race by:
          1. pre-inserting the row (so the INSERT will collide),
          2. wrapping the DuckDB connection in a proxy that returns an
             empty fetchone on the first SELECT (forcing reserve_expense_id_year
             onto the INSERT branch even though the row exists).
        """
        duckdb_repo.reserve_expense_id_year("e_race", 2026)

        real_get_config = duckdb_repo.get_config_connection

        class _NoRowResult:
            @staticmethod
            def fetchone():
                return None

        class _ConnProxy:
            """Forwards every attribute to the wrapped connection except
            the first SELECT against expense_id_registry, which it stubs
            to mimic 'row not yet visible to this transaction'."""

            def __init__(self, wrapped):
                self._wrapped = wrapped
                self._select_intercepted = False

            def execute(self, sql, *args, **kwargs):
                if not self._select_intercepted and "SELECT year FROM expense_id_registry" in sql:
                    self._select_intercepted = True
                    return _NoRowResult()
                return self._wrapped.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._wrapped, name)

        def stub_get_config(read_only=True):
            return _ConnProxy(real_get_config(read_only=read_only))

        # Patch only `get_config_connection` for the reserve call. Restore
        # via monkeypatch.setattr (idiomatic) and then explicitly undo just
        # this patch before the post-state assertion so the autouse
        # tmp-path patches set up by `_tmp_data_dir` survive.
        monkeypatch.setattr(duckdb_repo, "get_config_connection", stub_get_config)
        stored, newly_inserted = duckdb_repo.reserve_expense_id_year(
            "e_race",
            2027,
        )
        monkeypatch.setattr(duckdb_repo, "get_config_connection", real_get_config)

        assert stored == 2026
        assert newly_inserted is False
        # Original row must still be the one stored after the race recovery.
        assert duckdb_repo.get_registered_expense_year("e_race") == 2026


@allure.epic("DuckDB")
@allure.feature("Insert expense (3D)")
class TestInsertExpense:
    def _seed_basic(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        return con

    def test_insert_then_duplicate(self, populated_config):
        con = self._seed_basic(populated_config)
        try:
            r1 = duckdb_repo.insert_expense(
                con,
                expense_id="x1",
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
                enqueue_sync=False,
            )
            r2 = duckdb_repo.insert_expense(
                con,
                expense_id="x1",
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
                enqueue_sync=False,
            )
            assert r1 == "created"
            assert r2 == "duplicate"
        finally:
            con.close()

    def test_conflict_on_changed_amount(self, populated_config):
        con = self._seed_basic(populated_config)
        try:
            duckdb_repo.insert_expense(
                con,
                expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_sync=False,
            )
            r = duckdb_repo.insert_expense(
                con,
                expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=99.0,
                amount_original=99.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_sync=False,
            )
            assert r == "conflict"
        finally:
            con.close()

    def test_invalid_category_raises(self, populated_config):
        con = self._seed_basic(populated_config)
        try:
            with pytest.raises(ValueError, match="category_id"):
                duckdb_repo.insert_expense(
                    con,
                    expense_id="x3",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=9999,
                    event_id=None,
                    comment="",
                    sheet_category=None,
                    sheet_group=None,
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_invalid_provenance_pair_raises(self, populated_config):
        con = self._seed_basic(populated_config)
        try:
            with pytest.raises(ValueError, match="sheet_category"):
                duckdb_repo.insert_expense(
                    con,
                    expense_id="x4",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=1,
                    event_id=None,
                    comment="",
                    sheet_category="X",
                    sheet_group=None,
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_enqueue_sync_creates_pending_row(self, populated_config):
        con = self._seed_basic(populated_config)
        try:
            duckdb_repo.insert_expense(
                con,
                expense_id="x5",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_sync=True,
            )
            assert duckdb_repo.list_sync_jobs(con) == ["x5"]
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("sheet_sync_jobs queue")
class TestSyncQueue:
    def _setup_one_job(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        duckdb_repo.insert_expense(
            con,
            expense_id="job1",
            expense_datetime=datetime(2026, 1, 1, 12),
            amount=1.0,
            amount_original=1.0,
            currency_original="EUR",
            category_id=1,
            event_id=None,
            comment="",
            sheet_category=None,
            sheet_group=None,
            tag_ids=[],
            enqueue_sync=True,
        )
        return con

    def test_claim_then_clear(self, populated_config):
        con = self._setup_one_job(populated_config)
        try:
            token = duckdb_repo.claim_sync_job(con, "job1")
            assert token is not None
            assert duckdb_repo.clear_sync_job(con, "job1", token) is True
            assert duckdb_repo.list_sync_jobs(con) == []
        finally:
            con.close()

    def test_clear_with_wrong_token_returns_false(self, populated_config):
        con = self._setup_one_job(populated_config)
        try:
            duckdb_repo.claim_sync_job(con, "job1")
            assert duckdb_repo.clear_sync_job(con, "job1", "wrongtoken") is False
        finally:
            con.close()

    def test_release_returns_to_pending(self, populated_config):
        con = self._setup_one_job(populated_config)
        try:
            token = duckdb_repo.claim_sync_job(con, "job1")
            assert duckdb_repo.release_sync_claim(con, "job1", token) is True
            row = con.execute(
                "SELECT status, claim_token FROM sheet_sync_jobs WHERE expense_id = ?",
                ["job1"],
            ).fetchone()
            assert row[0] == "pending"
            assert row[1] is None
        finally:
            con.close()

    def test_double_claim_blocked(self, populated_config):
        con = self._setup_one_job(populated_config)
        try:
            t1 = duckdb_repo.claim_sync_job(con, "job1")
            t2 = duckdb_repo.claim_sync_job(con, "job1")
            assert t1 is not None
            assert t2 is None
        finally:
            con.close()

    def test_stale_claim_recoverable(self, populated_config):
        con = self._setup_one_job(populated_config)
        try:
            now = datetime(2026, 1, 1, 12)
            t1 = duckdb_repo.claim_sync_job(con, "job1", now=now)
            assert t1 is not None
            # Pretend the prior claim is older than the stale window.
            future = now + timedelta(hours=1)
            t2 = duckdb_repo.claim_sync_job(
                con,
                "job1",
                now=future,
                stale_before=future - timedelta(minutes=5),
            )
            assert t2 is not None
            assert t2 != t1
        finally:
            con.close()
