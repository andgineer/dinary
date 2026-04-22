"""Tests for ``dinary.reports.verify_budget`` — rich renderer.

Deliberately self-contained: we build fixture payloads in Python
that match the shape of
:func:`dinary.imports.verify_equivalence.verify_bootstrap_import`
and exercise the renderer without touching SSH, SQLite, or sheet
APIs. That keeps the suite fast and pins the payload→UI contract
against accidental drift.
"""

import io
import json

import allure
import pytest

from dinary.reports import verify_budget


def _passing_result(year: int = 2024, *, months: int = 12) -> dict:
    """Shape-accurate payload for an all-green verify_bootstrap_import."""
    return {
        "year": year,
        "months_checked": months,
        "missing_rows": [],
        "extra_rows": [],
        "amount_diffs": [],
        "comment_diffs": [],
        "ok": True,
    }


def _failing_result(year: int = 2024) -> dict:
    """Shape-accurate payload with one diff per drill-down category."""
    return {
        "year": year,
        "months_checked": 12,
        "missing_rows": [
            {
                "month": 3,
                "sheet_category": "транспорт",
                "sheet_group": "",
                "sheet_amount": 150.0,
            },
        ],
        "extra_rows": [
            {
                "month": 7,
                "sheet_category": "прочее",
                "sheet_group": "",
                "db_amount": 42.0,
            },
        ],
        "amount_diffs": [
            {
                "month": 4,
                "sheet_category": "еда",
                "sheet_group": "собака",
                "sheet_amount": 100.0,
                "db_amount": 120.0,
            },
        ],
        "comment_diffs": [
            {
                "month": 5,
                "sheet_category": "еда",
                "sheet_group": "собака",
                "sheet_comment": "корм",
                "db_comment": "корм; витамины",
            },
        ],
        "ok": False,
    }


@allure.epic("Reports")
@allure.feature("verify_budget — render_single")
class TestRenderSingle:
    def test_passing_year_renders_only_summary(self):
        buf = io.StringIO()
        verify_budget.render_single(_passing_result(2024), stream=buf)
        out = buf.getvalue()
        assert "year 2024" in out
        assert "OK" in out
        # None of the drill-down titles should appear when all lists
        # are empty — the renderer's "only when non-empty" contract.
        assert "Missing in DB" not in out
        assert "Amount diffs (present" not in out

    def test_failing_year_renders_all_drilldowns(self):
        buf = io.StringIO()
        verify_budget.render_single(_failing_result(2020), stream=buf)
        out = buf.getvalue()
        assert "FAIL" in out
        assert "year 2020" in out
        # One row per drill-down category — check both the section
        # title and one field from the row.
        assert "Missing in DB" in out
        assert "транспорт" in out
        assert "Extra in DB" in out
        assert "прочее" in out
        assert "Amount diffs" in out
        assert "собака" in out
        assert "Comment diffs" in out
        assert "корм" in out

    def test_unexpected_payload_shape(self):
        buf = io.StringIO()
        verify_budget.render_single({"not": "a result"}, stream=buf)
        out = buf.getvalue()
        assert "Unexpected payload shape" in out


@allure.epic("Reports")
@allure.feature("verify_budget — render_batch")
class TestRenderBatch:
    def test_all_ok_shows_only_summary(self):
        buf = io.StringIO()
        verify_budget.render_batch(
            [_passing_result(2022), _passing_result(2023)],
            stream=buf,
        )
        out = buf.getvalue()
        assert "2 year(s)" in out
        assert "2022" in out
        assert "2023" in out
        # No failing years → no drill-down section at all.
        assert "Drill-down for" not in out

    def test_mixed_shows_drilldown_for_failing_only(self):
        buf = io.StringIO()
        verify_budget.render_batch(
            [_passing_result(2022), _failing_result(2023)],
            stream=buf,
        )
        out = buf.getvalue()
        assert "Drill-down for 1 failing year(s)" in out
        # The drill-down renders the failing year's detail tables;
        # a passing year is never re-rendered in detail.
        assert "year 2023" in out

    def test_empty_list(self):
        buf = io.StringIO()
        verify_budget.render_batch([], stream=buf)
        assert "no years to verify" in buf.getvalue()


@allure.epic("Reports")
@allure.feature("verify_budget — exit codes")
class TestExitCodes:
    def test_single_ok_returns_zero(self):
        assert verify_budget.exit_code_for_single(_passing_result()) == 0

    def test_single_fail_returns_one(self):
        assert verify_budget.exit_code_for_single(_failing_result()) == 1

    def test_batch_all_ok(self):
        assert (
            verify_budget.exit_code_for_batch(
                [_passing_result(2022), _passing_result(2023)],
            )
            == 0
        )

    def test_batch_any_fail(self):
        assert (
            verify_budget.exit_code_for_batch(
                [_passing_result(2022), _failing_result(2023)],
            )
            == 1
        )

    def test_batch_empty_returns_zero(self):
        # Matches the old remote-side ``all(... for r in [])`` truth
        # value — an empty verify run has no failures.
        assert verify_budget.exit_code_for_batch([]) == 0


@allure.epic("Reports")
@allure.feature("verify_budget — print_json")
class TestPrintJson:
    @pytest.mark.parametrize(
        "payload",
        [
            _passing_result(2022),
            [_passing_result(2022), _failing_result(2023)],
        ],
    )
    def test_roundtrip(self, payload):
        buf = io.StringIO()
        verify_budget.print_json(payload, stream=buf)
        # Round-trip through json.loads guarantees we emit valid JSON
        # (trailing newline, indent, ensure_ascii=False all survive).
        assert json.loads(buf.getvalue()) == payload
