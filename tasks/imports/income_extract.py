"""Extract individual income records from Google Sheets in original currency.

Reads every registered income source from ``.deploy/import_sources.json``
and returns one entry per sheet row. Amounts are NOT summed by month.

Each entry carries the raw payment date and a predicted ``income_month``:
  - day <= 25 -> previous month  (salary paid at start of month for prior month)
  - day > 25  -> current month   (late payment belonging to the same month)

Output JSON:

.. code-block:: json

    {
      "generated_at": "2026-05-31",
      "entries": [
        {
          "year": 2019, "month": 1, "day": 10,
          "amount": "85000.00", "currency": "RUB",
          "income_year": 2018, "income_month": 12
        }
      ]
    }

``inv import-extract-income`` is the operator entry point.
Can also be run with ``uv run python -m tasks.imports.income_extract``.
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from dinary.adapters.sheets_client import get_sheet
from dinary.config import IMPORT_SOURCES_DOC_HINT, read_import_sources
from tasks.imports.expense_import import MONTHS_IN_YEAR
from tasks.imports.income_import import (
    INCOME_LAYOUTS,
    IncomeLayout,
    _cell,
    _parse_amount,
    _parse_full_date,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("data/income_extract.json")

INCOME_MONTH_LATE_CUTOFF = 25


def _predict_income_month(year: int, month: int, day: int) -> tuple[int, int]:
    """Return (income_year, income_month) for a payment on the given date.

    day <= 25 -> previous month; day > 25 -> current month.
    """
    if day > INCOME_MONTH_LATE_CUTOFF:
        return year, month
    return (year - 1, 12) if month == 1 else (year, month - 1)


def extract_income_records(
    year: int,
    spreadsheet_id: str,
    worksheet_name: str,
    layout: IncomeLayout,
) -> list[dict]:
    """Read one Income/Balance worksheet; return one dict per income row.

    Each row is a separate entry with raw payment date and predicted
    income_year/income_month. No aggregation.
    """
    ss = get_sheet(spreadsheet_id)
    ws = ss.worksheet(worksheet_name)
    all_values = ws.get_all_values()

    records: list[dict] = []

    for row_idx in range(layout.header_rows, len(all_values)):
        row = all_values[row_idx]
        parsed = _parse_full_date(_cell(row, layout.col_date))
        if parsed is None:
            continue
        row_year, month, day = parsed
        if row_year != year:
            continue
        if not 1 <= month <= MONTHS_IN_YEAR:
            continue

        amount = _parse_amount(_cell(row, layout.col_amount))
        if amount is None:
            continue

        currency = layout.currency
        if (
            layout.transition_month is not None
            and layout.transition_currency is not None
            and month >= layout.transition_month
        ):
            currency = layout.transition_currency

        income_year, income_month = _predict_income_month(year, month, day)
        records.append(
            {
                "year": year,
                "month": month,
                "day": day,
                "amount": format(amount, "f"),
                "currency": currency,
                "income_year": income_year,
                "income_month": income_month,
            },
        )

    return records


def extract_all_years() -> list[dict]:
    """Return a flat list of individual income records across all registered years."""
    sources = read_import_sources()
    entries: list[dict] = []

    for source in sorted(sources, key=lambda s: s.year):
        if not source.income_worksheet_name:
            continue
        if not source.income_layout_key or source.income_layout_key not in INCOME_LAYOUTS:
            logger.warning(
                "Year %d: unknown income_layout_key %r — skipping. %s",
                source.year,
                source.income_layout_key,
                IMPORT_SOURCES_DOC_HINT,
            )
            continue

        layout = INCOME_LAYOUTS[source.income_layout_key]
        logger.info(
            "Extracting year %d from worksheet %r",
            source.year,
            source.income_worksheet_name,
        )

        try:
            records = extract_income_records(
                source.year,
                source.spreadsheet_id,
                source.income_worksheet_name,
                layout,
            )
        except Exception:
            logger.exception("Failed to read year %d — skipping", source.year)
            continue

        entries.extend(sorted(records, key=lambda r: (r["month"], r["day"])))

    return entries


def export_to_file(output: Path) -> int:
    """Extract all income records and write to ``output``.

    Returns the number of entries written. Raises on I/O errors.
    """
    entries = extract_all_years()
    payload = {
        "generated_at": str(date.today()),
        "entries": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract individual income records from all sheets in original currency to JSON."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"destination JSON file (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)
    output = Path(args.output)
    count = export_to_file(output)
    print(f"Wrote {count} entries to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
