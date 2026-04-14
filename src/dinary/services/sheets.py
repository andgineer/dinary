"""Google Sheets read/write via gspread."""

import logging
from datetime import date
from decimal import Decimal

import gspread
from google.oauth2.service_account import Credentials

from dinary.config import settings
from dinary.services.category_store import Category, CategoryStore
from dinary.services.exchange_rate import fetch_eur_rsd_rate

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MIN_CATEGORY_COLS = 3

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


def _month_label(d: date) -> str:
    """Format used to identify month blocks, e.g. '2026-04'."""
    return d.strftime("%Y-%m")


def _find_month_block(ws: gspread.Worksheet, target: date) -> tuple[int, int] | None:
    """Find the row range (start, end) for a month block.

    Scans column A for the month label. Returns (first_row, end_row) where
    both are 1-indexed gspread row numbers. first_row is the header row,
    end_row is exclusive (one past the last category row).

    The month block starts with a header row containing the month label in
    column A (e.g. '2026-04') and ends just before the next month header or
    the end of data.
    """
    label = _month_label(target)
    all_values = ws.col_values(1)

    start_row = None
    for i, val in enumerate(all_values):
        if str(val).strip().startswith(label):
            start_row = i + 1  # convert 0-indexed enumerate to 1-indexed gspread row
            break

    if start_row is None:
        return None

    # end_row: 1-indexed, exclusive — the row *after* the last category row
    end_row = len(all_values) + 1
    for i in range(start_row, len(all_values)):
        cell_val = str(all_values[i]).strip()
        if cell_val and cell_val[:4].isdigit() and "-" in cell_val:
            end_row = i + 1  # convert 0-indexed to 1-indexed
            break

    return (start_row, end_row)


def load_categories(ws: gspread.Worksheet | None = None) -> list[Category]:
    """Read categories from the sheet and update the in-memory cache.

    Categories are extracted from the first month block found in the sheet.
    Each row within a month block has the category name in column B and the
    group name in column C.
    """
    if ws is None:
        ws = _get_sheet().sheet1

    all_values = ws.get_all_values()
    categories: list[Category] = []

    in_block = False
    for row in all_values:
        col_a = row[0].strip() if row else ""
        if col_a and col_a[:4].isdigit() and "-" in col_a:
            if in_block:
                break
            in_block = True
            continue

        if in_block and len(row) >= MIN_CATEGORY_COLS:
            cat_name = row[1].strip()
            group_name = row[2].strip()
            if cat_name:
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


def group_for_category(category_name: str) -> str | None:
    """Look up the group for a category name (uses cache)."""
    if _category_store.expired or not _category_store.categories:
        get_categories()
    return _category_store.group_for(category_name)


def _create_month_block(ws: gspread.Worksheet, target: date) -> tuple[int, int]:
    """Create a new month block by copying the previous month's structure.

    Appends the block at the end of existing data, sets column A to the new
    month label, and zeros out all numeric columns.
    """
    all_values = ws.get_all_values()
    block_starts: list[int] = []

    for i, row in enumerate(all_values):
        col_a = row[0].strip() if row else ""
        if col_a and col_a[:4].isdigit() and "-" in col_a:
            block_starts.append(i)

    if not block_starts:
        raise ValueError("No existing month block found to copy from")

    last_block_start = block_starts[-1]
    last_block_end = len(all_values)

    template_rows = all_values[last_block_start:last_block_end]
    new_start = len(all_values) + 1  # 1-indexed, after all existing data
    label = _month_label(target)

    new_rows: list[list[str]] = []
    for j, row in enumerate(template_rows):
        new_row = list(row)
        if j == 0:
            new_row[0] = label
        for col_idx in range(3, len(new_row)):
            if new_row[col_idx] and _is_numeric(new_row[col_idx]):
                new_row[col_idx] = "0"
        new_rows.append(new_row)

    end_cell = gspread.utils.rowcol_to_a1(
        new_start + len(new_rows) - 1,
        len(new_rows[0]),
    )
    start_cell = gspread.utils.rowcol_to_a1(new_start, 1)
    ws.update(range_name=f"{start_cell}:{end_cell}", values=new_rows)

    logger.info("Created month block %s at row %d", label, new_start)
    return (new_start, new_start + len(new_rows))  # 1-indexed, end is exclusive


def _is_numeric(value: str) -> bool:
    if not value:
        return False
    cleaned = value.replace(",", ".").replace(" ", "")
    try:
        float(cleaned)
    except ValueError:
        return False
    return True


async def write_expense(  # noqa: PLR0913
    amount_rsd: float,
    category: str,
    comment: str,
    expense_date: date,
    rate_col: int = 7,
    amount_col: int = 4,
    eur_col: int = 5,
    comment_col: int = 6,
) -> dict:
    """Write an expense to the Google Sheet.

    Finds the correct month block, locates the category row, and adds the
    amount to the running total. If the month block doesn't exist, creates it.

    Column layout (1-indexed, configurable):
      A(1)=month label, B(2)=category, C(3)=group,
      D(4)=amount RSD, E(5)=amount EUR, F(6)=comment, G(7)=exchange rate

    Returns a dict with the written data for confirmation.
    """
    ws = _get_sheet().sheet1

    block = _find_month_block(ws, expense_date)
    if block is None:
        block = _create_month_block(ws, expense_date)

    start_row, end_row = block

    max_col = max(rate_col, amount_col, eur_col, comment_col)
    range_str = (
        f"{gspread.utils.rowcol_to_a1(start_row, 1)}:"
        f"{gspread.utils.rowcol_to_a1(end_row - 1, max_col)}"
    )
    block_data = ws.get(range_str)

    target_local_idx = None
    for i, row in enumerate(block_data):
        cell_b = row[1].strip() if len(row) > 1 else ""
        if cell_b == category:
            target_local_idx = i
            break

    if target_local_idx is None:
        raise ValueError(
            f"Category '{category}' not found in month block {_month_label(expense_date)}",
        )

    target_row = start_row + target_local_idx

    header_row = block_data[0]
    rate_val = header_row[rate_col - 1].strip() if len(header_row) >= rate_col else ""
    if rate_val and _is_numeric(rate_val):
        rate = Decimal(rate_val.replace(",", "."))
    else:
        rate = await fetch_eur_rsd_rate(expense_date.replace(day=1))
        ws.update_cell(start_row, rate_col, str(rate))
        logger.info("Wrote EUR/RSD rate %s for %s", rate, _month_label(expense_date))

    cat_row = block_data[target_local_idx]

    def _cell(col_idx: int) -> str:
        return cat_row[col_idx - 1].strip() if len(cat_row) >= col_idx else ""

    existing_rsd = _cell(amount_col)
    current_rsd = float(existing_rsd.replace(",", ".")) if _is_numeric(existing_rsd) else 0.0
    new_rsd = current_rsd + amount_rsd

    eur_amount = float(Decimal(str(amount_rsd)) / rate)

    existing_eur = _cell(eur_col)
    current_eur = float(existing_eur.replace(",", ".")) if _is_numeric(existing_eur) else 0.0
    new_eur = current_eur + eur_amount

    updates: list[tuple[int, int, str]] = [
        (target_row, amount_col, f"{new_rsd:.2f}"),
        (target_row, eur_col, f"{new_eur:.2f}"),
    ]

    if comment:
        existing_comment = _cell(comment_col)
        separator = "; " if existing_comment else ""
        updates.append(
            (target_row, comment_col, f"{existing_comment}{separator}{comment}"),
        )

    for row, col, val in updates:
        ws.update_cell(row, col, val)

    logger.info(
        "Wrote expense: %s RSD (%.2f EUR) to %s in %s",
        amount_rsd,
        eur_amount,
        category,
        _month_label(expense_date),
    )

    return {
        "month": _month_label(expense_date),
        "category": category,
        "amount_rsd": amount_rsd,
        "amount_eur": round(eur_amount, 2),
        "new_total_rsd": round(new_rsd, 2),
    }
