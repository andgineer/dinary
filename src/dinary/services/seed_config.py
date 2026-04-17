"""Seed config.duckdb from Google Sheets category data.

Reads the current (category, group) pairs from the sheet and creates
the 4D reference data + mapping table in config.duckdb.
"""

import dataclasses
import json
import logging
from datetime import date

import duckdb

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.category_store import Category
from dinary.services.duckdb_repo import SYNTHETIC_EVENT_PREFIX
from dinary.services.sheets import HEADER_ROWS, _cell, get_categories, get_sheet
from dinary.services.sql_loader import fetchall_as, load_sql

logger = logging.getLogger(__name__)

BENEFICIARY_ENVELOPES = {
    "собака": "собака",
    "ребенок": "Аня",
    "лариса": "Лариса",
}

SPHERE_OF_LIFE_ENVELOPES = {
    "релокация": "релокация",
    "профессиональное": "профессиональное",
    "дача": "дача",
}

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

LEGACY_FOOD_CATEGORY = "еда&бытовые"

BULAVKI_CATEGORY = "булавки"


@dataclasses.dataclass(frozen=True, slots=True)
class MappingSeedRow:
    year: int
    source_type: str
    source_envelope: str
    category: str
    beneficiary: str | None = None
    sphere_of_life: str | None = None
    event_name: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ImportSourceSeedRow:
    year: int
    spreadsheet_id: str
    worksheet_name: str
    layout_key: str
    notes: str | None


_RUB_2016_LAST_YEAR = 2016
_RUB_6COL_LAST_YEAR = 2021
_RUB_FALLBACK_YEAR = 2022


def _default_layout_for_year(year: int) -> str:
    if year <= _RUB_2016_LAST_YEAR:
        return "rub_2016"
    if year <= _RUB_6COL_LAST_YEAR:
        return "rub_6col"
    if year == _RUB_FALLBACK_YEAR:
        return "rub_fallback"
    return "default"


def _bootstrap_import_sources(default_year: int) -> list[ImportSourceSeedRow]:
    """Load import sources from env JSON, or synthesize the current/default year."""
    if settings.sheet_import_sources_json:
        payload = json.loads(settings.sheet_import_sources_json)
        return [
            ImportSourceSeedRow(
                year=int(row["year"]),
                spreadsheet_id=row.get("spreadsheet_id", ""),
                worksheet_name=row.get("worksheet_name", ""),
                layout_key=row.get("layout_key", _default_layout_for_year(int(row["year"]))),
                notes=row.get("notes"),
            )
            for row in payload
        ]

    if settings.google_sheets_spreadsheet_id:
        return [
            ImportSourceSeedRow(
                year=default_year,
                spreadsheet_id=settings.google_sheets_spreadsheet_id,
                worksheet_name="",
                layout_key=_default_layout_for_year(default_year),
                notes="Bootstrapped from DINARY_GOOGLE_SHEETS_SPREADSHEET_ID",
            ),
        ]

    return []


def _ensure_import_sources(
    con: duckdb.DuckDBPyConnection,
    *,
    default_year: int,
) -> int:
    count_row = con.execute("SELECT COUNT(*) FROM sheet_import_sources").fetchone()
    existing_count = count_row[0] if count_row else 0
    if existing_count:
        return 0

    rows = _bootstrap_import_sources(default_year)
    if not rows:
        return 0

    for row in rows:
        con.execute(
            """
            INSERT INTO sheet_import_sources
                (year, spreadsheet_id, worksheet_name, layout_key, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            [row.year, row.spreadsheet_id, row.worksheet_name, row.layout_key, row.notes],
        )
    return len(rows)


EXPLICIT_MAPPING_OVERRIDES: list[MappingSeedRow] = [
    MappingSeedRow(
        0,
        "приложения",
        "профессиональное",
        "продуктивность",
        sphere_of_life="профессиональное",
    ),
    MappingSeedRow(0, "приложения", "", "развлечения"),
    MappingSeedRow(
        2023,
        "professional",
        "apps",
        "продуктивность",
        sphere_of_life="профессиональное",
    ),
    MappingSeedRow(
        2018,
        "Работа",
        "App",
        "продуктивность",
        sphere_of_life="профессиональное",
    ),
    MappingSeedRow(2018, "Parallels", "", "развлечения"),
    MappingSeedRow(0, "Коммунальные", "страховка", "коммунальные"),
    MappingSeedRow(0, "Коммунальные", "Страховка", "коммунальные"),
    MappingSeedRow(0, "Дача", "страховка", "коммунальные", sphere_of_life="дача"),
    MappingSeedRow(2017, "Дача", "Страховка", "коммунальные", sphere_of_life="дача"),
    MappingSeedRow(2018, "Дача", "Страховка", "коммунальные", sphere_of_life="дача"),
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

_CATEGORY_COLUMNS_BY_LAYOUT = {
    "default": (4, 5),
    "rub": (4, 5),
    "rub_fallback": (4, 5),
    "rub_6col": (3, 4),
    "rub_2016": (3, 4),
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
    """Collect category pairs from the default sheet and all configured import sources."""
    seen: set[tuple[str, str]] = set()
    categories: list[Category] = []

    def extend(rows: list[Category]) -> None:
        for row in rows:
            key = (row.name, row.group)
            if key not in seen:
                seen.add(key)
                categories.append(row)

    extend(get_categories())

    sources = con.execute(
        "SELECT spreadsheet_id, worksheet_name, layout_key FROM sheet_import_sources ORDER BY year",
    ).fetchall()
    for spreadsheet_id, worksheet_name, layout_key in sources:
        extend(_load_categories_for_sheet(spreadsheet_id, worksheet_name, layout_key))

    return categories


def _load_existing_import_sources() -> list[ImportSourceSeedRow]:
    if not duckdb_repo.CONFIG_DB.exists():
        return []

    con = duckdb.connect(str(duckdb_repo.CONFIG_DB), read_only=True)
    try:
        try:
            rows = con.execute(
                "SELECT year, spreadsheet_id, worksheet_name, layout_key, notes "
                "FROM sheet_import_sources ORDER BY year",
            ).fetchall()
        except duckdb.Error:
            return []
    finally:
        con.close()

    return [
        ImportSourceSeedRow(
            year=row[0],
            spreadsheet_id=row[1],
            worksheet_name=row[2],
            layout_key=row[3],
            notes=row[4],
        )
        for row in rows
    ]


def _restore_import_sources(rows: list[ImportSourceSeedRow]) -> None:
    if not rows:
        return

    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        for row in rows:
            con.execute(
                """
                INSERT INTO sheet_import_sources
                    (year, spreadsheet_id, worksheet_name, layout_key, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    spreadsheet_id = EXCLUDED.spreadsheet_id,
                    worksheet_name = EXCLUDED.worksheet_name,
                    layout_key = EXCLUDED.layout_key,
                    notes = EXCLUDED.notes
                """,
                [row.year, row.spreadsheet_id, row.worksheet_name, row.layout_key, row.notes],
            )
    finally:
        con.close()


TAXONOMY_CATEGORIES: frozenset[str] = frozenset(
    cat for _group, cats in ENTRY_GROUPS for cat in cats
)


_CATEGORY_BY_SOURCE_TYPE: dict[str, str] = {
    # Food & household
    "еда&бытовые": "еда",
    "еда": "еда",
    "фрукты": "фрукты",
    "деликатесы": "деликатесы",
    "алкоголь": "алкоголь",
    "бытовые": "хозтовары",
    "хозтовары": "хозтовары",
    "household": "хозтовары",
    "дача": "хозтовары",
    "обустройство": "хозтовары",
    # Housing
    "аренда": "аренда",
    "ремонт": "ремонт",
    "ремонт комнаты ани": "ремонт",
    "мебель": "мебель",
    "бытовая техника": "бытовая техника",
    # Utilities & services
    "коммунальные": "коммунальные",
    "мобильник": "мобильник",
    "интернет": "интернет",
    "сервисы": "сервисы",
    # Health
    "медицина": "медицина",
    "лекарства": "лекарства",
    "страхование жизни": "медицина",
    "очки": "медицина",
    # Hygiene & wellness (baseline; wellness envelope handled below)
    "гигиена": "гигиена",
    "стрижка": "гигиена",
    "косметика": "гигиена",
    "зож": "ЗОЖ",
    "бад": "ЗОЖ",
    # Sport
    "спорт": "спорт",
    "велосипед": "велосипед",
    "лыжи": "лыжи",
    # Leisure
    "развлечения": "развлечения",
    "кафе": "кафе",
    "гаджеты": "гаджеты",
    "электроника": "электроника",
    "инструменты": "инструменты",
    "avito": "гаджеты",
    # Transport
    "транспорт": "транспорт",
    "танспорт": "транспорт",
    "pubtransport": "транспорт",
    "машина": "машина",
    "топливо": "топливо",
    # Knowledge & productivity
    "обучение": "обучение",
    "продуктивность": "продуктивность",
    "работа": "продуктивность",
    "professional": "продуктивность",
    "учеба": "обучение",
    "школа": "обучение",
    "курсы": "обучение",
    # Appliances (explicit "Техника" source_type in 2016 sheet)
    "техника": "бытовая техника",
    # Personal / family
    "карманные": "карманные",
    "собака": "карманные",
    "подарки": "подарки",
    "социальное": "подарки",
    "одежда": "одежда",
    # State
    "налог": "налог",
    "налоги": "налог",
    "штрафы": "штрафы",
}

_EDA_SUBCATEGORY_BY_ENVELOPE: dict[str, str] = {
    "кафе": "кафе",
    "lunch": "кафе",
    "кофе": "кафе",
    "ужин": "кафе",
    "перекусы": "кафе",
    "фрукты": "фрукты",
    "деликатесы": "деликатесы",
    "алкоголь": "алкоголь",
}

_COMMUNAL_SUBCATEGORY_BY_ENVELOPE: dict[str, str] = {
    "mobile": "мобильник",
    "phone": "мобильник",
    "мобильник": "мобильник",
    "мобильный": "мобильник",
    "internet": "интернет",
    "интернет": "интернет",
}

_MASHINA_SUBCATEGORY_BY_ENVELOPE: dict[str, str] = {
    "gas": "топливо",
    "топливо": "топливо",
    "такси": "транспорт",
}

_DACHA_SUBCATEGORY_BY_ENVELOPE: dict[str, str] = {
    "ремонт": "ремонт",
    "налог": "налог",
    "налоги": "налог",
    "свет": "коммунальные",
    "электро": "коммунальные",
    "электроэнергия": "коммунальные",
    "сигнализация": "коммунальные",
    "сингнализация": "коммунальные",  # typo in source sheet preserved
    "страховка": "коммунальные",
    "интернет": "интернет",
    "транспорт": "транспорт",
    "техника": "бытовая техника",
}

_RAZVL_SUBCATEGORY_BY_ENVELOPE: dict[str, str] = {
    "спорт": "спорт",
    "skiitime": "лыжи",
    "wellness": "гигиена",
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
    "duty free": "гаджеты",
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


def _canonical_category_for_source(source_type: str, source_envelope: str) -> str:  # noqa: C901, PLR0911, PLR0912
    source_lower = source_type.lower().strip()
    envelope_lower = source_envelope.lower().strip()

    if source_type == BULAVKI_CATEGORY:
        return "карманные"

    if source_lower == "приложения":
        return "продуктивность" if envelope_lower == "профессиональное" else "развлечения"

    if source_lower == "wellness":
        if envelope_lower in {"yazio"}:
            return "ЗОЖ"
        return "гигиена"

    if source_lower == "отпуск":
        return _VACATION_CATEGORY_BY_ENVELOPE.get(envelope_lower, "аренда")

    if source_lower == "командировка":
        return _KOMANDIROVKA_CATEGORY_BY_ENVELOPE.get(envelope_lower, "аренда")

    # Multi-category source_types: envelope picks the sub-category.
    if source_lower in {"еда", LEGACY_FOOD_CATEGORY}:
        return _EDA_SUBCATEGORY_BY_ENVELOPE.get(envelope_lower, "еда")
    if source_lower == "коммунальные":
        return _COMMUNAL_SUBCATEGORY_BY_ENVELOPE.get(envelope_lower, "коммунальные")
    if source_lower == "машина":
        return _MASHINA_SUBCATEGORY_BY_ENVELOPE.get(envelope_lower, "машина")
    if source_lower == "дача":
        return _DACHA_SUBCATEGORY_BY_ENVELOPE.get(envelope_lower, "хозтовары")
    if source_lower == "развлечения":
        return _RAZVL_SUBCATEGORY_BY_ENVELOPE.get(envelope_lower, "развлечения")
    if source_lower == "спорт":
        if envelope_lower in _SKI_ENVELOPES:
            return "лыжи"
        return "спорт"

    if source_lower in _CATEGORY_BY_SOURCE_TYPE:
        return _CATEGORY_BY_SOURCE_TYPE[source_lower]

    if source_type in TAXONOMY_CATEGORIES:
        return source_type

    msg = (
        f"Unmapped sheet source_type={source_type!r} (envelope={source_envelope!r}). "
        "Add a rule to _CATEGORY_BY_SOURCE_TYPE or EXPLICIT_MAPPING_OVERRIDES."
    )
    raise ValueError(msg)


def _beneficiary_for_source(source_type: str, source_envelope: str) -> str | None:
    if source_envelope in BENEFICIARY_ENVELOPES:
        return BENEFICIARY_ENVELOPES[source_envelope]
    source_lower = source_type.lower().strip()
    if source_type == BULAVKI_CATEGORY:
        return "Лариса"
    if source_lower == "собака":
        return "собака"
    if source_type == "Ремонт комнаты Ани":
        return "Аня"
    if source_lower == "школа":
        return "Аня"
    return None


def _sphere_for_source(source_type: str, source_envelope: str) -> str | None:
    if source_envelope in SPHERE_OF_LIFE_ENVELOPES:
        return SPHERE_OF_LIFE_ENVELOPES[source_envelope]
    source_lower = source_type.lower().strip()
    if source_lower == "дача":
        return "дача"
    if source_lower == "командировка":
        return "релокация"
    return None


def _event_name_for_source(source_type: str, source_envelope: str, year: int) -> str | None:
    if source_type.lower().strip() == "отпуск":
        return f"{SYNTHETIC_EVENT_PREFIX}{year}"
    if source_envelope.lower().strip() == duckdb_repo.TRAVEL_ENVELOPE:
        return f"{SYNTHETIC_EVENT_PREFIX}{year}"
    return None


def _upsert_mapping(  # noqa: PLR0913
    con: duckdb.DuckDBPyConnection,
    *,
    year: int,
    source_type: str,
    source_envelope: str,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    sphere_of_life_id: int | None,
) -> None:
    con.execute(
        """
        INSERT INTO source_type_mapping
        (
            year,
            source_type,
            source_envelope,
            category_id,
            beneficiary_id,
            event_id,
            sphere_of_life_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO UPDATE SET
            category_id = EXCLUDED.category_id,
            beneficiary_id = EXCLUDED.beneficiary_id,
            event_id = EXCLUDED.event_id,
            sphere_of_life_id = EXCLUDED.sphere_of_life_id
        """,
        [
            year,
            source_type,
            source_envelope,
            category_id,
            beneficiary_id,
            event_id,
            sphere_of_life_id,
        ],
    )


def seed_from_sheet(year: int | None = None) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Read categories from Google Sheets and populate config.duckdb.

    Returns a summary of what was created.
    """
    if year is None:
        year = date.today().year

    duckdb_repo.init_config_db()
    con = duckdb_repo.get_config_connection(read_only=False)

    try:
        con.execute("BEGIN")
        bootstrapped_sources = _ensure_import_sources(con, default_year=year)

        cats = _collect_categories(con)
        if not cats:
            raise ValueError("No categories found in Google Sheets")

        cat_ids: dict[str, int] = {}
        beneficiary_ids: dict[str, int] = {}
        sphere_ids: dict[str, int] = {}

        for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_categories.sql")):
            cat_ids[r.name] = r.id
        for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_members.sql")):
            beneficiary_ids[r.name] = r.id
        for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_spheres.sql")):
            sphere_ids[r.name] = r.id

        if cat_ids:
            logger.info("Config DB already has data, merging new entries")

        next_cat_id = max(cat_ids.values(), default=0) + 1
        next_ben_id = max(beneficiary_ids.values(), default=0) + 1
        next_sphere_id = max(sphere_ids.values(), default=0) + 1

        def ensure_category(cat_name: str) -> int:
            nonlocal next_cat_id
            if cat_name not in TAXONOMY_CATEGORIES:
                msg = (
                    f"ensure_category({cat_name!r}): only categories from ENTRY_GROUPS "
                    "may be created in config.duckdb."
                )
                raise ValueError(msg)
            if cat_name not in cat_ids:
                cid = next_cat_id
                next_cat_id += 1
                con.execute(
                    "INSERT INTO categories (id, name) VALUES (?, ?)",
                    [cid, cat_name],
                )
                cat_ids[cat_name] = cid
            return cat_ids[cat_name]

        def ensure_beneficiary(name: str) -> int:
            nonlocal next_ben_id
            if name not in beneficiary_ids:
                bid = next_ben_id
                next_ben_id += 1
                con.execute(
                    "INSERT INTO family_members (id, name) VALUES (?, ?)",
                    [bid, name],
                )
                beneficiary_ids[name] = bid
            return beneficiary_ids[name]

        def ensure_sphere(name: str) -> int:
            nonlocal next_sphere_id
            if name not in sphere_ids:
                sid = next_sphere_id
                next_sphere_id += 1
                con.execute(
                    "INSERT INTO spheres_of_life (id, name) VALUES (?, ?)",
                    [sid, name],
                )
                sphere_ids[name] = sid
            return sphere_ids[name]

        def ensure_event(name: str) -> int:
            row = con.execute("SELECT id FROM events WHERE name = ?", [name]).fetchone()
            if row:
                return row[0]
            max_row = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            max_event_id = max_row[0] if max_row else 0
            new_id = max_event_id + 1
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
                [new_id, name, date(year, 1, 1), date(year, 12, 31)],
            )
            return new_id

        for _group_title, category_names in ENTRY_GROUPS:
            for cat_name in category_names:
                ensure_category(cat_name)

        for sphere_name in SPHERE_OF_LIFE_ENVELOPES.values():
            ensure_sphere(sphere_name)

        event_name = f"{SYNTHETIC_EVENT_PREFIX}{year}"
        existing_event = con.execute(
            "SELECT 1 FROM events WHERE name = ?",
            [event_name],
        ).fetchone()
        if not existing_event:
            max_row = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            max_event_id = max_row[0] if max_row else 0
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
                [max_event_id + 1, event_name, date(year, 1, 1), date(year, 12, 31)],
            )
            logger.info("Created synthetic travel event: %s", event_name)

        mapping_count = 0

        for c in cats:
            source_type = c.name
            source_envelope = c.group or ""

            category_name = _canonical_category_for_source(source_type, source_envelope)
            category_id = ensure_category(category_name)

            beneficiary_name = _beneficiary_for_source(source_type, source_envelope)
            beneficiary_id = ensure_beneficiary(beneficiary_name) if beneficiary_name else None

            sphere_name = _sphere_for_source(source_type, source_envelope)
            sphere_of_life_id = ensure_sphere(sphere_name) if sphere_name else None

            existing_mapping = con.execute(
                "SELECT 1 FROM source_type_mapping "
                "WHERE year = 0 AND source_type = ? AND source_envelope = ?",
                [source_type, source_envelope],
            ).fetchone()

            _upsert_mapping(
                con,
                year=0,
                source_type=source_type,
                source_envelope=source_envelope,
                category_id=category_id,
                beneficiary_id=beneficiary_id,
                event_id=None,
                sphere_of_life_id=sphere_of_life_id,
            )
            if not existing_mapping:
                mapping_count += 1

        for row in EXPLICIT_MAPPING_OVERRIDES:
            existing = con.execute(
                "SELECT 1 FROM source_type_mapping "
                "WHERE year = ? AND source_type = ? AND source_envelope = ?",
                [row.year, row.source_type, row.source_envelope],
            ).fetchone()
            category_id = ensure_category(row.category)
            beneficiary_id = ensure_beneficiary(row.beneficiary) if row.beneficiary else None
            sphere_of_life_id = ensure_sphere(row.sphere_of_life) if row.sphere_of_life else None
            event_id = ensure_event(row.event_name) if row.event_name else None
            _upsert_mapping(
                con,
                year=row.year,
                source_type=row.source_type,
                source_envelope=row.source_envelope,
                category_id=category_id,
                beneficiary_id=beneficiary_id,
                event_id=event_id,
                sphere_of_life_id=sphere_of_life_id,
            )
            if not existing:
                mapping_count += 1

        summary = {
            "categories": len(cat_ids),
            "beneficiaries": len(beneficiary_ids),
            "spheres_of_life": len(sphere_ids),
            "mappings_created": mapping_count,
            "bootstrapped_import_sources": bootstrapped_sources,
        }
        con.execute("COMMIT")
        logger.info("Seed complete: %s", summary)
        return summary

    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def rebuild_config_from_sheets() -> dict:
    """Wipe config DB, preserve import sources, then rebuild from sheets."""
    preserved_sources = _load_existing_import_sources()
    if duckdb_repo.CONFIG_DB.exists():
        duckdb_repo.CONFIG_DB.unlink()

    duckdb_repo.init_config_db()
    _restore_import_sources(preserved_sources)

    summary = seed_from_sheet()
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        memberships = rebuild_taxonomy(con)
    finally:
        con.close()

    summary["taxonomy_memberships"] = memberships
    summary["preserved_import_sources"] = len(preserved_sources)
    return summary


def rebuild_taxonomy(con: duckdb.DuckDBPyConnection) -> int:
    """Create the entry_groups taxonomy from ENTRY_GROUPS constant.

    Returns the number of membership rows created.
    """
    con.execute("DELETE FROM category_taxonomy_membership")
    con.execute("DELETE FROM category_taxonomy_nodes")
    con.execute("DELETE FROM category_taxonomies")

    con.execute(
        "INSERT INTO category_taxonomies (id, key, title)"
        " VALUES (1, 'entry_groups', 'Entry Groups')",
    )

    memberships = 0
    for sort_order, (group_title, category_names) in enumerate(ENTRY_GROUPS, start=1):
        node_key = group_title.lower().replace(" ", "_")
        con.execute(
            "INSERT INTO category_taxonomy_nodes (id, taxonomy_id, key, title, sort_order) "
            "VALUES (?, 1, ?, ?, ?)",
            [sort_order, node_key, group_title, sort_order],
        )
        for cat_name in category_names:
            cat_row = con.execute(
                "SELECT id FROM categories WHERE name = ?",
                [cat_name],
            ).fetchone()
            if cat_row:
                con.execute(
                    "INSERT INTO category_taxonomy_membership (category_id, node_id) VALUES (?, ?)",
                    [cat_row[0], sort_order],
                )
                memberships += 1
            else:
                logger.warning("Category '%s' not in DB — skipping taxonomy link", cat_name)

    return memberships
