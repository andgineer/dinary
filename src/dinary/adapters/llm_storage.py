"""aiosqlite-backed BrokerStorage for LLMBroker.

Absorbs the provider-seeding logic previously in llm_bootstrap.py.
Each method opens its own aiosqlite connection, does its work, and closes it.
"""

import contextlib
import logging
import tomllib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import aiosqlite

from dinary.adapters.llmbroker import CallEvent, ProviderConfig
from dinary.config import settings
from dinary.db import storage as db_storage

logger = logging.getLogger(__name__)

_DEPLOY_DIR = Path(__file__).resolve().parents[3] / ".deploy"
_LLM_PROVIDERS_TOML = _DEPLOY_DIR / "llm_providers.toml"

_PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
]


@asynccontextmanager
async def _open(db_path: str) -> AsyncGenerator[aiosqlite.Connection]:
    async with aiosqlite.connect(db_path) as db:
        for pragma in _PRAGMAS:
            await db.execute(pragma)
        yield db


class LLMBrokerStorage:
    """aiosqlite-backed LLMBroker storage. Every method opens a short-lived connection."""

    def __init__(self, providers_toml: Path | None = None) -> None:
        self._providers_toml = providers_toml if providers_toml is not None else _LLM_PROVIDERS_TOML

    async def load_providers(self) -> list[ProviderConfig]:
        db_path = str(db_storage.DB_PATH)
        async with _open(db_path) as db:
            row = await (await db.execute("SELECT COUNT(*) FROM llmbroker_providers")).fetchone()
            if row and row[0] == 0:
                await self._seed(db)
            rows = await (
                await db.execute(
                    """
                    SELECT id, label, base_url, api_key, model, priority,
                           default_rate_limit_sec, rate_limited_until
                      FROM llmbroker_providers
                     WHERE is_enabled = 1
                     ORDER BY priority, id
                    """,
                )
            ).fetchall()

        result = []
        for r in rows:
            rlu = None
            if r[7]:
                try:
                    rlu = datetime.fromisoformat(str(r[7]))
                    if rlu.tzinfo is None:
                        rlu = rlu.replace(tzinfo=UTC)
                except ValueError:
                    pass
            result.append(
                ProviderConfig(
                    id=int(r[0]),
                    label=str(r[1]),
                    base_url=str(r[2]),
                    api_key=str(r[3]),
                    model=str(r[4]),
                    priority=int(r[5]),
                    rate_limit_sec=int(r[6]),
                    rate_limited_until=rlu,
                ),
            )
        return result

    async def _seed(self, db: aiosqlite.Connection) -> None:
        providers = _providers_from_toml(self._providers_toml)
        if not providers:
            if not settings.llm_base_url or not settings.llm_api_key:
                return
            providers = [
                {
                    "label": _label_from_base_url(settings.llm_base_url),
                    "base_url": settings.llm_base_url,
                    "api_key": settings.llm_api_key,
                    "model": settings.llm_model,
                    "rate_limit_sec": 60,
                },
            ]

        try:
            await db.execute("BEGIN IMMEDIATE")
            for priority, p in enumerate(providers):
                await db.execute(
                    "INSERT INTO llmbroker_providers"
                    " (label, base_url, api_key, model, priority, default_rate_limit_sec)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        p["label"],
                        p["base_url"],
                        p["api_key"],
                        p["model"],
                        priority,
                        p.get("rate_limit_sec", 60),
                    ],
                )
            await db.commit()
        except Exception:
            with contextlib.suppress(Exception):
                await db.rollback()
            raise
        logger.info(
            "seeded %d llmbroker_providers from %s",
            len(providers),
            self._providers_toml.name,
        )

    async def on_call_logged(self, event: CallEvent) -> None:
        db_path = str(db_storage.DB_PATH)
        async with _open(db_path) as db:
            await db.execute(
                "INSERT INTO llmbroker_call_log"
                " (provider_id, receipt_id, status, latency_ms) VALUES (?, ?, ?, ?)",
                [event.provider_id, event.context_id, event.status, event.latency_ms],
            )
            await db.commit()

    async def on_rate_limited(self, provider_id: object, until: datetime) -> None:
        db_path = str(db_storage.DB_PATH)
        async with _open(db_path) as db:
            await db.execute(
                "UPDATE llmbroker_providers SET rate_limited_until = ? WHERE id = ?",
                [until.isoformat(), provider_id],
            )
            await db.commit()


def _providers_from_toml(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    providers = []
    for entry in data.get("providers", []):
        base_url = entry.get("base_url", "")
        api_key = entry.get("api_key", "")
        if not base_url or not api_key:
            continue
        providers.append(
            {
                "label": entry.get("label") or _label_from_base_url(base_url),
                "base_url": base_url,
                "api_key": api_key,
                "model": entry.get("model", ""),
                "rate_limit_sec": int(entry.get("rate_limit_sec", 60)),
            },
        )
    return providers


def _label_from_base_url(base_url: str) -> str:
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
