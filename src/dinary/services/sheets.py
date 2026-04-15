"""Google Sheets read/write via gspread."""

import logging
from datetime import date
from decimal import Decimal

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import ValueInputOption, ValueRenderOption

from dinary.config import settings
from dinary.services.category_store import Category, CategoryStore
from dinary.services.exchange_rate import fetch_eur_rsd_rate

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 1-indexed column numbers matching the actual Google Sheet layout:
#   A=Date  B=RSD(formula)  C=EUR(formula)  D=Category  E=Group
#   F=Comment  G=Month(formula)  H=Rate
COL_DATE = 1
COL_AMOUNT_RSD = 2
COL_CATEGORY = 4
COL_GROUP = 5
COL_COMMENT = 6
COL_MONTH = 7
COL_RATE_EUR = 8

HEADER_ROWS = 1

_gc: gspread.Client | None = None
_category_store = CategoryStore()


def _get_client() -> gspread.Client:
    global _gc  # noqa: PLW0603
    if _gc is None:
        creds = Credentials.from_service_account_file(
            str(settings.google_sheets_credentials_path),
            scopes=SCOPES,
        )
        _gc = gspread.authorize(creds)
    return _gc


def _get_sheet() -> gspread.Spreadsheet:
    return _get_client().open_by_key(settings.google_sheets_spreadsheet_id)


def _cell(row: list[str], col_1indexed: int) -> str:
    """Safely get a stripped cell value using a 1-indexed column number."""
    idx = col_1indexed - 1
    return row[idx].strip() if len(row) > idx else ""


def _is_numeric(value: str) -> bool:
    if not value:
        return False
    cleaned = value.replace(",", ".").replace(" ", "")
    try:
        float(cleaned)
    except ValueError:
        return False
    return True


def _fmt_amount(amount: float) -> str:
    """Format as integer when possible, e.g. 1500.0 → '1500'."""
    return str(int(amount)) if amount == int(amount) else f"{amount:.2f}"


def load_categories(ws: gspread.Worksheet | None = None) -> list[Category]:
    """Read unique (category, group) pairs from the sheet.

    Scans every data row (skipping the header) and deduplicates by
    (category, group), preserving first-seen order.
    """
    if ws is None:
        ws = _get_sheet().sheet1

    all_values = ws.get_all_values()
    seen: set[tuple[str, str]] = set()
    categories: list[Category] = []

    for row in all_values[HEADER_ROWS:]:
        cat_name = _cell(row, COL_CATEGORY)
        group_name = _cell(row, COL_GROUP)
        if cat_name and (cat_name, group_name) not in seen:
            seen.add((cat_name, group_name))
            categories.append(Category(name=cat_name, group=group_name))

    _category_store.load(categories)
    logger.info("Loaded %d categories from sheet", len(categories))
    return categories


def get_categories() -> list[Category]:
    """Return cached categories, refreshing if cache has expired."""
    if _category_store.expired or not _category_store.categories:
        ws = _get_sheet().sheet1
        load_categories(ws)
    return _category_store.categories


def validate_category(category_name: str, group: str) -> bool:
    """Check whether (category, group) exists in the sheet."""
    if _category_store.expired or not _category_store.categories:
        get_categories()
    return _category_store.has_category(category_name, group)


def _find_category_row(
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
) -> int | None:
    """Find the 1-indexed row for a (month, category, group) triple."""
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        month_val = _cell(row, COL_MONTH)
        cat_val = _cell(row, COL_CATEGORY)
        grp_val = _cell(row, COL_GROUP)
        if month_val == str(target_month) and cat_val == category and grp_val == group:
            return i
    return None


def _get_month_rate(all_values: list[list[str]], target_month: int) -> str | None:
    """Return the first non-empty EUR rate found for *target_month*."""
    for row in all_values[HEADER_ROWS:]:
        if _cell(row, COL_MONTH) == str(target_month):
            rate_val = _cell(row, COL_RATE_EUR)
            if rate_val and _is_numeric(rate_val):
                return rate_val
    return None


def _find_month_range(
    all_values: list[list[str]],
    month: int,
) -> tuple[int, int] | None:
    """Return (first_row, last_row) 1-indexed for a contiguous month block."""
    first: int | None = None
    last: int | None = None
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        if _cell(row, COL_MONTH) == str(month):
            if first is None:
                first = i
            last = i
    if first is None or last is None:
        return None
    return (first, last)


def _create_month_rows(
    ws: gspread.Worksheet,
    all_values: list[list[str]],
    target: date,
) -> None:
    """Create rows for a new month by copying the previous month's rows.

    Uses CopyPaste API to preserve formulas in C (EUR auto-calc) and
    G (month from date).  Then clears amounts (B), comments (F), rate (H),
    and sets the new date (A).
    """
    target_month = target.month
    prev_month = target_month - 1 if target_month > 1 else 12

    src = _find_month_range(all_values, prev_month)
    if src is None:
        raise ValueError(f"No rows found for month {prev_month} to copy from")

    src_start, src_end = src
    num_rows = src_end - src_start + 1
    dest_start = len(all_values) + 1

    ws.add_rows(num_rows)

    sheet_id = ws.id
    num_cols = len(all_values[0]) if all_values else COL_RATE_EUR

    ws.spreadsheet.batch_update(
        {
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": sheet_id,
                            "startRowIndex": src_start - 1,
                            "endRowIndex": src_end,
                            "startColumnIndex": 0,
                            "endColumnIndex": num_cols,
                        },
                        "destination": {
                            "sheetId": sheet_id,
                            "startRowIndex": dest_start - 1,
                            "endRowIndex": dest_start - 1 + num_rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": num_cols,
                        },
                        "pasteType": "PASTE_NORMAL",
                    },
                },
            ],
        },
    )

    date_str = target.replace(day=1).strftime("%Y-%m-%d")
    batch: list[dict] = []
    for i in range(num_rows):
        row = dest_start + i
        batch.extend(
            [
                {"range": f"A{row}", "values": [[date_str]]},
                {"range": f"B{row}", "values": [[""]]},
                {"range": f"F{row}", "values": [[""]]},
                {"range": f"H{row}", "values": [[""]]},
            ],
        )
    ws.batch_update(batch, value_input_option=ValueInputOption.user_entered)

    logger.info("Created %d rows for month %d", num_rows, target_month)


def _resolve_row(  # noqa: PLR0913
    ws: gspread.Worksheet,
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
    expense_date: date,
) -> int:
    """Return the 1-indexed row for (month, category, group), creating the month if needed."""
    target_row = _find_category_row(all_values, target_month, category, group)
    if target_row is not None:
        return target_row

    _create_month_rows(ws, all_values, expense_date)
    refreshed = ws.get_all_values()
    target_row = _find_category_row(refreshed, target_month, category, group)
    if target_row is None:
        raise ValueError(
            f"Category '{category}' (group '{group}') not found for month {target_month}",
        )
    return target_row


async def _ensure_rate(
    ws: gspread.Worksheet,
    all_values: list[list[str]],
    target_month: int,
    target_row: int,
    expense_date: date,
) -> Decimal:
    """Return the EUR/RSD rate for the month, fetching and storing it if absent."""
    rate_str = _get_month_rate(all_values, target_month)
    if rate_str:
        return Decimal(rate_str.replace(",", "."))

    rate = await fetch_eur_rsd_rate(expense_date.replace(day=1))
    ws.update_cell(target_row, COL_RATE_EUR, str(rate))
    logger.info("Wrote EUR/RSD rate %s for month %d", rate, target_month)
    return rate


def _append_to_rsd_formula(ws: gspread.Worksheet, row: int, amount_rsd: float) -> None:
    """Append +amount to the formula in column B (e.g. =460+373 → =460+373+1500)."""
    rsd_addr = gspread.utils.rowcol_to_a1(row, COL_AMOUNT_RSD)
    existing = ws.acell(rsd_addr, value_render_option=ValueRenderOption.formula).value

    amount_str = _fmt_amount(amount_rsd)
    formula = (
        f"{existing}+{amount_str}" if existing and existing.startswith("=") else f"={amount_str}"
    )

    ws.update(
        range_name=rsd_addr,
        values=[[formula]],
        value_input_option=ValueInputOption.user_entered,
    )


def _append_comment(ws: gspread.Worksheet, row: int, row_data: list[str], comment: str) -> None:
    """Append a comment to column F, semicolon-separated."""
    existing = _cell(row_data, COL_COMMENT)
    separator = "; " if existing else ""
    ws.update_cell(row, COL_COMMENT, f"{existing}{separator}{comment}")


async def write_expense(
    amount_rsd: float,
    category: str,
    group: str,
    comment: str,
    expense_date: date,
) -> dict:
    """Append an expense to the Google Sheet.

    Appends +amount to the formula in column B (RSD).
    Columns C (EUR) and G (month) are formula-driven and left untouched.
    """
    ws = _get_sheet().sheet1
    all_values = ws.get_all_values()
    target_month = expense_date.month

    target_row = _resolve_row(ws, all_values, target_month, category, group, expense_date)
    row_data = all_values[target_row - 1]

    rate = await _ensure_rate(ws, all_values, target_month, target_row, expense_date)
    _append_to_rsd_formula(ws, target_row, amount_rsd)

    if comment:
        _append_comment(ws, target_row, row_data, comment)

    existing_rsd = _cell(row_data, COL_AMOUNT_RSD)
    prev_total = float(existing_rsd.replace(",", ".")) if _is_numeric(existing_rsd) else 0.0
    eur_amount = float(Decimal(str(amount_rsd)) / rate)
    month_label = expense_date.strftime("%Y-%m")

    logger.info(
        "Wrote expense: %s RSD (%.2f EUR) to %s/%s in %s",
        amount_rsd,
        eur_amount,
        category,
        group,
        month_label,
    )

    return {
        "month": month_label,
        "category": category,
        "amount_rsd": amount_rsd,
        "amount_eur": round(eur_amount, 2),
        "new_total_rsd": round(prev_total + amount_rsd, 2),
    }
