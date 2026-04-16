"""Google Sheets sync layer: idempotent projection of DuckDB aggregates into the sheet."""

import asyncio
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

import gspread
from gspread.utils import ValueInputOption, ValueRenderOption

from dinary.services import duckdb_repo
from dinary.services.exchange_rate import fetch_eur_rsd_rate
from dinary.services.sheets import (
    COL_AMOUNT_RSD,
    COL_COMMENT,
    COL_RATE_EUR,
    append_comment,
    append_to_rsd_formula,
    create_month_rows,
    find_category_row,
    find_month_range,
    fmt_amount,
    get_month_rate,
    get_sheet,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Targeted single-row sync (primary path after each expense)
# ---------------------------------------------------------------------------


def schedule_sync(  # noqa: PLR0913
    year: int,
    month: int,
    sheet_category: str,
    sheet_group: str,
    amount: float,
    comment: str,
    expense_date: date,
) -> None:
    """Fire-and-forget sync of a single expense row after API response."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _async_sync_row(
                year,
                month,
                sheet_category,
                sheet_group,
                amount,
                comment,
                expense_date,
            ),
        )
    except RuntimeError:
        logger.debug("No event loop, skipping fire-and-forget sync")


async def _async_sync_row(  # noqa: PLR0913
    year: int,
    month: int,
    sheet_category: str,
    sheet_group: str,
    amount: float,
    comment: str,
    expense_date: date,
) -> None:
    """Async wrapper: fetch rate, then append to the single affected sheet row."""
    try:
        rate = None
        try:
            rate = await fetch_eur_rsd_rate(expense_date.replace(day=1))
        except (OSError, ValueError):
            logger.debug("Could not fetch exchange rate for %d-%02d", year, month)

        await asyncio.to_thread(
            _sync_single_row,
            year,
            month,
            sheet_category,
            sheet_group,
            amount,
            comment,
            expense_date,
            rate,
        )
    except (OSError, ValueError, gspread.exceptions.GSpreadException):
        logger.exception(
            "Background row sync failed for %s/%s %d-%02d",
            sheet_category,
            sheet_group,
            year,
            month,
        )


def _sync_single_row(  # noqa: PLR0913
    year: int,
    month: int,
    sheet_category: str,
    sheet_group: str,
    amount: float,
    comment: str,
    expense_date: date,
    rate: Decimal | None = None,
) -> None:
    """Append a single expense amount to the target sheet row.

    This is the hot path — touches only 1 row instead of rebuilding the entire month.
    The dirty job in sheet_sync_jobs is NOT cleared here; it stays as a durability
    guarantee so ``inv sync`` (full rebuild) can recover anything this path missed.
    """
    ws = get_sheet().sheet1
    all_values = ws.get_all_values()

    month_range = find_month_range(all_values, month)
    if month_range is None:
        create_month_rows(ws, all_values, expense_date)
        all_values = ws.get_all_values()
        month_range = find_month_range(all_values, month)
        if month_range is None:
            logger.error("Failed to create month block for %d-%02d", year, month)
            return

    rate_str = get_month_rate(all_values, month)
    if not rate_str and rate:
        ws.update_cell(month_range[0], COL_RATE_EUR, str(rate))

    row = find_category_row(all_values, month, sheet_category, sheet_group)
    if row is None:
        logger.error("Row not found for %s/%s in month %d", sheet_category, sheet_group, month)
        return

    append_to_rsd_formula(ws, row, amount)

    if comment:
        row_data = all_values[row - 1]
        append_comment(ws, row, row_data, comment)

    logger.info("Synced %s/%s +%s for %d-%02d", sheet_category, sheet_group, amount, year, month)


# ---------------------------------------------------------------------------
# Full-month rebuild (fallback for inv sync / recovery)
# ---------------------------------------------------------------------------


def _build_aggregates(
    con: duckdb_repo.duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> dict[tuple[str, str], dict] | None:
    """Aggregate expenses by (sheet_category, sheet_group). Returns None if no expenses."""
    expenses = duckdb_repo.get_month_expenses(con, year, month)
    if not expenses:
        return None

    aggregates: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"total_rsd": Decimal(0), "amounts": [], "comments": []},
    )
    for exp in expenses:
        result = duckdb_repo.reverse_lookup_mapping(
            con,
            exp.category_id,
            exp.beneficiary_id,
            exp.event_id,
            exp.tag_ids,
        )
        if result is None:
            logger.warning(
                "No reverse mapping for expense %s (cat=%d, ben=%s, ev=%s)",
                exp.id,
                exp.category_id,
                exp.beneficiary_id,
                exp.event_id,
            )
            continue

        sheet_cat, sheet_group = result
        key = (sheet_cat, sheet_group)
        amt = exp.amount
        aggregates[key]["total_rsd"] += amt
        aggregates[key]["amounts"].append(amt)
        if exp.comment:
            aggregates[key]["comments"].append(exp.comment)
    return aggregates


def _write_aggregates_to_sheet(  # noqa: C901
    ws,
    all_values: list[list[str]],
    month: int,
    aggregates: dict[tuple[str, str], dict],
) -> int:
    """Rewrite all aggregated expense data for the month. Returns number of cells written.

    Owned cells (fully overwritten): RSD amount formula, comment.
    Not owned (preserved): date, EUR formula, month formula, rate, category, group.
    """
    resolved: list[tuple[str, dict, str]] = []
    rsd_addrs: list[str] = []
    for (sheet_cat, sheet_group), data in aggregates.items():
        row = find_category_row(all_values, month, sheet_cat, sheet_group)
        if row is None:
            logger.warning(
                "Sheet row not found for %s/%s in month %d",
                sheet_cat,
                sheet_group,
                month,
            )
            continue

        amounts = [fmt_amount(float(exp_amt)) for exp_amt in data.get("amounts", [])]
        formula = "=" + "+".join(amounts) if amounts else f"={fmt_amount(float(data['total_rsd']))}"
        rsd_addr = gspread.utils.rowcol_to_a1(row, COL_AMOUNT_RSD)
        resolved.append((rsd_addr, data, formula))
        rsd_addrs.append(rsd_addr)

    if not resolved:
        return 0

    existing_formulas = ws.batch_get(
        rsd_addrs,
        value_render_option=ValueRenderOption.formula,
    )

    batch: list[dict] = []
    for (rsd_addr, data, formula), existing_vr in zip(resolved, existing_formulas, strict=False):
        existing_str = ""
        if existing_vr and existing_vr[0]:
            existing_str = str(existing_vr[0][0])

        if existing_str.startswith("="):
            # Skip write when the existing formula sums to the same total.
            # Safe while expense edits/deletes are not supported.
            existing_val = existing_str.lstrip("=")
            try:
                parts = [p.strip() for p in existing_val.split("+")]
                existing_total = sum(Decimal(p) for p in parts if p)
            except (ValueError, ArithmeticError):
                existing_total = Decimal(0)

            if existing_total == data["total_rsd"]:
                continue

        batch.append({"range": rsd_addr, "values": [[formula]]})

        if data["comments"]:
            comment_text = "; ".join(data["comments"])
            comment_addr = gspread.utils.rowcol_to_a1(
                gspread.utils.a1_to_rowcol(rsd_addr)[0],
                COL_COMMENT,
            )
            batch.append({"range": comment_addr, "values": [[comment_text]]})

    if batch:
        ws.batch_update(batch, value_input_option=ValueInputOption.user_entered)
    return len(batch)


def _sync_month_core(
    year: int,
    month: int,
    rate: Decimal | None = None,
) -> None:
    """Full-month rebuild: re-derive all sheet rows from DuckDB aggregates."""
    con = duckdb_repo.get_budget_connection(year)
    try:
        aggregates = _build_aggregates(con, year, month)
        if aggregates is None:
            logger.info("No expenses for %d-%02d, clearing sync job", year, month)
            duckdb_repo.clear_sync_job(con, year, month)
            return

        ws = get_sheet().sheet1
        all_values = ws.get_all_values()

        expense_date = date(year, month, 1)
        month_range = find_month_range(all_values, month)
        if month_range is None:
            create_month_rows(ws, all_values, expense_date)
            all_values = ws.get_all_values()
            month_range = find_month_range(all_values, month)
            if month_range is None:
                logger.error("Failed to create month block for %d-%02d", year, month)
                return

        rate_str = get_month_rate(all_values, month)
        if not rate_str and rate:
            rate_row = month_range[0]
            ws.update_cell(rate_row, COL_RATE_EUR, str(rate))
            logger.info("Wrote exchange rate %s for %d-%02d", rate, year, month)

        written = _write_aggregates_to_sheet(ws, all_values, month, aggregates)
        if written:
            logger.info("Synced %d cells for %d-%02d", written, year, month)

        duckdb_repo.clear_sync_job(con, year, month)
        logger.info("Full sync complete for %d-%02d", year, month)
    finally:
        con.close()


def sync_month(year: int, month: int) -> None:
    """Synchronous full-month sync (for inv sync / recovery)."""
    rate = None
    try:
        loop = asyncio.new_event_loop()
        rate = loop.run_until_complete(fetch_eur_rsd_rate(date(year, month, 1)))
        loop.close()
    except (OSError, ValueError):
        logger.debug("Could not fetch exchange rate for %d-%02d", year, month)
    _sync_month_core(year, month, rate)


async def async_sync_month(year: int, month: int) -> None:
    """Async full-month sync."""
    rate = None
    try:
        rate = await fetch_eur_rsd_rate(date(year, month, 1))
    except (OSError, ValueError):
        logger.debug("Could not fetch exchange rate for %d-%02d", year, month)
    await asyncio.to_thread(_sync_month_core, year, month, rate)


def sync_all_dirty() -> int:
    """Full-rebuild sync for all dirty months across all yearly DBs.

    Dirty jobs accumulate from targeted per-expense syncs and are only
    cleared here after a successful full-month rebuild. Run via ``inv sync``.
    """
    data_dir = duckdb_repo.DATA_DIR
    if not data_dir.exists():
        return 0

    synced = 0
    for db_path in sorted(data_dir.glob("budget_*.duckdb")):
        stem = db_path.stem
        try:
            year = int(stem.replace("budget_", ""))
        except ValueError:
            continue

        con = duckdb_repo.get_budget_connection(year)
        try:
            jobs = duckdb_repo.get_dirty_sync_jobs(con)
        finally:
            con.close()

        for y, m in jobs:
            try:
                sync_month(y, m)
                synced += 1
            except Exception:
                logger.exception("Sync failed for %d-%02d", y, m)

    return synced
