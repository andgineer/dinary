"""Tests for the 2D→3D resolution report pipeline."""

import io

import allure
import pytest

from dinary.imports import report_2d_3d as report_module
from dinary.imports.expense_import import (
    ParsedSheetRow,
    resolve_row_to_3d,
)
from dinary.imports.report_2d_3d import (
    DETAIL_COLUMNS,
    SUMMARY_COLUMNS,
    CollectStats,
    DetailRow,
    SummaryRow,
    build_summary,
    collect_detail_rows,
    render_amount_range,
    render_comments,
    render_csv,
    render_markdown,
    render_stdout,
    render_years,
)
from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


def _seed_catalog():
    """Seed a minimal catalog into ``dinary.duckdb`` for resolution tests."""
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
            " VALUES (2024, 'sid', '', 'default', NULL)",
        )
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


# ---------------------------------------------------------------------------
# resolve_row_to_3d — pipeline parity and resolution_kind tracking
# ---------------------------------------------------------------------------


@allure.epic("Report")
@allure.feature("Row resolution")
class TestResolveRowTo3d:
    def test_mapping_resolution_kind(self):
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="еда",
                sheet_group="собака",
                comment="lunch",
                amount_eur=45.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=2,
            )
            assert result is not None
            assert result.category_name == "еда"
            assert result.resolution_kind == "mapping"
            assert 1 in result.tag_ids
            assert "собака" in result.tag_names
        finally:
            con.close()

    def test_event_from_mapping(self):
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="кафе",
                sheet_group="путешествия",
                comment="resort",
                amount_eur=30.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=2,
            )
            assert result is not None
            assert result.event_id == 1
            assert result.event_name == "отпуск-2024"
        finally:
            con.close()

    def test_heuristic_detection_small_amount(self):
        """Amount < 200 EUR on 'аренда'+'релокация' → 'коммунальные' via heuristic."""
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="аренда",
                sheet_group="релокация",
                comment="water bill",
                amount_eur=50.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=2,
            )
            assert result is not None
            assert result.category_name == "коммунальные"
            assert "heuristic" in result.resolution_kind
        finally:
            con.close()

    def test_no_heuristic_for_large_amount(self):
        """Amount >= 200 EUR on 'аренда'+'релокация' stays 'аренда'."""
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="аренда",
                sheet_group="релокация",
                comment="monthly rent",
                amount_eur=500.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=2,
            )
            assert result is not None
            assert result.category_name == "аренда"
            assert "heuristic" not in result.resolution_kind
        finally:
            con.close()

    def test_beneficiary_tag_added(self):
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="мобильник",
                sheet_group="",
                comment="phone case",
                amount_eur=30.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=None,
                beneficiary_raw="ребенок",
            )
            assert result is not None
            assert "Аня" in result.tag_names
        finally:
            con.close()

    def test_returns_none_for_unknown_pair(self):
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="UNKNOWN_CATEGORY",
                sheet_group="",
                comment="",
                amount_eur=100.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=None,
            )
            assert result is None
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Post-import fix simulation
# ---------------------------------------------------------------------------


@allure.epic("Report")
@allure.feature("Post-import fix simulation")
class TestPostImportFixViaResolve:
    def test_comment_keyed_fix_overrides_mapping(self):
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="мобильник",
                sheet_group="",
                comment="эпоксидка гриль зарядник батарейки ножи аккумулятор",
                amount_eur=100.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=None,
            )
            assert result is not None
            assert result.category_name == "бытовая техника"
            assert "postfix" in result.resolution_kind
            assert "mapping" in result.resolution_kind
        finally:
            con.close()

    def test_unmatched_comment_keeps_mapping(self):
        _seed_catalog()
        con = duckdb_repo.get_connection()
        try:
            result = resolve_row_to_3d(
                con,
                sheet_category="еда",
                sheet_group="собака",
                comment="regular grocery shopping",
                amount_eur=45.0,
                year=2024,
                travel_event_id=1,
                business_trip_event_id=None,
                relocation_event_id=None,
            )
            assert result is not None
            assert result.category_name == "еда"
            assert "postfix" not in result.resolution_kind
        finally:
            con.close()


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------


@allure.epic("Report")
@allure.feature("Summary aggregation")
class TestBuildSummary:
    def test_groups_by_3d_and_2d(self):
        details = [
            DetailRow("еда", "", "собака", "еда", "собака", "mapping", 2022, 1, 45.0, "lunch"),
            DetailRow("еда", "", "собака", "еда", "собака", "mapping", 2022, 2, 50.0, "dinner"),
            DetailRow("еда", "", "собака", "еда", "собака", "mapping", 2023, 1, 48.0, "lunch"),
        ]
        summary = build_summary(details)
        assert len(summary) == 1
        row = summary[0]
        assert row.rows == 3
        assert row.category == "еда"
        assert "2022" in row.years
        assert "2023" in row.years

    def test_different_3d_outcomes_stay_separate(self):
        details = [
            DetailRow(
                "коммунальные",
                "",
                "релокация",
                "аренда",
                "релокация",
                "mapping+heuristic",
                2022,
                1,
                50.0,
                "water",
            ),
            DetailRow(
                "аренда", "", "релокация", "аренда", "релокация", "mapping", 2022, 2, 500.0, "rent"
            ),
        ]
        summary = build_summary(details)
        assert len(summary) == 2

    def test_same_2d_different_resolution_kind_separate(self):
        details = [
            DetailRow("еда", "", "", "еда", "", "mapping", 2022, 1, 45.0, "a"),
            DetailRow("еда", "", "", "еда", "", "derivation", 2022, 2, 50.0, "b"),
        ]
        summary = build_summary(details)
        assert len(summary) == 2

    def test_sorted_by_3d_then_2d(self):
        details = [
            DetailRow("мобильник", "", "", "мобильник", "", "mapping", 2022, 1, 30.0, ""),
            DetailRow("еда", "", "собака", "еда", "собака", "mapping", 2022, 1, 45.0, "lunch"),
        ]
        summary = build_summary(details)
        assert summary[0].category == "еда"
        assert summary[1].category == "мобильник"

    def test_amount_aggregated_as_range(self):
        details = [
            DetailRow("еда", "", "", "еда", "", "mapping", 2022, 1, 10.0, ""),
            DetailRow("еда", "", "", "еда", "", "mapping", 2022, 2, 250.0, ""),
        ]
        summary = build_summary(details)
        assert summary[0].amount == "10.00..250.00"

    def test_comment_aggregated(self):
        details = [
            DetailRow("еда", "", "", "еда", "", "mapping", 2022, 1, 10.0, "lunch"),
            DetailRow("еда", "", "", "еда", "", "mapping", 2022, 2, 20.0, "dinner"),
            DetailRow("еда", "", "", "еда", "", "mapping", 2022, 3, 15.0, "snack"),
        ]
        summary = build_summary(details)
        assert summary[0].comment == "3 variants"

    def test_empty_input(self):
        assert build_summary([]) == []


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


@allure.epic("Report")
@allure.feature("Renderers")
class TestRenderers:
    def test_render_years_single(self):
        assert render_years([2022]) == "2022"

    def test_render_years_contiguous(self):
        assert render_years([2020, 2021, 2022, 2023]) == "2020-2023"

    def test_render_years_gaps(self):
        assert render_years([2012, 2013, 2015, 2020, 2021]) == "2012-2013,2015,2020-2021"

    def test_render_years_empty(self):
        assert render_years([]) == ""

    def test_render_amount_single(self):
        assert render_amount_range([45.0]) == "45.00"

    def test_render_amount_range(self):
        assert render_amount_range([10.0, 45.0, 250.0]) == "10.00..250.00"

    def test_render_amount_dedup(self):
        assert render_amount_range([45.0, 45.0, 45.0]) == "45.00"

    def test_render_amount_empty(self):
        assert render_amount_range([]) == ""

    def test_render_comments_single(self):
        assert render_comments(["lunch"]) == "lunch"

    def test_render_comments_multiple(self):
        assert render_comments(["lunch", "dinner", "snack"]) == "3 variants"

    def test_render_comments_empty(self):
        assert render_comments([]) == ""

    def test_render_comments_dedup(self):
        assert render_comments(["lunch", "lunch"]) == "lunch"

    def test_render_stdout_summary(self):
        rows = [
            SummaryRow(
                "еда",
                "",
                "собака",
                3,
                "еда",
                "собака",
                "mapping",
                "2022-2023",
                "45.00..50.00",
                "lunch",
            ),
        ]
        buf = io.StringIO()
        render_stdout(rows, SUMMARY_COLUMNS, output=buf)
        output = buf.getvalue()
        assert "еда" in output
        assert "mapping" in output
        assert "2022-2023" in output

    def test_render_csv_summary(self):
        rows = [
            SummaryRow(
                "еда", "", "собака", 3, "еда", "собака", "mapping", "2022-2023", "45.00", "lunch"
            ),
        ]
        buf = io.StringIO()
        render_csv(rows, SUMMARY_COLUMNS, buf)
        output = buf.getvalue()
        assert "category,event,tags" in output
        assert "еда" in output

    def test_render_markdown_summary(self):
        rows = [
            SummaryRow(
                "еда", "", "собака", 3, "еда", "собака", "mapping", "2022-2023", "45.00", "lunch"
            ),
        ]
        buf = io.StringIO()
        render_markdown(rows, SUMMARY_COLUMNS, buf)
        output = buf.getvalue()
        assert "| category |" in output
        assert "| еда |" in output

    def test_render_markdown_pipe_escaped(self):
        rows = [
            SummaryRow("cat", "", "", 1, "a|b", "", "mapping", "2022", "1.00", "c|d"),
        ]
        buf = io.StringIO()
        render_markdown(rows, SUMMARY_COLUMNS, buf)
        output = buf.getvalue()
        assert "a\\|b" in output

    def test_render_stdout_empty(self):
        buf = io.StringIO()
        render_stdout([], SUMMARY_COLUMNS, output=buf)
        assert "No rows" in buf.getvalue()

    def test_render_stdout_detail(self):
        rows = [
            DetailRow("еда", "", "собака", "еда", "собака", "mapping", 2022, 1, 45.0, "lunch"),
        ]
        buf = io.StringIO()
        render_stdout(rows, DETAIL_COLUMNS, output=buf)
        output = buf.getvalue()
        assert "2022" in output
        assert "lunch" in output


# ---------------------------------------------------------------------------
# End-to-end: collect_detail_rows / _collect_year with the sheet iterator
# stubbed so tests don't have to fake gspread + the FX prefetch.
# ---------------------------------------------------------------------------


@allure.epic("Report")
@allure.feature("Detail collection")
class TestCollectDetailRows:
    def test_collects_resolved_rows_for_year(self, monkeypatch):
        _seed_catalog()

        seen_years: list[int] = []

        def fake_iter(year, *, con=None):
            seen_years.append(year)
            yield ParsedSheetRow(
                row_idx=10,
                year=2024,
                month=3,
                sheet_category="еда",
                sheet_group="собака",
                comment="lunch",
                beneficiary_raw="",
                amount_original=45.0,
                currency_original="EUR",
                amount_app=0.0,
                amount_eur=45.0,
            )
            yield ParsedSheetRow(
                row_idx=11,
                year=2024,
                month=4,
                sheet_category="мобильник",
                sheet_group="",
                comment="phone case",
                beneficiary_raw="",
                amount_original=30.0,
                currency_original="EUR",
                amount_app=0.0,
                amount_eur=30.0,
            )

        monkeypatch.setattr(report_module, "iter_parsed_sheet_rows", fake_iter)

        stats = CollectStats()
        rows = collect_detail_rows(years=[2024], stats=stats)

        assert seen_years == [2024]
        assert stats.rows == 2
        assert stats.skipped_unresolved == 0
        assert stats.skipped_errors == 0
        assert stats.skipped_years == 0
        assert len(rows) == 2

        by_cat = {r.category: r for r in rows}
        assert by_cat["еда"].sheet_group == "собака"
        assert by_cat["еда"].resolution_kind == "mapping"
        assert by_cat["еда"].tags == "собака"
        assert by_cat["мобильник"].resolution_kind == "mapping"

    def test_unresolved_row_increments_skipped(self, monkeypatch):
        _seed_catalog()

        def fake_iter(_year, *, con=None):
            yield ParsedSheetRow(
                row_idx=10,
                year=2024,
                month=3,
                sheet_category="DEFINITELY_UNKNOWN_2D_CATEGORY",
                sheet_group="DEFINITELY_UNKNOWN_2D_GROUP",
                comment="",
                beneficiary_raw="",
                amount_original=10.0,
                currency_original="EUR",
                amount_app=0.0,
                amount_eur=10.0,
            )

        monkeypatch.setattr(report_module, "iter_parsed_sheet_rows", fake_iter)

        stats = CollectStats()
        rows = collect_detail_rows(years=[2024], stats=stats)

        assert rows == []
        assert stats.rows == 0
        assert stats.skipped_unresolved + stats.skipped_errors == 1

    def test_unresolved_row_lands_in_skipped_unresolved_bucket(self, monkeypatch):
        """Unknown-but-syntactically-valid 2D pairs land in
        ``skipped_unresolved``, not ``skipped_errors``."""
        _seed_catalog()

        def fake_iter(_year, *, con=None):
            yield ParsedSheetRow(
                row_idx=10,
                year=2024,
                month=3,
                sheet_category="UNMAPPABLE_CATEGORY",
                sheet_group="UNMAPPABLE_GROUP",
                comment="",
                beneficiary_raw="",
                amount_original=10.0,
                currency_original="EUR",
                amount_app=0.0,
                amount_eur=10.0,
            )

        monkeypatch.setattr(report_module, "iter_parsed_sheet_rows", fake_iter)

        stats = CollectStats()
        rows = collect_detail_rows(years=[2024], stats=stats)

        assert rows == []
        assert stats.skipped_unresolved == 1
        assert stats.skipped_errors == 0

    def test_missing_resolution_context_skips_year(self, monkeypatch):
        """No catalog seeded → build_resolution_context returns None and
        the year is skipped without consulting the iterator."""
        duckdb_repo.init_db()

        called = {"value": False}

        def fake_iter(_year, *, con=None):
            called["value"] = True
            yield  # pragma: no cover

        monkeypatch.setattr(report_module, "iter_parsed_sheet_rows", fake_iter)

        stats = CollectStats()
        rows = collect_detail_rows(years=[2024], stats=stats)

        assert rows == []
        assert called["value"] is False
        assert stats.skipped_years == 1

    def test_iter_exception_skips_year_only(self, monkeypatch):
        """A failure in iter_parsed_sheet_rows must not abort the run."""
        _seed_catalog()

        def fake_iter(year, *, con=None):
            if year == 2024:
                msg = "simulated gspread failure"
                raise RuntimeError(msg)
            yield ParsedSheetRow(
                row_idx=10,
                year=year,
                month=3,
                sheet_category="еда",
                sheet_group="собака",
                comment="lunch",
                beneficiary_raw="",
                amount_original=45.0,
                currency_original="EUR",
                amount_app=0.0,
                amount_eur=45.0,
            )

        monkeypatch.setattr(report_module, "iter_parsed_sheet_rows", fake_iter)

        stats = CollectStats()
        rows = collect_detail_rows(years=[2024], stats=stats)

        assert rows == []
        assert stats.skipped_years == 1
        assert stats.rows == 0

    def test_iter_exception_in_one_year_does_not_abort_others(self, monkeypatch):
        """Failure in year N must not prevent year N+1 from producing rows."""
        _seed_catalog()
        # Seed an additional vacation event so 2025 also has a valid
        # resolution context. Without this, build_resolution_context
        # would itself skip 2025 and we couldn't tell the two skip
        # paths apart.
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
                " VALUES (3, 'отпуск-2025', '2025-01-01', '2025-12-31', TRUE)",
            )
        finally:
            con.close()

        def fake_iter(year, *, con=None):
            if year == 2024:
                msg = "simulated gspread failure for 2024"
                raise RuntimeError(msg)
            yield ParsedSheetRow(
                row_idx=10,
                year=year,
                month=3,
                sheet_category="еда",
                sheet_group="собака",
                comment="lunch",
                beneficiary_raw="",
                amount_original=45.0,
                currency_original="EUR",
                amount_app=0.0,
                amount_eur=45.0,
            )

        monkeypatch.setattr(report_module, "iter_parsed_sheet_rows", fake_iter)

        stats = CollectStats()
        rows = collect_detail_rows(years=[2024, 2025], stats=stats)

        assert stats.skipped_years == 1
        assert stats.rows == 1
        assert len(rows) == 1
        assert rows[0].year == 2025
        assert rows[0].category == "еда"
