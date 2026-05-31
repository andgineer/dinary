"""Extract monthly income from Google Sheets in original currency (no EUR conversion).

Reads every registered income source from ``.deploy/import_sources.json``
and aggregates amounts per (year, month) in the currency the sheet is
denominated in — RUB for years up to mid-2022, RSD from mid-2022 onward
(exact boundary is the ``transition_month`` in each year's ``IncomeLayout``).

The result is written as a JSON file with a flat list of entries:

.. code-block:: json

    {
      "generated_at": "2026-05-31",
      "entries": [
        {"year": 2019, "month": 1, "amount": "85000.00", "currency": "RUB"},
        ...
      ]
    }

``inv export-income-original`` is the operator entry point. The module
can also be run with ``uv run python -m tasks.imports.income_original_export``.
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from dinary.adapters.sheets_client import get_sheet
from dinary.config import IMPORT_SOURCES_DOC_HINT, read_import_sources
from tasks.imports.expense_import import MONTHS_IN_YEAR
from tasks.imports.income_import import (
    INCOME_LAYOUTS,
    IncomeLayout,
    _cell,
    _parse_amount,
    _parse_date,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("data/income_original.json")


def aggregate_original_currency(
    year: int,
    spreadsheet_id: str,
    worksheet_name: str,
    layout: IncomeLayout,
) -> dict[tuple[int, str], Decimal]:
    """Read one Income/Balance worksheet; return ``{(month, currency): total}``.

    Does NOT convert to accounting currency. Each month carries exactly
    one currency (the layout's ``currency`` before ``transition_month``,
    ``transition_currency`` from that month onward).
    """
    ss = get_sheet(spreadsheet_id)
    ws = ss.worksheet(worksheet_name)
    all_values = ws.get_all_values()

    monthly: dict[tuple[int, str], Decimal] = defaultdict(Decimal)

    for row_idx in range(layout.header_rows, len(all_values)):
        row = all_values[row_idx]
        parsed = _parse_date(_cell(row, layout.col_date))
        if parsed is None:
            continue
        row_year, month = parsed
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

        monthly[(month, currency)] += amount

    return dict(monthly)


def extract_all_years() -> list[dict]:
    """Return a flat list of ``{year, month, amount, currency}`` dicts.

    Iterates every import source that has an income worksheet. Years with
    no registered source or no income worksheet are silently skipped.
    Amounts are ``str`` (canonical decimal notation) to avoid float rounding.
    """
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
            monthly = aggregate_original_currency(
                source.year,
                source.spreadsheet_id,
                source.income_worksheet_name,
                layout,
            )
        except Exception:
            logger.exception("Failed to read year %d — skipping", source.year)
            continue

        for (month, currency), total in sorted(monthly.items()):
            entries.append(
                {
                    "year": source.year,
                    "month": month,
                    "amount": format(total, "f"),
                    "currency": currency,
                },
            )

    return entries


def export_to_file(output: Path) -> int:
    """Extract all income in original currency and write to ``output``.

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
        description="Extract income from all sheets in original currency to JSON.",
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
