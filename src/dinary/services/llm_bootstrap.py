"""LLM provider seeding — moved from ledger_repo to break the urlparse dependency."""

import contextlib
import logging
import sqlite3
from urllib.parse import urlparse

from dinary.config import settings

logger = logging.getLogger(__name__)


def seed_llm_provider_if_empty(con: sqlite3.Connection) -> None:
    """Seed llm_providers from DINARY_LLM_* env vars on first boot.

    No-op when the table already has rows (operator manages providers
    via the admin API after initial setup). Only seeds when
    DINARY_LLM_BASE_URL and DINARY_LLM_API_KEY are both non-empty.
    """
    if not settings.llm_base_url or not settings.llm_api_key:
        return
    existing = con.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
    if existing:
        return
    label = _label_from_base_url(settings.llm_base_url)
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "INSERT INTO llm_providers (label, base_url, api_key, model, priority)"
            " VALUES (?, ?, ?, ?, 0)",
            [label, settings.llm_base_url, settings.llm_api_key, settings.llm_model],
        )
        con.execute("COMMIT")
    except Exception:
        with contextlib.suppress(sqlite3.Error):
            con.execute("ROLLBACK")
        raise
    logger.info("seeded llm_providers from env: %s / %s", label, settings.llm_model)


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
