"""Tests for DuckDB repository layer."""

from datetime import date, datetime

import allure
import pytest

from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    """Point DATA_DIR to a temp directory for test isolation."""
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


@pytest.fixture
def config_db(tmp_path):
    duckdb_repo.init_config_db()


@pytest.fixture
def populated_config(config_db, tmp_path):
    """Seed config.duckdb with a minimal reference dataset."""
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, 'еда&бытовые', NULL)")
        con.execute("INSERT INTO category_groups VALUES (2, 'путешествия', NULL)")
        con.execute("INSERT INTO category_groups VALUES (3, '', NULL)")
        con.execute("INSERT INTO categories VALUES (1, 'еда&бытовые', 1)")
        con.execute("INSERT INTO categories VALUES (2, 'кафе', 2)")
        con.execute("INSERT INTO categories VALUES (3, 'топливо', 2)")
        con.execute("INSERT INTO categories VALUES (4, 'мобильник', 3)")
        con.execute("INSERT INTO family_members VALUES (1, 'собака')")
        con.execute("INSERT INTO tags VALUES (1, 'test-tag')")
        con.execute("INSERT INTO stores VALUES (1, 'Lidl', 'supermarket')")
        con.execute(
            """
            INSERT INTO sheet_category_mapping
            VALUES ('еда&бытовые', 'собака', 1, 1, NULL, NULL, NULL)
            """
        )
        con.execute(
            """
            INSERT INTO sheet_category_mapping
            VALUES ('кафе', 'путешествия', 2, NULL, NULL, NULL, NULL)
            """
        )
        con.execute(
            """
            INSERT INTO sheet_category_mapping
            VALUES ('топливо', 'путешествия', 3, NULL, NULL, NULL, NULL)
            """
        )
        con.execute(
            """
            INSERT INTO sheet_category_mapping
            VALUES ('мобильник', '', 4, NULL, NULL, NULL, NULL)
            """
        )
    finally:
        con.close()


@allure.epic("DuckDB")
@allure.feature("Bootstrap")
class TestBootstrap:
    def test_init_config_creates_all_tables(self, config_db):
        con = duckdb_repo.get_config_connection()
        try:
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            expected = {
                "category_groups", "categories", "family_members",
                "events", "event_members", "tags", "stores",
                "sheet_category_mapping",
            }
            assert expected.issubset(set(tables))
        finally:
            con.close()

    def test_budget_db_created_on_demand(self, config_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            assert "expenses" in tables
            assert "expense_tags" in tables
            assert "sheet_sync_jobs" in tables
        finally:
            con.close()

    def test_budget_db_has_config_attached(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            r = con.execute("SELECT COUNT(*) FROM config.categories").fetchone()
            assert r[0] == 4
        finally:
            con.close()

    def test_init_config_idempotent(self, config_db):
        duckdb_repo.init_config_db()
        con = duckdb_repo.get_config_connection()
        try:
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            assert "categories" in tables
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Mapping")
class TestMapping:
    def test_resolve_known_mapping(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.resolve_mapping(con, "еда&бытовые", "собака")
            assert result is not None
            assert result.category_id == 1
            assert result.beneficiary_id == 1
        finally:
            con.close()

    def test_resolve_unknown_mapping_returns_none(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.resolve_mapping(con, "unknown", "")
            assert result is None
        finally:
            con.close()

    def test_resolve_mapping_no_group(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.resolve_mapping(con, "мобильник", "")
            assert result is not None
            assert result.category_id == 4
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Travel Events")
class TestTravelEvents:
    def test_resolve_creates_synthetic_event(self, populated_config):
        event_id = duckdb_repo.resolve_travel_event(date(2026, 4, 14))
        assert event_id is not None
        assert event_id > 0

        cfg = duckdb_repo.get_config_connection()
        try:
            ev = cfg.execute(
                "SELECT name, date_from, date_to FROM events WHERE id = ?",
                [event_id],
            ).fetchone()
            assert ev[0] == "отпуск-2026"
            assert ev[1] == date(2026, 1, 1)
            assert ev[2] == date(2026, 12, 31)
        finally:
            cfg.close()

    def test_resolve_reuses_existing_event(self, populated_config):
        id1 = duckdb_repo.resolve_travel_event(date(2026, 4, 14))
        id2 = duckdb_repo.resolve_travel_event(date(2026, 7, 1))
        assert id1 == id2

    def test_different_years_create_different_events(self, populated_config):
        id_2026 = duckdb_repo.resolve_travel_event(date(2026, 4, 14))
        id_2025 = duckdb_repo.resolve_travel_event(date(2025, 6, 1))
        assert id_2026 != id_2025


@allure.epic("Data Safety")
@allure.feature("Deduplication")
class TestIdempotentInsert:
    def test_insert_creates_expense(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.insert_expense(
                con, "test-uuid-1",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, 1, None, None, [], "lunch",
            )
            assert result == "created"

            row = con.execute("SELECT amount FROM expenses WHERE id = 'test-uuid-1'").fetchone()
            assert float(row[0]) == 1500.0
        finally:
            con.close()

    def test_duplicate_returns_duplicate(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con, "test-uuid-2",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "lunch",
            )
            result = duckdb_repo.insert_expense(
                con, "test-uuid-2",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "lunch",
            )
            assert result == "duplicate"
        finally:
            con.close()

    def test_conflict_on_different_payload(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con, "test-uuid-3",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "lunch",
            )
            result = duckdb_repo.insert_expense(
                con, "test-uuid-3",
                datetime(2026, 4, 14, 12, 0),
                2000.0, "RSD", 1, None, None, None, [], "dinner",
            )
            assert result == "conflict"
        finally:
            con.close()

    def test_insert_creates_sync_job(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con, "test-uuid-4",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "",
            )
            jobs = duckdb_repo.get_dirty_sync_jobs(con)
            assert (2026, 4) in jobs
        finally:
            con.close()

    def test_insert_with_tags(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.insert_expense(
                con, "test-uuid-5",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [1], "lunch",
            )
            assert result == "created"

            tags = con.execute(
                "SELECT tag_id FROM expense_tags WHERE expense_id = 'test-uuid-5'"
            ).fetchall()
            assert len(tags) == 1
            assert tags[0][0] == 1
        finally:
            con.close()

    def test_duplicate_with_matching_tags(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con, "test-uuid-6",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [1], "lunch",
            )
            result = duckdb_repo.insert_expense(
                con, "test-uuid-6",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [1], "lunch",
            )
            assert result == "duplicate"
        finally:
            con.close()

    def test_conflict_on_different_tags(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con, "test-uuid-7",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "lunch",
            )
            result = duckdb_repo.insert_expense(
                con, "test-uuid-7",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [1], "lunch",
            )
            assert result == "conflict"
        finally:
            con.close()

    def test_amount_not_doubled_on_duplicate(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con, "test-uuid-8",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "",
            )
            duckdb_repo.insert_expense(
                con, "test-uuid-8",
                datetime(2026, 4, 14, 12, 0),
                1500.0, "RSD", 1, None, None, None, [], "",
            )
            total = con.execute("SELECT SUM(amount) FROM expenses").fetchone()
            assert float(total[0]) == 1500.0
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Reverse Mapping")
class TestReverseMapping:
    def test_reverse_lookup_simple(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.reverse_lookup_mapping(con, 1, 1, None, None, [])
            assert result == ("еда&бытовые", "собака")
        finally:
            con.close()

    def test_reverse_lookup_travel(self, populated_config):
        event_id = duckdb_repo.resolve_travel_event(date(2026, 4, 14))
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.reverse_lookup_mapping(con, 2, None, event_id, None, [])
            assert result is not None
            assert result[1] == "путешествия"
        finally:
            con.close()

    def test_reverse_lookup_no_group(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.reverse_lookup_mapping(con, 4, None, None, None, [])
            assert result == ("мобильник", "")
        finally:
            con.close()

    def test_reverse_lookup_unknown(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.reverse_lookup_mapping(con, 999, None, None, None, [])
            assert result is None
        finally:
            con.close()


@allure.epic("Data Safety")
@allure.feature("Referential Integrity")
class TestReferentialIntegrity:
    def test_insert_with_invalid_category_raises(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            with pytest.raises(ValueError, match="category_id 999"):
                duckdb_repo.insert_expense(
                    con, "ri-1",
                    datetime(2026, 4, 14, 12, 0),
                    100.0, "RSD", 999, None, None, None, [], "",
                )
        finally:
            con.close()

    def test_insert_with_invalid_beneficiary_raises(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            with pytest.raises(ValueError, match="beneficiary_id 999"):
                duckdb_repo.insert_expense(
                    con, "ri-2",
                    datetime(2026, 4, 14, 12, 0),
                    100.0, "RSD", 1, 999, None, None, [], "",
                )
        finally:
            con.close()

    def test_insert_with_invalid_tag_raises(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            with pytest.raises(ValueError, match="tag_id 999"):
                duckdb_repo.insert_expense(
                    con, "ri-3",
                    datetime(2026, 4, 14, 12, 0),
                    100.0, "RSD", 1, None, None, None, [999], "",
                )
        finally:
            con.close()

    def test_insert_with_invalid_event_raises(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            with pytest.raises(ValueError, match="event_id 999"):
                duckdb_repo.insert_expense(
                    con, "ri-4",
                    datetime(2026, 4, 14, 12, 0),
                    100.0, "RSD", 1, None, 999, None, [], "",
                )
        finally:
            con.close()

    def test_insert_with_invalid_store_raises(self, populated_config):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            with pytest.raises(ValueError, match="store_id 999"):
                duckdb_repo.insert_expense(
                    con, "ri-5",
                    datetime(2026, 4, 14, 12, 0),
                    100.0, "RSD", 1, None, None, 999, [], "",
                )
        finally:
            con.close()


@allure.epic("DuckDB")
@allure.feature("Year Boundary")
class TestYearBoundary:
    def test_expense_routed_to_correct_year_db(self, populated_config):
        con_2025 = duckdb_repo.get_budget_connection(2025)
        con_2026 = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.insert_expense(
                con_2025, "exp-2025",
                datetime(2025, 12, 31, 23, 59),
                1000.0, "RSD", 1, None, None, None, [], "",
            )
            duckdb_repo.insert_expense(
                con_2026, "exp-2026",
                datetime(2026, 1, 1, 0, 1),
                2000.0, "RSD", 1, None, None, None, [], "",
            )

            r_2025 = con_2025.execute("SELECT COUNT(*) FROM expenses").fetchone()
            r_2026 = con_2026.execute("SELECT COUNT(*) FROM expenses").fetchone()
            assert r_2025[0] == 1
            assert r_2026[0] == 1
        finally:
            con_2025.close()
            con_2026.close()
