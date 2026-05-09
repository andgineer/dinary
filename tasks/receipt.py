"""Receipt tasks: classify-receipt experiment and reclassify-receipts operator tool."""

import asyncio
import sys
from collections import defaultdict

from invoke import task
from sr_invoice_parser.exceptions import ParserParseException, ParserRequestException

from dinary.config import settings
from dinary.services.item_normalizer import normalize_item_name
from dinary.services.ledger_repo import get_connection, list_categories
from dinary.services.llm_client import OpenAICompatibleClient
from dinary.services.receipt_parser import parse_receipt
from dinary.services.receipt_repo import requeue_receipts


@task(name="classify-receipt", iterable=["url"])
def classify_receipt(c, url):  # noqa: ARG001
    """Classify items from one or more Serbian fiscal receipt URLs using a real LLM.

    Requires DINARY_LLM_BASE_URL and DINARY_LLM_API_KEY env vars.
    DINARY_LLM_MODEL defaults to gemini-2.5-flash.

    Example:
        inv classify-receipt --url https://suf.purs.gov.rs/v/?vl=...
        inv classify-receipt --url URL1 --url URL2
    """
    if not url:
        print("Usage: inv classify-receipt --url URL [--url URL2 ...]")
        return

    if not settings.llm_base_url:
        print("Error: DINARY_LLM_BASE_URL is not set", file=sys.stderr)
        sys.exit(1)
    if not settings.llm_api_key:
        print("Error: DINARY_LLM_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    llm = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)

    con = get_connection()
    try:
        cats = list_categories(con)
    finally:
        con.close()
    categories = {row.id: f"{row.group_name}: {row.name}" for row in cats}

    for receipt_url in url:
        print(f"\n{'=' * 70}")
        _run_receipt(receipt_url, llm, categories)


def _run_receipt(
    url: str,
    llm: OpenAICompatibleClient,
    categories: dict[int, str],
) -> None:
    print(f"URL: {url[:80]}{'...' if len(url) > 80 else ''}")
    try:
        receipt = parse_receipt(url)
    except (ParserParseException, ParserRequestException) as exc:
        print(f"  Parse error: {exc}")
        return

    print(f"Store: {receipt.store_name} (PIB {receipt.store_pib})")

    if not receipt.items:
        print("  No items found.")
        return

    if not receipt.total_ok:
        print(
            f"  Total mismatch: items sum {receipt.items_total:.2f} "
            f"!= receipt total {receipt.total_amount:.2f} RSD",
        )

    normalized = [normalize_item_name(item.name_raw) for item in receipt.items]

    try:
        results = asyncio.run(llm.classify_receipt(normalized, receipt.store_name, categories))
    except Exception as exc:  # noqa: BLE001
        print(f"  LLM error: {exc}")
        return

    print(f"\n{'Item raw':<38} {'qty':>6} {'total':>8}  {'Normalized':<28} {'Category':<35} Conf")
    print("-" * 125)
    totals: dict[str, float] = defaultdict(float)
    for item, norm, result in zip(receipt.items, normalized, results, strict=False):
        cat_name = (
            categories.get(result.category_id, "Unclassified")
            if result.category_id is not None
            else "Unclassified"
        )
        conf_str = str(result.confidence_level) if result.category_id is not None else "-"
        qty_str = (
            f"{item.quantity:.3f}"
            if item.quantity != int(item.quantity)
            else str(int(item.quantity))
        )
        print(
            f"{item.name_raw:<38} {qty_str:>6} {item.total_price:>8.2f}  "
            f"{norm:<28} {cat_name:<35} {conf_str}",
        )
        if result.category_id is not None and result.confidence_level > 1:
            totals[cat_name] += item.total_price

    print(f"\n{'Expense totals by category':}")
    print("-" * 55)
    for cat_name, total in sorted(totals.items()):
        print(f"  {cat_name:<45} {total:>8.2f} RSD")
    print(f"  {'CLASSIFIED TOTAL':<45} {sum(totals.values()):>8.2f} RSD")
    print(f"  {'RECEIPT TOTAL':<45} {receipt.total_amount:>8.2f} RSD")


@task(name="reclassify-receipts", iterable=["receipt_id"])
def reclassify_receipts(c, receipt_id=None, from_date=None, clear_rules=False, yes=False):  # noqa: ARG001
    """Re-run classification for receipts (resets expenses and re-queues drain jobs).

    Examples:
        inv reclassify-receipts                          # all receipts
        inv reclassify-receipts --from-date 2026-05-01  # receipts from date
        inv reclassify-receipts --receipt-id 42         # single receipt
        inv reclassify-receipts --clear-rules           # also delete classification rules

    Requires --yes when scope > 1 receipt.
    """
    con = get_connection()
    try:
        if receipt_id:
            ids = [int(rid) for rid in receipt_id]
        elif from_date:
            rows = con.execute(
                "SELECT id FROM receipts WHERE created_at >= ?",
                [from_date],
            ).fetchall()
            ids = [int(r[0]) for r in rows]
        else:
            rows = con.execute("SELECT id FROM receipts").fetchall()
            ids = [int(r[0]) for r in rows]

        if not ids:
            print("No matching receipts found.")
            return

        if len(ids) > 1 and not yes:
            print(f"This will reset classification for {len(ids)} receipts.")
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                print("Aborted.")
                return

        con.execute("BEGIN IMMEDIATE")
        try:
            requeue_receipts(con, ids, clear_rules=clear_rules)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

        print(f"Queued {len(ids)} receipt(s) for reclassification.")
        if clear_rules:
            print("Classification rules for those items were also cleared.")
        print("The drain will process them at the next interval (default: 300s).")
    finally:
        con.close()
