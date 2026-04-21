"""Tests for ``dinary.reports.verify_income`` — rich renderer."""

import io
import json

import allure
import pytest

from dinary.reports import verify_income


def _passing_result(year: int = 2024, *, total: float = 1200.0) -> dict:
    return {
        "year": year,
        "ok": True,
        "app_currency": "RSD",
        "total_sheet_app": total,
        "total_db_app": total,
        "months_in_sheet": 12,
        "months_in_db": 12,
        "month_diffs": [],
    }


def _failing_result(year: int = 2024) -> dict:
    return {
        "year": year,
        "ok": False,
        "app_currency": "RSD",
        "total_sheet_app": 1200.0,
        "total_db_app": 1180.0,
        "months_in_sheet": 12,
        "months_in_db": 12,
        "month_diffs": [
            {"month": 6, "sheet_app": 100.0, "db_app": 80.0, "diff": 20.0},
        ],
    }


def _error_result(year: int = 2024, *, error: str = "no entry for year 2024") -> dict:
    """Shape matches the early-exit branch of verify_income_equivalence."""
    return {"year": year, "ok": False, "error": error}


@allure.epic("Reports")
@allure.feature("verify_income — render_single")
class TestRenderSingle:
    def test_passing_year_renders_summary_no_diffs(self):
        buf = io.StringIO()
        verify_income.render_single(_passing_result(2024), stream=buf)
        out = buf.getvalue()
        assert "year 2024" in out
        assert "OK" in out
        assert "RSD" in out
        # month_diffs is empty — table should not render at all.
        assert "Month diffs" not in out

    def test_failing_year_renders_month_diffs(self):
        buf = io.StringIO()
        verify_income.render_single(_failing_result(2023), stream=buf)
        out = buf.getvalue()
        assert "FAIL" in out
        assert "Month diffs" in out
        assert "1 row(s)" in out

    def test_error_branch_renders_red_panel_only(self):
        buf = io.StringIO()
        verify_income.render_single(
            _error_result(2019, error="no entry for year 2019 in .deploy/import_sources.json"),
            stream=buf,
        )
        out = buf.getvalue()
        assert "FAIL" in out
        assert "no entry for year 2019" in out
        # Error branch has no totals → renderer must NOT invent empty
        # placeholders, and the month_diffs table must be absent.
        assert "Months in sheet" not in out
        assert "Month diffs" not in out

    def test_unexpected_payload_shape(self):
        buf = io.StringIO()
        verify_income.render_single({"not": "a result"}, stream=buf)
        assert "Unexpected payload shape" in buf.getvalue()


@allure.epic("Reports")
@allure.feature("verify_income — render_batch")
class TestRenderBatch:
    def test_all_ok_no_drilldown(self):
        buf = io.StringIO()
        verify_income.render_batch(
            [_passing_result(2022), _passing_result(2023)],
            stream=buf,
        )
        out = buf.getvalue()
        assert "2 year(s)" in out
        assert "Drill-down" not in out

    def test_mixed_shows_drilldown_for_failing_only(self):
        buf = io.StringIO()
        verify_income.render_batch(
            [_passing_result(2022), _failing_result(2023)],
            stream=buf,
        )
        out = buf.getvalue()
        assert "Drill-down for 1 failing year(s)" in out
        assert "year 2023" in out

    def test_error_entry_rendered_in_summary(self):
        buf = io.StringIO()
        verify_income.render_batch(
            [_passing_result(2022), _error_result(2019, error="no entry")],
            stream=buf,
        )
        out = buf.getvalue()
        assert "ERROR" in out
        # Error entry is a failing year → drill-down must include it.
        assert "no entry" in out

    def test_empty_list(self):
        buf = io.StringIO()
        verify_income.render_batch([], stream=buf)
        assert "no years to verify" in buf.getvalue()


@allure.epic("Reports")
@allure.feature("verify_income — exit codes")
class TestExitCodes:
    def test_single_ok(self):
        assert verify_income.exit_code_for_single(_passing_result()) == 0

    def test_single_fail(self):
        assert verify_income.exit_code_for_single(_failing_result()) == 1

    def test_single_error(self):
        # Error branch has ok=False → must be treated as failure too.
        assert verify_income.exit_code_for_single(_error_result()) == 1

    def test_batch_all_ok(self):
        assert (
            verify_income.exit_code_for_batch(
                [_passing_result(2022), _passing_result(2023)],
            )
            == 0
        )

    def test_batch_any_fail(self):
        assert (
            verify_income.exit_code_for_batch(
                [_passing_result(2022), _failing_result(2023)],
            )
            == 1
        )

    def test_batch_any_error(self):
        assert (
            verify_income.exit_code_for_batch(
                [_passing_result(2022), _error_result(2019)],
            )
            == 1
        )

    def test_batch_empty_zero(self):
        assert verify_income.exit_code_for_batch([]) == 0


@allure.epic("Reports")
@allure.feature("verify_income — print_json")
class TestPrintJson:
    @pytest.mark.parametrize(
        "payload",
        [
            _passing_result(2022),
            _error_result(2019),
            [_passing_result(2022), _failing_result(2023), _error_result(2019)],
        ],
    )
    def test_roundtrip(self, payload):
        buf = io.StringIO()
        verify_income.print_json(payload, stream=buf)
        assert json.loads(buf.getvalue()) == payload
