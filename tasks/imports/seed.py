"""Import-side catalog seeding: mapping rebuild + public entry points.

The 2D→3D derivation rules (lookup tables and derivation functions) live in
``imports.seed_derivation``; this module handles the heavier machinery:

* ``EXPLICIT_MAPPING_OVERRIDES`` — per-year escape hatches for rows the
  generic derivation gets wrong;
* the ``.deploy/import_sources.json``-driven category discovery
  (``_load_categories_for_sheet`` / ``_collect_categories``);
* the ``import_mapping`` table rebuild that consumes discovered pairs +
  overrides, keyed by the ``TaxonomyIdMaps`` returned from
  ``seed_classification_catalog``;
* the public entry points ``seed_from_sheet`` (non-destructive) and
  ``rebuild_config_from_sheets`` (the ``inv import-catalog`` driver,
  hash-gated and transactional).

Direction of dependency is strictly ``imports.seed -> services.seed_config``
and ``imports.seed -> config.read_import_sources``. Runtime code never
reaches into this module; non-import deployments never import it.
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from dinary import config
from dinary.adapters.sheets_client import get_sheet
from dinary.api.controllers.catalog_writer import hash_catalog_state
from dinary.config import (
    IMPORT_SOURCES_DOC_HINT,
    KNOWN_LAYOUT_KEYS,
    ImportSourceRow,
)
from dinary.db import storage
from dinary.db.catalog import get_catalog_version
from dinary.sheets import sheet_mapping
from dinary.sheets.sheets import HEADER_ROWS, _cell
from tasks.imports.seed_config import (
    HISTORICAL_YEAR_FROM,
    HISTORICAL_YEAR_TO,
    TaxonomyIdMaps,
    _bump_catalog_version,
    seed_classification_catalog,
)
from tasks.imports.seed_derivation import (
    canonical_category_for_source,
    event_name_for_source,
    tags_for_source,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Category:
    """Lightweight ``(category, group)`` value object produced by the import-side
    category discovery (``_collect_categories`` /
    ``_load_categories_for_sheet``) and consumed by the import-mapping
    rebuild (``_rebuild_import_mapping``).

    Lives in ``imports.seed`` — not in ``services.seed_config`` —
    because it is purely an import-pipeline artifact. Non-import
    deployments (``inv bootstrap-catalog``) never touch it, and
    runtime code has no reason to import it.
    """

    name: str
    group: str


# ---------------------------------------------------------------------------
# Per-year explicit overrides (rare cases the generic derivation gets wrong)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MappingSeedRow:
    year: int
    sheet_category: str
    sheet_group: str
    category: str
    tags: tuple[str, ...] = ()
    event_name: str | None = None


EXPLICIT_MAPPING_OVERRIDES: list[MappingSeedRow] = [
    # ("приложения", "") and ("приложения", "профессиональное") are resolved
    # entirely by canonical_category_for_source + tags_for_source, so no
    # year=0 entries are needed for them.
    MappingSeedRow(2023, "professional", "apps", "продуктивность", ("профессиональное",)),
    MappingSeedRow(2018, "Работа", "App", "продуктивность", ("профессиональное",)),
    MappingSeedRow(2018, "Parallels", "", "развлечения"),
    MappingSeedRow(0, "Коммунальные", "страховка", "коммунальные"),
    MappingSeedRow(0, "Коммунальные", "Страховка", "коммунальные"),
    MappingSeedRow(0, "Дача", "страховка", "коммунальные", ("дача",)),
    MappingSeedRow(2017, "Дача", "Страховка", "коммунальные", ("дача",)),
    MappingSeedRow(2018, "Дача", "Страховка", "коммунальные", ("дача",)),
    MappingSeedRow(0, "Машина", "страховка", "машина"),
    MappingSeedRow(2017, "Машина", "Страховка", "машина"),
    MappingSeedRow(2017, "Страхование жизни", "", "медицина"),
    MappingSeedRow(2018, "Коммунальные", "Газфонд", "коммунальные"),
    MappingSeedRow(2019, "Wellness", "Стрижка", "гигиена"),
    MappingSeedRow(0, "Wellness", "стрижка", "гигиена"),
    MappingSeedRow(0, "стрижка", "личный уход", "гигиена"),
    MappingSeedRow(2018, "wellness", "рив гош", "гигиена"),
    MappingSeedRow(2022, "подарки", "", "подарки"),
    MappingSeedRow(2017, "Развлечения", "SkiiTime", "лыжи"),
    MappingSeedRow(2018, "Спорт", "SkiiTime", "лыжи"),
    MappingSeedRow(2018, "Спорт", "лыжи", "лыжи"),
    MappingSeedRow(2019, "Спорт", "SkiiTime", "лыжи"),
    MappingSeedRow(2021, "Спорт", "skitime", "лыжи"),
    MappingSeedRow(2021, "Спорт", "лыжероллеры", "лыжи"),
    MappingSeedRow(2021, "Спорт", "лыжи", "лыжи"),
    MappingSeedRow(2021, "спорт", "лыжи", "лыжи"),
    MappingSeedRow(2022, "спорт", "лыжи", "лыжи"),
    MappingSeedRow(2018, "Avito", "", "гаджеты"),
    MappingSeedRow(2022, "очки", "", "медицина"),
]


# ---------------------------------------------------------------------------
# Discovery: pull category pairs from configured sheets
# ---------------------------------------------------------------------------

_CATEGORY_COLUMNS_BY_LAYOUT = {
    "default": (4, 5),
    "rub": (4, 5),
    "rub_fallback": (4, 5),
    "rub_6col": (3, 4),
    "rub_2016": (3, 4),
    "rub_2014": (3, 4),
    "rub_2012": (3, 4),
}


def _load_categories_for_sheet(
    spreadsheet_id: str,
    worksheet_name: str,
    layout_key: str,
) -> list[Category]:
    ss = get_sheet(spreadsheet_id)
    ws = ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1
    all_values = ws.get_all_values()
    col_category, col_group = _CATEGORY_COLUMNS_BY_LAYOUT[layout_key]

    seen: set[tuple[str, str]] = set()
    categories: list[Category] = []
    for row in all_values[HEADER_ROWS:]:
        cat_name = _cell(row, col_category)
        group_name = _cell(row, col_group)
        if cat_name and (cat_name, group_name) not in seen:
            seen.add((cat_name, group_name))
            categories.append(Category(name=cat_name, group=group_name))
    return categories


def _collect_categories() -> list[Category]:
    """Collect ``(category, group)`` pairs across every registered import source.

    Reads the operator-local ``.deploy/import_sources.json`` via
    ``config.read_import_sources`` (which returns ``[]`` when the file
    is absent). Callers that need a non-empty list raise their own
    actionable error pointing at the repo-root ``imports/`` directory.
    """
    seen: set[tuple[str, str]] = set()
    categories: list[Category] = []

    sources = sorted(config.read_import_sources(), key=lambda r: r.year)
    for source in sources:
        for row in _load_categories_for_sheet(
            source.spreadsheet_id,
            source.worksheet_name,
            source.layout_key,
        ):
            key = (row.name, row.group)
            if key not in seen:
                seen.add(key)
                categories.append(row)
    return categories


# ---------------------------------------------------------------------------
# Mapping-table rebuild (import_mapping + import_mapping_tags)
# ---------------------------------------------------------------------------


def _purge_mapping_tables(con: sqlite3.Connection) -> None:
    """Clear all import-mapping rows; they are rebuilt from current active taxonomy.

    Ledger tables do NOT FK into mapping tables, so this is safe under
    FKs. Mapping tables are catalog-side derived state: rename/retire
    of a taxonomy row must re-point any affected mapping row onto the
    new active id, and doing that by DELETE+INSERT is simpler and more
    correct than tracking per-row deltas.

    Callers are expected to invoke this INSIDE the same write
    transaction that rebuilds the rows (see ``seed_from_sheet``). The
    ``import_mapping_tags`` FK into ``import_mapping`` is honoured
    transactionally by SQLite, so children are deleted before
    parents and the single ``BEGIN`` / ``COMMIT`` envelope keeps the
    rebuild atomic: either the whole table is the pre-rebuild set or
    the whole table is the post-rebuild set, never a window where a
    concurrent reader would see an empty ``import_mapping``.
    """
    con.execute("DELETE FROM import_mapping_tags")
    con.execute("DELETE FROM import_mapping")
    # sheet_mapping(_tags) are owned by sheet_mapping.py; seed never touches them.


def _insert_generic_pair_rows(pairs: list, insert_fn) -> None:  # type: ignore[type-arg]
    for c in pairs:
        sheet_category = c.name
        sheet_group = c.group or ""
        try:
            category_name = canonical_category_for_source(sheet_category, sheet_group)
        except ValueError:
            logger.exception(
                "No legacy mapping for (%r, %r); add a rule to imports.seed.",
                sheet_category,
                sheet_group,
            )
            raise
        tag_names = tags_for_source(sheet_category, sheet_group, 0)
        insert_fn(0, sheet_category, sheet_group, category_name, tag_names, None)


def _insert_per_year_event_rows(pairs: list, insert_fn) -> None:  # type: ignore[type-arg]
    for c in pairs:
        sheet_category = c.name
        sheet_group = c.group or ""
        category_name = canonical_category_for_source(sheet_category, sheet_group)
        for y in range(HISTORICAL_YEAR_FROM, HISTORICAL_YEAR_TO + 1):
            event_name = event_name_for_source(sheet_category, sheet_group, y)
            if event_name is None:
                continue
            tag_names = tags_for_source(sheet_category, sheet_group, y)
            insert_fn(y, sheet_category, sheet_group, category_name, tag_names, event_name)


def _insert_forward_projection_rows(
    con: sqlite3.Connection,
    cat_id_by_name: dict,
    insert_fn,
) -> None:  # type: ignore[type-arg]
    latest_year = max(
        (r.year for r in config.read_import_sources() if r.year > 0),
        default=0,
    )
    if not latest_year:
        return
    for cat_name, cat_id in cat_id_by_name.items():
        existing = con.execute(
            "SELECT 1 FROM import_mapping WHERE year = ? AND category_id = ? LIMIT 1",
            [latest_year, cat_id],
        ).fetchone()
        if existing:
            continue
        insert_fn(latest_year, cat_name, "", cat_name, (), None)


def _rebuild_import_mapping(
    con: sqlite3.Connection,
    id_maps: TaxonomyIdMaps,
    pairs: list[Category],
) -> dict:
    """Rebuild ``import_mapping`` + ``import_mapping_tags`` from discovered pairs.

    Assumes the caller has already called ``_purge_mapping_tables``
    inside the same write transaction (``BEGIN`` … ``COMMIT``). Tables
    are therefore empty on entry and every INSERT emitted below is
    made atomic with the purge.

    Steps:

    5. For each discovered ``(sheet_category, sheet_group)`` pair,
       derive category/tags via ``canonical_category_for_source`` +
       ``tags_for_source`` and insert a year=0 generic row.
    6. Apply every ``EXPLICIT_MAPPING_OVERRIDES`` row as-is.
    7. For each discovered pair, emit per-year rows when
       ``event_name_for_source`` returns a non-``None`` event name.
    8. Forward-projection: every hardcoded category gets at least one
       row in the latest configured ``import_sources`` year so
       ``POST /api/expenses`` can always resolve a sheet target.

    Returns ``{"mappings_created": N}``.
    """
    cat_id_by_name = id_maps.cat_id_by_name
    tag_id_by_name = id_maps.tag_id_by_name
    event_id_by_name = id_maps.event_id_by_name

    mapping_count = 0
    next_mapping_id = 1

    def insert_mapping(
        seed_year: int,
        sheet_category: str,
        sheet_group: str,
        category_name: str,
        tag_names: list[str] | tuple[str, ...],
        event_name: str | None,
    ) -> None:
        nonlocal mapping_count, next_mapping_id

        category_id = cat_id_by_name.get(category_name)
        if category_id is None:
            msg = f"Seeded mapping references unknown category {category_name!r}"
            raise ValueError(msg)
        event_id = None
        if event_name is not None:
            event_id = event_id_by_name.get(event_name)
            if event_id is None:
                msg = f"Seeded mapping references unknown event {event_name!r}"
                raise ValueError(msg)
        for t in tag_names:
            if t not in tag_id_by_name:
                msg = f"Seeded mapping references unknown tag {t!r}"
                raise ValueError(msg)
        existing = con.execute(
            "SELECT id FROM import_mapping"
            " WHERE year = ? AND sheet_category = ? AND sheet_group = ?",
            [seed_year, sheet_category, sheet_group],
        ).fetchone()
        if existing is not None:
            return
        mapping_id = next_mapping_id
        next_mapping_id += 1
        con.execute(
            "INSERT INTO import_mapping"
            " (id, year, sheet_category, sheet_group, category_id, event_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [mapping_id, seed_year, sheet_category, sheet_group, category_id, event_id],
        )
        mapping_count += 1
        for tag_id in sorted({tag_id_by_name[t] for t in tag_names}):
            con.execute(
                "INSERT INTO import_mapping_tags (mapping_id, tag_id) VALUES (?, ?)",
                [mapping_id, tag_id],
            )

    _insert_generic_pair_rows(pairs, insert_mapping)
    for row in EXPLICIT_MAPPING_OVERRIDES:
        insert_mapping(
            row.year,
            row.sheet_category,
            row.sheet_group,
            row.category,
            row.tags,
            row.event_name,
        )
    _insert_per_year_event_rows(pairs, insert_mapping)
    _insert_forward_projection_rows(con, cat_id_by_name, insert_mapping)

    return {"mappings_created": mapping_count}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_latest_import_source() -> int:
    """Validate the latest configured import source row.

    Returns the latest positive year. Raises if the row is missing
    fields the import pipeline relies on (``spreadsheet_id``,
    ``worksheet_name``, ``layout_key`` from ``KNOWN_LAYOUT_KEYS``).
    Reads ``.deploy/import_sources.json`` via
    ``config.read_import_sources``; runtime sheet logging is not
    involved here (it has its own ``DINARY_SHEET_LOGGING_SPREADSHEET``
    config and ``sheet_mapping`` table owned by ``sheet_mapping.py``).
    """
    sources = [r for r in config.read_import_sources() if r.year > 0]
    if not sources:
        msg = (
            ".deploy/import_sources.json has no positive-year entry; "
            f"nothing to import. {IMPORT_SOURCES_DOC_HINT}"
        )
        raise ValueError(msg)
    latest = max(r.year for r in sources)

    src: ImportSourceRow | None = next(
        (r for r in sources if r.year == latest),
        None,
    )
    if src is None:
        msg = f".deploy/import_sources.json row missing for latest year {latest}"
        raise ValueError(msg)
    if not src.spreadsheet_id:
        msg = f".deploy/import_sources.json year {latest} has empty spreadsheet_id"
        raise ValueError(msg)
    if not src.worksheet_name:
        msg = f".deploy/import_sources.json year {latest} has empty worksheet_name"
        raise ValueError(msg)
    if not src.layout_key or src.layout_key not in KNOWN_LAYOUT_KEYS:
        msg = (
            f".deploy/import_sources.json year {latest} has unsupported "
            f"layout_key {src.layout_key!r}; known: {sorted(KNOWN_LAYOUT_KEYS)}"
        )
        raise ValueError(msg)

    return latest


def _validate_import_coverage(con: sqlite3.Connection, latest_year: int) -> None:
    """Every category must have at least one ``import_mapping`` row in the latest year.

    This protects the bootstrap import pipeline (which needs
    year-scoped ``import_mapping`` coverage). Runtime sheet logging
    uses the separate ``sheet_mapping`` table driven by the
    hand-curated ``map`` worksheet (see ``sheet_mapping.py``), so a
    gap here does not break runtime logging.
    """
    rows = con.execute(
        "SELECT c.name FROM categories c"
        " WHERE c.is_active"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM import_mapping m"
        "   WHERE m.year = ? AND m.category_id = c.id"
        " )",
        [latest_year],
    ).fetchall()
    if rows:
        missing = [r[0] for r in rows]
        msg = (
            f"Import coverage gap: latest sheet year {latest_year} has no "
            f"import_mapping row for categories {missing}."
        )
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Public entry points — driven by ``inv import-config`` / ``inv import-catalog``
# ---------------------------------------------------------------------------


def seed_from_sheet(
    year: int | None = None,
    *,
    finalize: Callable[[sqlite3.Connection], dict] | None = None,
) -> dict:
    """Seed the catalog + ``import_mapping`` from the configured sheets.

    Idempotent: re-runnable on top of an existing DB. Used both for
    the fresh-bootstrap path and for incremental seeding during dev.

    ``finalize``: optional hook invoked *inside* the write transaction,
    after ``seed_classification_catalog`` and ``_rebuild_import_mapping``
    have applied changes but *before* we ``COMMIT``. Any dict it
    returns is merged into the summary. The hook exists so
    ``rebuild_config_from_sheets`` can validate coverage and bump
    ``catalog_version`` atomically with the catalog rebuild — a
    failure in any of those steps must roll back the catalog rebuild
    too, not leave the DB half-committed.

    NOTE: this path does NOT bump ``app_metadata.catalog_version`` on
    its own. By design only ``inv import-catalog`` (via
    ``rebuild_config_from_sheets``) touches the version (so PWA
    clients don't get invalidated by every routine ``import-config``
    run). When this function adds new mappings on top of an existing
    catalog without a ``finalize`` hook, it logs a warning so the
    operator knows the PWA caches will not refresh until the next
    ``inv import-catalog``.

    Raises ``ValueError`` with a pointer to the repo-root ``imports/``
    directory when ``.deploy/import_sources.json`` is absent or empty —
    this path is only reachable from import-specific invocations, so
    the error is actionable.
    """
    if year is None:
        year = date.today().year

    if not config.read_import_sources():
        msg = (
            "No import sources configured; create "
            f".deploy/import_sources.json. {IMPORT_SOURCES_DOC_HINT}"
        )
        raise ValueError(msg)

    storage.init_db()

    # Step 1: pull categories from each registered sheet. Pure HTTP
    # against Google Sheets; no DB lock held.
    pairs = _collect_categories()
    if not pairs:
        msg = (
            f"No categories discovered from .deploy/import_sources.json. {IMPORT_SOURCES_DOC_HINT}"
        )
        raise ValueError(msg)

    # Step 2: apply the catalog rows. Mapping-table purge and rebuild
    # run inside a single write transaction so concurrent readers
    # (inv sql --remote, report modules, the API) never observe an
    # empty ``import_mapping`` state: SQLite's WAL mode gives each
    # reader a snapshot at transaction-start, and the whole rebuild
    # is committed atomically.
    con = storage.get_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            _purge_mapping_tables(con)
            summary, id_maps = seed_classification_catalog(con, year=year)
            summary.update(_rebuild_import_mapping(con, id_maps, pairs))

            if finalize is not None:
                extra = finalize(con)
                if extra:
                    summary.update(extra)

            con.execute("COMMIT")
        except Exception:
            storage.best_effort_rollback(con, context="seed_from_sheet write")
            raise
        logger.info("Seed complete: %s", summary)

        if finalize is None and summary.get("mappings_created", 0) > 0:
            logger.warning(
                "import-config inserted %d new import_mapping row(s) without "
                "bumping catalog_version; run `inv import-catalog` to force "
                "PWA clients to refresh the catalog.",
                summary["mappings_created"],
            )
        return summary
    finally:
        con.close()


def rebuild_config_from_sheets() -> dict:
    """FK-safe in-place catalog sync from the configured sheets (``inv import-catalog``).

    Never deletes the DB file. Ledger tables (``expenses``,
    ``expense_tags``, ``sheet_logging_jobs``, ``income``) retain real
    FKs into the catalog and are left completely untouched.

    Sync steps (all inside one write transaction, driven by the
    ``seed_from_sheet`` finalize hook):

    * Run ``seed_classification_catalog``: deactivate every catalog
      row, upsert the new taxonomy by natural key (stable ids
      preserved, new ids for new vocabulary, retired rows stay
      ``is_active=FALSE``).
    * Rebuild ``import_mapping`` tables from scratch against the
      current active ids.
    * Validate that the latest configured year has ``import_mapping``
      coverage for every active category.
    * Hash-gate and monotonically bump ``catalog_version``: the
      pre-rebuild catalog hash is compared with the post-rebuild one;
      when they match, the rebuild was a no-op and we keep the
      existing version so PWA caches keep serving 304s.

    Returns a summary dict including ``previous_catalog_version``
    (value before the bump, never less than 1) and ``catalog_version``
    (the new value).
    """
    storage.init_db()

    previous_version = 0
    try:
        con = storage.get_connection()
        try:
            previous_version = get_catalog_version(con)
        finally:
            con.close()
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        # Only expected failure modes: sqlite3 errors (file corruption,
        # schema drift, locked DB), filesystem errors, and RuntimeError
        # raised by get_catalog_version when app_metadata is missing.
        # Anything else (KeyboardInterrupt, MemoryError) should propagate.
        logger.warning(
            "Could not read previous catalog_version (%s); defaulting to 0",
            exc.__class__.__name__,
        )
        previous_version = 0

    con = storage.get_connection()
    try:
        # Snapshot the catalog hash BEFORE ``seed_from_sheet`` mutates
        # the catalog tables. We use the same canonical state that
        # ``_commit_with_bump`` hashes, so the two
        # write paths share a single definition of "observable
        # catalog change". If a rebuild from the sheet is a genuine
        # no-op (same hardcoded groups, same remote mappings), the
        # hash survives unchanged and we skip the bump — this keeps
        # PWA clients' ETag-validated ``GET /api/catalog`` returning
        # 304 Not Modified across idempotent reseeds.
        before_hash = hash_catalog_state(con)
    finally:
        con.close()

    def finalize(write_con: sqlite3.Connection) -> dict:
        """Atomic post-rebuild steps (inside ``seed_from_sheet`` write txn).

        Validation and the version bump MUST be in the same
        transaction as the catalog rebuild — otherwise a validation
        failure here (e.g. the latest year losing import-mapping
        coverage for some category) would leave the catalog rebuilt
        but ``catalog_version`` unbumped.
        """
        latest_year = _validate_latest_import_source()
        _validate_import_coverage(write_con, latest_year)
        effective_previous = max(previous_version, get_catalog_version(write_con))
        after_hash = hash_catalog_state(write_con)
        if before_hash == after_hash:
            # No observable catalog change — skip the bump. The
            # PWA's cached snapshot is still valid; 304s on the
            # next ``GET /api/catalog`` save bandwidth and keep
            # the operator's offline queue unblocked.
            new_version = effective_previous
            logger.info(
                "rebuild_config_from_sheets: catalog hash unchanged; keeping catalog_version=%d",
                new_version,
            )
        else:
            new_version = _bump_catalog_version(write_con, previous=effective_previous)
        return {
            "catalog_version": new_version,
            "previous_catalog_version": effective_previous,
            "latest_import_year": latest_year,
            "catalog_version_changed": before_hash != after_hash,
        }

    summary = seed_from_sheet(finalize=finalize)

    # Phase 2: make sure the ``map`` worksheet tab exists with a
    # default-identity layout (one row per active category mapping
    # name->name). Idempotent; safe on every reseed. Network failure
    # here downgrades to a log warning — the catalog side is already
    # committed and the operator can re-run reload-map later.
    try:
        sheet_mapping.ensure_default_map_tab()
    except Exception:
        logger.exception(
            "ensure_default_map_tab failed; runtime 3D->2D mapping "
            "may be empty until the operator creates the map tab manually",
        )
    return summary
