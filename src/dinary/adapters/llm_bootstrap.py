"""LLM provider seeding."""

import contextlib
import logging
import sqlite3
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from dinary.config import settings

logger = logging.getLogger(__name__)

_DEPLOY_DIR = Path(__file__).resolve().parents[3] / ".deploy"
LLM_PROVIDERS_TOML = _DEPLOY_DIR / "llm_providers.toml"


def seed_llm_provider_if_empty(
    con: sqlite3.Connection,
    *,
    providers_toml: Path | None = None,
) -> None:
    """Seed llm_providers on first boot; no-op when the table already has rows.

    Reads provider list from ``providers_toml`` (default:
    ``.deploy/llm_providers.toml``). Falls back to the single-provider
    ``DINARY_LLM_BASE_URL`` / ``DINARY_LLM_API_KEY`` env vars when the
    file is absent.
    """
    existing = con.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
    if existing:
        return

    toml_path = providers_toml if providers_toml is not None else LLM_PROVIDERS_TOML
    providers = _providers_from_toml(toml_path)
    if not providers:
        if not settings.llm_base_url or not settings.llm_api_key:
            return
        providers = [
            {
                "label": _label_from_base_url(settings.llm_base_url),
                "base_url": settings.llm_base_url,
                "api_key": settings.llm_api_key,
                "model": settings.llm_model,
            },
        ]

    con.execute("BEGIN IMMEDIATE")
    try:
        for priority, p in enumerate(providers):
            con.execute(
                "INSERT INTO llm_providers (label, base_url, api_key, model, priority)"
                " VALUES (?, ?, ?, ?, ?)",
                [p["label"], p["base_url"], p["api_key"], p["model"], priority],
            )
        con.execute("COMMIT")
    except Exception:
        with contextlib.suppress(sqlite3.Error):
            con.execute("ROLLBACK")
        raise
    logger.info("seeded %d llm_providers from %s", len(providers), toml_path.name)


def _providers_from_toml(path: Path) -> list[dict]:
    """Parse [[providers]] entries from a TOML file; return [] if file absent."""
    if not path.exists():
        return []
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    raw = data.get("providers", [])
    providers = []
    for entry in raw:
        base_url = entry.get("base_url", "")
        api_key = entry.get("api_key", "")
        if not base_url or not api_key:
            continue
        label = entry.get("label") or _label_from_base_url(base_url)
        model = entry.get("model", "")
        providers.append({"label": label, "base_url": base_url, "api_key": api_key, "model": model})
    return providers


def _label_from_base_url(base_url: str) -> str:
    """Derive a human-readable provider label from the base URL."""
    lower = base_url.lower()
    if "groq" in lower:
        return "Groq"
    if "openrouter" in lower:
        return "OpenRouter"
    if "gemini" in lower or "googleapis" in lower or "aistudio" in lower:
        return "Gemini"
    if "cerebras" in lower:
        return "Cerebras"
    try:
        host = urlparse(base_url).hostname or base_url
        return host.split(".")[0].capitalize()
    except Exception:  # noqa: BLE001
        return base_url[:40]
