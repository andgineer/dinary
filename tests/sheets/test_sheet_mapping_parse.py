"""Parsing tests for the ``map`` worksheet tab.

Covers ``parse_rows`` (validation, wildcards, did-you-mean hints) and
the minimal-template emitter ``_default_template_rows``. Resolution,
catalog loading, and reload pipeline live in sibling
``test_sheet_mapping_*.py`` files.
"""

import logging

import allure
import pytest

from dinary.services import sheet_mapping

from _sheet_mapping_helpers import (  # noqa: F401  (autouse + helpers)
    _catalog,
    _tmp_db,
)


@allure.epic("SheetMapping")
@allure.feature("parse_rows")
class TestParseRows:
    def test_happy_path(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["еда", "*", "*", "Food", "*"],
                ["машина", "*", "*", "Car", "Transport"],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert len(rows) == 2
        assert [r.row_order for r in rows] == [1, 2]
        assert rows[0].category_id == 1
        assert rows[0].sheet_category == "Food"
        assert rows[0].sheet_group == "*"
        assert rows[1].sheet_category == "Car"
        assert rows[1].sheet_group == "Transport"

    def test_wildcards_and_blanks_are_equivalent(self):
        """Blank cells and ``*`` collapse to the same wildcard sentinel
        across every column — A/B/C (catalog dimensions) and D/E
        (output columns). Operators can leave cells empty for
        readability without changing resolver behaviour.
        """
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["*", "", "", "*", "путешествия"],
                ["еда", "", "", "", ""],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert len(rows) == 2
        assert rows[0].category_id is None
        assert rows[0].event_id is None
        assert rows[0].tag_ids == ()
        assert rows[0].sheet_category == "*"
        assert rows[0].sheet_group == "путешествия"
        # Second row: empty D / E must become WILDCARD so the resolver
        # skips that row for both output columns (matches the module
        # docstring and the Конверт-inheritance semantics the
        # operator authoring the tab expects).
        assert rows[1].sheet_category == "*"
        assert rows[1].sheet_group == "*"

    def test_all_wildcard_row_skipped(self):
        """A row that wildcards *everything* contributes nothing and
        would only burn a ``row_order``. parse_rows must skip it so
        reload diagnostics reflect meaningful rules only."""
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["еда", "*", "*", "Food", "*"],
                ["*", "*", "*", "*", "*"],
                ["машина", "*", "*", "Car", "*"],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert [r.row_order for r in rows] == [1, 2]
        assert [r.sheet_category for r in rows] == ["Food", "Car"]

    def test_tags_cell_parsed(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["еда", "*", "собака, аня", "FoodPet", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert rows[0].tag_ids == (1, 2)

    def test_event_resolved_by_name(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["*", "отпуск-2026", "*", "*", "путешествия"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert rows[0].event_id == 1

    def test_blank_rows_skipped(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["еда", "*", "*", "Food", "*"],
                ["", "", "", "", ""],
                ["машина", "*", "*", "Car", "*"],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert [r.row_order for r in rows] == [1, 2]
        assert rows[1].category_id == 2

    def test_unknown_category_raises(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError, match="category"):
            sheet_mapping.parse_rows(
                [["ghost", "*", "*", "X", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_unknown_event_raises(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError, match="event"):
            sheet_mapping.parse_rows(
                [["*", "ghost_event", "*", "X", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_unknown_tag_raises(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError, match="tag"):
            sheet_mapping.parse_rows(
                [["еда", "*", "ghost_tag", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_case_only_mismatch_on_category_surfaces_did_you_mean(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["Еда", "*", "*", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'еда'" in msg

    def test_case_only_mismatch_on_tag_surfaces_did_you_mean(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["еда", "*", "Аня", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'аня'" in msg

    def test_unrelated_missing_category_has_no_hint(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["ghost", "*", "*", "X", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        assert "did you mean" not in str(excinfo.value)


@allure.epic("SheetMapping")
@allure.feature("_default_template_rows")
class TestDefaultTemplateRows:
    """Pin the minimal-template contract.

    Identity rows (Расходы = category name, Конверт = wildcard) are
    NOT emitted: they would be indistinguishable from the resolver's
    no-rule-matched fallback (`resolve_projection` / `logging_projection`
    already substitute the category's canonical name for a missing
    ``sheet_category``) and only bloat the `map` tab, making it harder
    for the operator to see the rules that actually matter.
    """

    def test_emits_tag_rules_and_envelope_overrides_only(self):
        category_names = ["еда", "гигиена", "ЗОЖ", "машина", "ghost-inactive-ignored"]
        active_tag_names = {tag for tag, _ in sheet_mapping._TAG_RULES} | {"extra-unused-tag"}
        rows = sheet_mapping._default_template_rows(
            category_names,
            active_tag_names=active_tag_names,
        )
        tag_rule_count = len(sheet_mapping._TAG_RULES)
        override_rows = [r for r in rows if r[0] != sheet_mapping.WILDCARD]
        # Overrides are emitted only for categories in the active
        # catalog; their ``Расходы`` stays wildcard (the resolver
        # fallback fills in the category name).
        assert {(r[0], r[4]) for r in override_rows} == {
            ("гигиена", "гигиена"),
            ("ЗОЖ", "ЗОЖ"),
        }
        for r in override_rows:
            assert r[3] == sheet_mapping.WILDCARD, (
                "override rows must keep Расходы=* so the row stays"
                " distinguishable from the fallback it would otherwise"
                " duplicate"
            )
        assert len(rows) == tag_rule_count + len(override_rows)
        # No "identity" row (cname, *, *, cname, *) must appear.
        for r in rows:
            is_identity = (
                r[0] != sheet_mapping.WILDCARD and r[0] == r[3] and r[4] == sheet_mapping.WILDCARD
            )
            assert not is_identity, f"identity row leaked into template: {r}"

    def test_inactive_envelope_override_is_dropped(self, caplog):
        caplog.set_level(logging.WARNING, logger="dinary.services.sheet_mapping")
        # ``гигиена`` is an envelope override but absent from the
        # active catalog — the row must be skipped with a WARN rather
        # than emitted (the parser would reject it as unknown anyway).
        rows = sheet_mapping._default_template_rows(
            ["еда"],
            active_tag_names={tag for tag, _ in sheet_mapping._TAG_RULES},
        )
        assert all(r[0] != "гигиена" for r in rows)
        assert any("гигиена" in m for m in caplog.messages)
