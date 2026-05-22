import json
import logging
from dataclasses import dataclass, field

import httpx

from dinary.adapters.llmbroker import LLMBroker

logger = logging.getLogger(__name__)

_CHAIN_NAME_PROMPT = (
    "What retail chain is this store? "
    "Raw name: {store_name_raw}. "
    "Reply with just the canonical chain name (e.g. Lidl, Maxi, DM, Metro). "
    "No explanation."
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
    items: list[str],
    tag_id_set: set[int],
) -> list[ClassificationResult]:
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected list")  # noqa: TRY004
        return [
            ClassificationResult(
                item_name_normalized=str(entry.get("item", "")),
                category_id=int(entry["category_id"])
                if entry.get("category_id") is not None
                else None,
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
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("LLM parse error (%s), fallback conf=1: %.200s", exc, raw)
        return [
            ClassificationResult(item_name_normalized=item, category_id=None, confidence_level=1)
            for item in items
        ]


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def classify_receipt(
        self,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
        tags: dict[int, str] | None = None,
    ) -> list[ClassificationResult]:
        if tags is None:
            tags = {}
        user_msg = _build_user_message(items, store_name_raw, categories, tags)
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_response(content, items, set(tags.keys()))

    async def get_chain_name(self, store_name_raw: str) -> str:
        prompt = _CHAIN_NAME_PROMPT.format(store_name_raw=store_name_raw)
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return next((ln.strip() for ln in content.splitlines() if ln.strip()), "")


async def classify_receipt(
    broker: LLMBroker,
    items: list[str],
    store_name_raw: str,
    categories: dict[int, str],
    tags: dict[int, str] | None = None,
    context_id: int | None = None,
) -> tuple[list[ClassificationResult], bool]:
    if tags is None:
        tags = {}
    user_msg = _build_user_message(items, store_name_raw, categories, tags)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    raw, used_fallback = await broker.complete(
        messages,
        context_id=str(context_id) if context_id is not None else None,
    )
    return _parse_response(raw, items, set(tags.keys())), used_fallback


async def get_chain_name(broker: LLMBroker, store_name_raw: str) -> str:
    prompt = _CHAIN_NAME_PROMPT.format(store_name_raw=store_name_raw)
    raw = await broker.try_complete([{"role": "user", "content": prompt}])
    if raw is None:
        return store_name_raw
    return next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
