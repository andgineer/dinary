"""Runtime classification taxonomy for ``dinary.db``.

This file is the authoritative source of truth for the runtime catalog
tables: ``category_groups``, ``categories``, ``tags``, and ``events``.
When this file disagrees with ``docs/src/ru/taxonomy.md``, the code
wins.

Phase 1 PWA hardcodes the same tag dictionary defined here. There is
no ``/api/tags`` endpoint.

Scope of this module
--------------------

Only the *runtime* catalog lives here — the hardcoded 3D vocabulary
(``ENTRY_GROUPS``, ``PHASE1_TAGS``, event constants, ``EXPLICIT_EVENTS``)
plus the FK-safe in-place upsert primitives and the single public
entry point ``seed_classification_catalog``. The ``import_mapping``
table, the legacy 2D→3D derivation rules, and the
``.deploy/import_sources.json``-driven category discovery all live
in ``dinary.imports.seed``. The direction of dependency is strictly
``imports.seed -> services.seed_config`` (never the reverse); runtime
code never reaches into ``imports.seed``, and non-import deployments
never import it.

Non-import bootstrap: ``bootstrap_catalog()`` provides a zero-Sheets
entry point that seeds only the hardcoded runtime taxonomy. This is
what ``inv bootstrap-catalog`` invokes on every deploy, so every
deployment lands with a populated catalog even when the operator has
no historical Google Sheets to import from.

FK-safe in-place sync
---------------------

``seed_classification_catalog`` never deletes the DB file and never
renumbers catalog ids. Ledger tables (``expenses``, ``expense_tags``,
``sheet_logging_jobs``, ``income``) carry real FKs into the catalog;
deleting and renumbering referenced rows would violate those FKs.

The sync algorithm:

1. Mark every catalog row ``is_active=FALSE``. Rows that reappear in
   the new taxonomy snapshot get flipped back to ``TRUE``; rows that
   don't stay ``FALSE`` and are hidden from the live API while
   remaining FK-valid targets for historical ledger rows.
2. Upsert groups / categories / tags / events by ``name`` (the
   natural key). Pre-existing rows keep their integer ``id``; new
   rows get a fresh ``id = max(id)+1``.
3. Return a summary dict + a ``TaxonomyIdMaps`` snapshot the caller
   needs to hand to any import-mapping rebuild step.
"""

import dataclasses
import json
import logging
import sqlite3
from datetime import date

from dinary.services import ledger_repo
from dinary.services.sql_loader import fetchall_as, load_sql

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class TaxonomyIdMaps:
    """``name -> id`` lookups returned by ``seed_classification_catalog``.

    The import-mapping rebuild in ``imports.seed._rebuild_import_mapping``
    consumes these to resolve ``category_name`` / ``tag_name`` /
    ``event_name`` references into stable integer ids. Exposing them
    as an explicit return value (instead of re-querying the DB)
    avoids an extra round of ``SELECT id FROM …`` and keeps the
    invariants from the upsert phase (ordering, stable ids) visible
    to the downstream caller.
    """

    group_id_by_title: dict[str, int]
    cat_id_by_name: dict[str, int]
    tag_id_by_name: dict[str, int]
    event_id_by_name: dict[str, int]


# ---------------------------------------------------------------------------
# Hardcoded taxonomy: category groups + categories
# ---------------------------------------------------------------------------

#: Phase-1 group ordering. (group_title, [category_name, ...]).
ENTRY_GROUPS: list[tuple[str, list[str]]] = [
    ("Еда", ["еда", "фрукты", "деликатесы", "алкоголь"]),
    ("Жильё", ["хозтовары", "аренда", "ремонт", "мебель", "бытовая техника"]),
    ("ЖКХ и сервисы", ["коммунальные", "мобильник", "интернет", "сервисы"]),
    ("Медицина", ["медицина", "лекарства"]),
    ("Красота и ЗОЖ", ["гигиена", "ЗОЖ"]),
    ("Спорт", ["спорт", "велосипед", "лыжи"]),
    ("Хобби и отдых", ["развлечения", "кафе", "гаджеты", "электроника", "инструменты"]),
    ("Транспорт", ["транспорт", "машина", "топливо"]),
    ("Знания и продуктивность", ["обучение", "продуктивность"]),
    ("Семья и личное", ["карманные", "подарки", "одежда"]),
    ("Государство", ["налог", "штрафы"]),
]

#: Set of all valid category names exposed by `GET /api/categories`.
TAXONOMY_CATEGORIES: frozenset[str] = frozenset(
    cat for _group, cats in ENTRY_GROUPS for cat in cats
)


# ---------------------------------------------------------------------------
# Hardcoded tag dictionary (Phase 1 fixed list)
# ---------------------------------------------------------------------------

#: Phase-1 fixed tag dictionary. Beneficiary + sphere-of-life axes from the
#: 4D model collapse into this flat list. The PWA hardcodes the same set.
#:
#: Vacation events auto-attach BOTH "отпуск" and "путешествия" — the two
#: tags are treated as equivalent triggers for the "путешествия" envelope
#: in the default ``map`` tab, but kept separate so downstream analytics
#: can distinguish "explicitly a vacation event" from "general travel
#: spend classified by tag alone".
PHASE1_TAGS: list[str] = [
    # beneficiary tags
    "собака",
    "Аня",
    "Лариса",
    "Андрей",
    # sphere-of-life tags
    "релокация",
    "профессиональное",
    "дача",
    "отпуск",
    "путешествия",
]

#: Tag names auto-attached by vacation events (both runtime POSTs and
#: historical imports). Kept as a module-level constant so seed,
#: historical import, and runtime writes agree on the same set.
VACATION_AUTO_TAGS: list[str] = ["отпуск", "путешествия"]


# ---------------------------------------------------------------------------
# Events: vacations per year + business trips (pre-2022) + relocation (2022+)
# ---------------------------------------------------------------------------

#: Synthetic vacation event prefix; one event per year covers full-year span.
SYNTHETIC_EVENT_PREFIX = "отпуск-"
#: Pre-2022 "командировка" envelope is a real business trip; one event per year.
BUSINESS_TRIP_EVENT_PREFIX = "командировка-"
#: Last historical year where "командировка" still meant a real trip.
BUSINESS_TRIP_EVENT_LAST_YEAR = 2021

#: Relocation is one long event with auto_attach_enabled=false (no auto-attach
#: on existing rows). 2022 is when the relocation to Serbia started.
RELOCATION_EVENT_NAME = "релокация-в-Сербию"
RELOCATION_EVENT_FROM = date(2022, 4, 1)
RELOCATION_EVENT_TO = date(2030, 12, 31)

#: Year range we pre-create vacation/business-trip events for.
HISTORICAL_YEAR_FROM = 2012
HISTORICAL_YEAR_TO = 2030

#: Last year for which we pre-create a synthetic "отпуск-YYYY" event.
#: Post-2026 vacations are entered manually (the operator creates an
#: explicit event with a human-readable name like "Доломиты Апрель").
#: The 2026 placeholder we do seed ends at ``VACATION_EVENT_2026_END``
#: — real 2026 vacations past that cut-over become manual events too.
VACATION_EVENT_YEAR_TO = 2026
VACATION_EVENT_2026_END = date(2026, 4, 20)


@dataclasses.dataclass(frozen=True, slots=True)
class ExplicitEvent:
    """One-off event row that the bootstrap importer references by name.

    Lives in the catalog (``events`` table in ``dinary.db``) so
    ``catalog_version`` reflects its presence; ``imports/expense_import.py``
    looks events up by name and never mutates the catalog directly.

    ``auto_tags`` is a list of tag names that must also exist in the
    catalog (``PHASE1_TAGS``); both the runtime ``POST /api/expenses``
    path and the historical importer union these tags into the
    expense's tag set whenever the event is attached.
    """

    name: str
    date_from: date
    date_to: date
    auto_attach_enabled: bool = False
    auto_tags: tuple[str, ...] = ()


#: Name of the one-off "Russia trip" event. Re-exported so
#: `imports/expense_import.py` can look it up by name without redefining
#: the literal.
RUSSIA_TRIP_EVENT_NAME = "поездка в Россию"

#: Events that don't fit the per-year vacation/business-trip pattern but are
#: still needed by the historical importer. Add new one-off events here so
#: `import-catalog` (and only `import-catalog`) creates them.
EXPLICIT_EVENTS: list[ExplicitEvent] = [
    ExplicitEvent(
        name=RUSSIA_TRIP_EVENT_NAME,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        auto_tags=tuple(VACATION_AUTO_TAGS),
    ),
]


# ---------------------------------------------------------------------------
# Group assignment for hardcoded categories
# ---------------------------------------------------------------------------


def _category_group_lookup() -> dict[str, str]:
    """Return {category_name: group_title}. Each category belongs to exactly one group."""
    out: dict[str, str] = {}
    for group_title, cats in ENTRY_GROUPS:
        for c in cats:
            if c in out:
                msg = f"Category {c!r} is listed in multiple groups in ENTRY_GROUPS"
                raise ValueError(msg)
            out[c] = group_title
    return out


# ---------------------------------------------------------------------------
# Upsert primitives
# ---------------------------------------------------------------------------


def _next_id(con: sqlite3.Connection, table: str) -> int:
    """Return the next available integer id for a catalog table."""
    row = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()  # noqa: S608
    return int(row[0]) + 1 if row else 1


def _upsert_category_group(
    con: sqlite3.Connection,
    *,
    name: str,
    sort_order: int,
) -> int:
    """UPSERT a category_group by natural key (name); return its stable id."""
    row = con.execute(
        "SELECT id FROM category_groups WHERE name = ?",
        [name],
    ).fetchone()
    if row is not None:
        gid = int(row[0])
        con.execute(
            "UPDATE category_groups SET sort_order = ?, is_active = TRUE WHERE id = ?",
            [sort_order, gid],
        )
        return gid
    gid = _next_id(con, "category_groups")
    con.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (?, ?, ?, TRUE)",
        [gid, name, sort_order],
    )
    return gid


def _upsert_category(
    con: sqlite3.Connection,
    *,
    name: str,
    group_id: int,
) -> int:
    """UPSERT a category by natural key (name); return its stable id.

    A single ``UPDATE`` is enough even when child rows in ``expenses``
    or mapping tables reference this category's id — updates on
    non-key columns are FK-safe.
    """
    row = con.execute(
        "SELECT id, group_id, is_active FROM categories WHERE name = ?",
        [name],
    ).fetchone()
    if row is None:
        cid = _next_id(con, "categories")
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (?, ?, ?, TRUE)",
            [cid, name, group_id],
        )
        return cid

    cid = int(row[0])
    existing_group = int(row[1]) if row[1] is not None else None
    existing_active = bool(row[2])

    if existing_group != group_id or not existing_active:
        con.execute(
            "UPDATE categories SET group_id = ?, is_active = TRUE WHERE id = ?",
            [group_id, cid],
        )
    return cid


def _upsert_event(  # noqa: PLR0913
    con: sqlite3.Connection,
    *,
    name: str,
    date_from: date,
    date_to: date,
    auto_attach_enabled: bool,
    auto_tags: tuple[str, ...] | list[str] = (),
) -> int:
    """UPSERT an event by natural key (name); return its stable id.

    ``auto_tags`` is stored as a JSON array on ``events.auto_tags``.
    The names must match ``tags.name`` rows at write time (runtime +
    historical write paths look them up by name; ``is_active = FALSE``
    on a tag is a "hide from the ручной пикер" affordance and does
    not block event-driven auto-attach). This helper does not validate
    that — seed explicitly orders tag upserts before event upserts so
    the invariant holds.
    """
    auto_tags_json = json.dumps(list(auto_tags), ensure_ascii=False)
    row = con.execute("SELECT id FROM events WHERE name = ?", [name]).fetchone()
    if row is not None:
        eid = int(row[0])
        con.execute(
            "UPDATE events SET date_from = ?, date_to = ?, auto_attach_enabled = ?,"
            " auto_tags = ?, is_active = TRUE WHERE id = ?",
            [date_from, date_to, auto_attach_enabled, auto_tags_json, eid],
        )
        return eid
    eid = _next_id(con, "events")
    con.execute(
        "INSERT INTO events"
        " (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
        " VALUES (?, ?, ?, ?, ?, TRUE, ?)",
        [eid, name, date_from, date_to, auto_attach_enabled, auto_tags_json],
    )
    return eid


def _upsert_tag(con: sqlite3.Connection, *, name: str) -> int:
    """UPSERT a tag by natural key (name); return its stable id."""
    row = con.execute("SELECT id FROM tags WHERE name = ?", [name]).fetchone()
    if row is not None:
        tid = int(row[0])
        con.execute("UPDATE tags SET is_active = TRUE WHERE id = ?", [tid])
        return tid
    tid = _next_id(con, "tags")
    con.execute(
        "INSERT INTO tags (id, name, is_active) VALUES (?, ?, TRUE)",
        [tid, name],
    )
    return tid


# ---------------------------------------------------------------------------
# Main seeding entry point — runtime taxonomy only
# ---------------------------------------------------------------------------


def seed_classification_catalog(  # noqa: PLR0915
    con: sqlite3.Connection,
    *,
    year: int | None = None,
) -> tuple[dict, TaxonomyIdMaps]:
    """FK-safe in-place sync of the runtime taxonomy into ``dinary.db``.

    Seeding order: deactivate-all -> category_groups -> categories ->
    tags -> events. Integer ids for pre-existing vocabulary are
    preserved; new vocabulary gets a fresh ``max(id)+1``. Rows present
    in the DB but absent from the new taxonomy snapshot stay
    ``is_active=FALSE`` so ledger rows keep a valid FK target while
    the live API hides them.

    Does NOT touch ``import_mapping`` / ``import_mapping_tags`` —
    those tables are owned by ``imports.seed._rebuild_import_mapping``
    and only relevant to deployments that actually import from
    Google Sheets. Non-import deployments (``inv bootstrap-catalog``,
    which calls ``bootstrap_catalog()`` below) never populate them.

    Returns ``(summary_dict, TaxonomyIdMaps)``. The summary includes
    counts of each catalog table; the id-maps expose the ``name -> id``
    lookups that ``_rebuild_import_mapping`` needs.
    """
    if year is None:
        year = date.today().year

    # 0. Deactivate everything; subsequent steps flip back active rows
    # in the new taxonomy snapshot. Rows that don't reappear stay
    # inactive and ledger FKs remain valid.
    con.execute("UPDATE category_groups SET is_active = FALSE")
    con.execute("UPDATE categories SET is_active = FALSE")
    con.execute("UPDATE events SET is_active = FALSE")
    con.execute("UPDATE tags SET is_active = FALSE")

    # 1. category_groups (stable ids by name)
    group_id_by_title: dict[str, int] = {}
    for sort_order, (title, _cats) in enumerate(ENTRY_GROUPS, start=1):
        group_id_by_title[title] = _upsert_category_group(
            con,
            name=title,
            sort_order=sort_order,
        )

    # 2. categories (stable ids by name)
    cat_id_by_name: dict[str, int] = {}
    cat_to_group = _category_group_lookup()
    for cat_name in cat_to_group:
        cat_id_by_name[cat_name] = _upsert_category(
            con,
            name=cat_name,
            group_id=group_id_by_title[cat_to_group[cat_name]],
        )
    # Also expose retired categories (is_active=FALSE) through the
    # in-memory map so any stray seed rule that names them can resolve
    # to the existing id rather than silently failing. Retired rows
    # deliberately keep their ids so mapping rebuilds can point at
    # them if a rule explicitly does (tests cover the rename path).
    for r in fetchall_as(ledger_repo.IdNameRow, con, load_sql("seed_load_categories.sql")):
        cat_id_by_name.setdefault(r.name, r.id)

    # 3. tags (stable ids by name). Upserted BEFORE events so that
    # auto_tags names stored on ``events.auto_tags`` reliably resolve
    # to live ``tags.name`` rows on every subsequent lookup.
    tag_id_by_name: dict[str, int] = {}
    for tag_name in PHASE1_TAGS:
        tag_id_by_name[tag_name] = _upsert_tag(con, name=tag_name)

    # 4. events (stable ids by name).
    event_id_by_name: dict[str, int] = {}
    vacation_auto_tags = tuple(VACATION_AUTO_TAGS)
    for y in range(HISTORICAL_YEAR_FROM, VACATION_EVENT_YEAR_TO + 1):
        name = f"{SYNTHETIC_EVENT_PREFIX}{y}"
        date_to_value = VACATION_EVENT_2026_END if y == VACATION_EVENT_YEAR_TO else date(y, 12, 31)
        event_id_by_name[name] = _upsert_event(
            con,
            name=name,
            date_from=date(y, 1, 1),
            date_to=date_to_value,
            auto_attach_enabled=True,
            auto_tags=vacation_auto_tags,
        )
    for y in range(HISTORICAL_YEAR_FROM, BUSINESS_TRIP_EVENT_LAST_YEAR + 1):
        name = f"{BUSINESS_TRIP_EVENT_PREFIX}{y}"
        event_id_by_name[name] = _upsert_event(
            con,
            name=name,
            date_from=date(y, 1, 1),
            date_to=date(y, 12, 31),
            auto_attach_enabled=True,
        )
    event_id_by_name[RELOCATION_EVENT_NAME] = _upsert_event(
        con,
        name=RELOCATION_EVENT_NAME,
        date_from=RELOCATION_EVENT_FROM,
        date_to=RELOCATION_EVENT_TO,
        auto_attach_enabled=False,
    )
    for ev in EXPLICIT_EVENTS:
        event_id_by_name[ev.name] = _upsert_event(
            con,
            name=ev.name,
            date_from=ev.date_from,
            date_to=ev.date_to,
            auto_attach_enabled=ev.auto_attach_enabled,
            auto_tags=ev.auto_tags,
        )

    summary = {
        "category_groups": len(group_id_by_title),
        "categories": len(cat_id_by_name),
        "events": len(event_id_by_name),
        "tags": len(tag_id_by_name),
    }
    id_maps = TaxonomyIdMaps(
        group_id_by_title=group_id_by_title,
        cat_id_by_name=cat_id_by_name,
        tag_id_by_name=tag_id_by_name,
        event_id_by_name=event_id_by_name,
    )
    return summary, id_maps


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def bootstrap_catalog(year: int | None = None) -> dict:
    """Non-destructive runtime-catalog bootstrap (no Google Sheets, no imports).

    Seeds (and keeps in sync) the hardcoded Phase-1 taxonomy:
    ``category_groups``, ``categories``, ``tags``, ``events``.
    Idempotent and FK-safe — re-running never renumbers ids, never
    drops FK-referenced rows, and never mutates ``import_mapping``
    / ``sheet_mapping``.

    This is what ``inv bootstrap-catalog`` invokes on every deploy.
    Non-import users (``.deploy/import_sources.json`` absent or empty)
    rely on this as their only catalog-population path; import users
    get it as the first step of ``inv import-catalog`` too.

    Does NOT bump ``app_metadata.catalog_version`` — that's reserved
    for ``rebuild_config_from_sheets`` (``inv import-catalog``) so
    routine bootstraps don't churn PWA caches. The very first run on
    a fresh DB still observes a version change because the row
    transitions from the schema-default ``0`` to the initial post-seed
    state; subsequent runs are proper no-ops.
    """
    ledger_repo.init_db()

    con = ledger_repo.get_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            summary, _ = seed_classification_catalog(con, year=year)
            con.execute("COMMIT")
        except Exception:
            ledger_repo.best_effort_rollback(con, context="bootstrap_catalog")
            raise
    finally:
        con.close()

    logger.info("bootstrap_catalog complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Catalog versioning
# ---------------------------------------------------------------------------


def _bump_catalog_version(con: sqlite3.Connection, *, previous: int) -> int:
    """Increment ``catalog_version`` on the seed path (``inv import-catalog``).

    One of the two write paths that touch ``catalog_version`` — the
    other is ``catalog_writer._commit_with_bump`` on the admin-API
    path. Both funnel through ``ledger_repo.set_catalog_version`` so
    future auditing hooks can intercept writes uniformly. See
    ``.plans/architecture.md`` §Catalog versioning.
    """
    new_version = previous + 1
    ledger_repo.set_catalog_version(con, new_version)
    return new_version


# ---------------------------------------------------------------------------
# Deprecated no-op shim (kept for back-compat with old callers)
# ---------------------------------------------------------------------------


def _rebuild_logging_mapping_from_latest_year(
    con: sqlite3.Connection,  # noqa: ARG001
    *,
    latest_year: int,  # noqa: ARG001
    cat_id_by_name: dict[str, int],  # noqa: ARG001
) -> int:
    """Deprecated no-op shim; runtime 3D->2D mapping lives in ``sheet_mapping``."""
    logger.warning(
        "_rebuild_logging_mapping_from_latest_year is a no-op in Phase 2; "
        "runtime 3D->2D routing is owned by sheet_mapping.py (sheet_mapping table).",
    )
    return 0
