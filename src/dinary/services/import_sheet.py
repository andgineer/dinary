"""Import historical Google Sheets data into DuckDB for a given year.

Reads the sheet with raw formulas, parses individual amounts from the
RSD formula column (e.g. =460+373+1500), and creates one expense row
per individual amount in budget_YYYY.duckdb.

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
    _is_numeric,
    get_sheet,
)

logger = logging.getLogger(__name__)


def _parse_formula_amounts(formula: str) -> list[float]:
    """Extract individual amounts from a sheet formula like '=460+373+1500'."""
    if not formula:
        return []
    cleaned = formula.lstrip("=").strip()
    if not cleaned:
        return []
    parts = re.split(r"\+", cleaned)
    amounts: list[float] = []
    for part in parts:
        cleaned = part.strip().replace(",", ".").replace(" ", "")
        if not cleaned:
            continue
        try:
            val = float(cleaned)
            if val != 0:
                amounts.append(val)
        except ValueError:
            logger.warning("Cannot parse amount part: %r from formula %r", part, formula)
    return amounts


def _stable_id(year: int, month: int, category: str, group: str, idx: int) -> str:
    """Deterministic expense ID for idempotent import."""
    raw = f"legacy-{year}-{month:02d}-{category}-{group}-{idx}"
    short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"legacy-{year}{month:02d}-{short_hash}"


def import_year(year: int) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Import all months for *year* from Google Sheets into DuckDB.

    Returns a summary dict with counts.
    """
    duckdb_repo.init_config_db()
    con = duckdb_repo.get_budget_connection(year)

    try:
        ws = get_sheet().sheet1
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

            date_cell = _cell(row_display, 1)
            if not date_cell:
                continue
            try:
                row_date = datetime.strptime(date_cell[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if row_date.year != year:
                continue

            category = _cell(row_display, COL_CATEGORY)
            group = _cell(row_display, COL_GROUP)
            if not category:
                continue

            formula_raw = _cell(row_formula, COL_AMOUNT_RSD)
            amounts = _parse_formula_amounts(formula_raw)
            if not amounts:
                rsd_val = _cell(row_display, COL_AMOUNT_RSD)
                if rsd_val and _is_numeric(rsd_val):
                    val = float(rsd_val.replace(",", ".").replace(" ", ""))
                    if val != 0:
                        amounts = [val]
            if not amounts:
                continue

            comment_raw = _cell(row_display, COL_COMMENT)
            comments = [c.strip() for c in comment_raw.split(";")] if comment_raw else []

            mapping = duckdb_repo.resolve_mapping(con, category, group)
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
                event_id = duckdb_repo.resolve_travel_event(date(year, month, 1))

            months_seen.add(month)

            for i, amount in enumerate(amounts):
                expense_id = _stable_id(year, month, category, group, i)
                expense_dt = datetime(
                    year,
                    month,
                    1,
                    12,
                    0,
                    0,
                ) + timedelta(seconds=i)
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
