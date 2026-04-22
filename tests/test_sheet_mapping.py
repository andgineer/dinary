"""Tests for ``sheet_mapping`` — parsing + atomic swap of the ``map`` tab.

The drain loop reads from ``sheet_mapping`` to decide where each
expense lands in the logging sheet. Those rows are derived from a
human-edited ``map`` worksheet tab via this module.

Tests pin:

* Row validation: unknown category / event / tag names raise
  ``MapTabError``; blank rows become visual separators (skipped
  without error); blank cells + ``*`` are both wildcards.
* ``_atomic_swap`` wipes and repopulates the DB tables inside a
  single transaction — a mid-way crash leaves the previous mapping
  in place.
* ``parse_rows`` ``row_order`` is contiguous starting from 1,
  matching the "first non-``*`` wins per column" resolver contract.
* ``reload_now`` captures ``modifiedTime`` both before and after the
  reload; if the value shifts during the read, the cache is not
  advanced so the next ``ensure_fresh`` retries.
* The pure ``resolve_projection`` implements "first non-``*`` wins
  per column" independently across sheet_category and sheet_group.
"""

from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.config import settings
from dinary.services import ledger_repo, sheet_mapping


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", tmp_path / "dinary.db")
    ledger_repo.init_db()
    con = ledger_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'машина', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (1, 'отпуск-2026', '2026-01-01', '2026-04-20', TRUE, TRUE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'аня', TRUE)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (3, 'путешествия', TRUE)",
        )
    finally:
        con.close()


def _catalog():
    return (
        {"еда": 1, "машина": 2},
        {"отпуск-2026": 1},
        {"собака": 1, "аня": 2, "путешествия": 3},
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
@allure.feature("resolve_projection")
class TestResolveProjection:
    def test_first_non_star_wins_per_column_independently(self):
        """Column A sets Расходы; column B sets Конверт; the resolver
        must take the first non-``*`` for each column independently
        even when they come from different rows."""
        rows = [
            sheet_mapping.MapRow(
                row_order=1,
                category_id=None,
                event_id=None,
                tag_ids=(3,),
                sheet_category=sheet_mapping.WILDCARD,
                sheet_group="путешествия",
            ),
            sheet_mapping.MapRow(
                row_order=2,
                category_id=1,
                event_id=None,
                tag_ids=(),
                sheet_category="Food",
                sheet_group=sheet_mapping.WILDCARD,
            ),
        ]
        result = sheet_mapping.resolve_projection(
            rows,
            category_id=1,
            event_id=None,
            tag_ids={3},
            default_sheet_category="еда",
        )
        assert result == ("Food", "путешествия")

    def test_falls_back_to_default_when_no_row_decides(self):
        result = sheet_mapping.resolve_projection(
            [],
            category_id=1,
            event_id=None,
            tag_ids=set(),
            default_sheet_category="еда",
        )
        assert result == ("еда", "")

    def test_category_mismatch_skips_row(self):
        rows = [
            sheet_mapping.MapRow(
                row_order=1,
                category_id=2,
                event_id=None,
                tag_ids=(),
                sheet_category="Car",
                sheet_group="Transport",
            ),
        ]
        result = sheet_mapping.resolve_projection(
            rows,
            category_id=1,
            event_id=None,
            tag_ids=set(),
            default_sheet_category="еда",
        )
        assert result == ("еда", "")

    def test_tag_subset_required(self):
        """Row requires both ``собака`` AND ``аня``; an expense with
        only ``собака`` must not match — the resolver falls through
        to the default."""
        rows = [
            sheet_mapping.MapRow(
                row_order=1,
                category_id=None,
                event_id=None,
                tag_ids=(1, 2),
                sheet_category=sheet_mapping.WILDCARD,
                sheet_group="dog+kid",
            ),
        ]
        result = sheet_mapping.resolve_projection(
            rows,
            category_id=1,
            event_id=None,
            tag_ids={1},
            default_sheet_category="еда",
        )
        assert result == ("еда", "")


@allure.epic("SheetMapping")
@allure.feature("_atomic_swap")
class TestAtomicSwap:
    def test_swap_replaces_sheet_mapping(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["еда", "*", "*", "Food", "Ess"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        con = ledger_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, rows)
            result = con.execute(
                "SELECT row_order, category_id, sheet_category, sheet_group"
                " FROM sheet_mapping ORDER BY row_order",
            ).fetchall()
        finally:
            con.close()
        assert result == [(1, 1, "Food", "Ess")]

    def test_swap_wipes_previous_rows(self):
        cats, events, tags = _catalog()
        first = sheet_mapping.parse_rows(
            [["еда", "*", "*", "Food", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        second = sheet_mapping.parse_rows(
            [["машина", "*", "*", "Car", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        con = ledger_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, first)
            sheet_mapping._atomic_swap(con, second)
            rows = con.execute(
                "SELECT category_id, sheet_category FROM sheet_mapping",
            ).fetchall()
        finally:
            con.close()
        assert rows == [(2, "Car")]

    def test_swap_survives_preexisting_tag_rows(self):
        """When a prior swap inserted ``sheet_mapping_tags`` rows, a
        follow-up swap must successfully DELETE both them and their
        parent ``sheet_mapping`` rows. This test exercises the
        sequence ``swap with tags → swap without tags`` on the same
        cursor to pin that behaviour.
        """
        cats, events, tags = _catalog()
        first = sheet_mapping.parse_rows(
            [["еда", "*", "собака, аня", "Food", "dog+kid"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        second = sheet_mapping.parse_rows(
            [["машина", "*", "путешествия", "Car", "путешествия"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        con = ledger_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, first)
            first_tags = con.execute(
                "SELECT mapping_row_order, tag_id FROM sheet_mapping_tags"
                " ORDER BY mapping_row_order, tag_id",
            ).fetchall()
            sheet_mapping._atomic_swap(con, second)
            rows = con.execute(
                "SELECT category_id, sheet_category, sheet_group FROM sheet_mapping",
            ).fetchall()
            new_tags = con.execute(
                "SELECT mapping_row_order, tag_id FROM sheet_mapping_tags"
                " ORDER BY mapping_row_order, tag_id",
            ).fetchall()
        finally:
            con.close()
        assert first_tags == [(1, 1), (1, 2)]
        assert rows == [(2, "Car", "путешествия")]
        assert new_tags == [(1, 3)]


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
        import logging

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


def _fake_worksheet(raw_rows_including_header):
    ws = MagicMock()
    ws.get_all_values.return_value = raw_rows_including_header
    return ws


def _fake_sheet(ws):
    sh = MagicMock()
    sh.worksheet.return_value = ws
    return sh


@allure.epic("SheetMapping")
@allure.feature("reload_now modifiedTime ordering")
class TestReloadNowOrdering:
    def test_cache_updated_when_modified_time_stable(self, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)

        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            summary = sheet_mapping.reload_now()

        assert summary["modified_time_cached"] is True
        assert sheet_mapping._cache_state() == "2026-04-20T10:00:00Z"

    def test_cache_not_updated_when_modified_time_shifts_during_read(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)

        modified_times = iter(["2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z"])
        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                side_effect=lambda _ssid: next(modified_times),
            ),
        ):
            summary = sheet_mapping.reload_now()

        assert summary["modified_time_cached"] is False
        assert sheet_mapping._cache_state() is None

    def test_check_after_false_skips_second_drive_get_and_caches_eagerly(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)
        drive_mock = MagicMock(return_value="2026-04-20T10:00:00Z")

        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(sheet_mapping, "drive_get_modified_time", drive_mock),
        ):
            summary = sheet_mapping.reload_now(check_after=False)

        assert drive_mock.call_count == 1
        assert summary["modified_time_cached"] is True
        assert summary["modified_time"] == "2026-04-20T10:00:00Z"
        assert sheet_mapping._cache_state() == "2026-04-20T10:00:00Z"


@allure.epic("SheetMapping")
@allure.feature("ensure_fresh skips when modifiedTime unchanged")
class TestEnsureFresh:
    def test_ensure_fresh_is_noop_when_cache_matches_drive(self, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)
        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            sheet_mapping.reload_now()

        assert sheet_mapping._cache_state() == "2026-04-20T10:00:00Z"

        get_sheet_mock = MagicMock()
        with (
            patch.object(sheet_mapping, "get_sheet", get_sheet_mock),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            sheet_mapping.ensure_fresh()

        get_sheet_mock.assert_not_called()

    def test_ensure_fresh_triggers_reload_when_drive_reports_newer(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)

        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            sheet_mapping.reload_now()

        sh.worksheet.reset_mock()
        ws.get_all_values.reset_mock()
        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T11:00:00Z",
            ),
        ):
            sheet_mapping.ensure_fresh()

        ws.get_all_values.assert_called_once()
        assert sheet_mapping._cache_state() == "2026-04-20T11:00:00Z"


@allure.epic("SheetMapping")
@allure.feature("_load_catalog (inactive-tolerant)")
class TestLoadCatalog:
    """``_load_catalog`` must include inactive categories / events /
    tags. ``is_active = FALSE`` is a "hide from the ручной пикер"
    affordance and must not break map-tab reload — otherwise the
    operator hiding a single auto-tag (e.g. "отпуск") wedges the
    whole reload pipeline with a ``MapTabError`` on every tick.
    """

    def test_loads_inactive_tags(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 3")
            _, _, tag_id_by_name = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        assert tag_id_by_name.get("путешествия") == 3

    def test_loads_inactive_categories(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE categories SET is_active = FALSE WHERE id = 2")
            cat_id_by_name, _, _ = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        assert cat_id_by_name.get("машина") == 2

    def test_loads_inactive_events(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE events SET is_active = FALSE WHERE id = 1")
            _, event_id_by_name, _ = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        assert event_id_by_name.get("отпуск-2026") == 1

    def test_parse_rows_accepts_inactive_tag_reference(self):
        """End-to-end: when the catalog loader picks up an inactive tag,
        ``parse_rows`` must accept a map-tab row that references it.
        This pins the exact user-reported failure mode — a map-tab
        row ``*,*,отпуск,*,путешествия`` surviving the operator
        deactivating "отпуск" via the PWA "Управлять" list.
        """
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 3")
            cats, events, tags = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        rows = sheet_mapping.parse_rows(
            [["*", "*", "путешествия", "*", "путешествия"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert len(rows) == 1
        assert rows[0].tag_ids == (3,)


@allure.epic("SheetMapping")
@allure.feature("event auto_tags helpers")
class TestEventAutoTags:
    def test_resolve_returns_active_tag_ids(self):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "UPDATE events SET auto_tags = '[\"путешествия\"]' WHERE id = 1",
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

    def test_missing_event_returns_empty(self):
        con = ledger_repo.get_connection()
        try:
            assert sheet_mapping.resolve_event_auto_tag_ids(con, 999) == []
        finally:
            con.close()

    def test_malformed_json_is_treated_as_empty(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE events SET auto_tags = 'not-json' WHERE id = 1")
            assert sheet_mapping.resolve_event_auto_tag_ids(con, 1) == []
        finally:
            con.close()

    def test_unknown_tag_names_are_dropped(self):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                'UPDATE events SET auto_tags = \'["путешествия", "missing"]\' WHERE id = 1',
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

    def test_inactive_tag_name_still_resolves(self):
        """``tags.is_active = FALSE`` means "hide from the ручной
        пикер", not "retire from event-driven auto-attach". An event
        whose ``auto_tags`` names an inactive tag must still get that
        tag attached on expense write — otherwise hiding a
        vacation-only tag like "отпуск" silently breaks the
        event-based auto-attach pipeline (the direct complaint behind
        this regression test)."""
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 3")
            con.execute(
                "UPDATE events SET auto_tags = '[\"путешествия\"]' WHERE id = 1",
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]
