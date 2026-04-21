"""Bootstrap historical Google Sheets import (one-time, destructive).

This is the only path that reads from sheets and writes expenses. Once a
year has been imported into ``data/dinary.duckdb``, runtime traffic
(``POST /api/expenses`` plus the async sheet-append worker) takes over and
the sheet becomes append-only.

The importer:

  * resolves ``(sheet_category, sheet_group, year)`` against
    ``import_mapping`` (year-scoped first, year=0 fallback) — the catalog
    is pre-baked by ``seed_config.seed_classification_catalog``;
  * applies a small set of per-row heuristics that depend on data only
    available at import time (housing keywords, DIY routing, RUB→EUR
    fallback, "kto" beneficiary column);
  * unions ``events.auto_tags`` into the row's ``tag_ids`` once an event
    has been attached — same invariant as ``POST /api/expenses``, so
    every historical vacation row ends up carrying both ``отпуск`` and
    ``путешествия`` without relying on the legacy sheet tag columns;
  * inserts one expense row + tags + provenance pair into
    ``data/dinary.duckdb`` with ``enqueue_logging=False`` (rows are
    already in the sheet — re-uploading them is a bug) and
    ``client_expense_id=None`` (legacy rows have no PWA-generated
    idempotency key; ``UNIQUE`` allows multiple NULLs).

It does not touch the runtime queue (``sheet_logging_jobs``) and does not
bump ``catalog_version``.
"""

import dataclasses
import logging
from datetime import date, datetime
from decimal import Decimal

from dinary.config import settings
from dinary.services import duckdb_repo, sheet_mapping
from dinary.services.nbs import get_rate
from dinary.services.seed_config import (
    BUSINESS_TRIP_EVENT_LAST_YEAR,
    BUSINESS_TRIP_EVENT_PREFIX,
    RELOCATION_EVENT_NAME,
    RUSSIA_TRIP_EVENT_NAME,
    SYNTHETIC_EVENT_PREFIX,
    VACATION_ENVELOPES,
    VACATION_EVENT_YEAR_TO,
    canonical_category_for_source,
    event_name_for_source,
    tags_for_source,
)
from dinary.services.sheets import (
    HEADER_ROWS,
    _cell,
    get_sheet,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class SheetLayout:
    col_amount: int
    col_category: int
    col_group: int
    col_comment: int
    col_month: int
    currency: str = "RSD"
    # Fallback column used when the primary amount cell is empty.
    # 2022 col B (RSD) is empty for Jan-Mar (pre-relocation, RUB lived in col C).
    col_amount_fallback: int | None = None
    # Optional "kto" beneficiary column used by 2014+ historical sheets.
    col_beneficiary: int | None = None


LAYOUTS: dict[str, SheetLayout] = {
    "default": SheetLayout(2, 4, 5, 6, 7),
    "rub": SheetLayout(2, 4, 5, 6, 7, currency="RUB"),
    "rub_fallback": SheetLayout(2, 4, 5, 6, 7, col_amount_fallback=3),
    "rub_6col": SheetLayout(2, 3, 4, 5, 6, currency="RUB"),
    "rub_2016": SheetLayout(2, 3, 4, 5, 8, currency="RUB"),
    "rub_2014": SheetLayout(2, 3, 4, 6, 10, currency="RUB", col_beneficiary=5),
    "rub_2012": SheetLayout(2, 3, 4, 6, 8, currency="RUB", col_beneficiary=5),
}

#: Maps the raw "kto" cell value to a tag name from the Phase-1 dictionary.
_SHEET_BENEFICIARY_TAG_BY_VALUE: dict[str, str] = {
    "ребенок": "Аня",
    "аня": "Аня",
    "лариса": "Лариса",
    "iskin": "Лариса",
    "anso": "Андрей",
    "andrey": "Андрей",
    "андрей": "Андрей",
}

MONTHS_IN_YEAR = 12

_RUB_RSD_TRANSITION_YEAR = 2022
_RUB_RSD_TRANSITION_MONTH = 4
_RELOCATION_UTILITIES_THRESHOLD_EUR = 200
RUSSIA_TRIP_FIX_YEAR = 2026

_REPAIR_KEYWORDS = (
    "ремонт",
    "мастер",
    "сантех",
    "электрик",
    "покраск",
    "краск",
    "ламинат",
    "обои",
    "дрель",
    "перфорат",
    "шпаклев",
    "цемент",
)
_FURNITURE_KEYWORDS = (
    "стол",
    "стул",
    "шкаф",
    "полка",
    "вешалка",
    "матрас",
    "кровать",
    "тумба",
    "зеркало",
    "диван",
    "комод",
)
_APPLIANCE_KEYWORDS = (
    "гриль",
    "чайник",
    "пылесос",
    "блендер",
    "микроволнов",
    "холодильник",
    "стирал",
    "утюг",
    "сушилка",
    "кофемаш",
    "зарядник",
)
_TOOL_KEYWORDS = (
    "шуруповёрт",
    "шуруповерт",
    "бензопила",
    "триммер",
    "газонокосилка",
    "косилка",
    "перфоратор",
    "рубанок",
    "лобзик",
    "культиватор",
    "мотоблок",
    "секатор",
)
_ELECTRONICS_KEYWORDS = (
    "паяльн",
    "припой",
    "флюс",
    "ардуино",
    "arduino",
    "raspberry",
    "распберри",
    "расберри",
    "atmega",
    "esp32",
    "esp8266",
    "stm32",
    "светодиод",
    " led ",
    "led ",
    " led",
    "rgb",
    "диммер",
    "контроллер",
    "controller",
    "конденсатор",
    "резистор",
    "транзистор",
    " smd",
    "усилитель",
    "цап",
    " dac",
    "разъём",
    "разъем",
    "разьем",
    "коннектор",
    "lightning",
    "hdmi",
    "raspberrypi",
    "ebay rgb",
    "магнитол",
    "оплетка",
    "оплётка",
)
_MATERIAL_KEYWORDS = (
    "фанера",
    "оргстекл",
    " лак ",
    " лак,",
    " лак.",
    "морилк",
    "эпоксид",
    "изолент",
    "саморез",
    "гипсокартон",
    "обои",
    "цемент",
    "штукатур",
    "плитк",
    "выключател",
    "клемм",
    "щиток",
    "лампочк",
    "розетк",
    "патрон ",
)
_HOUSING_SOURCE_TYPES = {
    "аренда",
    "квартира",
    "жильё",
    "жилье",
    "обустройство",
    "household",
    "дом",
}
_POST_IMPORT_CATEGORY_FIXES = {
    "эпоксидка гриль зарядник батарейки ножи аккумулятор": "бытовая техника",
    "кабель для наушников кино проектор byintek ufo p20": "электроника",
    "амазон - наушники перчатки стевия фильтр вентилятор": "электроника",
    "маме на др iphone 7 gold 128gb цветы бабушке": "подарки",
    "папе на ячейку": "сервисы",
    "переводы родителям": "подарки",
}


# ---------------------------------------------------------------------------
# Per-row helpers
# ---------------------------------------------------------------------------


def parse_display_amount(display: str) -> float | None:
    """Parse a sheet's "display" amount cell into a positive float (or None).

    Public because ``verify_equivalence`` reads the same display-formatted
    cells and must apply the exact same parsing rules — keeping two copies
    in lockstep was a foot-gun.
    """
    if not display:
        return None
    cleaned = display.replace(" ", "").replace(",", "")
    try:
        val = float(cleaned)
        return val if val != 0 else None
    except ValueError:
        return None


def resolve_currency(year: int, month: int, layout: SheetLayout) -> str:
    """Return the operative currency for a sheet cell at ``(year, month)``.

    Public because ``verify_equivalence`` must reproduce the same RUB/RSD
    cutover logic when re-aggregating the source sheet for the
    bootstrap-import equivalence check. The April 2022 cutover is the
    point where the historical sheets switched from quoting amounts in
    RUB to RSD; cells before that month must be interpreted as RUB even
    if the layout's nominal currency is RSD.
    """
    if layout.currency != "RSD":
        return layout.currency
    if year < _RUB_RSD_TRANSITION_YEAR:
        return "RUB"
    if year == _RUB_RSD_TRANSITION_YEAR and month < _RUB_RSD_TRANSITION_MONTH:
        return "RUB"
    return "RSD"


def _prefetch_monthly_rates(
    year: int,
    layout: SheetLayout,
    *,
    con=None,
) -> dict[int, dict[str, Decimal]]:
    """Pre-fetch NBS rates for every month in ``year``, keyed by month.

    Stored keys (all as ``RSD per 1 unit of X``, i.e. NBS middle rates):

      * ``rate_src`` — source currency for the layout+month;
        ``Decimal(1)`` when the source is "RSD".
      * ``rate_eur`` — always present, used by the legacy housing
        heuristic (threshold expressed in EUR) and the 2D→3D report.
      * ``rate_app`` — the configured app currency
        (``settings.app_currency``); ``Decimal(1)`` when app is "RSD".

    ``con`` is the single shared ``data/dinary.duckdb`` cursor. Passing
    it is purely an optimization for long-running callers (the 2D-to-3D
    report) to avoid one extra ``cursor()`` round-trip per year. When
    ``con`` is ``None`` we cut a fresh cursor ourselves.
    """
    app_currency = settings.app_currency.upper()
    own_con = con is None
    if own_con:
        con = duckdb_repo.get_connection()
    try:
        rates: dict[int, dict[str, Decimal]] = {}
        for month in range(1, MONTHS_IN_YEAR + 1):
            currency = resolve_currency(year, month, layout).upper()
            rate_date = date(year, month, 1)
            rate_src = Decimal(1) if currency == "RSD" else get_rate(con, rate_date, currency)
            rate_eur = Decimal(1) if currency == "EUR" else get_rate(con, rate_date, "EUR")
            rate_app = (
                Decimal(1) if app_currency == "RSD" else get_rate(con, rate_date, app_currency)
            )
            rates[month] = {
                "rate_src": rate_src,
                "rate_eur": rate_eur,
                "rate_app": rate_app,
            }
        return rates
    finally:
        if own_con:
            con.close()


def _convert_to(
    amount_original: Decimal,
    *,
    rate_src: Decimal,
    rate_target: Decimal,
) -> Decimal:
    """Convert ``amount_original`` via two RSD-anchored NBS rates.

    Both rates are ``RSD per 1 unit of X``, so the source amount in
    RSD is ``amount_original * rate_src``, and dividing by
    ``rate_target`` yields the amount in the target currency. When
    ``rate_src == rate_target`` (same currency) the result is
    bit-identical to ``amount_original`` modulo the final quantize.
    """
    return (amount_original * rate_src / rate_target).quantize(Decimal("0.01"))


def _read_amounts_for_row(
    *,
    row_display: list[str],
    layout: SheetLayout,
    year: int,
    month: int,
    monthly_rates: dict[int, dict[str, Decimal]],
) -> tuple[float, str, float, float] | None:
    """Return ``(amount_original, currency_original, amount_app, amount_eur)``.

    * ``amount_app`` is the value stored in ``expenses.amount`` (the app
      currency, ``settings.app_currency``).
    * ``amount_eur`` is retained for the housing heuristic (threshold
      expressed in EUR) and the 2D→3D diagnostic report.
    """
    amount_original = parse_display_amount(_cell(row_display, layout.col_amount))
    currency_original = resolve_currency(year, month, layout)

    # Fallback column is authoritative only when we expect RUB (pre-April 2022).
    # For Apr 2022+ col C in the 2022 sheet is a rough auto-conversion from col B
    # (RSD) by a fixed annual rate and must not be read.
    if (
        amount_original is None
        and layout.col_amount_fallback is not None
        and currency_original == "RUB"
    ):
        amount_original = parse_display_amount(
            _cell(row_display, layout.col_amount_fallback),
        )
    if amount_original is None:
        return None

    rates = monthly_rates[month]
    original_dec = Decimal(str(amount_original))
    amount_app = _convert_to(
        original_dec,
        rate_src=rates["rate_src"],
        rate_target=rates["rate_app"],
    )
    amount_eur = _convert_to(
        original_dec,
        rate_src=rates["rate_src"],
        rate_target=rates["rate_eur"],
    )
    return (
        amount_original,
        currency_original,
        float(amount_app),
        float(amount_eur),
    )


def _apply_housing_heuristics(  # noqa: C901, PLR0912
    *,
    source_type: str,
    source_envelope: str,
    comment: str,
    amount_eur: float,
    default_category_name: str,
) -> str:
    category_name = default_category_name
    source_lower = source_type.lower()
    envelope_lower = source_envelope.lower()
    comment_lower = comment.lower()

    # DIY/DYI is a mixed envelope. Priority (target category in parens):
    #   1) explicit power tool                                     ("инструменты")
    #   2) electronics: soldering, LEDs, Raspberry, ...            ("гаджеты")
    #   3) repair materials: plywood, acrylic, varnish, tape, ...  ("ремонт")
    #   4) default (historically the user used DIY for maker hobby) ("гаджеты")
    if envelope_lower in {"diy", "dyi"}:
        if any(kw in comment_lower for kw in _TOOL_KEYWORDS):
            return "инструменты"
        if any(kw in comment_lower for kw in _ELECTRONICS_KEYWORDS):
            return "гаджеты"
        if any(kw in comment_lower for kw in _MATERIAL_KEYWORDS):
            return "ремонт"
        return default_category_name

    if source_lower == "дача" and any(kw in comment_lower for kw in _TOOL_KEYWORDS):
        return "инструменты"

    if source_type == "аренда" and source_envelope == "релокация":
        category_name = (
            "коммунальные" if amount_eur < _RELOCATION_UTILITIES_THRESHOLD_EUR else "аренда"
        )
    elif source_type == "Household" and source_envelope == "Бытовая техника":
        category_name = "бытовая техника"
    elif source_type == "Ремонт комнаты Ани":
        category_name = "ремонт"
    elif source_lower in _HOUSING_SOURCE_TYPES:
        if any(keyword in comment_lower for keyword in _TOOL_KEYWORDS):
            category_name = "инструменты"
        elif any(keyword in comment_lower for keyword in _REPAIR_KEYWORDS):
            category_name = "ремонт"
        elif any(keyword in comment_lower for keyword in _FURNITURE_KEYWORDS):
            category_name = "мебель"
        elif any(keyword in comment_lower for keyword in _APPLIANCE_KEYWORDS):
            category_name = "бытовая техника"

    return category_name


# ---------------------------------------------------------------------------
# Catalog id resolution helpers (single DB)
# ---------------------------------------------------------------------------


def _resolve_category_id(con, name: str) -> int | None:
    row = con.execute("SELECT id FROM categories WHERE name = ?", [name]).fetchone()
    return int(row[0]) if row else None


def _resolve_event_id(con, name: str) -> int | None:
    row = con.execute("SELECT id FROM events WHERE name = ?", [name]).fetchone()
    return int(row[0]) if row else None


def _resolve_tag_ids(con, names: list[str]) -> list[int]:
    if not names:
        return []
    ids: list[int] = []
    for name in names:
        row = con.execute("SELECT id FROM tags WHERE name = ?", [name]).fetchone()
        if row is None:
            msg = f"tag {name!r} not found in tags; re-seed required"
            raise ValueError(msg)
        ids.append(int(row[0]))
    return ids


def _event_name_from_id(con, event_id: int | None) -> str | None:
    if event_id is None:
        return None
    row = con.execute("SELECT name FROM events WHERE id = ?", [event_id]).fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# Per-year synthetic events: travel + business trip
# ---------------------------------------------------------------------------


def _synthetic_event_for_year(year: int) -> str:
    return f"{SYNTHETIC_EVENT_PREFIX}{year}"


def _business_trip_event_for_year(year: int) -> str | None:
    if year > BUSINESS_TRIP_EVENT_LAST_YEAR:
        return None
    return f"{BUSINESS_TRIP_EVENT_PREFIX}{year}"


# ---------------------------------------------------------------------------
# Per-row resolution: mapping → 3D dimensions
# ---------------------------------------------------------------------------


def _resolve_dimensions(  # noqa: C901, PLR0913, PLR0912
    con,
    *,
    source_type: str,
    source_envelope: str,
    comment: str,
    amount_eur: float,
    year: int,
    travel_event_id: int,
    business_trip_event_id: int | None,
    relocation_event_id: int | None,
) -> tuple[int, int | None, list[int]] | None:
    """Resolve ``(sheet_category, sheet_group)`` to ``(category_id, event_id, tag_ids)``.

    Always consults ``import_mapping`` first (year-scoped, then year=0
    fallback). Then refines ``category_id`` by per-row heuristics that
    pre-baked seed rows cannot encode (housing keyword routing, DIY
    classification, RUB fallback). Returns None when no mapping exists at
    all (caller logs+skips).
    """
    mapping = duckdb_repo.resolve_mapping_for_year(con, source_type, source_envelope, year)
    if mapping is not None:
        category_id = mapping.category_id
        event_id = mapping.event_id
        tag_ids = duckdb_repo.get_mapping_tag_ids(con, mapping.id)
        canonical_default_name = con.execute(
            "SELECT name FROM categories WHERE id = ?",
            [category_id],
        ).fetchone()
        canonical_default = canonical_default_name[0] if canonical_default_name else None
    else:
        try:
            canonical_default = canonical_category_for_source(source_type, source_envelope)
        except ValueError:
            return None
        category_id = _resolve_category_id(con, canonical_default)
        if category_id is None:
            return None
        event_id = None
        tag_ids = _resolve_tag_ids(con, tags_for_source(source_type, source_envelope, year))

    if canonical_default is not None:
        adjusted_name = _apply_housing_heuristics(
            source_type=source_type,
            source_envelope=source_envelope,
            comment=comment,
            amount_eur=amount_eur,
            default_category_name=canonical_default,
        )
        if adjusted_name != canonical_default:
            adjusted_id = _resolve_category_id(con, adjusted_name)
            if adjusted_id is not None:
                category_id = adjusted_id

    if event_id is None:
        ev_name = event_name_for_source(source_type, source_envelope, year)
        if ev_name is not None:
            if ev_name.startswith(BUSINESS_TRIP_EVENT_PREFIX):
                event_id = business_trip_event_id
            elif ev_name.startswith(SYNTHETIC_EVENT_PREFIX):
                event_id = travel_event_id
        elif year > VACATION_EVENT_YEAR_TO:
            source_lower = source_type.lower().strip()
            envelope_lower = source_envelope.lower().strip()
            is_vacation_row = source_lower == "отпуск" or envelope_lower in VACATION_ENVELOPES
            if is_vacation_row:
                # ``seed_config`` only pre-creates synthetic vacation
                # events through ``VACATION_EVENT_YEAR_TO``; rows past
                # that cut-over need a manually-created event so
                # ``auto_tags`` (``отпуск`` / ``путешествия``) can
                # attach. If nobody created one yet the row goes in
                # without any vacation tag — warn loudly so the
                # importer run flags this as an action item instead of
                # silently losing analytics value.
                logger.warning(
                    "Vacation-shaped row in year %d has no synthetic "
                    "event (post-%d cut-over) — create a manual "
                    "event via PWA / Admin API before re-running "
                    "import or the row will lose "
                    "'отпуск'/'путешествия' auto_tags. "
                    "source_type=%r source_envelope=%r comment=%r",
                    year,
                    VACATION_EVENT_YEAR_TO,
                    source_type,
                    source_envelope,
                    comment,
                )

    # Relocation tag implies the relocation event when we don't already have one.
    if event_id is None and tag_ids and relocation_event_id is not None:
        placeholders = ",".join("?" * len(tag_ids))
        tag_names = {
            r[0]
            for r in con.execute(
                f"SELECT name FROM tags WHERE id IN ({placeholders})",  # noqa: S608
                tag_ids,
            ).fetchall()
        }
        if "релокация" in tag_names:
            event_id = relocation_event_id

    return category_id, event_id, tag_ids


# ---------------------------------------------------------------------------
# Shared row-resolution helper (used by both the importer and the report)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ResolutionContext:
    """Per-year event ids needed by ``resolve_row_to_3d``."""

    travel_event_id: int
    business_trip_event_id: int | None
    relocation_event_id: int | None
    russia_trip_event_id: int | None


def build_resolution_context(con, year: int) -> ResolutionContext | None:
    """Look up all per-year event ids needed to resolve sheet rows.

    Returns ``None`` if the synthetic vacation event for *year* is missing,
    which means the catalog has not been seeded for this year yet.

    Behavioral note: callers that consider a missing seed a fatal error
    (``import_year``) translate ``None`` into a ``ValueError`` with a
    "re-run import-catalog" hint. Tolerant callers (the 2D-to-3D
    report) skip the year and continue.
    """
    travel_event_id = _resolve_event_id(con, _synthetic_event_for_year(year))
    if travel_event_id is None:
        return None
    bt_name = _business_trip_event_for_year(year)
    business_trip_event_id = _resolve_event_id(con, bt_name) if bt_name else None
    relocation_event_id = _resolve_event_id(con, RELOCATION_EVENT_NAME)
    russia_trip_event_id = (
        _resolve_event_id(con, RUSSIA_TRIP_EVENT_NAME) if year == RUSSIA_TRIP_FIX_YEAR else None
    )
    return ResolutionContext(
        travel_event_id=travel_event_id,
        business_trip_event_id=business_trip_event_id,
        relocation_event_id=relocation_event_id,
        russia_trip_event_id=russia_trip_event_id,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class RowResolution:
    """Side-effect-free result of resolving a sheet row to 3D dimensions."""

    category_id: int
    category_name: str
    event_id: int | None
    event_name: str | None
    tag_ids: tuple[int, ...]
    tag_names: tuple[str, ...]
    resolution_kind: str


def _union_event_auto_tags(
    con,
    *,
    event_id: int | None,
    tag_ids: list[int] | tuple[int, ...],
) -> list[int]:
    """Return ``tag_ids`` with the event's ``auto_tags`` ids unioned in.

    Historical imports go through ``_resolve_dimensions`` and
    ``resolve_row_to_3d``, both of which build a ``tag_ids`` set from
    the (sheet_category, sheet_group, beneficiary) axes. Once an
    event has been attached, this helper folds in the event's
    ``auto_tags`` so post-2025 vacations land with ``отпуск`` AND
    ``путешествия`` even when the row's sheet axes never said so —
    matching the runtime ``POST /api/expenses`` behaviour. Order is
    preserved for the original ``tag_ids`` (stable under rerun) and
    extras are appended in sorted id order.
    """
    existing = list(dict.fromkeys(int(t) for t in tag_ids))
    if event_id is None:
        return existing
    extras = sheet_mapping.resolve_event_auto_tag_ids(con, event_id)
    for tid in extras:
        if tid not in existing:
            existing.append(tid)
    return existing


def _resolve_tag_names(con, tag_ids: list[int] | tuple[int, ...]) -> tuple[str, ...]:
    if not tag_ids:
        return ()
    placeholders = ",".join("?" * len(tag_ids))
    rows = con.execute(
        f"SELECT name FROM tags WHERE id IN ({placeholders}) ORDER BY name",  # noqa: S608
        list(tag_ids),
    ).fetchall()
    return tuple(r[0] for r in rows)


def _category_name_for_id(con, category_id: int) -> str:
    row = con.execute(
        "SELECT name FROM categories WHERE id = ?",
        [category_id],
    ).fetchone()
    return row[0] if row else ""


def _simulate_post_import_fix(
    con,
    *,
    comment: str,
    year: int,
    russia_trip_event_id: int | None,
) -> tuple[int, int | None] | None:
    """Check whether a post-import fix would override this row.

    Mirrors ``_apply_post_import_fixes_body`` without any DB writes.
    Returns ``(category_id, event_id_or_None)`` when a fix matches.

    The 2018 rent fix is keyed off ``comment == "покупка валюты"`` only
    (the legacy deterministic-id branch was removed along with the
    single-file DB refactor; expense ids are now opaque integers).
    """
    comment_lower = comment.lower().strip()

    for fix_comment, category_name in _POST_IMPORT_CATEGORY_FIXES.items():
        if comment_lower == fix_comment:
            cat_id = _resolve_category_id(con, category_name)
            if cat_id is not None:
                return (cat_id, None)

    if year == 2018 and comment_lower == "покупка валюты":  # noqa: PLR2004
        rent_id = _resolve_category_id(con, "аренда")
        if rent_id is not None:
            return (rent_id, None)

    if year == RUSSIA_TRIP_FIX_YEAR and comment_lower == "билеты в россию август 2026":
        transport_id = _resolve_category_id(con, "транспорт")
        if transport_id is not None and russia_trip_event_id is not None:
            return (transport_id, russia_trip_event_id)

    return None


def resolve_row_to_3d(  # noqa: PLR0913
    con,
    *,
    sheet_category: str,
    sheet_group: str,
    comment: str,
    amount_eur: float,
    year: int,
    travel_event_id: int,
    business_trip_event_id: int | None,
    relocation_event_id: int | None,
    russia_trip_event_id: int | None = None,
    beneficiary_raw: str = "",
) -> RowResolution | None:
    """Resolve a raw sheet row to 3D dimensions with provenance.

    Side-effect-free: no row inserts, no ID allocation, no writes.
    Combines ``_resolve_dimensions``, beneficiary tagging, and
    post-import fix simulation into one call for the 2D-to-3D report.
    """
    mapping = duckdb_repo.resolve_mapping_for_year(
        con,
        sheet_category,
        sheet_group,
        year,
    )
    if mapping is not None:
        pre_heuristic_name = _category_name_for_id(con, mapping.category_id)
        kind_primary = "mapping"
    else:
        try:
            pre_heuristic_name = canonical_category_for_source(
                sheet_category,
                sheet_group,
            )
        except ValueError:
            pre_heuristic_name = None
        kind_primary = "derivation"

    resolved = _resolve_dimensions(
        con,
        source_type=sheet_category,
        source_envelope=sheet_group,
        comment=comment,
        amount_eur=amount_eur,
        year=year,
        travel_event_id=travel_event_id,
        business_trip_event_id=business_trip_event_id,
        relocation_event_id=relocation_event_id,
    )
    if resolved is None:
        return None

    category_id, event_id, tag_ids = resolved
    kind_parts: list[str] = [kind_primary]

    category_name = _category_name_for_id(con, category_id)
    if pre_heuristic_name and category_name != pre_heuristic_name:
        kind_parts.append("heuristic")

    if beneficiary_raw:
        who_key = beneficiary_raw.lower().strip()
        tag_name = _SHEET_BENEFICIARY_TAG_BY_VALUE.get(who_key)
        if tag_name is not None:
            extra_ids = _resolve_tag_ids(con, [tag_name])
            tag_ids = sorted(set(tag_ids) | set(extra_ids))

    postfix = _simulate_post_import_fix(
        con,
        comment=comment,
        year=year,
        russia_trip_event_id=russia_trip_event_id,
    )
    if postfix is not None:
        category_id, fix_event_id = postfix
        category_name = _category_name_for_id(con, category_id)
        if fix_event_id is not None and fix_event_id != event_id:
            # Mirror ``_apply_post_import_fixes_body``: the event_id is
            # being rewritten to ``fix_event_id``, so strip the old
            # event's ``auto_tags`` that the new event does not carry.
            # Without this, the report would show a tag set the DB
            # will never actually contain after the postfix runs.
            new_auto_ids = set(
                sheet_mapping.resolve_event_auto_tag_ids(con, fix_event_id),
            )
            old_auto_ids = (
                set(sheet_mapping.resolve_event_auto_tag_ids(con, event_id))
                if event_id is not None
                else set()
            )
            stale = old_auto_ids - new_auto_ids
            if stale:
                tag_ids = [tid for tid in tag_ids if tid not in stale]
            event_id = fix_event_id
        kind_parts.append("postfix")

    final_tag_ids = _union_event_auto_tags(con, event_id=event_id, tag_ids=tag_ids)

    return RowResolution(
        category_id=category_id,
        category_name=category_name,
        event_id=event_id,
        event_name=_event_name_from_id(con, event_id),
        tag_ids=tuple(final_tag_ids),
        tag_names=_resolve_tag_names(con, final_tag_ids),
        resolution_kind="+".join(kind_parts),
    )


# ---------------------------------------------------------------------------
# Post-import fixes (operate directly on inserted rows by deterministic id /
# comment match; preserved verbatim from the previous importer)
# ---------------------------------------------------------------------------


def _apply_post_import_fixes(
    year: int,
    con,
    *,
    russia_trip_event_id: int | None = None,
) -> None:
    con.execute("BEGIN")
    try:
        _apply_post_import_fixes_body(
            year,
            con,
            russia_trip_event_id=russia_trip_event_id,
        )
        con.execute("COMMIT")
    except Exception:
        duckdb_repo.best_effort_rollback(
            con,
            context=f"_apply_post_import_fixes({year})",
        )
        raise


def _fk_safe_update_expenses(  # noqa: PLR0913
    con,
    *,
    where_clause: str,
    where_params: list,
    set_clause: str,
    set_params: list,
    tag_id_rewrite=None,
) -> list[int]:
    """Run ``UPDATE expenses SET <set_clause> WHERE <where_clause>`` in a
    way that dodges DuckDB 1.5's FK-on-UPDATE limitation.

    DuckDB 1.5 implements ``UPDATE`` as DELETE+INSERT under the hood, and
    its FK validator rejects the statement whenever the parent row is
    still referenced from ``expense_tags`` — even when the UPDATE does
    not touch the primary key. We work around it by snapshotting every
    ``expense_tags`` row for the affected expenses, detaching them,
    running the UPDATE against an FK-clear parent set, and re-attaching
    the children afterwards.

    ``tag_id_rewrite`` is an optional ``(expense_id, tag_ids) ->
    Iterable[int]`` callback that rewrites the tag set per expense
    before the re-attach step (e.g. to strip a previous event's
    ``auto_tags`` and union the new event's ``auto_tags``). Returns
    the list of affected expense ids so callers can do follow-up work.
    """
    affected_ids = [
        row[0]
        for row in con.execute(
            f"SELECT id FROM expenses WHERE {where_clause}",  # noqa: S608
            where_params,
        ).fetchall()
    ]
    if not affected_ids:
        return affected_ids

    placeholders = ",".join(["?"] * len(affected_ids))
    tag_pairs = con.execute(
        f"SELECT expense_id, tag_id FROM expense_tags WHERE expense_id IN ({placeholders})",  # noqa: S608
        affected_ids,
    ).fetchall()
    tags_by_expense: dict[int, list[int]] = {}
    for expense_id, tag_id in tag_pairs:
        tags_by_expense.setdefault(expense_id, []).append(tag_id)

    con.execute(
        f"DELETE FROM expense_tags WHERE expense_id IN ({placeholders})",  # noqa: S608
        affected_ids,
    )
    con.execute(
        f"UPDATE expenses SET {set_clause} WHERE {where_clause}",  # noqa: S608
        [*set_params, *where_params],
    )

    for expense_id in affected_ids:
        tag_ids = tags_by_expense.get(expense_id, [])
        if tag_id_rewrite is not None:
            tag_ids = list(tag_id_rewrite(expense_id, tag_ids))
        for tag_id in dict.fromkeys(tag_ids):
            con.execute(
                "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)"
                " ON CONFLICT DO NOTHING",
                [expense_id, tag_id],
            )
    return affected_ids


def _apply_post_import_fixes_body(
    year: int,
    con,
    *,
    russia_trip_event_id: int | None = None,
) -> None:
    for comment_text, category_name in _POST_IMPORT_CATEGORY_FIXES.items():
        category_id = _resolve_category_id(con, category_name)
        if category_id is None:
            continue
        _fk_safe_update_expenses(
            con,
            where_clause="YEAR(datetime) = ? AND LOWER(COALESCE(comment, '')) = ?",
            where_params=[year, comment_text],
            set_clause="category_id = ?",
            set_params=[category_id],
        )

    rent_category_id = _resolve_category_id(con, "аренда")
    if rent_category_id is not None:
        _fk_safe_update_expenses(
            con,
            where_clause=(
                "YEAR(datetime) = 2018 AND LOWER(COALESCE(comment, '')) = 'покупка валюты'"
            ),
            where_params=[],
            set_clause="category_id = ?",
            set_params=[rent_category_id],
        )

    if year == RUSSIA_TRIP_FIX_YEAR:
        transport_category_id = _resolve_category_id(con, "транспорт")
        if transport_category_id is not None and russia_trip_event_id is not None:
            # Snapshot the old per-expense event id BEFORE the UPDATE so
            # we can strip the previous event's ``auto_tags`` from each
            # affected expense. Without this step the rewrite is
            # additive only: if ``resolve_row_to_3d`` originally
            # attached the row to "отпуск-2026" with
            # {"отпуск", "путешествия"}, rewriting to
            # "russia-trip-2026" would leave those tags behind while
            # also adding whatever the new event carries, producing a
            # tag set that no runtime path would ever write.
            old_event_ids: dict[int, int | None] = {
                row[0]: row[1]
                for row in con.execute(
                    """
                    SELECT id, event_id
                    FROM expenses
                    WHERE YEAR(datetime) = 2026
                      AND LOWER(COALESCE(comment, '')) = 'билеты в россию август 2026'
                    """,
                ).fetchall()
            }
            new_auto_tag_ids = set(
                sheet_mapping.resolve_event_auto_tag_ids(
                    con,
                    russia_trip_event_id,
                ),
            )

            def rewrite_tags(expense_id: int, tag_ids: list[int]) -> list[int]:
                old_event_id = old_event_ids.get(expense_id)
                stale: set[int] = set()
                if old_event_id is not None and old_event_id != russia_trip_event_id:
                    stale = {
                        tid
                        for tid in sheet_mapping.resolve_event_auto_tag_ids(
                            con,
                            old_event_id,
                        )
                        if tid not in new_auto_tag_ids
                    }
                kept = [tid for tid in tag_ids if tid not in stale]
                for auto_id in new_auto_tag_ids:
                    if auto_id not in kept:
                        kept.append(auto_id)
                return kept

            _fk_safe_update_expenses(
                con,
                where_clause=(
                    "YEAR(datetime) = 2026 "
                    "AND LOWER(COALESCE(comment, '')) = 'билеты в россию август 2026'"
                ),
                where_params=[],
                set_clause="category_id = ?, event_id = ?",
                set_params=[transport_category_id, russia_trip_event_id],
                tag_id_rewrite=rewrite_tags,
            )
    logger.info("Applied post-import fixes for %d", year)


# ---------------------------------------------------------------------------
# Public sheet-row iteration helper
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedSheetRow:
    """A sheet row that has cleared the importer's filtering and parsing."""

    row_idx: int
    year: int
    month: int
    sheet_category: str
    sheet_group: str
    comment: str
    beneficiary_raw: str
    amount_original: float
    currency_original: str
    # Amount stored in ``expenses.amount`` (settings.app_currency).
    amount_app: float
    # Amount in EUR, used by the housing heuristic threshold
    # (200 EUR for "аренда/релокация") and by the 2D→3D report.
    amount_eur: float


def iter_parsed_sheet_rows(year: int, *, con=None):
    """Yield ``ParsedSheetRow`` for every importable row of *year*.

    Reuses the same parsing, month filtering, and FX conversion that
    ``import_year`` applies, so consumers (e.g. the 2D-to-3D report) get
    bit-identical inputs to whatever the importer would have processed.

    If *con* is provided, ``_prefetch_monthly_rates`` reuses it
    instead of opening a fresh cursor on the single DB.
    """
    source = duckdb_repo.get_import_source(year)
    if source is None:
        own_con = con is None
        if own_con:
            con = duckdb_repo.get_connection()
        try:
            available = [
                r[0]
                for r in con.execute(
                    "SELECT year FROM import_sources ORDER BY year",
                ).fetchall()
            ]
        finally:
            if own_con:
                con.close()
        msg = f"year {year} not in import_sources. Available years: {available}"
        raise ValueError(msg)
    layout = LAYOUTS[source.layout_key]
    monthly_rates = _prefetch_monthly_rates(year, layout, con=con)

    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.worksheet_name) if source.worksheet_name else ss.sheet1
    all_values = ws.get_all_values()

    for row_idx in range(HEADER_ROWS, len(all_values)):
        row_display = all_values[row_idx]

        month_str = _cell(row_display, layout.col_month)
        if not month_str or not month_str.isdigit():
            continue
        month = int(month_str)
        if not 1 <= month <= MONTHS_IN_YEAR:
            continue

        sheet_category = _cell(row_display, layout.col_category)
        sheet_group = _cell(row_display, layout.col_group)
        if not sheet_category:
            continue

        amounts = _read_amounts_for_row(
            row_display=row_display,
            layout=layout,
            year=year,
            month=month,
            monthly_rates=monthly_rates,
        )
        if amounts is None:
            continue
        amount_original, currency_original, amount_app, amount_eur = amounts

        comment = _cell(row_display, layout.col_comment)
        beneficiary_raw = (
            _cell(row_display, layout.col_beneficiary) if layout.col_beneficiary else ""
        )

        yield ParsedSheetRow(
            row_idx=row_idx,
            year=year,
            month=month,
            sheet_category=sheet_category,
            sheet_group=sheet_group,
            comment=comment,
            beneficiary_raw=beneficiary_raw,
            amount_original=amount_original,
            currency_original=currency_original,
            amount_app=amount_app,
            amount_eur=amount_eur,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def import_year(year: int) -> dict:  # noqa: C901, PLR0915, PLR0912
    """Import all months for *year* from the configured sheet into DuckDB.

    Always destructive for rows whose ``YEAR(datetime) = year`` in the
    single ``data/dinary.duckdb`` (DELETE expense_tags + expenses +
    sheet_logging_jobs for *year*, then re-insert). Runtime rows from
    other years are not touched; the single-file DB is shared with the
    runtime POST path and with every other historical year.

    Rows are inserted with ``client_expense_id=NULL`` — ``UNIQUE`` allows
    multiple NULL values, so re-imports don't collide on the idempotency
    key. There is no server-side stable id for legacy rows anymore; if
    you need to reach a single legacy row, filter by
    ``(YEAR(datetime), sheet_category, sheet_group, amount_original,
    comment)``.

    Failure semantics: this function is destructive-then-populate and
    each call wipes every ledger row whose ``YEAR(datetime) = year``
    before inserting anything. A mid-import crash therefore leaves the
    year partially populated, and the operator's recovery is simply to
    re-run ``inv import-budget --year YYYY``: the leading DELETEs make
    the next attempt self-healing without needing a multi-thousand-row
    transactional envelope around every INSERT. ``insert_expense``
    still owns its own small transaction per row so a single bad row
    (FK miss, constraint violation) does not corrupt its siblings.
    """
    duckdb_repo.init_db()

    con = duckdb_repo.get_connection()
    try:
        ctx = build_resolution_context(con, year)
        if ctx is None:
            msg = (
                f"Synthetic vacation event for year {year} is missing from events. "
                "Re-run `inv import-catalog` so seed_config can create it."
            )
            raise ValueError(msg)
        if year == RUSSIA_TRIP_FIX_YEAR and ctx.russia_trip_event_id is None:
            msg = (
                f"Event {RUSSIA_TRIP_EVENT_NAME!r} is missing from events. "
                "Re-run `inv import-catalog --yes` so seed_config can create it."
            )
            raise ValueError(msg)

        # Wipe order matters: sheet_logging_jobs -> expense_tags ->
        # expenses. DuckDB has no ON DELETE CASCADE; child tables FK
        # into expenses. Restricting each DELETE to rows that belong to
        # *year* leaves runtime rows and other historical years
        # untouched. These three statements run outside an explicit
        # transaction; each DELETE auto-commits. Their combination is
        # idempotent, so a crash between them just leaves a smaller
        # ledger for *year* that the next DELETE will reduce further.
        con.execute(
            "DELETE FROM sheet_logging_jobs WHERE expense_id IN"
            " (SELECT id FROM expenses WHERE YEAR(datetime) = ?)",
            [year],
        )
        con.execute(
            "DELETE FROM expense_tags WHERE expense_id IN"
            " (SELECT id FROM expenses WHERE YEAR(datetime) = ?)",
            [year],
        )
        con.execute("DELETE FROM expenses WHERE YEAR(datetime) = ?", [year])

        created = 0
        errors = 0
        months_seen: set[int] = set()

        for parsed in iter_parsed_sheet_rows(year, con=con):
            try:
                resolved = _resolve_dimensions(
                    con,
                    source_type=parsed.sheet_category,
                    source_envelope=parsed.sheet_group,
                    comment=parsed.comment,
                    amount_eur=parsed.amount_eur,
                    year=year,
                    travel_event_id=ctx.travel_event_id,
                    business_trip_event_id=ctx.business_trip_event_id,
                    relocation_event_id=ctx.relocation_event_id,
                )
            except ValueError:
                logger.exception(
                    "Failed to resolve dimensions for (%r, %r) in year %d",
                    parsed.sheet_category,
                    parsed.sheet_group,
                    year,
                )
                errors += 1
                continue

            if resolved is None:
                logger.warning(
                    "No mapping for (%r, %r) in year %d; skipping row",
                    parsed.sheet_category,
                    parsed.sheet_group,
                    year,
                )
                errors += 1
                continue
            category_id, event_id, tag_ids = resolved

            if parsed.beneficiary_raw:
                who_key = parsed.beneficiary_raw.lower().strip()
                tag_name = _SHEET_BENEFICIARY_TAG_BY_VALUE.get(who_key)
                if tag_name is not None:
                    try:
                        extra_ids = _resolve_tag_ids(con, [tag_name])
                    except ValueError:
                        logger.exception(
                            "Failed to resolve beneficiary tag %r for row %d",
                            tag_name,
                            parsed.row_idx,
                        )
                        errors += 1
                        continue
                    tag_ids = sorted(set(tag_ids) | set(extra_ids))

            tag_ids = _union_event_auto_tags(
                con,
                event_id=event_id,
                tag_ids=tag_ids,
            )

            months_seen.add(parsed.month)
            expense_dt = datetime(year, parsed.month, 1, 12, 0, 0)

            try:
                duckdb_repo.insert_expense(
                    con,
                    client_expense_id=None,
                    expense_datetime=expense_dt,
                    amount=parsed.amount_app,
                    amount_original=parsed.amount_original,
                    currency_original=parsed.currency_original,
                    category_id=category_id,
                    event_id=event_id,
                    comment=parsed.comment,
                    sheet_category=parsed.sheet_category,
                    sheet_group=parsed.sheet_group,
                    tag_ids=tag_ids,
                    enqueue_logging=False,
                )
                created += 1
            except Exception:
                logger.exception(
                    "Failed to insert expense %d-%02d row %d (%s/%s)",
                    year,
                    parsed.month,
                    parsed.row_idx,
                    parsed.sheet_category,
                    parsed.sheet_group,
                )
                errors += 1

        _apply_post_import_fixes(
            year,
            con,
            russia_trip_event_id=ctx.russia_trip_event_id,
        )

        return {
            "year": year,
            "expenses_created": created,
            "errors": errors,
            "months": sorted(months_seen),
        }
    finally:
        con.close()
