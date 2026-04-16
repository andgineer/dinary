"""Import historical Google Sheets data into DuckDB for a given year.

Reads the sheet with raw formulas, parses individual amounts from the
RSD formula column (e.g. =460+373+1500), and creates one expense row
per individual amount in budget_YYYY.duckdb.

For formulas containing cell references (e.g. =550*H134 for EUR amounts),
falls back to the evaluated display value as a single aggregate amount.

Idempotent: uses deterministic expense IDs so re-running is safe.
Does NOT create sheet_sync_jobs — the data is already in the sheet.
"""

import hashlib
import logging
import re
from datetime import date, datetime, timedelta

from gspread.utils import ValueRenderOption

from dinary.services import duckdb_repo
from dinary.services.sheets import (
    COL_AMOUNT_RSD,
    COL_CATEGORY,
    COL_COMMENT,
    COL_GROUP,
    COL_MONTH,
    HEADER_ROWS,
    _cell,
    get_sheet,
)

logger = logging.getLogger(__name__)

_PURE_ADDITIVE_RE = re.compile(r"^[\d.+\s]+$")
_MONTHS_IN_YEAR = 12


def _formula_cell_str(row: list, col_1indexed: int) -> str:
    """Get a formula cell value as string (gspread may return int/float)."""
    idx = col_1indexed - 1
    if len(row) <= idx:
        return ""
    val = row[idx]
    if isinstance(val, int | float):
        return str(val)
    return str(val).strip()


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


def _parse_formula_amounts(formula_raw: str, display_raw: str) -> list[float]:
    """Extract individual amounts from a formula, falling back to display value.

    Pure additive formulas (=460+373+1500) are split into individual amounts.
    Formulas with cell references or multiplication (=550*H134) fall back
    to the display value as a single amount.
    """
    if not formula_raw and not display_raw:
        return []

    if formula_raw.startswith("="):
        body = formula_raw[1:].strip()
        if _PURE_ADDITIVE_RE.match(body):
            parts = body.split("+")
            amounts: list[float] = []
            for part in parts:
                cleaned = part.strip().replace(",", ".")
                if not cleaned:
                    continue
                try:
                    val = float(cleaned)
                    if val != 0:
                        amounts.append(val)
                except ValueError:
                    pass
            if amounts:
                return amounts

    val = _parse_display_amount(display_raw)
    if val is not None:
        return [val]
    return []


def _stable_id(  # noqa: PLR0913
    year: int,
    month: int,
    row_idx: int,
    category: str,
    group: str,
    idx: int,
) -> str:
    """Deterministic expense ID for idempotent import."""
    raw = f"legacy-{year}-{month:02d}-r{row_idx}-{category}-{group}-{idx}"
    short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"legacy-{year}{month:02d}-{short_hash}"


def _ensure_travel_event(year: int) -> int:
    """Pre-create the synthetic travel event before budget connection locks config.duckdb."""
    return duckdb_repo.resolve_travel_event(date(year, 1, 1))


def import_year(year: int) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Import all months for *year* from Google Sheets into DuckDB.

    Resolves the spreadsheet and worksheet from sheet_import_sources
    if a row exists for *year*; otherwise falls back to the default
    spreadsheet configured via DINARY_GOOGLE_SHEETS_SPREADSHEET_ID.

    Returns a summary dict with counts.
    """
    duckdb_repo.init_config_db()
    travel_event_id = _ensure_travel_event(year)

    source = duckdb_repo.get_import_source(year)
    spreadsheet_id = source.spreadsheet_id if source else ""
    worksheet_name = source.worksheet_name if source else ""

    con = duckdb_repo.get_budget_connection(year)

    try:
        con.execute("DELETE FROM expense_tags WHERE expense_id LIKE 'legacy-%'")
        con.execute("DELETE FROM expenses WHERE source = 'legacy_import'")

        ss = get_sheet(spreadsheet_id)
        ws = ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1
        all_values = ws.get_all_values()
        all_formulas = ws.get_all_values(value_render_option=ValueRenderOption.formula)

        created = 0
        skipped = 0
        errors = 0
        months_seen: set[int] = set()

        for row_idx in range(HEADER_ROWS, len(all_values)):
            row_display = all_values[row_idx]
            row_formula = all_formulas[row_idx] if row_idx < len(all_formulas) else row_display

            month_str = _cell(row_display, COL_MONTH)
            if not month_str or not month_str.isdigit():
                continue
            month = int(month_str)
            if not 1 <= month <= _MONTHS_IN_YEAR:
                continue

            category = _cell(row_display, COL_CATEGORY)
            group = _cell(row_display, COL_GROUP)
            if not category:
                continue

            formula_raw = _formula_cell_str(row_formula, COL_AMOUNT_RSD)
            display_raw = _cell(row_display, COL_AMOUNT_RSD)
            amounts = _parse_formula_amounts(formula_raw, display_raw)
            if not amounts:
                continue

            comment_raw = _cell(row_display, COL_COMMENT)
            comments = [c.strip() for c in comment_raw.split(";")] if comment_raw else []

            mapping = duckdb_repo.resolve_mapping_for_year(con, category, group, year)
            if mapping is None:
                logger.warning("No mapping for %s/%s — skipping row", category, group)
                errors += 1
                continue

            category_id = mapping.category_id
            beneficiary_id = mapping.beneficiary_id
            event_id = mapping.event_id
            store_id = mapping.store_id
            tag_ids = mapping.tag_ids

            if group == duckdb_repo.TRAVEL_GROUP and event_id is None:
                event_id = travel_event_id

            months_seen.add(month)

            for i, amount in enumerate(amounts):
                expense_id = _stable_id(year, month, row_idx, category, group, i)
                expense_dt = datetime(year, month, 1, 12, 0, 0) + timedelta(seconds=i)
                comment = comments[i] if i < len(comments) else ""

                try:
                    con.execute(
                        """INSERT INTO expenses
                        (id, datetime, name, amount, currency,
                         category_id, beneficiary_id, event_id, store_id,
                         comment, source)
                        VALUES (?, ?, ?, ?, 'RSD', ?, ?, ?, ?, ?, 'legacy_import')
                        ON CONFLICT DO NOTHING""",
                        [
                            expense_id,
                            expense_dt,
                            category,
                            amount,
                            category_id,
                            beneficiary_id,
                            event_id,
                            store_id,
                            comment,
                        ],
                    )
                    for tid in tag_ids:
                        con.execute(
                            """INSERT INTO expense_tags (expense_id, tag_id)
                            VALUES (?, ?)
                            ON CONFLICT DO NOTHING""",
                            [expense_id, tid],
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

        return {
            "year": year,
            "expenses_created": created,
            "skipped": skipped,
            "errors": errors,
            "months": sorted(months_seen),
        }
    finally:
        con.close()
