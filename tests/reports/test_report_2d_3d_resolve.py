"""Resolution tests for the 2D→3D report pipeline.

Covers ``resolve_row_to_3d`` (mapping / event / heuristic /
beneficiary / unknown branches) and the post-import comment-keyed
fix path. Aggregation, rendering and CLI dispatch live in sibling
``test_report_2d_3d_*.py`` files.
"""

import allure

from dinary.imports.expense_import import resolve_row_to_3d
from dinary.services import ledger_repo

from _report_2d_3d_helpers import (  # noqa: F401  (autouse + helper)
    _seed_catalog,
    _stub_import_sources,
    data_dir,
)


@allure.epic("Report")
@allure.feature("Row resolution")
class TestResolveRowTo3d:
    def test_mapping_resolution_kind(self, blank_db):
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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

    def test_event_from_mapping(self, blank_db):
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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

    def test_heuristic_detection_small_amount(self, blank_db):
        """Amount < 200 EUR on 'аренда'+'релокация' → 'коммунальные' via heuristic."""
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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

    def test_no_heuristic_for_large_amount(self, blank_db):
        """Amount >= 200 EUR on 'аренда'+'релокация' stays 'аренда'."""
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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

    def test_beneficiary_tag_added(self, blank_db):
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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

    def test_returns_none_for_unknown_pair(self, blank_db):
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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


@allure.epic("Report")
@allure.feature("Post-import fix simulation")
class TestPostImportFixViaResolve:
    def test_comment_keyed_fix_overrides_mapping(self, blank_db):
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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

    def test_unmatched_comment_keeps_mapping(self, blank_db):
        _seed_catalog(blank_db)
        con = ledger_repo.get_connection()
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
