"""Import historical Google Sheets data into DuckDB for a given year.

Reads the sheet, takes the evaluated display value as a single aggregate
amount per row (no formula splitting), converts to EUR via NBS rates,
and creates one expense row per sheet row in budget_YYYY.duckdb.

Idempotent: uses deterministic expense IDs so re-running is safe.
Does NOT create sheet_sync_jobs -- the data is already in the sheet.
"""

import dataclasses
import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal

from dinary.services import duckdb_repo
from dinary.services.seed_config import (
    _beneficiary_for_source,
    _canonical_category_for_source,
    _event_name_for_source,
    _sphere_for_source,
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
    # Used for 2022, where col B (RSD) is empty for Jan-Mar (pre-relocation,
    # the user lived in Russia and recorded amounts in RUB in col C).
    # The fallback amount is in the same currency as resolved by
    # _resolve_currency() for the given year+month.
    col_amount_fallback: int | None = None


LAYOUTS: dict[str, SheetLayout] = {
    "default": SheetLayout(
        col_amount=2,
        col_category=4,
        col_group=5,
        col_comment=6,
        col_month=7,
    ),
    "rub": SheetLayout(
        col_amount=2,
        col_category=4,
        col_group=5,
        col_comment=6,
        col_month=7,
        currency="RUB",
    ),
    "rub_fallback": SheetLayout(
        col_amount=2,
        col_category=4,
        col_group=5,
        col_comment=6,
        col_month=7,
        col_amount_fallback=3,
    ),
    "rub_6col": SheetLayout(
        col_amount=2,
        col_category=3,
        col_group=4,
        col_comment=5,
        col_month=6,
        currency="RUB",
    ),
    "rub_2016": SheetLayout(
        col_amount=2,
        col_category=3,
        col_group=4,
        col_comment=5,
        col_month=8,
        currency="RUB",
    ),
}

_MONTHS_IN_YEAR = 12

# 2022 Apr+ is RSD; Jan-Mar is RUB
_RUB_RSD_TRANSITION_YEAR = 2022
_RUB_RSD_TRANSITION_MONTH = 4
_RELOCATION_UTILITIES_THRESHOLD_EUR = 200
_RUSSIA_TRIP_FIX_YEAR = 2026
_RUSSIA_TRIP_EVENT_NAME = "поездка в Россию"
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


def _parse_display_amount(display: str) -> float | None:
    """Parse a displayed amount like '30,805' or '1 500.50'."""
    if not display:
        return None
    cleaned = display.replace(" ", "").replace(",", "")
    try:
        val = float(cleaned)
        return val if val != 0 else None
    except ValueError:
        return None


def _resolve_currency(year: int, month: int, layout: SheetLayout) -> str:
    """Determine the original currency for this row based on year/month and layout."""
    if layout.currency != "RSD":
        return layout.currency
    if year < _RUB_RSD_TRANSITION_YEAR:
        return "RUB"
    if year == _RUB_RSD_TRANSITION_YEAR and month < _RUB_RSD_TRANSITION_MONTH:
        return "RUB"
    return "RSD"


def _resolve_layout(year: int, layout_key: str) -> SheetLayout:  # noqa: ARG001
    return LAYOUTS[layout_key]


def _stable_id(year: int, month: int, row_idx: int, category: str, group: str) -> str:
    """Deterministic expense ID for idempotent import."""
    raw = f"legacy-{year}-{month:02d}-r{row_idx}-{category}-{group}"
    short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"legacy-{year}{month:02d}-{short_hash}"


def _ensure_travel_event(year: int) -> int:
    """Pre-create the synthetic travel event before budget connection locks config.duckdb."""
    return duckdb_repo.resolve_travel_event(date(year, 1, 1))


def _prefetch_monthly_rates(year: int, layout: SheetLayout) -> dict[int, dict[str, Decimal]]:
    """Pre-fetch NBS rates for all months in the year, keyed by month.

    Opens and closes config_con internally so it doesn't conflict with budget_con.
    Returns {month: {currency: eur_equivalent_factor}}.
    """
    rates: dict[int, dict[str, Decimal]] = {}
    config_con = duckdb_repo.get_config_connection(read_only=False)
    try:
        for month in range(1, _MONTHS_IN_YEAR + 1):
            currency = _resolve_currency(year, month, layout)
            if currency == "EUR":
                rates[month] = {}
                continue
            rate_date = date(year, month, 1)
            from dinary.services.nbs import get_rate

            rate_cur = get_rate(config_con, rate_date, currency)
            rate_eur = get_rate(config_con, rate_date, "EUR")
            rates[month] = {"rate_cur": rate_cur, "rate_eur": rate_eur}
    finally:
        config_con.close()
    return rates


def _convert_with_prefetched(
    amount_original: Decimal,
    currency_original: str,
    month_rates: dict[str, Decimal],
) -> Decimal:
    """Convert using pre-fetched rates (avoids opening config_con during import)."""
    if currency_original == "EUR":
        return amount_original
    rate_cur = month_rates["rate_cur"]
    rate_eur = month_rates["rate_eur"]
    return (amount_original * rate_cur / rate_eur).quantize(Decimal("0.01"))


def _read_amounts_for_row(
    *,
    row_display: list[str],
    layout: SheetLayout,
    year: int,
    month: int,
    monthly_rates: dict[int, dict[str, Decimal]],
) -> tuple[float, str, float] | None:
    amount_original = _parse_display_amount(_cell(row_display, layout.col_amount))
    currency_original = _resolve_currency(year, month, layout)

    # Fallback column is authoritative only when we expect RUB (pre-April 2022).
    # For Apr 2022+ col C in the 2022 sheet is a rough auto-conversion from col B
    # (RSD) by a fixed annual rate and must not be read.
    if (
        amount_original is None
        and layout.col_amount_fallback is not None
        and currency_original == "RUB"
    ):
        amount_original = _parse_display_amount(
            _cell(row_display, layout.col_amount_fallback),
        )
    if amount_original is None:
        return None

    amount_eur_decimal = _convert_with_prefetched(
        Decimal(str(amount_original)),
        currency_original,
        monthly_rates[month],
    )
    return amount_original, currency_original, float(amount_eur_decimal)


def _lookup_id_by_name(
    con,
    *,
    table: str,
    name: str,
) -> int | None:
    query_by_table = {
        "categories": "SELECT id FROM config.categories WHERE name = ?",
        "family_members": "SELECT id FROM config.family_members WHERE name = ?",
        "spheres_of_life": "SELECT id FROM config.spheres_of_life WHERE name = ?",
    }
    row = con.execute(query_by_table[table], [name]).fetchone()
    return row[0] if row else None


def _apply_housing_heuristics(
    *,
    source_type: str,
    source_envelope: str,
    comment: str,
    amount_eur: float,
    default_category_name: str,
) -> str:
    category_name = default_category_name
    source_lower = source_type.lower()
    comment_lower = comment.lower()

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


def _fallback_dimensions_from_plan(  # noqa: PLR0913
    con,
    *,
    source_type: str,
    source_envelope: str,
    comment: str,
    amount_eur: float,
    year: int,
    travel_event_id: int,
) -> tuple[int, int | None, int | None, int | None] | None:
    try:
        category_name = _canonical_category_for_source(source_type, source_envelope)
    except ValueError:
        return None
    category_name = _apply_housing_heuristics(
        source_type=source_type,
        source_envelope=source_envelope,
        comment=comment,
        amount_eur=amount_eur,
        default_category_name=category_name,
    )
    category_id = _lookup_id_by_name(con, table="categories", name=category_name)
    if category_id is None:
        return None

    beneficiary_id = None
    beneficiary_name = _beneficiary_for_source(source_type, source_envelope)
    if beneficiary_name is not None:
        beneficiary_id = _lookup_id_by_name(con, table="family_members", name=beneficiary_name)

    sphere_of_life_id = None
    sphere_name = _sphere_for_source(source_type, source_envelope)
    if sphere_name is not None:
        sphere_of_life_id = _lookup_id_by_name(con, table="spheres_of_life", name=sphere_name)

    event_name = _event_name_for_source(source_type, source_envelope, year)
    event_id = travel_event_id if event_name is not None else None
    return category_id, beneficiary_id, event_id, sphere_of_life_id


def _apply_post_import_fixes(year: int, con, *, russia_trip_event_id: int | None = None) -> None:
    for comment_text, category_name in _POST_IMPORT_CATEGORY_FIXES.items():
        category_id = _lookup_id_by_name(con, table="categories", name=category_name)
        if category_id is None:
            continue
        con.execute(
            """
            UPDATE expenses
            SET category_id = ?
            WHERE source = 'sheet_import'
              AND YEAR(datetime) = ?
              AND LOWER(COALESCE(comment, '')) = ?
            """,
            [category_id, year, comment_text],
        )

    rent_category_id = _lookup_id_by_name(con, table="categories", name="аренда")
    if rent_category_id is not None:
        con.execute(
            """
            UPDATE expenses
            SET category_id = ?
            WHERE source = 'sheet_import'
              AND YEAR(datetime) = 2018
              AND (id = 'legacy-201806-269ec46a' OR LOWER(COALESCE(comment, '')) = 'покупка валюты')
            """,
            [rent_category_id],
        )

    if year == _RUSSIA_TRIP_FIX_YEAR:
        transport_category_id = _lookup_id_by_name(con, table="categories", name="транспорт")
        if transport_category_id is not None and russia_trip_event_id is not None:
            con.execute(
                """
                UPDATE expenses
                SET category_id = ?, event_id = ?, beneficiary_id = NULL
                WHERE source = 'sheet_import'
                  AND YEAR(datetime) = 2026
                  AND LOWER(COALESCE(comment, '')) = 'билеты в россию август 2026'
                """,
                [transport_category_id, russia_trip_event_id],
            )
    logger.info("Applied post-import fixes for %d", year)


def import_year(year: int, *, wipe_all: bool = False) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Import all months for *year* from Google Sheets into DuckDB.

    Returns a summary dict with counts.
    """
    duckdb_repo.init_config_db()
    travel_event_id = _ensure_travel_event(year)
    russia_trip_event_id = None
    if year == _RUSSIA_TRIP_FIX_YEAR:
        russia_trip_event_id = duckdb_repo.ensure_event(
            name=_RUSSIA_TRIP_EVENT_NAME,
            date_from=date(_RUSSIA_TRIP_FIX_YEAR, 8, 1),
            date_to=date(_RUSSIA_TRIP_FIX_YEAR, 8, 31),
            comment="Auto-created from import post-fix",
        )

    source = duckdb_repo.get_import_source(year)
    if source is None:
        msg = f"sheet_import_sources is missing a row for year {year}"
        raise ValueError(msg)

    spreadsheet_id = source.spreadsheet_id
    worksheet_name = source.worksheet_name
    layout_key = source.layout_key
    layout = _resolve_layout(year, layout_key)

    monthly_rates = _prefetch_monthly_rates(year, layout)

    con = duckdb_repo.get_budget_connection(year)

    try:
        if wipe_all:
            con.execute("DELETE FROM expenses")
        else:
            con.execute("DELETE FROM expenses WHERE source = 'sheet_import'")

        ss = get_sheet(spreadsheet_id)
        ws = ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1
        all_values = ws.get_all_values()

        created = 0
        skipped = 0
        errors = 0
        months_seen: set[int] = set()

        for row_idx in range(HEADER_ROWS, len(all_values)):
            row_display = all_values[row_idx]

            month_str = _cell(row_display, layout.col_month)
            if not month_str or not month_str.isdigit():
                continue
            month = int(month_str)
            if not 1 <= month <= _MONTHS_IN_YEAR:
                continue

            category = _cell(row_display, layout.col_category)
            group = _cell(row_display, layout.col_group)
            if not category:
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
            amount_original, currency_original, amount_eur = amounts

            comment = _cell(row_display, layout.col_comment)

            mapping = duckdb_repo.resolve_mapping_for_year(con, category, group, year)
            if mapping is None:
                fallback = _fallback_dimensions_from_plan(
                    con,
                    source_type=category,
                    source_envelope=group,
                    comment=comment,
                    amount_eur=amount_eur,
                    year=year,
                    travel_event_id=travel_event_id,
                )
                if fallback is None:
                    logger.warning("No mapping for %s/%s — skipping row", category, group)
                    errors += 1
                    continue
                category_id, beneficiary_id, event_id, sphere_of_life_id = fallback
            else:
                category_id = mapping.category_id
                beneficiary_id = mapping.beneficiary_id
                event_id = mapping.event_id
                sphere_of_life_id = mapping.sphere_of_life_id

                try:
                    canonical_default = _canonical_category_for_source(category, group)
                except ValueError:
                    canonical_default = None
                if canonical_default is not None:
                    adjusted_name = _apply_housing_heuristics(
                        source_type=category,
                        source_envelope=group,
                        comment=comment,
                        amount_eur=amount_eur,
                        default_category_name=canonical_default,
                    )
                    if adjusted_name != canonical_default:
                        adjusted_id = _lookup_id_by_name(
                            con,
                            table="categories",
                            name=adjusted_name,
                        )
                        if adjusted_id is not None:
                            category_id = adjusted_id

            if event_id is None:
                event_name = _event_name_for_source(category, group, year)
                if event_name is not None:
                    event_id = travel_event_id

            months_seen.add(month)

            expense_id = _stable_id(year, month, row_idx, category, group)
            expense_dt = datetime(year, month, 1, 12, 0, 0)

            try:
                con.execute(
                    """INSERT INTO expenses
                    (id, datetime, name, amount, amount_original, currency_original,
                     category_id, beneficiary_id, event_id, sphere_of_life_id,
                     comment, source, source_type, source_envelope)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sheet_import', ?, ?)
                    ON CONFLICT DO NOTHING""",
                    [
                        expense_id,
                        expense_dt,
                        category,
                        amount_eur,
                        amount_original,
                        currency_original,
                        category_id,
                        beneficiary_id,
                        event_id,
                        sphere_of_life_id,
                        comment,
                        category,
                        group,
                    ],
                )
                created += 1
            except Exception:
                logger.exception(
                    "Failed to insert expense %s for %s/%s",
                    expense_id,
                    category,
                    group,
                )
                errors += 1

        _apply_post_import_fixes(year, con, russia_trip_event_id=russia_trip_event_id)

        return {
            "year": year,
            "expenses_created": created,
            "skipped": skipped,
            "errors": errors,
            "months": sorted(months_seen),
        }
    finally:
        con.close()
