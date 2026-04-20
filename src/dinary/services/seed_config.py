"""Seed data/dinary.duckdb with the Phase-1 3D classification catalog.

This file is the authoritative source of truth for the catalog tables
(``category_groups``, ``categories``, ``events``, ``tags``, and the
derived ``import_mapping`` table). When this file disagrees with
``docs/src/ru/taxonomy.md``, the code wins.

Phase 1 PWA hardcodes the same tag dictionary defined here. There is no
``/api/tags`` endpoint.

FK-safe in-place sync
---------------------

``rebuild_config_from_sheets`` (driven by ``inv import-catalog``) never
deletes the DB file and never renumbers catalog ids. Ledger tables
(``expenses``, ``expense_tags``, ``sheet_logging_jobs``, ``income``)
carry real FKs into the catalog; deleting and renumbering referenced
rows would violate those FKs.

The sync algorithm:

1. Preserve ``import_sources`` intact (it's the operator-configured
   source list, not a derived value).
2. Mark every catalog row ``is_active=FALSE``. Rows that reappear in
   the new taxonomy snapshot get flipped back to ``TRUE`` in step 3;
   rows that don't stay ``FALSE`` and are hidden from the live API
   while remaining FK-valid targets for historical ledger rows.
3. Upsert groups / categories / events / tags by ``name`` (the natural
   key). Pre-existing rows keep their integer ``id``; new rows get a
   fresh ``id = max(id)+1``.
4. Rebuild the ``import_mapping(_tags)`` table from scratch by
   DELETE + INSERT. Runtime 3D->2D routing for sheet logging now lives
   in the separate ``runtime_mapping`` table, populated exclusively
   from the hand-curated ``map`` tab in the logging spreadsheet (see
   ``runtime_map.py``); this seed pipeline no longer writes to it.
5. Bump ``app_metadata.catalog_version``.
"""

import dataclasses
import json
import logging
from datetime import date

import duckdb

from dinary.config import settings
from dinary.services import catalog_writer, duckdb_repo, runtime_map
from dinary.services.sheets import HEADER_ROWS, _cell, get_sheet
from dinary.services.sql_loader import fetchall_as, load_sql

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Category:
    """Lightweight (category, group) value object used by the seed pipeline."""

    name: str
    group: str


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
]


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


@dataclasses.dataclass(frozen=True, slots=True)
class ExplicitEvent:
    """One-off event row that the bootstrap importer references by name.

    Lives in the catalog (``events`` table in ``dinary.duckdb``) so
    ``catalog_version`` reflects its presence; ``imports/expense_import.py``
    looks events up by name and never mutates the catalog directly.
    """

    name: str
    date_from: date
    date_to: date
    auto_attach_enabled: bool = False


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
    ),
]


# ---------------------------------------------------------------------------
# Sheet import sources bootstrap
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ImportSourceSeedRow:
    year: int
    spreadsheet_id: str
    worksheet_name: str
    layout_key: str
    notes: str | None = None
    income_worksheet_name: str = ""
    income_layout_key: str = ""


_RUB_2012_LAST_YEAR = 2012
_RUB_2014_LAST_YEAR = 2014
_RUB_2016_LAST_YEAR = 2016
_RUB_6COL_LAST_YEAR = 2021
_RUB_FALLBACK_YEAR = 2022

#: Layout keys recognized by the historical-import / runtime-export code.
KNOWN_LAYOUT_KEYS: frozenset[str] = frozenset(
    {"default", "rub", "rub_fallback", "rub_6col", "rub_2016", "rub_2014", "rub_2012"},
)


def _default_layout_for_year(year: int) -> str:
    if year <= _RUB_2012_LAST_YEAR:
        return "rub_2012"
    if year <= _RUB_2014_LAST_YEAR:
        return "rub_2014"
    if year <= _RUB_2016_LAST_YEAR:
        return "rub_2016"
    if year <= _RUB_6COL_LAST_YEAR:
        return "rub_6col"
    if year == _RUB_FALLBACK_YEAR:
        return "rub_fallback"
    return "default"


def _bootstrap_import_sources() -> list[ImportSourceSeedRow]:
    if settings.import_sources_json:
        payload = json.loads(settings.import_sources_json)
        return [
            ImportSourceSeedRow(
                year=int(row["year"]),
                spreadsheet_id=row.get("spreadsheet_id", ""),
                worksheet_name=row.get("worksheet_name", ""),
                layout_key=row.get("layout_key", _default_layout_for_year(int(row["year"]))),
                notes=row.get("notes"),
                income_worksheet_name=row.get("income_worksheet_name", ""),
                income_layout_key=row.get("income_layout_key", ""),
            )
            for row in payload
        ]
    return []


def _ensure_import_sources(con: duckdb.DuckDBPyConnection) -> int:
    """Insert bootstrap rows only if `import_sources` is empty."""
    count_row = con.execute("SELECT COUNT(*) FROM import_sources").fetchone()
    if count_row and count_row[0]:
        return 0
    rows = _bootstrap_import_sources()
    if not rows:
        return 0
    for row in rows:
        con.execute(
            "INSERT INTO import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes,"
            "  income_worksheet_name, income_layout_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                row.year,
                row.spreadsheet_id,
                row.worksheet_name,
                row.layout_key,
                row.notes,
                row.income_worksheet_name,
                row.income_layout_key,
            ],
        )
    return len(rows)


def _load_existing_import_sources(
    con: duckdb.DuckDBPyConnection,
) -> list[ImportSourceSeedRow]:
    """Snapshot ``import_sources`` rows from the single DB.

    Kept as a helper so ``rebuild_config_from_sheets`` can capture the
    operator-configured source list before the catalog rebuild and
    reapply it afterwards — defending against accidental edits to the
    catalog hardcodes wiping out a configured source row.
    """
    try:
        rows = con.execute(
            "SELECT year, spreadsheet_id, worksheet_name, layout_key, notes,"
            " income_worksheet_name, income_layout_key"
            " FROM import_sources ORDER BY year",
        ).fetchall()
    except duckdb.Error:
        return []
    return [
        ImportSourceSeedRow(
            year=row[0],
            spreadsheet_id=row[1],
            worksheet_name=row[2],
            layout_key=row[3],
            notes=row[4],
            income_worksheet_name=row[5] or "",
            income_layout_key=row[6] or "",
        )
        for row in rows
    ]


def _restore_import_sources(
    con: duckdb.DuckDBPyConnection,
    rows: list[ImportSourceSeedRow],
) -> None:
    if not rows:
        return
    for row in rows:
        con.execute(
            "INSERT INTO import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes,"
            "  income_worksheet_name, income_layout_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT DO UPDATE SET"
            "  spreadsheet_id = EXCLUDED.spreadsheet_id,"
            "  worksheet_name = EXCLUDED.worksheet_name,"
            "  layout_key = EXCLUDED.layout_key,"
            "  notes = EXCLUDED.notes,"
            "  income_worksheet_name = EXCLUDED.income_worksheet_name,"
            "  income_layout_key = EXCLUDED.income_layout_key",
            [
                row.year,
                row.spreadsheet_id,
                row.worksheet_name,
                row.layout_key,
                row.notes,
                row.income_worksheet_name,
                row.income_layout_key,
            ],
        )


# ---------------------------------------------------------------------------
# Legacy (sheet_category, sheet_group) -> 3D derivation rules
#
# These rules used to live in seed/import code split between beneficiary,
# sphere, and event helpers. In the 3D model they collapse into a single
# (category_name, tag_set, event_name) tuple per legacy pair, and the result
# is pre-baked into `import_mapping` + `import_mapping_tags` rows during seed.
# `imports/expense_import.py` only does table lookups at import time.
# ---------------------------------------------------------------------------

LEGACY_FOOD_CATEGORY = "еда&бытовые"
BULAVKI_CATEGORY = "булавки"

#: envelope value -> beneficiary tag name
_BENEFICIARY_BY_ENVELOPE: dict[str, str] = {
    "собака": "собака",
    "ребенок": "Аня",
    "лариса": "Лариса",
}

#: envelope value -> sphere-of-life tag name
_SPHERE_BY_ENVELOPE: dict[str, str] = {
    "релокация": "релокация",
    "профессиональное": "профессиональное",
    "дача": "дача",
}

#: source_type -> canonical category name (lowercased keys)
_CATEGORY_BY_SOURCE_TYPE: dict[str, str] = {
    "еда&бытовые": "еда",
    "еда": "еда",
    "фрукты": "фрукты",
    "деликатесы": "деликатесы",
    "алкоголь": "алкоголь",
    "бытовые": "хозтовары",
    "хозтовары": "хозтовары",
    "household": "хозтовары",
    "обустройство": "хозтовары",
    "аренда": "аренда",
    "ремонт": "ремонт",
    "ремонт комнаты ани": "ремонт",
    "мебель": "мебель",
    "бытовая техника": "бытовая техника",
    "техника": "бытовая техника",
    "коммунальные": "коммунальные",
    "мобильник": "мобильник",
    "интернет": "интернет",
    "сервисы": "сервисы",
    "медицина": "медицина",
    "лекарства": "лекарства",
    "страхование жизни": "медицина",
    "очки": "медицина",
    "гигиена": "гигиена",
    "стрижка": "гигиена",
    "косметика": "гигиена",
    "зож": "ЗОЖ",
    "бад": "ЗОЖ",
    "спорт": "спорт",
    "велосипед": "велосипед",
    "лыжи": "лыжи",
    "развлечения": "развлечения",
    "кафе": "кафе",
    "гаджеты": "гаджеты",
    "электроника": "электроника",
    "инструменты": "инструменты",
    "avito": "гаджеты",
    "транспорт": "транспорт",
    "танспорт": "транспорт",
    "pubtransport": "транспорт",
    "машина": "машина",
    "топливо": "топливо",
    "обучение": "обучение",
    "продуктивность": "продуктивность",
    "работа": "продуктивность",
    "professional": "продуктивность",
    "учеба": "обучение",
    "школа": "обучение",
    "курсы": "обучение",
    "карманные": "карманные",
    "собака": "карманные",
    "подарки": "подарки",
    "социальное": "подарки",
    "социализация": "подарки",
    "страховка": "коммунальные",
    "одежда": "одежда",
    "банк": "сервисы",
    "налог": "налог",
    "налоги": "налог",
    "штрафы": "штрафы",
    "приложения": "продуктивность",
    "parallels": "развлечения",
    "wellness": "гигиена",
    "welness": "гигиена",
}

_EDA_SUB: dict[str, str] = {
    "кафе": "кафе",
    "lunch": "кафе",
    "общепит": "кафе",
    "ресторан": "кафе",
    "кофе": "кафе",
    "ужин": "кафе",
    "перекусы": "кафе",
    "фрукты": "фрукты",
    "деликатесы": "деликатесы",
    "алкоголь": "алкоголь",
}

_COMMUNAL_SUB: dict[str, str] = {
    "mobile": "мобильник",
    "phone": "мобильник",
    "мобильник": "мобильник",
    "мобильный": "мобильник",
    "internet": "интернет",
    "интернет": "интернет",
    "video": "сервисы",
    "skype": "сервисы",
}

_MASHINA_SUB: dict[str, str] = {
    "gas": "топливо",
    "топливо": "топливо",
    "такси": "транспорт",
    "налог": "налог",
    "налоги": "налог",
    "штраф": "штрафы",
    "штрафы": "штрафы",
    "страховка": "машина",
    "gadgets": "гаджеты",
}

_DACHA_SUB: dict[str, str] = {
    "": "ремонт",
    "ремонт": "ремонт",
    "колодец": "ремонт",
    "electro": "ремонт",
    "diy": "ремонт",
    "краски": "ремонт",
    "мебель": "мебель",
    "инструменты": "инструменты",
    "налог": "налог",
    "налоги": "налог",
    "свет": "коммунальные",
    "электро": "коммунальные",
    "электроэнергия": "коммунальные",
    "сигнализация": "коммунальные",
    "сингнализация": "коммунальные",
    "страховка": "коммунальные",
    "коммунальные": "коммунальные",
    "интернет": "интернет",
    "internet": "интернет",
    "mobile": "мобильник",
    "транспорт": "транспорт",
    "техника": "бытовая техника",
}

_RAZVL_SUB: dict[str, str] = {
    "спорт": "спорт",
    "skiitime": "лыжи",
    "wellness": "гигиена",
    "diy": "гаджеты",
    "dyi": "гаджеты",
    "gadgets": "гаджеты",
    "подарок": "подарки",
    "подарки": "подарки",
    "отпуск": "аренда",
    "игрушки": "развлечения",
    "игрушка": "развлечения",
    "ресторан": "кафе",
    "books": "обучение",
    "книги": "обучение",
    "журналы": "обучение",
    "apps": "продуктивность",
}

_SKI_ENVELOPES: frozenset[str] = frozenset(
    {"skiitime", "skitime", "лыжи", "лыжероллеры"},
)

_VACATION_CATEGORY_BY_ENVELOPE: dict[str, str] = {
    "": "аренда",
    "проживание": "аренда",
    "жилье": "аренда",
    "отель": "аренда",
    "еда": "кафе",
    "кафе": "кафе",
    "кофе": "кафе",
    "перекусы": "кафе",
    "ужин": "кафе",
    "продукты": "еда",
    "магазин": "еда",
    "фрукты": "фрукты",
    "транспорт": "транспорт",
    "билеты": "транспорт",
    "такси": "транспорт",
    "перелет": "транспорт",
    "развлечения": "развлечения",
    "музей": "развлечения",
    "шезлонги": "развлечения",
    "экскурсии": "развлечения",
    "подарки": "подарки",
    "игрушки и подарки": "подарки",
    "медицина": "медицина",
    "мобильный": "мобильник",
    "sim-travel": "мобильник",
    "duty free": "алкоголь",
    "сейф": "сервисы",
    "фото": "развлечения",
    "чемодан": "одежда",
    "ларисе": "подарки",
}

_KOMANDIROVKA_CATEGORY_BY_ENVELOPE: dict[str, str] = {
    "": "аренда",
    "аренда": "аренда",
    "обустройство": "бытовая техника",
    "транспорт": "транспорт",
    "еда": "еда",
    "развлечения": "развлечения",
    "внж": "налог",
    "банк": "сервисы",
    "комуннальные": "коммунальные",
    "коммунальные": "коммунальные",
    "налог": "налог",
    "обучение": "обучение",
    "поиск квартиры": "сервисы",
    "школа": "обучение",
}

_VACATION_ENVELOPES: frozenset[str] = frozenset(
    {"путешествия", "sim-travel", "отпуск", "travel"},
)


def canonical_category_for_source(  # noqa: C901, PLR0911, PLR0912
    source_type: str,
    source_envelope: str,
) -> str:
    """Map a legacy `(source_type, source_envelope)` to a canonical category name.

    Public because `imports/expense_import.py` reads the same legacy
    `(source_type, source_envelope)` cells and must apply the same mapping
    rules at import time. Keeping a private alias would silently drift if
    one caller's signature changes without the other's.
    """
    source_lower = source_type.lower().strip()
    envelope_lower = source_envelope.lower().strip()

    if source_type == BULAVKI_CATEGORY:
        return "карманные"
    if source_lower == "приложения":
        # Empty envelope = entertainment apps, "профессиональное" envelope = work
        # productivity tools. Without this split the year=0 fallback collapses
        # both into one row and conflicts with EXPLICIT_MAPPING_OVERRIDES.
        if not envelope_lower:
            return "развлечения"
        return "продуктивность"
    if source_lower in {"wellness", "welness"}:
        if envelope_lower in _SKI_ENVELOPES:
            return "лыжи"
        if envelope_lower == "спорт":
            return "спорт"
        if envelope_lower in {"yazio"}:
            return "ЗОЖ"
        return "гигиена"
    if source_lower == "отпуск":
        return _VACATION_CATEGORY_BY_ENVELOPE.get(envelope_lower, "аренда")
    if source_lower == "командировка":
        return _KOMANDIROVKA_CATEGORY_BY_ENVELOPE.get(envelope_lower, "аренда")
    if source_lower in {"еда", LEGACY_FOOD_CATEGORY}:
        return _EDA_SUB.get(envelope_lower, "еда")
    if source_lower == "коммунальные":
        return _COMMUNAL_SUB.get(envelope_lower, "коммунальные")
    if source_lower == "машина":
        return _MASHINA_SUB.get(envelope_lower, "машина")
    if source_lower == "дача":
        return _DACHA_SUB.get(envelope_lower, "хозтовары")
    if source_lower == "развлечения":
        if envelope_lower in _SKI_ENVELOPES:
            return "лыжи"
        return _RAZVL_SUB.get(envelope_lower, "развлечения")
    if source_lower == "спорт":
        if envelope_lower in _SKI_ENVELOPES:
            return "лыжи"
        return "спорт"
    if source_lower == "household":
        if envelope_lower in {"налог", "налоги"}:
            return "налог"
        if envelope_lower == "мебель":
            return "мебель"
        if envelope_lower == "страховка":
            return "коммунальные"
        if envelope_lower in {"diy", "dyi"}:
            return "гаджеты"
    if source_lower in _CATEGORY_BY_SOURCE_TYPE:
        return _CATEGORY_BY_SOURCE_TYPE[source_lower]
    if source_type in TAXONOMY_CATEGORIES:
        return source_type
    msg = (
        f"Unmapped sheet (sheet_category={source_type!r}, sheet_group={source_envelope!r}). "
        "Add a rule to seed_config._CATEGORY_BY_SOURCE_TYPE or to the legacy-derivation tables."
    )
    raise ValueError(msg)


def tags_for_source(  # noqa: C901
    source_type: str,
    source_envelope: str,
    year: int,
) -> list[str]:
    """Return tag names for a legacy `(source_type, source_envelope)` pair.

    Combines beneficiary + sphere-of-life axes from the old 4D model into
    one tag set. Year-aware rules (e.g. "командировка" relocation tag from
    2022 onward) are resolved here so per-year `import_mapping` rows can
    carry the right tags.
    """
    tags: set[str] = set()
    envelope_lower = source_envelope.lower().strip()
    source_lower = source_type.lower().strip()

    if source_envelope in _BENEFICIARY_BY_ENVELOPE:
        tags.add(_BENEFICIARY_BY_ENVELOPE[source_envelope])
    if source_type == BULAVKI_CATEGORY:
        tags.add("Лариса")
    if source_lower == "собака":
        tags.add("собака")
    if source_type == "Ремонт комнаты Ани":
        tags.add("Аня")
    if source_lower == "школа":
        tags.add("Аня")

    if source_envelope in _SPHERE_BY_ENVELOPE:
        tags.add(_SPHERE_BY_ENVELOPE[source_envelope])
    if source_lower == "дача":
        tags.add("дача")

    if year != 0:
        is_komandirovka = source_lower == "командировка" or envelope_lower == "командировка"
        if is_komandirovka:
            if year > BUSINESS_TRIP_EVENT_LAST_YEAR:
                tags.add("релокация")
            else:
                tags.add("профессиональное")

    return sorted(tags)


def event_name_for_source(source_type: str, source_envelope: str, year: int) -> str | None:
    """Return the synthetic event name (if any) for a legacy `(source, envelope)` pair.

    Public because `imports/expense_import.py` must derive the same per-year event
    name when promoting historical rows that lack an explicit mapping.
    Returns None when the pair has no event association (the common
    case — only vacations and pre-cutover business trips synthesize an
    event from this layer).
    """
    source_lower = source_type.lower().strip()
    envelope_lower = source_envelope.lower().strip()
    if source_lower == "отпуск":
        return f"{SYNTHETIC_EVENT_PREFIX}{year}"
    if envelope_lower in _VACATION_ENVELOPES:
        return f"{SYNTHETIC_EVENT_PREFIX}{year}"
    is_komandirovka = source_lower == "командировка" or envelope_lower == "командировка"
    if is_komandirovka and year <= BUSINESS_TRIP_EVENT_LAST_YEAR:
        return f"{BUSINESS_TRIP_EVENT_PREFIX}{year}"
    return None


# ---------------------------------------------------------------------------
# Per-year explicit overrides (rare cases the generic derivation gets wrong)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class MappingSeedRow:
    year: int
    sheet_category: str
    sheet_group: str
    category: str
    tags: tuple[str, ...] = ()
    event_name: str | None = None


EXPLICIT_MAPPING_OVERRIDES: list[MappingSeedRow] = [
    # ("приложения", "") and ("приложения", "профессиональное") are now resolved
    # entirely by canonical_category_for_source + tags_for_source, so the
    # year=0 entries that used to live here are intentionally absent.
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
# Discovery: pull category pairs from configured sheets to feed seed_mapping
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


def _collect_categories(con: duckdb.DuckDBPyConnection) -> list[Category]:
    """Collect (category, group) pairs across every registered import source."""
    seen: set[tuple[str, str]] = set()
    categories: list[Category] = []

    sources = con.execute(
        "SELECT spreadsheet_id, worksheet_name, layout_key FROM import_sources ORDER BY year",
    ).fetchall()
    for spreadsheet_id, worksheet_name, layout_key in sources:
        for row in _load_categories_for_sheet(spreadsheet_id, worksheet_name, layout_key):
            key = (row.name, row.group)
            if key not in seen:
                seen.add(key)
                categories.append(row)
    return categories


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
# Main seeding entry points
# ---------------------------------------------------------------------------


def _next_id(con: duckdb.DuckDBPyConnection, table: str) -> int:
    """Return the next available integer id for a catalog table."""
    row = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()  # noqa: S608
    return int(row[0]) + 1 if row else 1


def _upsert_category_group(
    con: duckdb.DuckDBPyConnection,
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
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    group_id: int,
) -> int:
    """UPSERT a category by natural key (name); return its stable id.

    DuckDB 1.5 caveat: any UPDATE that touches ``categories.group_id``
    (an FK column) is internally implemented as DELETE+INSERT of the
    row, which fails FK validation when ``expenses.category_id`` (or
    the mapping tables) already reference that category id. Non-FK
    columns like ``is_active`` update in place and are safe. So:

    * always flip ``is_active`` back to TRUE in its own UPDATE — this
      always succeeds, even on FK-referenced rows;
    * only issue a ``group_id`` UPDATE when the value actually
      changed, and only when no FK-referenced child row would block
      it. When the category is already referenced by a ledger row,
      ``group_id`` is left pinned to whatever the DB has: a natural
      consequence of ``is_active=FALSE`` being the only way Phase 1
      retires catalog rows.
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

    if not existing_active:
        con.execute(
            "UPDATE categories SET is_active = TRUE WHERE id = ?",
            [cid],
        )

    if existing_group != group_id:
        try:
            con.execute(
                "UPDATE categories SET group_id = ? WHERE id = ?",
                [group_id, cid],
            )
        except duckdb.ConstraintException:
            # DuckDB 1.5 refuses to DELETE+INSERT an FK-referenced
            # row even when the target FK column is unchanged at
            # SQL level. Leaving group_id pinned is the lesser evil:
            # the category is still reachable via its stable id and
            # the live taxonomy (ENTRY_GROUPS) rarely re-homes a
            # name between groups in production anyway.
            logger.warning(
                "Cannot re-home category %r (id=%d) from group_id=%s to %s: "
                "row is FK-referenced and DuckDB 1.5 cannot UPDATE FK columns "
                "on such rows. Leaving group_id unchanged.",
                name,
                cid,
                existing_group,
                group_id,
            )
    return cid


def _upsert_event(  # noqa: PLR0913
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    date_from: date,
    date_to: date,
    auto_attach_enabled: bool,
) -> int:
    """UPSERT an event by natural key (name); return its stable id."""
    row = con.execute("SELECT id FROM events WHERE name = ?", [name]).fetchone()
    if row is not None:
        eid = int(row[0])
        con.execute(
            "UPDATE events SET date_from = ?, date_to = ?, auto_attach_enabled = ?,"
            " is_active = TRUE WHERE id = ?",
            [date_from, date_to, auto_attach_enabled, eid],
        )
        return eid
    eid = _next_id(con, "events")
    con.execute(
        "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
        " VALUES (?, ?, ?, ?, ?, TRUE)",
        [eid, name, date_from, date_to, auto_attach_enabled],
    )
    return eid


def _upsert_tag(con: duckdb.DuckDBPyConnection, *, name: str) -> int:
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


def _purge_mapping_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Clear all mapping rows; they are rebuilt from current active taxonomy.

    Ledger tables do NOT FK into mapping tables, so this is safe under
    FKs. Mapping tables are catalog-side derived state: rename/retire
    of a taxonomy row in step 3 must re-point any affected mapping row
    onto the new active id, and doing that by DELETE+INSERT is simpler
    and more correct than tracking per-row deltas.

    MUST be called outside an open write transaction. DuckDB 1.5 does
    not see intra-transaction DELETEs in the FK index, so a ``DELETE
    FROM import_mapping_tags`` followed by ``DELETE FROM import_mapping``
    inside a single ``BEGIN``/``COMMIT`` block raises a FK violation
    on the second statement. Running each DELETE in its own implicit
    auto-commit transaction sidesteps the limitation. Losing
    transactional atomicity here is acceptable because mapping tables
    are pure derived state: if the subsequent rebuild crashes we end
    up with empty mapping tables, which is the same state a fresh
    migration would produce and the very next seed run would
    re-populate.
    """
    con.execute("DELETE FROM import_mapping_tags")
    con.execute("DELETE FROM import_mapping")
    # runtime_mapping(_tags) are owned by runtime_map.py; seed never touches them.


def seed_classification_catalog(  # noqa: C901, PLR0915, PLR0912
    con: duckdb.DuckDBPyConnection,
    *,
    year: int | None = None,
    discovered_pairs: list[Category] | None = None,
) -> dict:
    """FK-safe in-place sync of the 3D taxonomy into ``dinary.duckdb``.

    Seeding order: deactivate-all -> category_groups -> categories ->
    events -> tags -> rebuild mapping tables. Integer ids for
    pre-existing vocabulary are preserved; new vocabulary gets a fresh
    ``max(id)+1``. Rows present in the DB but absent from the new
    taxonomy snapshot stay ``is_active=FALSE`` so ledger rows keep a
    valid FK target while the live API hides them.

    ``discovered_pairs`` is the union of legacy
    ``(sheet_category, sheet_group)`` pairs across configured import
    sources; it drives year=0 mapping rows.
    """
    if year is None:
        year = date.today().year

    # 0. Deactivate everything; step 3 will flip back active rows in the
    # new taxonomy snapshot. Rows that don't reappear stay inactive and
    # ledger FKs remain valid.
    con.execute("UPDATE category_groups SET is_active = FALSE")
    con.execute("UPDATE categories SET is_active = FALSE")
    con.execute("UPDATE events SET is_active = FALSE")
    con.execute("UPDATE tags SET is_active = FALSE")

    # NOTE: mapping tables must have been purged by the caller BEFORE
    # opening the main transaction (see ``_purge_mapping_tables``
    # docstring for the DuckDB 1.5 FK-in-txn limitation that forces
    # this ordering).

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
    for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_categories.sql")):
        cat_id_by_name.setdefault(r.name, r.id)

    # 3. events (stable ids by name)
    event_id_by_name: dict[str, int] = {}
    for y in range(HISTORICAL_YEAR_FROM, HISTORICAL_YEAR_TO + 1):
        name = f"{SYNTHETIC_EVENT_PREFIX}{y}"
        event_id_by_name[name] = _upsert_event(
            con,
            name=name,
            date_from=date(y, 1, 1),
            date_to=date(y, 12, 31),
            auto_attach_enabled=True,
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
        )

    # 4. tags (stable ids by name)
    tag_id_by_name: dict[str, int] = {}
    for tag_name in PHASE1_TAGS:
        tag_id_by_name[tag_name] = _upsert_tag(con, name=tag_name)

    # 5-8. Mapping rebuild. Tables were wiped in step 0, so we insert
    # fresh rows here with sequential ids; the (year, sheet_category,
    # sheet_group) UNIQUE constraint de-duplicates collisions within
    # this rebuild.
    mapping_count = 0
    next_mapping_id = 1

    def insert_mapping(  # noqa: PLR0913
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

        # UNIQUE (year, sheet_category, sheet_group); step 8 may revisit
        # the same triple via canonical forward-projection. Skip duplicates
        # silently — the first insert wins.
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

    pairs = discovered_pairs or []
    for c in pairs:
        sheet_category = c.name
        sheet_group = c.group or ""
        try:
            category_name = canonical_category_for_source(sheet_category, sheet_group)
        except ValueError:
            logger.exception(
                "No legacy mapping for (%r, %r); add a rule to seed_config.",
                sheet_category,
                sheet_group,
            )
            raise
        tag_names = tags_for_source(sheet_category, sheet_group, 0)
        insert_mapping(0, sheet_category, sheet_group, category_name, tag_names, None)

    # 6. import_mapping (per-year explicit overrides)
    for row in EXPLICIT_MAPPING_OVERRIDES:
        insert_mapping(
            row.year,
            row.sheet_category,
            row.sheet_group,
            row.category,
            row.tags,
            row.event_name,
        )

    # 7. per-year synthetic event mappings derived from generic pairs (e.g.
    # "отпуск-2026" event for a vacation pair). Only emit when the year-aware
    # derivation actually produces an event different from the generic row.
    # `canonical_category_for_source` cannot raise here: step 5 above iterates
    # the same `pairs` and re-raises on every unmapped pair, so by the time we
    # reach step 7 every pair is known to resolve.
    for c in pairs:
        sheet_category = c.name
        sheet_group = c.group or ""
        category_name = canonical_category_for_source(sheet_category, sheet_group)
        for y in range(HISTORICAL_YEAR_FROM, HISTORICAL_YEAR_TO + 1):
            event_name = event_name_for_source(sheet_category, sheet_group, y)
            if event_name is None:
                continue
            tag_names = tags_for_source(sheet_category, sheet_group, y)
            insert_mapping(y, sheet_category, sheet_group, category_name, tag_names, event_name)

    # 8. Forward-projection coverage: every hardcoded category needs at least
    # one row in the latest sheet year so `POST /api/expenses` can always
    # resolve a sheet target. Emit a default-landing mapping
    # (sheet_category=<category_name>, sheet_group="") for the latest year.
    latest_row = con.execute(
        "SELECT MAX(year) FROM import_sources WHERE year > 0",
    ).fetchone()
    latest_year = int(latest_row[0]) if latest_row and latest_row[0] is not None else 0
    if latest_year:
        for cat_name, cat_id in cat_id_by_name.items():
            existing = con.execute(
                "SELECT 1 FROM import_mapping WHERE year = ? AND category_id = ? LIMIT 1",
                [latest_year, cat_id],
            ).fetchone()
            if existing:
                continue
            insert_mapping(latest_year, cat_name, "", cat_name, (), None)

    # Runtime 3D->2D mapping used to be rebuilt here from the latest-year
    # import_mapping rows. Phase 2 replaced that with ``runtime_mapping``,
    # owned exclusively by ``runtime_map.py`` and sourced from a
    # hand-curated ``map`` worksheet tab. Seed never writes to it — the
    # drain loop pulls a fresh copy on its own schedule.

    return {
        "category_groups": len(group_id_by_title),
        "categories": len(cat_id_by_name),
        "events": len(event_id_by_name),
        "tags": len(tag_id_by_name),
        "mappings_created": mapping_count,
    }


# Deprecated: kept as a public no-op shim for back-compat with old
# callers / tests that still import the symbol. Runtime 3D->2D mapping
# moved to ``runtime_map.py`` (``runtime_mapping`` table, hand-curated
# ``map`` worksheet). This function intentionally does nothing and
# returns zero so any accidental call site fails loudly in review
# rather than silently diverging from the new source of truth.
def _rebuild_logging_mapping_from_latest_year(
    con: duckdb.DuckDBPyConnection,  # noqa: ARG001
    *,
    latest_year: int,  # noqa: ARG001
    cat_id_by_name: dict[str, int],  # noqa: ARG001
) -> int:
    """Deprecated no-op shim. See module docstring."""
    logger.warning(
        "_rebuild_logging_mapping_from_latest_year is a no-op in Phase 2; "
        "runtime 3D->2D routing is owned by runtime_map.py (runtime_mapping table).",
    )
    return 0


# ---------------------------------------------------------------------------
# Validation: catch silent breakage before the server boots
# ---------------------------------------------------------------------------


def _validate_latest_import_source(con: duckdb.DuckDBPyConnection) -> int:
    """Validate the latest configured import_sources row.

    Returns the latest positive year. Raises if the row is missing fields
    the import pipeline relies on (spreadsheet_id, worksheet_name,
    layout_key from KNOWN_LAYOUT_KEYS). Runtime sheet logging is no
    longer involved here — it has its own separate spreadsheet config
    (DINARY_SHEET_LOGGING_SPREADSHEET) and its own table
    (runtime_mapping, owned by runtime_map.py).
    """
    row = con.execute(
        "SELECT MAX(year) FROM import_sources WHERE year > 0",
    ).fetchone()
    if not row or row[0] is None:
        msg = "import_sources has no positive year; nothing to import"
        raise ValueError(msg)
    latest = int(row[0])

    src = con.execute(
        "SELECT spreadsheet_id, worksheet_name, layout_key FROM import_sources WHERE year = ?",
        [latest],
    ).fetchone()
    if src is None:
        msg = f"import_sources row missing for latest year {latest}"
        raise ValueError(msg)
    spreadsheet_id, worksheet_name, layout_key = src
    if not spreadsheet_id:
        msg = f"import_sources year {latest} has empty spreadsheet_id"
        raise ValueError(msg)
    if not worksheet_name:
        msg = f"import_sources year {latest} has empty worksheet_name"
        raise ValueError(msg)
    if not layout_key or layout_key not in KNOWN_LAYOUT_KEYS:
        msg = (
            f"import_sources year {latest} has unsupported layout_key {layout_key!r}; "
            f"known: {sorted(KNOWN_LAYOUT_KEYS)}"
        )
        raise ValueError(msg)

    return latest


def _validate_import_coverage(con: duckdb.DuckDBPyConnection, latest_year: int) -> None:
    """Every category must have at least one import_mapping row in the latest year.

    This protects the bootstrap import pipeline (which needs year-scoped
    import_mapping coverage). Runtime sheet logging uses the separate
    ``runtime_mapping`` table driven by the hand-curated ``map`` worksheet
    (see ``runtime_map.py``), so a gap here does not break runtime logging.
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


def _bump_catalog_version(con: duckdb.DuckDBPyConnection, *, previous: int) -> int:
    """Increment ``catalog_version`` on the seed path (``inv import-catalog``).

    One of the two write paths that touch ``catalog_version`` — the
    other is ``catalog_writer._commit_with_bump`` on the admin-API
    path. Both funnel through ``duckdb_repo.set_catalog_version`` so
    future auditing hooks can intercept writes uniformly. See
    ``.plans/architecture.md`` §Catalog versioning.
    """
    new_version = previous + 1
    duckdb_repo.set_catalog_version(con, new_version)
    return new_version


# ---------------------------------------------------------------------------
# Public seed entry points
# ---------------------------------------------------------------------------


def seed_from_sheet(year: int | None = None) -> dict:
    """Seed the catalog from the configured sheets.

    Idempotent: re-runnable on top of an existing DB. Used both for the
    fresh-bootstrap path and for incremental seeding during dev.

    NOTE: this path does NOT bump ``app_metadata.catalog_version``. By
    design only ``inv import-catalog`` touches the version (so PWA
    clients don't get invalidated by every routine ``import-config``
    run). When this function adds new mappings on top of an existing
    catalog, it logs a warning so the operator knows the PWA caches
    will not refresh until the next ``inv import-catalog``.

    CONCURRENCY: the function uses three sequential cursors on the
    single ``dinary.duckdb`` connection (write sources -> read sources
    + Sheets HTTP -> write catalog) instead of one long transaction.
    This trades atomicity for not holding the writer slot across
    multi-second Google API calls (which would block
    ``POST /api/expenses`` on the rate-cache miss path). Callers MUST
    NOT run two ``seed_from_sheet`` invocations concurrently — the
    split allows a second invocation to mutate ``import_sources``
    between Steps 1 and 3, producing a Step-3 catalog derived from a
    stale source list. Phase 1 deployment runs ``inv`` tasks one at a
    time, so this is operationally safe; if that ever changes, take a
    coarse-grained lock around the whole function.
    """
    if year is None:
        year = date.today().year

    duckdb_repo.init_db()

    # Step 1: bootstrap import sources in a quick write txn, then commit
    # before any Sheets HTTP work so we don't hold the DB writer slot
    # while waiting on Google's API.
    con = duckdb_repo.get_connection()
    try:
        con.execute("BEGIN")
        try:
            bootstrapped_sources = _ensure_import_sources(con)
            con.execute("COMMIT")
        except Exception:
            duckdb_repo.best_effort_rollback(con, context="seed_from_sheet step 1")
            raise
    finally:
        con.close()

    # Step 2: pull categories from each registered sheet. This is pure HTTP
    # against Google Sheets — no DB lock held.
    read_con = duckdb_repo.get_connection()
    try:
        pairs = _collect_categories(read_con)
    finally:
        read_con.close()
    if not pairs:
        raise ValueError("No categories discovered from import_sources")

    # Step 3: now that the slow Sheets I/O is done, take the writer slot
    # again and apply the catalog rows. The mapping-table purge runs
    # BEFORE BEGIN because of a DuckDB 1.5 FK-in-transaction limitation
    # (see ``_purge_mapping_tables`` docstring).
    con = duckdb_repo.get_connection()
    try:
        _purge_mapping_tables(con)
        con.execute("BEGIN")
        summary = seed_classification_catalog(con, year=year, discovered_pairs=pairs)
        summary["bootstrapped_import_sources"] = bootstrapped_sources

        con.execute("COMMIT")
        logger.info("Seed complete: %s", summary)

        if summary.get("mappings_created", 0) > 0:
            logger.warning(
                "import-config inserted %d new import_mapping row(s) without "
                "bumping catalog_version; run `inv import-catalog` to force "
                "PWA clients to refresh the catalog.",
                summary["mappings_created"],
            )
        return summary
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="seed_from_sheet step 3")
        raise
    finally:
        con.close()


def _finalize_rebuild_transaction(
    *,
    preserved_sources: list,
    previous_version: int,
    before_hash: str,
) -> dict:
    """Run the post-``seed_from_sheet`` transaction: restore sources,
    validate, and hash-gate the ``catalog_version`` bump.

    Split out of ``rebuild_config_from_sheets`` to keep that function
    under the ruff PLR0915 "too many statements" ceiling. Returns the
    summary fragment merged into the outer summary dict:

    * ``catalog_version`` — the post-bump (or unchanged) version.
    * ``previous_catalog_version`` — the max of the caller's snapshot
      and the pre-transaction DB value (handles the rare case where
      the caller couldn't read it at the top of the outer function).
    * ``latest_import_year`` — validated latest year from
      ``import_sources``.
    * ``catalog_version_changed`` — ``True`` if the hash differed and
      the bump actually happened.
    """
    con = duckdb_repo.get_connection()
    try:
        con.execute("BEGIN")
        try:
            _restore_import_sources(con, preserved_sources)
            latest_year = _validate_latest_import_source(con)
            _validate_import_coverage(con, latest_year)
            effective_previous = max(previous_version, duckdb_repo.get_catalog_version(con))
            after_hash = catalog_writer.hash_catalog_state(con)
            if before_hash == after_hash:
                # No observable catalog change — skip the bump. The
                # PWA's cached snapshot is still valid; 304s on the
                # next ``GET /api/catalog`` save bandwidth and keep
                # the operator's offline queue unblocked.
                new_version = effective_previous
                logger.info(
                    "rebuild_config_from_sheets: catalog hash unchanged; "
                    "keeping catalog_version=%d",
                    new_version,
                )
            else:
                new_version = _bump_catalog_version(con, previous=effective_previous)
            con.execute("COMMIT")
        except Exception:
            duckdb_repo.best_effort_rollback(con, context="rebuild_config_from_sheets commit")
            raise
    finally:
        con.close()

    return {
        "catalog_version": new_version,
        "previous_catalog_version": effective_previous,
        "latest_import_year": latest_year,
        "catalog_version_changed": before_hash != after_hash,
    }


def rebuild_config_from_sheets() -> dict:
    """FK-safe in-place catalog sync from the configured sheets.

    Never deletes the DB file. Ledger tables (``expenses``,
    ``expense_tags``, ``sheet_logging_jobs``, ``income``) retain real
    FKs into the catalog and are left completely untouched.

    Sync steps (all inside one write transaction):

    * Preserve ``import_sources`` by snapshotting then restoring; this
      guards against hardcodes accidentally wiping the operator's
      configured source list.
    * Run ``seed_classification_catalog``: deactivate every catalog
      row, upsert the new taxonomy by natural key (stable ids
      preserved, new ids for new vocabulary, retired rows stay
      ``is_active=FALSE``), then rebuild mapping tables from scratch
      against the current active ids.
    * Validate that the latest configured year has import-mapping
      coverage for every active category.
    * Monotonically bump ``catalog_version``.

    Returns a summary dict including ``previous_catalog_version``
    (value before the bump, never less than 1) and ``catalog_version``
    (the new value).
    """
    duckdb_repo.init_db()

    previous_version = 0
    try:
        con = duckdb_repo.get_connection()
        try:
            previous_version = duckdb_repo.get_catalog_version(con)
        finally:
            con.close()
    except (duckdb.Error, OSError, RuntimeError) as exc:
        # Only expected failure modes: DuckDB errors (file corruption,
        # schema drift, locked DB), filesystem errors, and RuntimeError
        # raised by get_catalog_version when app_metadata is missing.
        # Anything else (KeyboardInterrupt, MemoryError) should propagate.
        logger.warning(
            "Could not read previous catalog_version (%s); defaulting to 0",
            exc.__class__.__name__,
        )
        previous_version = 0

    con = duckdb_repo.get_connection()
    try:
        preserved_sources = _load_existing_import_sources(con)
        # Snapshot the catalog hash BEFORE ``seed_from_sheet`` mutates
        # (and commits) the catalog tables. We use the same canonical
        # state that ``catalog_writer._commit_with_bump`` hashes, so
        # the two write paths share a single definition of "observable
        # catalog change". If a rebuild from the sheet is a genuine
        # no-op (same hardcoded groups, same remote mappings), the
        # hash survives unchanged and we skip the bump — this keeps
        # PWA clients' ETag-validated ``GET /api/catalog`` returning
        # 304 Not Modified across idempotent reseeds.
        before_hash = catalog_writer.hash_catalog_state(con)
    finally:
        con.close()

    summary = seed_from_sheet()
    summary["preserved_import_sources"] = len(preserved_sources)

    bump = _finalize_rebuild_transaction(
        preserved_sources=preserved_sources,
        previous_version=previous_version,
        before_hash=before_hash,
    )
    summary.update(bump)

    # Phase 2: make sure the ``map`` worksheet tab exists with a
    # default-identity layout (one row per active category mapping
    # name->name). Idempotent; safe on every reseed. Network failure
    # here downgrades to a log warning — the catalog side is
    # already committed and the operator can re-run reload-map later.
    try:
        runtime_map.ensure_default_map_tab()
    except Exception:
        logger.exception(
            "ensure_default_map_tab failed; runtime 3D->2D mapping "
            "may be empty until the operator creates the map tab manually",
        )
    return summary
