"""Receipt tasks: classify-receipt experiment and reclassify-receipts operator tool."""

import asyncio

from invoke import task

from dinary.adapters.llm_storage import TomlLLMBrokerStorage
from dinary.adapters.llmbroker import LLMBroker
from dinary.adapters.serbian_receipt_parser import (
    ParserParseError,
    ParserRequestError,
    parse_receipt,
)
from dinary.background.classification.item_normalizer import normalize_item_name
from dinary.background.classification.receipt_classifier import (
    classify_receipt as llm_classify_receipt,
)
from dinary.db.catalog import list_visible_categories
from dinary.db.receipts import requeue_receipts
from dinary.db.storage import get_connection


@task(name="classify-receipt", iterable=["url"])
def classify_receipt(c, url):  # noqa: ARG001
    """Classify items from one or more Serbian fiscal receipt URLs using a real LLM.

    Providers are read from .deploy/llm_providers.toml.

    Example:
        inv classify-receipt --url https://suf.purs.gov.rs/v/?vl=...
        inv classify-receipt --url URL1 --url URL2
    """
    if not url:
        print("Usage: inv classify-receipt --url URL [--url URL2 ...]")
        return

    broker = LLMBroker(TomlLLMBrokerStorage())

    con = get_connection()
    try:
        cats = list_visible_categories(con)
        tag_rows = con.execute("SELECT id, name FROM tags WHERE is_active = 1").fetchall()
        tags = {int(r[0]): str(r[1]) for r in tag_rows}
    finally:
        con.close()
    categories = {row.id: f"{row.group_name}: {row.name}" for row in cats}

    async def _run_all() -> None:
        await broker.start()
        try:
            for receipt_url in url:
                print(f"\n{'=' * 70}")
                await _run_receipt(receipt_url, broker, categories, tags)
        finally:
            await broker.stop()

    asyncio.run(_run_all())


async def _run_receipt(
    url: str,
    broker: LLMBroker,
    categories: dict[int, str],
    tags: dict[int, str],
) -> None:
    print(f"URL: {url[:80]}{'...' if len(url) > 80 else ''}")
    try:
        receipt = await parse_receipt(url)
    except (ParserParseError, ParserRequestError) as exc:
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
        outcome = await llm_classify_receipt(
            broker,
            normalized,
            receipt.store_name,
            categories,
            tags,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  LLM error: {exc}")
        return
    results = outcome.results

    col_item = 36
    col_qty = 6
    col_total = 8
    col_norm = 26
    col_cat = 32
    col_conf = 4
    col_alt = 38
    header = (
        f"{'Item raw':<{col_item}} {'qty':>{col_qty}} {'total':>{col_total}}  "
        f"{'Normalized':<{col_norm}} {'Category':<{col_cat}} {'C':>{col_conf}}  "
        f"{'Alternatives':<{col_alt}} Tags"
    )
    print(f"\n{header}")
    print("-" * len(header))

    totals: dict[str, float] = {}
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

        if result.alternative_category_ids and result.confidence_level < 4:
            alt_parts = [categories.get(aid, f"?{aid}") for aid in result.alternative_category_ids]
            alt_str = " / ".join(p.split(": ", 1)[-1] for p in alt_parts)
        else:
            alt_str = ""

        tag_str = (
            ", ".join(tags.get(tid, f"?{tid}") for tid in result.tag_ids) if result.tag_ids else ""
        )

        print(
            f"{item.name_raw:<{col_item}} {qty_str:>{col_qty}} {item.total_price:>{col_total}.2f}  "
            f"{norm:<{col_norm}} {cat_name:<{col_cat}} {conf_str:>{col_conf}}  "
            f"{alt_str:<{col_alt}} {tag_str}",
        )
        if result.category_id is not None and result.confidence_level > 1:
            totals[cat_name] = totals.get(cat_name, 0) + item.total_price

    print(f"\n{'Expense totals by category':}")
    print("-" * 55)
    for cat_name, total in sorted(totals.items()):
        print(f"  {cat_name:<45} {total:>8.2f} RSD")
    print(f"  {'CLASSIFIED TOTAL':<45} {sum(totals.values()):>8.2f} RSD")
    print(f"  {'RECEIPT TOTAL':<45} {receipt.total_amount:>8.2f} RSD")


@task(name="reclassify-receipts", iterable=["receipt_id"])
def reclassify_receipts(
    _c,
    receipt_id=None,
    from_date=None,
    clear_rules=False,
    yes=False,
):
    """Re-run classification for receipts (resets expenses and re-queues drain jobs).

    Examples:
        inv reclassify-receipts                          # all receipts
        inv reclassify-receipts --from-date 2026-05-01  # receipts from date
        inv reclassify-receipts --receipt-id 42         # single receipt

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
