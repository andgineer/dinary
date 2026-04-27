"""Renderer tests for the 2D→3D report pipeline.

Covers the small string helpers (``render_years`` / ``render_amount_range``
/ ``render_comments``), CSV / rich table output, and the
``render_json`` ↔ ``rows_from_json`` wire format used by ``inv
import-report-2d-3d --remote``. Resolution, aggregation, and CLI
dispatch live in sibling ``test_report_2d_3d_*.py`` files.
"""

import io
import json

import allure

from dinary.imports import report_2d_3d as report_module
from dinary.imports.report_2d_3d import (
    DETAIL_COLUMNS,
    SUMMARY_COLUMNS,
    DetailRow,
    SummaryRow,
    _style_for_resolution_kind,
    render_amount_range,
    render_comments,
    render_csv,
    render_rich,
    render_years,
)


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

    def test_render_csv_summary(self):
        rows = [
            SummaryRow(
                "еда", "", "собака", 3, "еда", "собака", "mapping", "2022-2023", "45.00", "lunch"
            ),
        ]
        buf = io.StringIO()
        render_csv(rows, SUMMARY_COLUMNS, output=buf)
        output = buf.getvalue()
        assert "category,event,tags" in output
        assert "еда" in output


@allure.epic("Report")
@allure.feature("Rich renderer")
class TestRenderRich:
    """Smoke tests for the rich summary / detail renderer.

    We don't parse the rich box-drawing output — layout is a
    black-box contract — but we pin that the renderer completes,
    emits the key data values, picks the right title, and that the
    resolution-kind colour-mapping helper keeps its known-values
    contract (so the renderer's colouring step stays correct).
    """

    def test_renders_summary_rows(self):
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
        render_rich(rows, SUMMARY_COLUMNS, output=buf)
        out = buf.getvalue()
        assert "summary" in out
        assert "еда" in out
        assert "mapping" in out
        assert "2022-2023" in out

    def test_renders_detail_rows(self):
        rows = [
            DetailRow(
                "еда",
                "",
                "собака",
                "еда",
                "собака",
                "derivation+heuristic",
                2022,
                1,
                45.0,
                "lunch",
            ),
        ]
        buf = io.StringIO()
        render_rich(rows, DETAIL_COLUMNS, output=buf)
        out = buf.getvalue()
        assert "detail" in out
        # Compound resolution_kind values render with their primary
        # kind used for colour and the full text kept for the eye.
        assert "derivation+heuristic" in out
        assert "2022" in out

    def test_empty_rows_prints_placeholder(self):
        buf = io.StringIO()
        render_rich([], SUMMARY_COLUMNS, output=buf)
        assert "no rows" in buf.getvalue()

    def test_style_for_known_kinds(self):
        # Happy-path mapping → green; fallback derivation → yellow.
        # Compound labels ("mapping+heuristic") colour by the primary
        # segment so the palette stays declarative.
        assert _style_for_resolution_kind("mapping") == "green"
        assert _style_for_resolution_kind("mapping+heuristic") == "green"
        assert _style_for_resolution_kind("derivation") == "yellow"
        assert _style_for_resolution_kind("derivation+postfix") == "yellow"

    def test_style_for_unknown_kind_is_empty(self):
        # Unknown primary kind → no style so rich prints the cell
        # verbatim instead of blowing up on a bad markup tag.
        assert _style_for_resolution_kind("banana") == ""
        assert _style_for_resolution_kind("") == ""


@allure.epic("Report")
@allure.feature("render_json / rows_from_json — remote transport")
class TestRenderJson:
    """The JSON wire format used by ``inv import-report-2d-3d --remote``
    for ``rich`` / ``stdout`` output. Same motivation as
    :mod:`dinary.reports.income` / ``.expenses``: ``invoke.Runner``
    decodes SSH stdout in chunks with ``errors='replace'``, corrupting
    any multi-byte UTF-8 character (``─`` box-drawing, Cyrillic) that
    lands on a read-buffer boundary. Shipping structured JSON bytes
    and decoding once on the client sidesteps that entirely.
    """

    def test_summary_roundtrip(self):
        rows = [
            SummaryRow(
                category="путешествия",
                event="",
                tags="собака",
                rows=3,
                sheet_category="еда",
                sheet_group="собака",
                resolution_kind="mapping",
                years="2022-2024",
                amount="45.00..50.00",
                comment="lunch",
            ),
        ]
        buf = io.StringIO()
        report_module.render_json(rows, SUMMARY_COLUMNS, buf)
        payload = json.loads(buf.getvalue())
        assert payload["detail"] is False
        assert payload["columns"] == list(SUMMARY_COLUMNS)
        rebuilt = report_module.rows_from_json(payload)
        assert rebuilt == rows

    def test_detail_roundtrip(self):
        rows = [
            DetailRow(
                category="еда",
                event="",
                tags="собака",
                sheet_category="еда",
                sheet_group="собака",
                resolution_kind="mapping",
                year=2022,
                month=1,
                amount_eur=45.0,
                comment="обед у бабушки",
            ),
        ]
        buf = io.StringIO()
        report_module.render_json(rows, DETAIL_COLUMNS, buf)
        payload = json.loads(buf.getvalue())
        assert payload["detail"] is True
        rebuilt = report_module.rows_from_json(payload)
        assert rebuilt == rows

    def test_empty_rows_roundtrip(self):
        buf = io.StringIO()
        report_module.render_json([], SUMMARY_COLUMNS, buf)
        payload = json.loads(buf.getvalue())
        assert payload["rows"] == []
        assert report_module.rows_from_json(payload) == []

    def test_cyrillic_stays_unescaped_on_the_wire(self):
        """``ensure_ascii=False`` keeps Cyrillic readable in raw
        ``--json`` output and avoids a 6× payload blow-up.
        """
        rows = [
            SummaryRow(
                category="путешествия",
                event="отпуск-2026",
                tags="собака,кот",
                rows=1,
                sheet_category="путешествия",
                sheet_group="отпуск",
                resolution_kind="mapping",
                years="2026",
                amount="42000.00",
                comment="Бали",
            ),
        ]
        buf = io.StringIO()
        report_module.render_json(rows, SUMMARY_COLUMNS, buf)
        raw = buf.getvalue()
        assert "путешествия" in raw
        assert "отпуск-2026" in raw
        assert "Бали" in raw
        assert "\\u" not in raw
