"""Aggregation tests for the 2D→3D report pipeline.

Covers ``build_summary`` (grouping by 3D + 2D + resolution_kind) and
``collect_detail_rows`` (the per-year iterator wrapper that surfaces
skipped/unresolved/error counters). Resolution, rendering, and CLI
dispatch live in sibling ``test_report_2d_3d_*.py`` files.
"""

import allure

from dinary.imports import report_2d_3d as report_module
from dinary.imports.expense_import import ParsedSheetRow
from dinary.imports.report_2d_3d import (
    CollectStats,
    DetailRow,
    build_summary,
    collect_detail_rows,
)
from dinary.services import ledger_repo

from _report_2d_3d_helpers import (  # noqa: F401  (autouse + helper)
    _seed_catalog,
    _stub_import_sources,
    _tmp_data_dir,
)


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
                amount_acc=0.0,
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
                amount_acc=0.0,
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
                amount_acc=0.0,
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
                amount_acc=0.0,
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
        ledger_repo.init_db()

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
                amount_acc=0.0,
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
        con = ledger_repo.get_connection()
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
                amount_acc=0.0,
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
