"""Legacy 2D (sheet_category, sheet_group) → 3D derivation rules.

Lookup tables and pure functions that map a historical sheet row's two-column
key to the canonical category name, tag set, and synthetic event name used by
the runtime catalog. Split out of ``imports.seed`` so the import pipeline and
the report pipeline can import a lighter module without pulling in the full
mapping-rebuild machinery.

Public symbols consumed by ``imports.expense_import``:
    ``canonical_category_for_source``, ``tags_for_source``,
    ``event_name_for_source``, ``VACATION_ENVELOPES``
"""

from dinary.services.seed_config import (
    BUSINESS_TRIP_EVENT_LAST_YEAR,
    BUSINESS_TRIP_EVENT_PREFIX,
    SYNTHETIC_EVENT_PREFIX,
    TAXONOMY_CATEGORIES,
    VACATION_EVENT_YEAR_TO,
)

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

#: Legacy ``source_envelope`` values that mark a row as a vacation
#: expense for the synthetic-event derivation in
#: ``event_name_for_source`` (and for the post-cut-over warning in
#: ``imports/expense_import.py``). Public because both the importer
#: and the historical report need to share the exact same set.
VACATION_ENVELOPES: frozenset[str] = frozenset(
    {"путешествия", "sim-travel", "отпуск", "travel"},
)


def _category_for_wellness(envelope_lower: str) -> str:
    if envelope_lower in _SKI_ENVELOPES:
        return "лыжи"
    if envelope_lower == "спорт":
        return "спорт"
    if envelope_lower == "yazio":
        return "ЗОЖ"
    return "гигиена"


def _category_for_sport_or_razvl(source_lower: str, envelope_lower: str) -> str:
    if envelope_lower in _SKI_ENVELOPES:
        return "лыжи"
    if source_lower == "развлечения":
        return _RAZVL_SUB.get(envelope_lower, "развлечения")
    return "спорт"


def _category_for_household(envelope_lower: str) -> str | None:
    if envelope_lower in {"налог", "налоги"}:
        return "налог"
    if envelope_lower == "мебель":
        return "мебель"
    if envelope_lower == "страховка":
        return "коммунальные"
    if envelope_lower in {"diy", "dyi"}:
        return "гаджеты"
    return None


# source_lower → (sub-dict, default) for simple envelope-lookup cases
_ENVELOPE_DICT_DISPATCH: dict[str, tuple[dict[str, str], str]] = {
    "отпуск": (_VACATION_CATEGORY_BY_ENVELOPE, "аренда"),
    "командировка": (_KOMANDIROVKA_CATEGORY_BY_ENVELOPE, "аренда"),
    "еда": (_EDA_SUB, "еда"),
    "еда&бытовые": (_EDA_SUB, "еда"),
    "коммунальные": (_COMMUNAL_SUB, "коммунальные"),
    "машина": (_MASHINA_SUB, "машина"),
    "дача": (_DACHA_SUB, "хозтовары"),
}


def _dispatch_source_category(source_lower: str, envelope_lower: str) -> str | None:
    """Dispatch envelope-sensitive source types; returns None for simple fallthrough."""
    if source_lower == "приложения":
        return "развлечения" if not envelope_lower else "продуктивность"
    if source_lower in {"wellness", "welness"}:
        return _category_for_wellness(envelope_lower)
    if source_lower in _ENVELOPE_DICT_DISPATCH:
        sub_map, default = _ENVELOPE_DICT_DISPATCH[source_lower]
        return sub_map.get(envelope_lower, default)
    if source_lower in {"развлечения", "спорт"}:
        return _category_for_sport_or_razvl(source_lower, envelope_lower)
    if source_lower == "household":
        return _category_for_household(envelope_lower)
    return None


def canonical_category_for_source(
    source_type: str,
    source_envelope: str,
) -> str:
    """Map a legacy ``(source_type, source_envelope)`` to a canonical category name.

    Public because ``imports/expense_import.py`` reads the same legacy
    ``(source_type, source_envelope)`` cells and must apply the same
    mapping rules at import time. Keeping a private alias would
    silently drift if one caller's signature changes without the other's.
    """
    source_lower = source_type.lower().strip()
    envelope_lower = source_envelope.lower().strip()

    if source_type == BULAVKI_CATEGORY:
        return "карманные"
    dispatched = _dispatch_source_category(source_lower, envelope_lower)
    if dispatched is not None:
        return dispatched
    if source_lower in _CATEGORY_BY_SOURCE_TYPE:
        return _CATEGORY_BY_SOURCE_TYPE[source_lower]
    if source_type in TAXONOMY_CATEGORIES:
        return source_type
    msg = (
        f"Unmapped sheet (sheet_category={source_type!r}, sheet_group={source_envelope!r}). "
        "Add a rule to imports.seed._CATEGORY_BY_SOURCE_TYPE or to the "
        "legacy-derivation tables."
    )
    raise ValueError(msg)


def _komandirovka_sphere_tag(source_lower: str, envelope_lower: str, year: int) -> str | None:
    if source_lower == "командировка" or envelope_lower == "командировка":
        return "релокация" if year > BUSINESS_TRIP_EVENT_LAST_YEAR else "профессиональное"
    return None


def tags_for_source(
    source_type: str,
    source_envelope: str,
    year: int,
) -> list[str]:
    """Return tag names for a legacy ``(source_type, source_envelope)`` pair.

    Combines beneficiary + sphere-of-life axes from the old 4D model
    into one tag set. Year-aware rules (e.g. "командировка" relocation
    tag from 2022 onward) are resolved here so per-year
    ``import_mapping`` rows can carry the right tags.
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
        tag = _komandirovka_sphere_tag(source_lower, envelope_lower, year)
        if tag is not None:
            tags.add(tag)
    return sorted(tags)


def event_name_for_source(source_type: str, source_envelope: str, year: int) -> str | None:
    """Return the synthetic event name (if any) for a legacy ``(source, envelope)`` pair.

    Public because ``imports/expense_import.py`` must derive the same
    per-year event name when promoting historical rows that lack an
    explicit mapping. Returns ``None`` when the pair has no event
    association (the common case — only vacations and pre-cutover
    business trips synthesize an event from this layer).
    """
    source_lower = source_type.lower().strip()
    envelope_lower = source_envelope.lower().strip()
    if source_lower == "отпуск" and year <= VACATION_EVENT_YEAR_TO:
        return f"{SYNTHETIC_EVENT_PREFIX}{year}"
    if envelope_lower in VACATION_ENVELOPES and year <= VACATION_EVENT_YEAR_TO:
        return f"{SYNTHETIC_EVENT_PREFIX}{year}"
    is_komandirovka = source_lower == "командировка" or envelope_lower == "командировка"
    if is_komandirovka and year <= BUSINESS_TRIP_EVENT_LAST_YEAR:
        return f"{BUSINESS_TRIP_EVENT_PREFIX}{year}"
    return None
