"""Runtime 3D -> 2D mapping sourced from a curated ``map`` worksheet tab.

The tab is a flat, human-editable table; the drain loop uses it to
resolve a 2D target ``(sheet_category, sheet_group)`` for each 3D
expense ``(category_id, event_id, tag_ids)``. The evaluation model is
"first non-``*`` wins per column, independently": rows are scanned
top-to-bottom, every matching row contributes its values, and for each
of the two output columns we pick the first non-wildcard value we see.

Tab layout (columns A..E, one header row, then data rows):

    A  category    canonical category name, or ``*`` for "any"
    B  event       event name, or ``*`` for "any" (includes no event)
    C  tags        comma- or whitespace-separated tag names that must
                   ALL be present on the expense; ``*`` / empty = no
                   required tags
    D  Расходы     target sheet_category; ``*`` / empty means
                   "don't decide here"; any other value wins
    E  Конверт     same three semantics for sheet_group

Fallbacks applied only when no row decides a column:
  sheet_category -> ``categories.name``
  sheet_group    -> ``""``

The DB tables ``sheet_mapping`` / ``sheet_mapping_tags`` are derived
state; the tab is the source of truth. ``reload_now`` swaps them
atomically.
"""

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import duckdb
import gspread

from dinary.config import settings, spreadsheet_id_from_setting
from dinary.services import duckdb_repo
from dinary.services.sheets import drive_get_modified_time, get_sheet

logger = logging.getLogger(__name__)


_TAG_SEPARATOR_RE = re.compile(r"[,\s]+")

#: Sentinel for wildcard cells. Blank cells are normalized to
#: ``WILDCARD`` by ``_normalize_cell`` so operators can author the tab
#: with either ``*`` or an empty cell without changing the resolver.
WILDCARD = "*"

#: Header row written by the template generator and skipped when
#: reading rows from the tab.
MAP_TAB_HEADER: list[str] = ["category", "event", "tags", "Расходы", "Конверт"]


@dataclass(frozen=True, slots=True)
class MapRow:
    """A single parsed + validated map-tab row."""

    row_order: int
    category_id: int | None
    event_id: int | None
    tag_ids: tuple[int, ...]
    sheet_category: str
    sheet_group: str


class MapTabError(Exception):
    """Raised when the ``map`` tab cannot be parsed or contains unknown names.

    Used by the admin reload endpoint to surface validation errors as
    400-class HTTP responses; the drain loop logs and keeps the cached
    (last known-good) mapping rather than crashing.
    """


# ---------------------------------------------------------------------------
# Lazy reload state (process-local cache)
# ---------------------------------------------------------------------------


_last_seen_modified_time: str | None = None


def _cache_state() -> str | None:
    """Return the cached modifiedTime (testing aid)."""
    return _last_seen_modified_time


def _reset_cache() -> None:
    """Clear the cached modifiedTime so the next ``ensure_fresh`` reloads."""
    global _last_seen_modified_time  # noqa: PLW0603
    _last_seen_modified_time = None


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------


def _case_insensitive_match_hint(missing: str, candidates: Iterable[str]) -> str:
    lower = missing.casefold()
    for candidate in candidates:
        if candidate.casefold() == lower:
            return f"; did you mean {candidate!r}?"
    return ""


def _parse_tags_cell(raw: str) -> list[str]:
    stripped = raw.strip()
    if not stripped or stripped == WILDCARD:
        return []
    return [part for part in _TAG_SEPARATOR_RE.split(stripped) if part and part != WILDCARD]


def _normalize_cell(raw: str) -> str:
    stripped = raw.strip()
    if stripped in ("", WILDCARD):
        return WILDCARD
    return stripped


def parse_rows(  # noqa: C901, PLR0912
    raw_rows: list[list[str]],
    *,
    cat_id_by_name: dict[str, int],
    event_id_by_name: dict[str, int],
    tag_id_by_name: dict[str, int],
) -> list[MapRow]:
    """Validate and resolve raw ``map`` tab rows into ``MapRow`` objects.

    Skips visually-blank rows so operators can use empty rows as
    separators. Resolves category / event / tags against the catalog;
    ``*`` / empty cells become wildcards. Raises ``MapTabError`` on
    unknown names.
    """
    parsed: list[MapRow] = []
    # ``raw_rows`` is ``ws.get_all_values()[1:]`` (header stripped by
    # the caller), so ``raw_rows[0]`` is sheet row 2. ``sheet_row``
    # numbers errors the way the operator sees them in Google Sheets.
    for sheet_row, row in enumerate(raw_rows, start=2):
        cells = list(row) + [""] * max(0, 5 - len(row))
        category_cell = cells[0].strip()
        event_cell = cells[1].strip()
        tags_cell = cells[2]
        sheet_category_raw = cells[3]
        sheet_group_raw = cells[4]

        if not any(
            [
                category_cell,
                event_cell,
                tags_cell.strip(),
                sheet_category_raw.strip(),
                sheet_group_raw.strip(),
            ],
        ):
            continue

        category_norm = _normalize_cell(category_cell)
        event_norm = _normalize_cell(event_cell)

        category_id: int | None = None
        if category_norm != WILDCARD:
            if category_norm not in cat_id_by_name:
                hint = _case_insensitive_match_hint(
                    category_norm,
                    cat_id_by_name.keys(),
                )
                msg = (
                    f"map tab row {sheet_row}: category {category_norm!r} is not a known "
                    f"categories.name (case-sensitive){hint}"
                )
                raise MapTabError(msg)
            category_id = cat_id_by_name[category_norm]

        event_id: int | None = None
        if event_norm != WILDCARD:
            if event_norm not in event_id_by_name:
                hint = _case_insensitive_match_hint(
                    event_norm,
                    event_id_by_name.keys(),
                )
                msg = (
                    f"map tab row {sheet_row}: event {event_norm!r} is not a known "
                    f"events.name (case-sensitive){hint}"
                )
                raise MapTabError(msg)
            event_id = event_id_by_name[event_norm]

        tag_names = _parse_tags_cell(tags_cell)
        tag_ids: list[int] = []
        for tag_name in tag_names:
            if tag_name not in tag_id_by_name:
                hint = _case_insensitive_match_hint(tag_name, tag_id_by_name.keys())
                msg = (
                    f"map tab row {sheet_row}: tag {tag_name!r} is not a known "
                    f"tags.name (case-sensitive){hint}"
                )
                raise MapTabError(msg)
            tag_ids.append(tag_id_by_name[tag_name])

        # D / E follow the same wildcard-vs-literal convention as A / B:
        # either ``*`` or an empty cell means "don't decide here, defer
        # to a later row". An all-literal-explicit-clear case ("keep
        # the column blank") is not expressible in the sheet; the
        # default fallback (empty string for ``sheet_group``) is what
        # operators get when no row assigns a value.
        sheet_category = _normalize_cell(sheet_category_raw)
        sheet_group = _normalize_cell(sheet_group_raw)

        # All-wildcard rows contribute nothing to either output column
        # and just burn a ``row_order``. Skip them so reload diagnostics
        # and the row count reflect meaningful rules only.
        if (
            category_id is None
            and event_id is None
            and not tag_ids
            and sheet_category == WILDCARD
            and sheet_group == WILDCARD
        ):
            continue

        parsed.append(
            MapRow(
                row_order=len(parsed) + 1,
                category_id=category_id,
                event_id=event_id,
                tag_ids=tuple(sorted(set(tag_ids))),
                sheet_category=sheet_category,
                sheet_group=sheet_group,
            ),
        )
    return parsed


# ---------------------------------------------------------------------------
# Resolver (pure)
# ---------------------------------------------------------------------------


def resolve_projection(
    rows: Iterable[MapRow],
    *,
    category_id: int,
    event_id: int | None,
    tag_ids: set[int],
    default_sheet_category: str,
) -> tuple[str, str]:
    """Apply the "first non-* wins per column" resolver.

    ``rows`` must be ordered by ``row_order`` ascending. Returns the
    resolved ``(sheet_category, sheet_group)`` pair; an empty string
    is a legitimate per-column result (explicit clear).

    NOTE: ``duckdb_repo.logging_projection`` contains a second copy of
    this same semantics (running directly against ``sheet_mapping`` /
    ``sheet_mapping_tags`` rows fetched from the DB). The two live
    apart so this pure helper can stay as a dependency-free building
    block that tests pin, while the DB-backed variant avoids
    materializing every ``MapRow`` in memory on every drain. Any
    change to the matching rule (new wildcard semantics, reorder
    precedence, extra dimensions) **must** be applied in both places.
    """
    resolved_category: str | None = None
    resolved_group: str | None = None

    for row in rows:
        if row.category_id is not None and row.category_id != category_id:
            continue
        if row.event_id is not None and row.event_id != event_id:
            continue
        if row.tag_ids and not set(row.tag_ids).issubset(tag_ids):
            continue

        if resolved_category is None and row.sheet_category != WILDCARD:
            resolved_category = row.sheet_category
        if resolved_group is None and row.sheet_group != WILDCARD:
            resolved_group = row.sheet_group

        if resolved_category is not None and resolved_group is not None:
            break

    if resolved_category is None:
        resolved_category = default_sheet_category
    if resolved_group is None:
        resolved_group = ""
    return (resolved_category, resolved_group)


# ---------------------------------------------------------------------------
# DB swap
# ---------------------------------------------------------------------------


def _load_catalog(
    con: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Load every catalog row (active or inactive) by name.

    The map tab references names that must stay resolvable after an
    operator hides a row from the manual PWA picker. ``is_active`` on
    a category / event / tag is purely a "hide from the ручной пикер"
    affordance; it must not break the mapping reload. Hard-deleted
    rows are the only invariant — their names are simply absent and
    ``parse_rows`` will still raise ``MapTabError`` with the
    "did you mean" hint.
    """
    cat_rows = con.execute("SELECT name, id FROM categories").fetchall()
    event_rows = con.execute("SELECT name, id FROM events").fetchall()
    tag_rows = con.execute("SELECT name, id FROM tags").fetchall()
    return (
        {str(r[0]): int(r[1]) for r in cat_rows},
        {str(r[0]): int(r[1]) for r in event_rows},
        {str(r[0]): int(r[1]) for r in tag_rows},
    )


def _atomic_swap(con: duckdb.DuckDBPyConnection, rows: list[MapRow]) -> None:
    """Wipe ``sheet_mapping(_tags)`` and repopulate in a single transaction."""
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM sheet_mapping_tags")
        con.execute("DELETE FROM sheet_mapping")
        for row in rows:
            con.execute(
                "INSERT INTO sheet_mapping"
                " (row_order, category_id, event_id, sheet_category, sheet_group)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    row.row_order,
                    row.category_id,
                    row.event_id,
                    row.sheet_category,
                    row.sheet_group,
                ],
            )
            for tag_id in row.tag_ids:
                con.execute(
                    "INSERT INTO sheet_mapping_tags (mapping_row_order, tag_id) VALUES (?, ?)",
                    [row.row_order, tag_id],
                )
        con.execute("COMMIT")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="sheet_mapping._atomic_swap")
        raise


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def reload_now(*, check_after: bool = True) -> dict:
    """Unconditional reload: fetch the map tab, parse, swap the DB.

    Returns a summary dict with the new ``modifiedTime``, number of
    parsed rows, and the tab name. Raises ``MapTabError`` on
    parse/validation failure without touching the DB; the current
    ``sheet_mapping`` contents stay in place.

    ``check_after`` controls the lost-update guard (two Drive metadata
    GETs). The drain-loop path uses ``check_after=True`` so a
    concurrent edit that shifts ``modifiedTime`` mid-reload leaves
    the cache unset and the next tick retries; the admin path uses
    ``check_after=False`` to halve the Drive quota cost.
    """
    # Normalise the env value — operators may paste the full browser
    # URL instead of the bare id. The Sheets + Drive clients want the
    # bare id, so extract it here before any API call.
    spreadsheet_id = spreadsheet_id_from_setting(settings.sheet_logging_spreadsheet)
    if not spreadsheet_id:
        msg = "sheet_logging_spreadsheet not configured; nothing to reload"
        raise MapTabError(msg)

    modified_time_before = drive_get_modified_time(spreadsheet_id)

    sh = get_sheet(spreadsheet_id)
    try:
        ws = sh.worksheet(settings.sheet_mapping_tab_name)
    except gspread.WorksheetNotFound as exc:
        msg = (
            f"map tab {settings.sheet_mapping_tab_name!r} not found on "
            f"spreadsheet {spreadsheet_id!r}; create it via "
            "ensure_default_map_tab() or copy the template from docs/"
        )
        raise MapTabError(msg) from exc

    raw = ws.get_all_values()[1:]

    con = duckdb_repo.get_connection()
    try:
        cat_id_by_name, event_id_by_name, tag_id_by_name = _load_catalog(con)
        rows = parse_rows(
            raw,
            cat_id_by_name=cat_id_by_name,
            event_id_by_name=event_id_by_name,
            tag_id_by_name=tag_id_by_name,
        )
        _atomic_swap(con, rows)
    finally:
        con.close()

    global _last_seen_modified_time  # noqa: PLW0603
    if not check_after:
        _last_seen_modified_time = modified_time_before
        modified_time_after = modified_time_before
        cached = True
    else:
        modified_time_after = drive_get_modified_time(spreadsheet_id)
        if modified_time_after == modified_time_before:
            _last_seen_modified_time = modified_time_after
            cached = True
        else:
            logger.info(
                "sheet_mapping: modifiedTime shifted during reload (%s -> %s); "
                "leaving cache unset so the next ensure_fresh() retries",
                modified_time_before,
                modified_time_after,
            )
            cached = False

    summary: dict = {
        "row_count": len(rows),
        "modified_time": modified_time_after,
        "tab": settings.sheet_mapping_tab_name,
        "modified_time_cached": cached,
    }
    logger.info("sheet_mapping reloaded: %s", summary)
    return summary


def ensure_fresh() -> None:
    """Drain-loop hook: reload the map tab iff Drive says it changed."""
    spreadsheet_id = spreadsheet_id_from_setting(settings.sheet_logging_spreadsheet)
    if not spreadsheet_id:
        return
    try:
        modified_time = drive_get_modified_time(spreadsheet_id)
    except Exception:  # noqa: BLE001 — see comment below
        # Drive failures are routine (token refresh, 5xx, network
        # blips, gspread/google-auth transient errors) and we
        # deliberately swallow *all* of them here: the cached
        # ``sheet_mapping`` keeps serving writes while we wait for
        # the next drain tick. ``exc_info=True`` preserves the
        # traceback so a persistent failure still has enough
        # context for triage.
        logger.warning(
            "sheet_mapping: drive_get_modified_time failed; keeping cached sheet_mapping",
            exc_info=True,
        )
        return
    # Snapshot the module global once. Without this, a concurrent
    # ``reload_now`` between the equality check and the log line below
    # would print mismatched "before" values.
    last_seen = _last_seen_modified_time
    if modified_time == last_seen:
        return
    logger.info(
        "sheet_mapping: map-tab changed (%s -> %s); reloading",
        last_seen,
        modified_time,
    )
    try:
        reload_now()
    except Exception:
        logger.exception(
            "sheet_mapping: reload_now() failed; keeping cached mapping",
        )


def _warn_if_existing_map_tab_is_stale(
    ws: gspread.Worksheet,
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Dry-run the parser against an existing tab and WARN on stale names."""
    try:
        raw = ws.get_all_values()[1:]
        cat_id_by_name, event_id_by_name, tag_id_by_name = _load_catalog(con)
        parse_rows(
            raw,
            cat_id_by_name=cat_id_by_name,
            event_id_by_name=event_id_by_name,
            tag_id_by_name=tag_id_by_name,
        )
    except MapTabError as exc:
        logger.warning(
            "ensure_default_map_tab: existing %r tab references names not in the "
            "current active catalog — runtime logging will fail until the tab is "
            "fixed: %s",
            settings.sheet_mapping_tab_name,
            exc,
        )
    except Exception:
        logger.exception(
            "ensure_default_map_tab: could not validate existing %r tab; skipping staleness check",
            settings.sheet_mapping_tab_name,
        )


# ---------------------------------------------------------------------------
# Default template
# ---------------------------------------------------------------------------


# Tag-driven envelope rules (category=*, event=*, Расходы=*). Ordered:
# beneficiary-specific tags first, then sphere-of-life tags. Any
# expense carrying the tag lands in the matching envelope regardless
# of category.
_TAG_RULES: list[tuple[str, str]] = [
    ("Лариса", "лариса"),
    ("Аня", "ребенок"),
    ("собака", "собака"),
    # Vacation events attach both "отпуск" and "путешествия" auto-tags,
    # so either on its own resolves to the "путешествия" envelope.
    # Keep both rules so a manual tag-only classification (without
    # attaching an event) still routes to the right envelope.
    ("отпуск", "путешествия"),
    ("путешествия", "путешествия"),
    ("релокация", "релокация"),
    ("дача", "дача"),
    ("профессиональное", "профессиональное"),
]


# Per-category envelope overrides. Emitted only for categories whose
# Конверт is not the resolver's default blank. ``Расходы`` is left as
# ``WILDCARD`` because the "no rule matched" fallback in both
# ``resolve_projection`` (sheet_mapping.py) and
# ``logging_projection`` (duckdb_repo.py) already substitutes the
# category's canonical name — so a literal ``cname`` here would just
# be noise duplicating a fallback that is exercised by tests.
_CATEGORY_ENVELOPES: dict[str, str] = {
    "гигиена": "гигиена",
    "ЗОЖ": "ЗОЖ",
}


def _default_template_rows(
    category_names: list[str],
    *,
    active_tag_names: set[str] | None = None,
) -> list[list[str]]:
    """Produce the body rows for the default map tab, in evaluation order.

    1. Generic tag → envelope rules (category=*, event=*, Расходы=*).
       Rules referencing a tag name not in ``active_tag_names`` are
       dropped with a WARN — a rename in the catalog without a
       corresponding ``_TAG_RULES`` update would otherwise emit a
       template that trips ``MapTabError`` on first read.
    2. Per-category envelope overrides, *one row per entry in*
       ``_CATEGORY_ENVELOPES``: Расходы stays ``*`` (the resolver falls
       back to ``category.name``), Конверт takes the override value.
       Pure identity rows (Расходы = category, Конверт = ``*``) are
       deliberately not emitted — they would be indistinguishable from
       the resolver's no-rule-matched fallback and only bloat the tab.

    ``active_tag_names`` defaults to "skip filtering" so call sites
    that only have category names (tests, older call sites) keep
    working; callers that have the active catalog handy pass it in
    to get the filtered template.

    ``category_names`` is consulted only to filter
    ``_CATEGORY_ENVELOPES`` against the active catalog: an override
    for a category that is no longer active is dropped with a WARN
    rather than emitted (the parser would reject it as an unknown
    category name anyway).
    """
    rows: list[list[str]] = []
    for tag, envelope in _TAG_RULES:
        if active_tag_names is not None and tag not in active_tag_names:
            logger.warning(
                "ensure_default_map_tab: skipping tag rule %r -> %r "
                "because the tag is not in the active catalog; "
                "update sheet_mapping._TAG_RULES after renaming tags",
                tag,
                envelope,
            )
            continue
        rows.append([WILDCARD, WILDCARD, tag, WILDCARD, envelope])
    active_category_names = set(category_names)
    for cname, envelope in _CATEGORY_ENVELOPES.items():
        if cname not in active_category_names:
            logger.warning(
                "ensure_default_map_tab: skipping envelope override %r -> %r "
                "because the category is not in the active catalog; "
                "update sheet_mapping._CATEGORY_ENVELOPES after renaming categories",
                cname,
                envelope,
            )
            continue
        rows.append([cname, WILDCARD, WILDCARD, WILDCARD, envelope])
    return rows


def ensure_default_map_tab() -> None:
    """Create the ``map`` worksheet tab with a default template if missing.

    Idempotent: a second call dry-runs the existing tab against the
    current active catalog and WARN-logs any stale references rather
    than mutating.
    """
    spreadsheet_id = spreadsheet_id_from_setting(settings.sheet_logging_spreadsheet)
    if not spreadsheet_id:
        logger.info("ensure_default_map_tab: sheet_logging_spreadsheet empty; skipping")
        return
    sh = get_sheet(spreadsheet_id)
    try:
        existing_ws = sh.worksheet(settings.sheet_mapping_tab_name)
    except gspread.WorksheetNotFound:
        pass
    else:
        con = duckdb_repo.get_connection()
        try:
            _warn_if_existing_map_tab_is_stale(existing_ws, con)
        finally:
            con.close()
        return

    con = duckdb_repo.get_connection()
    try:
        cat_rows = con.execute(
            "SELECT c.name FROM categories c"
            " JOIN category_groups g ON g.id = c.group_id"
            " WHERE c.is_active AND g.is_active"
            " ORDER BY g.sort_order, c.name",
        ).fetchall()
        tag_rows = con.execute(
            "SELECT name FROM tags WHERE is_active",
        ).fetchall()
    finally:
        con.close()

    category_names = [str(r[0]) for r in cat_rows]
    active_tag_names = {str(r[0]) for r in tag_rows}
    body = _default_template_rows(category_names, active_tag_names=active_tag_names)
    values = [MAP_TAB_HEADER, *body]

    ws = sh.add_worksheet(
        title=settings.sheet_mapping_tab_name,
        rows=max(len(values) + 10, 50),
        cols=len(MAP_TAB_HEADER),
    )
    ws.update(range_name="A1", values=values)

    try:
        ws.columns_auto_resize(0, len(MAP_TAB_HEADER) - 1)
    except Exception:  # noqa: BLE001 — see comment below
        # Auto-resize is a cosmetic best-effort: gspread can raise
        # APIError / HTTPError / TimeoutError / arbitrary JSON
        # decode errors from the Sheets batchUpdate endpoint, and
        # the tab is already fully populated at this point — a
        # narrow resize failure must not fail the whole
        # ``ensure_default_map_tab`` call.
        logger.warning(
            "ensure_default_map_tab: failed to auto-resize columns; using Sheets defaults",
        )

    logger.info(
        "ensure_default_map_tab: created %r with %d rows "
        "(%d tag rules + %d category rules) — review before relying on "
        "runtime logging",
        settings.sheet_mapping_tab_name,
        len(body),
        len(_TAG_RULES),
        len(category_names),
    )


# ---------------------------------------------------------------------------
# Event auto_tags helpers (used by ledger write paths)
# ---------------------------------------------------------------------------


def decode_auto_tags_value(raw: object, *, context: str = "") -> list[str]:
    """Decode a raw ``events.auto_tags`` JSON value into a list of names.

    Single canonical implementation shared by every code path that
    reads ``events.auto_tags`` (``api/catalog``, ``catalog_writer``,
    ``sheet_mapping.load_event_auto_tag_names``). Blank / NULL /
    malformed payloads degrade to ``[]`` so a partially migrated DB
    cannot wedge the read path; a WARN is logged once per bad value
    with the caller-supplied ``context`` string so operators can trace
    which event / endpoint surfaced the issue.
    """
    if raw is None or raw == "":
        return []
    try:
        parsed = json.loads(raw if isinstance(raw, str) else str(raw))
    except json.JSONDecodeError:
        logger.warning(
            "events.auto_tags%s is not valid JSON (%r); treating as empty",
            f" for {context}" if context else "",
            raw,
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "events.auto_tags%s is not a JSON list (%r); treating as empty",
            f" for {context}" if context else "",
            raw,
        )
        return []
    return [str(name) for name in parsed]


def load_event_auto_tag_names(
    con: duckdb.DuckDBPyConnection,
    event_id: int,
) -> list[str]:
    """Return the JSON-decoded ``events.auto_tags`` list for ``event_id``.

    Empty list when the event has no auto-tags, the column is blank, or
    the event does not exist.
    """
    row = con.execute(
        "SELECT auto_tags FROM events WHERE id = ?",
        [event_id],
    ).fetchone()
    if row is None:
        return []
    return decode_auto_tags_value(row[0], context=f"event_id={event_id}")


def resolve_event_auto_tag_ids(
    con: duckdb.DuckDBPyConnection,
    event_id: int,
) -> list[int]:
    """Return catalog ids for the event's ``auto_tags`` names.

    The returned ids preserve the authoring order from
    ``events.auto_tags`` (dedup by first occurrence). Tag rows are
    matched by name regardless of their ``is_active`` flag —
    ``tags.is_active = FALSE`` means "hide from the ручной пикер" and
    must not break event-driven auto-attach. Only names that are
    absent from the ``tags`` table entirely (hard-deleted or typo)
    are logged at WARN and dropped; we never block an expense write
    because the operator hid a tag from the picker.
    """
    names = load_event_auto_tag_names(con, event_id)
    if not names:
        return []
    ordered_unique: list[str] = list(dict.fromkeys(names))
    placeholders = ",".join(["?"] * len(ordered_unique))
    rows = con.execute(
        f"SELECT id, name FROM tags WHERE name IN ({placeholders})",  # noqa: S608
        ordered_unique,
    ).fetchall()
    found = {str(r[1]): int(r[0]) for r in rows}
    missing = [n for n in ordered_unique if n not in found]
    if missing:
        logger.warning(
            "events.auto_tags for event_id=%d references unknown tag names %r; dropping",
            event_id,
            missing,
        )
    return [found[name] for name in ordered_unique if name in found]
