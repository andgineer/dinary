"""Runtime 3D -> 2D mapping sourced from a curated ``map`` worksheet tab.

The map tab is a flat, human-editable table whose rows are evaluated
first-match-wins by the drain loop when it needs to pick a
``(sheet_category, sheet_group)`` target for an expense.

Tab layout (columns A..E, one header row, then data rows):

    A  category        canonical category name (must match an active
                       categories.name)
    B  event_pattern   fnmatch-style glob against the expense's event
                       name ("" -> match any event or no event;
                       "отпуск-*" -> any vacation year; "evt" ->
                       exact match)
    C  tags            comma- or space-separated list of tag names
                       required on the expense ("" -> no tags required;
                       expenses may carry extra tags)
    D  sheet_category  target column D value on the logging sheet
    E  sheet_group     target column E value on the logging sheet

More-specific rules live near the top; generic catch-alls live near
the bottom. Row ordering in the sheet maps directly to
``runtime_mapping.row_order`` in the DB.

Reload model
------------

The DB side (``runtime_mapping`` + ``runtime_mapping_tags``) is
considered derived state; the tab is the source of truth. This module
exposes two entry points for refreshing the DB side:

* ``ensure_fresh()`` -- cheap modifiedTime check via Drive API; the
  drain loop calls this before each batch. Re-parses only when the
  tab has actually changed since the last successful reload.
* ``reload_now()`` -- unconditional reload, used by the admin reload
  endpoint.

Both swap the DB tables atomically inside a single transaction so the
drain worker never observes a half-populated map.
"""

import fnmatch
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import duckdb
import gspread

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.sheets import drive_get_modified_time, get_sheet

logger = logging.getLogger(__name__)


_TAG_SEPARATOR_RE = re.compile(r"[,\s]+")


@dataclass(frozen=True, slots=True)
class MapRow:
    """A single parsed + validated map-tab row.

    ``tag_ids`` is the sorted list of catalog tag ids resolved from the
    original tag-name list. ``category_id`` is the resolved category id.
    """

    row_order: int
    category_id: int
    event_pattern: str
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


# ``modifiedTime`` of the spreadsheet the last time we successfully
# reloaded the map. Stored as the opaque RFC3339 string returned by
# Drive. Process-local — the drain loop is single-process by design,
# so a per-process cache is sufficient. A server restart triggers a
# mandatory reload on the next drain pass (cache is None -> always
# different from whatever Drive returns).
_last_seen_modified_time: str | None = None


def _cache_state() -> str | None:
    """Return the cached modifiedTime (testing aid)."""
    return _last_seen_modified_time


def _reset_cache() -> None:
    """Clear the cached modifiedTime so the next ensure_fresh() always reloads.

    Used by tests; production code should never clear this directly.
    """
    global _last_seen_modified_time  # noqa: PLW0603
    _last_seen_modified_time = None


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------


def _case_insensitive_match_hint(missing: str, candidates: Iterable[str]) -> str:
    """Return a ``; did you mean 'X'?`` hint for case-only mismatches.

    Helps the operator land on "the DB has 'еда', the map tab has
    'Еда'" without having to compare character by character in a
    Cyrillic string. Empty when no case-insensitive match exists;
    only suggests the first matching candidate to keep the error
    message short.
    """
    lower = missing.casefold()
    for candidate in candidates:
        if candidate.casefold() == lower:
            return f"; did you mean {candidate!r}?"
    return ""


def _parse_tags_cell(raw: str) -> list[str]:
    """Split a tags-cell value into a list of trimmed tag names.

    Accepts comma- or whitespace-separated names. Empty cell -> [].
    """
    if not raw or not raw.strip():
        return []
    return [part for part in _TAG_SEPARATOR_RE.split(raw.strip()) if part]


def _validate_event_pattern(pattern: str) -> None:
    """Reject patterns that would match literally nothing.

    Empty pattern is legal ("any event or no event"). Any other pattern
    is passed through to ``fnmatch`` at resolve time; here we just
    sanity-check the fnmatch compile doesn't crash.
    """
    if pattern == "":
        return
    try:
        fnmatch.translate(pattern)
    except Exception as exc:
        msg = f"Invalid event_pattern {pattern!r}: {exc}"
        raise MapTabError(msg) from exc


def parse_rows(
    raw_rows: list[list[str]],
    *,
    cat_id_by_name: dict[str, int],
    tag_id_by_name: dict[str, int],
) -> list[MapRow]:
    """Validate and resolve raw ``map`` tab rows into ``MapRow`` objects.

    Skips blank rows (every cell empty) so the operator can use empty
    rows as visual separators. Raises ``MapTabError`` on the first row
    whose category/tag name doesn't resolve against the active catalog.
    """
    parsed: list[MapRow] = []
    for idx, row in enumerate(raw_rows, start=1):
        # Pad to 5 columns for safety; gspread drops trailing empty cells.
        cells = list(row) + [""] * max(0, 5 - len(row))
        cat_name, event_pattern, tags_cell, sheet_category, sheet_group = (
            cells[0].strip(),
            cells[1].strip(),
            cells[2].strip(),
            cells[3].strip(),
            cells[4].strip(),
        )

        # Visual separator / blank line: every meaningful cell empty.
        if not any([cat_name, event_pattern, tags_cell, sheet_category, sheet_group]):
            continue

        if not cat_name:
            msg = f"map tab row {idx}: category name is required"
            raise MapTabError(msg)
        if cat_name not in cat_id_by_name:
            hint = _case_insensitive_match_hint(cat_name, cat_id_by_name.keys())
            msg = (
                f"map tab row {idx}: category {cat_name!r} is not an active "
                f"categories.name (names are case-sensitive; was it renamed or retired?)"
                f"{hint}"
            )
            raise MapTabError(msg)

        if not sheet_category:
            msg = (
                f"map tab row {idx} ({cat_name!r}): sheet_category (column D) "
                "must not be empty — it's the target cell on the logging sheet"
            )
            raise MapTabError(msg)

        _validate_event_pattern(event_pattern)

        tag_names = _parse_tags_cell(tags_cell)
        tag_ids: list[int] = []
        for tag_name in tag_names:
            if tag_name not in tag_id_by_name:
                hint = _case_insensitive_match_hint(tag_name, tag_id_by_name.keys())
                msg = (
                    f"map tab row {idx} ({cat_name!r}): tag {tag_name!r} is not an "
                    "active tags.name (names are case-sensitive; was it renamed or retired?)"
                    f"{hint}"
                )
                raise MapTabError(msg)
            tag_ids.append(tag_id_by_name[tag_name])

        parsed.append(
            MapRow(
                row_order=len(parsed) + 1,
                category_id=cat_id_by_name[cat_name],
                event_pattern=event_pattern,
                tag_ids=tuple(sorted(set(tag_ids))),
                sheet_category=sheet_category,
                sheet_group=sheet_group,
            ),
        )
    return parsed


# ---------------------------------------------------------------------------
# DB swap
# ---------------------------------------------------------------------------


def _load_active_catalog(
    con: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, int], dict[str, int]]:
    cat_rows = con.execute(
        "SELECT name, id FROM categories WHERE is_active",
    ).fetchall()
    tag_rows = con.execute(
        "SELECT name, id FROM tags WHERE is_active",
    ).fetchall()
    cat_id_by_name = {str(r[0]): int(r[1]) for r in cat_rows}
    tag_id_by_name = {str(r[0]): int(r[1]) for r in tag_rows}
    return cat_id_by_name, tag_id_by_name


def _atomic_swap(con: duckdb.DuckDBPyConnection, rows: list[MapRow]) -> None:
    """Wipe runtime_mapping(_tags) and repopulate from ``rows``.

    Single transaction so the drain loop never sees a half-populated
    table. DuckDB's FK-in-transaction behaviour (see
    ``seed_config._purge_mapping_tables``) is OK here because the only
    FK points from ``runtime_mapping_tags`` into ``runtime_mapping``,
    which we delete in the correct order.
    """
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM runtime_mapping_tags")
        con.execute("DELETE FROM runtime_mapping")
        for row in rows:
            con.execute(
                "INSERT INTO runtime_mapping"
                " (row_order, category_id, event_pattern, sheet_category, sheet_group)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    row.row_order,
                    row.category_id,
                    row.event_pattern,
                    row.sheet_category,
                    row.sheet_group,
                ],
            )
            for tag_id in row.tag_ids:
                con.execute(
                    "INSERT INTO runtime_mapping_tags (mapping_row_order, tag_id) VALUES (?, ?)",
                    [row.row_order, tag_id],
                )
        con.execute("COMMIT")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="runtime_map._atomic_swap")
        raise


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def reload_now(*, check_after: bool = True) -> dict:
    """Unconditional reload: fetch the map tab, parse, swap the DB.

    Returns a summary dict with the new ``modifiedTime``, number of
    parsed rows, and the per-category row counts. Raises
    ``MapTabError`` on parse/validation failure without touching the
    DB; the current ``runtime_mapping`` contents stay in place.

    ``check_after`` controls the lost-update guard (two Drive
    metadata GETs):

    * ``True`` (default, drain-loop path): capture ``modifiedTime``
      both before and after reading the rows. When they agree, the
      tab was stable during the read and we cache the value; when
      they differ, the parsed rows pre-date an edit, so we install
      them anyway (they're at least as fresh as what was in the DB)
      but leave the cache unset so the next ``ensure_fresh`` tick
      retries and converges.
    * ``False`` (admin-reload path): single Drive GET before the
      read, cache it unconditionally. The operator explicitly asked
      for a reload, so saving one Drive round-trip matters more than
      the self-healing retry — if they edit again, they click
      reload again. This halves the Drive quota spend on the hot
      admin button, which matters because Drive's metadata quota is
      lower than the Sheets one.
    """
    spreadsheet_id = settings.sheet_logging_spreadsheet
    if not spreadsheet_id:
        msg = "sheet_logging_spreadsheet not configured; nothing to reload"
        raise MapTabError(msg)

    # Capture modifiedTime BEFORE the content read so a concurrent
    # edit that bumps modifiedTime while we're fetching rows can be
    # detected by the optional second GET below.
    modified_time_before = drive_get_modified_time(spreadsheet_id)

    sh = get_sheet(spreadsheet_id)
    try:
        ws = sh.worksheet(settings.runtime_map_tab_name)
    except gspread.WorksheetNotFound as exc:
        msg = (
            f"map tab {settings.runtime_map_tab_name!r} not found on "
            f"spreadsheet {spreadsheet_id!r}; create it via "
            "ensure_default_map_tab() or copy the template from docs/"
        )
        raise MapTabError(msg) from exc

    # Skip one header row; tolerate an arbitrary number of data rows.
    raw = ws.get_all_values()[1:]

    con = duckdb_repo.get_connection()
    try:
        cat_id_by_name, tag_id_by_name = _load_active_catalog(con)
        rows = parse_rows(
            raw,
            cat_id_by_name=cat_id_by_name,
            tag_id_by_name=tag_id_by_name,
        )
        _atomic_swap(con, rows)
    finally:
        con.close()

    global _last_seen_modified_time  # noqa: PLW0603
    if not check_after:
        # Admin-initiated reload: cache eagerly, skip the second GET.
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
                "runtime_map: modifiedTime shifted during reload (%s -> %s); "
                "leaving cache unset so the next ensure_fresh() retries",
                modified_time_before,
                modified_time_after,
            )
            cached = False

    summary: dict = {
        "row_count": len(rows),
        "modified_time": modified_time_after,
        "tab": settings.runtime_map_tab_name,
        "modified_time_cached": cached,
    }
    logger.info("runtime_map reloaded: %s", summary)
    return summary


def ensure_fresh() -> None:
    """Drain-loop hook: reload the map tab iff Drive says it changed.

    Cheap path (steady state): one Drive metadata GET, sub-second,
    compares a cached RFC3339 timestamp. Reload path (tab edited):
    one Sheets ``get_all_values()`` call plus a DB swap.

    Network errors downgrade to a log warning without raising — the
    drain loop must keep draining whatever is in ``runtime_mapping``
    rather than stalling on an unrelated Google outage.
    """
    spreadsheet_id = settings.sheet_logging_spreadsheet
    if not spreadsheet_id:
        return
    try:
        modified_time = drive_get_modified_time(spreadsheet_id)
    except Exception as exc:  # noqa: BLE001
        # Drive client raises from a large surface (auth expiry,
        # network errors, 5xx, JSON decode). The drain loop must
        # keep draining whatever is in ``runtime_mapping`` rather
        # than stall on an unrelated Google outage, so we downgrade
        # *any* exception to a warning and bail out of this tick.
        logger.warning(
            "runtime_map: drive_get_modified_time failed (%s); keeping cached runtime_mapping",
            exc.__class__.__name__,
        )
        return
    if modified_time == _last_seen_modified_time:
        return
    logger.info(
        "runtime_map: map-tab changed (%s -> %s); reloading",
        _last_seen_modified_time,
        modified_time,
    )
    try:
        reload_now()
    except Exception:
        # Keep the last-known-good runtime_mapping; surface the problem
        # in logs for the operator. Intentionally don't update
        # _last_seen_modified_time on failure so the next drain pass
        # retries.
        logger.exception("runtime_map: reload_now() failed; keeping cached mapping")


def _warn_if_existing_map_tab_is_stale(
    ws: gspread.Worksheet,
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Emit a WARN log if the existing ``map`` tab references names
    no longer in the active catalog.

    Called from ``ensure_default_map_tab`` when the tab already
    exists. Silence (tab exists → return) used to leave the operator
    guessing whether a reseed that renamed/retired categories had
    broken the runtime mapping; the drain loop would only surface
    the problem on the next job via ``MapTabError``. Running the
    parser here as a dry-run lets the reseed command log the problem
    immediately without mutating the tab (the operator is the one
    who should pick the replacement cell, not us).

    Failures in the dry-run are intentionally non-fatal: the seed
    task has already committed the catalog change and we don't want
    to roll it back just because the map tab is out of date.
    """
    try:
        raw = ws.get_all_values()[1:]
        cat_id_by_name, tag_id_by_name = _load_active_catalog(con)
        parse_rows(
            raw,
            cat_id_by_name=cat_id_by_name,
            tag_id_by_name=tag_id_by_name,
        )
    except MapTabError as exc:
        logger.warning(
            "ensure_default_map_tab: existing %r tab references names not in the "
            "current active catalog — runtime logging will fail until the tab is "
            "fixed: %s",
            settings.runtime_map_tab_name,
            exc,
        )
    except Exception:
        # Tab read itself failed (network / permissions) — surface in
        # logs but don't block reseed completion.
        logger.exception(
            "ensure_default_map_tab: could not validate existing %r tab; skipping staleness check",
            settings.runtime_map_tab_name,
        )


def ensure_default_map_tab() -> None:
    """Create the ``map`` worksheet tab with a default layout if it doesn't exist.

    Per-category default: prefer the ``(sheet_category, sheet_group)``
    pair the historical importer resolved for the **latest**
    ``import_sources.year`` — that's the pair the operator has been
    using in the actual logging sheet, so the default mapping produces
    cells that already exist on the sheet (column D labels in the
    legacy sheet are often mixed-case localised forms like ``"Машина"``
    that don't match the canonical 3D name ``"машина"``). If no
    historical mapping exists for a category (e.g. a brand-new
    taxonomy entry), fall back to the 3D category name in both A and
    D and an empty group in E; the drain-loop category-name fallback
    in ``sheet_logging._drain_one_job`` takes care of the rest.

    Idempotent: does nothing when the tab already exists. When it
    does exist, though, we dry-run the parser against the current
    active catalog and WARN if any row references names that have
    been renamed or retired since the tab was last curated — the
    operator needs to know immediately rather than waiting for the
    drain loop to surface ``MapTabError`` at runtime. Safe to call
    from seed and from the admin API.
    """
    spreadsheet_id = settings.sheet_logging_spreadsheet
    if not spreadsheet_id:
        logger.info("ensure_default_map_tab: sheet_logging_spreadsheet empty; skipping")
        return
    sh = get_sheet(spreadsheet_id)
    try:
        existing_ws = sh.worksheet(settings.runtime_map_tab_name)
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
            "SELECT c.id, c.name FROM categories c"
            " JOIN category_groups g ON g.id = c.group_id"
            " WHERE c.is_active AND g.is_active"
            " ORDER BY g.sort_order, c.name",
        ).fetchall()
        latest_year_row = con.execute(
            "SELECT MAX(year) FROM import_sources",
        ).fetchone()
        latest_year = (
            int(latest_year_row[0])
            if latest_year_row is not None and latest_year_row[0] is not None
            else None
        )
        # Historical 2D labels per category_id, keyed to the latest
        # imported year (or year=0 fallback entries). A category may
        # appear under several legacy ``(sheet_category, sheet_group)``
        # pairs; we pick the first one deterministically so the seed
        # output is stable. The operator is expected to curate after.
        latest_pairs: dict[int, tuple[str, str]] = {}
        if latest_year is not None:
            for r in con.execute(
                "SELECT category_id, sheet_category, sheet_group"
                " FROM import_mapping"
                " WHERE year IN (?, 0)"
                " ORDER BY year DESC, id",
                [latest_year],
            ).fetchall():
                cid = int(r[0])
                if cid in latest_pairs:
                    continue
                latest_pairs[cid] = (str(r[1]), str(r[2] or ""))
    finally:
        con.close()

    header = ["category", "event_pattern", "tags", "sheet_category", "sheet_group"]
    body: list[list[str]] = []
    identity_fallbacks = 0
    for cid, cname in cat_rows:
        cname_s = str(cname)
        pair = latest_pairs.get(int(cid))
        if pair is None:
            identity_fallbacks += 1
            body.append([cname_s, "", "", cname_s, ""])
        else:
            body.append([cname_s, "", "", pair[0], pair[1]])
    values = [header, *body]

    ws = sh.add_worksheet(
        title=settings.runtime_map_tab_name,
        rows=max(len(values) + 10, 50),
        cols=len(header),
    )
    ws.update(range_name="A1", values=values)
    logger.info(
        "ensure_default_map_tab: created %r with %d rows "
        "(%d seeded from import_mapping year=%s, %d identity fallbacks) — "
        "please review the tab before relying on runtime logging",
        settings.runtime_map_tab_name,
        len(body),
        len(body) - identity_fallbacks,
        latest_year,
        identity_fallbacks,
    )
