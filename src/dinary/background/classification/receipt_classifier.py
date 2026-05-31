"""LLM classification adapter: prompt building, response parsing, and data loading."""

import json
import logging
import sqlite3
from dataclasses import dataclass, field

from dinary.adapters.llmbroker import Execution, LLMBroker

logger = logging.getLogger(__name__)

_CHAIN_NAME_PROMPT = (
    "Normalize this Serbian retail store name to its canonical brand name. "
    "Raw name: {store_name_raw}. "
    "Strip all legal suffixes (d.o.o., k.d., a.d.), country/region words "
    "(Srbija, Serbia, RS, Beograd), "
    "and store-type words (supermarket, market, centar, prodavnica, shop). "
    "Return proper-case brand name only — no explanation, no punctuation. "
    "Examples: 'LIDL SRBIJA KD' → 'Lidl', 'MAXI DOO BEOGRAD' → 'Maxi', 'DM DROGERIE MARKT' → 'DM'."
)

_SYSTEM_PROMPT = (
    "You are a receipt classifier for a personal expense tracker in Serbia.\n"
    "Classify each item into one of the provided categories.\n"
    "Reply with a JSON array only — no explanation, no markdown fences.\n"
    'Each element: {"item": "<item name>", "category_id": <int or null>, "confidence": <1-4>}\n'
    "Confidence scale: 1=cannot classify, 2=rough guess, 3=likely correct, 4=certain\n"
    'Always add "alternatives": [<cat_id>, ...] with 2-3 next-best category IDs'
    " ordered by likelihood.\n"
    'If tags are provided, add "tags": [<tag_id>, ...] with tag IDs that clearly apply to the'
    " item; omit if none clearly fit; do not guess."
)


@dataclass(slots=True)
class ClassificationResult:
    item_name_normalized: str
    category_id: int | None
    confidence_level: int
    alternative_category_ids: list[int] = field(default_factory=list)
    tag_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ClassifyOutcome:
    results: list[ClassificationResult]
    broker_unavailable: bool
    execution_failed: bool
    execution: Execution


def load_categories(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute(
        "SELECT c.id, cg.name, c.name"
        " FROM categories c"
        " LEFT JOIN category_groups cg ON cg.id = c.group_id"
        " WHERE c.is_active = 1",
    ).fetchall()
    return {int(r[0]): f"{r[1]}: {r[2]}" if r[1] else str(r[2]) for r in rows}


def load_tags(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT id, name FROM tags WHERE is_active = 1").fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def _build_user_message(
    items: list[str],
    store_name_raw: str,
    categories: dict[int, str],
    tags: dict[int, str],
) -> str:
    cat_lines = "\n".join(f"{cat_id}: {name}" for cat_id, name in sorted(categories.items()))
    item_lines = "\n".join(f"- {item}" for item in items)
    msg = f"Store: {store_name_raw}\n\nCategories:\n{cat_lines}"
    if tags:
        tag_lines = "\n".join(f"{tag_id}: {name}" for tag_id, name in sorted(tags.items()))
        msg += f"\n\nTags:\n{tag_lines}"
    msg += f"\n\nItems:\n{item_lines}"
    return msg


def _parse_response(
    raw: str,
    tag_id_set: set[int],
) -> list[ClassificationResult]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("expected list")  # noqa: TRY004
    return [
        ClassificationResult(
            item_name_normalized=str(entry.get("item", "")),
            category_id=int(entry["category_id"]) if entry["category_id"] is not None else None,
            confidence_level=int(entry.get("confidence", 1)),
            alternative_category_ids=[
                int(a)
                for a in entry.get("alternatives", [])
                if isinstance(a, (int, float)) and float(a) == int(a)
            ][:3],
            tag_ids=[
                int(t)
                for t in entry.get("tags", [])
                if isinstance(t, (int, float)) and int(t) in tag_id_set
            ],
        )
        for entry in parsed
    ]


async def classify_receipt(
    broker: LLMBroker,
    items: list[str],
    store_name_raw: str,
    categories: dict[int, str],
    tags: dict[int, str] | None = None,
    execution_id: int | None = None,
) -> ClassifyOutcome:
    if tags is None:
        tags = {}
    user_msg = _build_user_message(items, store_name_raw, categories, tags)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    execution = await broker.execute(
        messages,
        execution_id=str(execution_id) if execution_id is not None else None,
    )
    if execution.output is None:
        return ClassifyOutcome(
            results=[],
            broker_unavailable=True,
            execution_failed=False,
            execution=execution,
        )

    try:
        results = _parse_response(execution.output, set(tags.keys()))
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("LLM parse error (%s): %.200s", exc, execution.output)
        return ClassifyOutcome(
            results=[],
            broker_unavailable=False,
            execution_failed=True,
            execution=execution,
        )

    execution_failed = len(results) != len(items) or any(r.category_id is None for r in results)
    return ClassifyOutcome(
        results=results,
        broker_unavailable=False,
        execution_failed=execution_failed,
        execution=execution,
    )


async def get_chain_name(broker: LLMBroker, store_name_raw: str) -> str:
    prompt = _CHAIN_NAME_PROMPT.format(store_name_raw=store_name_raw)
    execution = await broker.execute([{"role": "user", "content": prompt}], wait=False)
    if execution.output is None:
        return store_name_raw
    return next((ln.strip() for ln in execution.output.splitlines() if ln.strip()), "")
