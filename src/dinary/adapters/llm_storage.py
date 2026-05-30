"""BrokerStorage implementations for LLMBroker.

SqliteLLMBrokerStorage — production: seeds from toml, persists to SQLite.
TomlLLMBrokerStorage   — CLI/standalone: reads toml directly, logs via Python logging, no DB.
"""

import contextlib
import json
import logging
import tomllib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import aiosqlite

from dinary.adapters.llmbroker import CallEvent, ProviderConfig
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


class SqliteLLMBrokerStorage:
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
                    SELECT label, base_url, api_key, model,
                           default_rate_limit_sec, rate_limited_until
                      FROM llmbroker_providers
                     WHERE is_enabled = 1
                     ORDER BY label
                    """,
                )
            ).fetchall()

        result = []
        for r in rows:
            rlu = None
            if r[5]:
                try:
                    rlu = datetime.fromisoformat(str(r[5]))
                    if rlu.tzinfo is None:
                        rlu = rlu.replace(tzinfo=UTC)
                except ValueError:
                    pass
            result.append(
                ProviderConfig(
                    label=str(r[0]),
                    base_url=str(r[1]),
                    api_key=str(r[2]),
                    model=str(r[3]),
                    rate_limit_sec=int(r[4]),
                    rate_limited_until=rlu,
                ),
            )
        return result

    async def _seed(self, db: aiosqlite.Connection) -> None:
        providers = _providers_from_toml(self._providers_toml)
        if not providers:
            logger.warning(
                "no LLM providers found in %s — skipping seed",
                self._providers_toml.name,
            )
            return

        try:
            await db.execute("BEGIN IMMEDIATE")
            for p in providers:
                await db.execute(
                    "INSERT INTO llmbroker_providers"
                    " (label, base_url, api_key, model, default_rate_limit_sec)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [
                        p["label"],
                        p["base_url"],
                        p["api_key"],
                        p["model"],
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
                " (provider_label, execution_id, status, latency_ms, error_detail)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    event.provider_label,
                    event.execution_id,
                    event.status,
                    event.latency_ms,
                    event.error_detail,
                ],
            )
            await db.commit()

    async def on_rate_limited(self, provider_label: str, until: datetime) -> None:
        db_path = str(db_storage.DB_PATH)
        async with _open(db_path) as db:
            await db.execute(
                "UPDATE llmbroker_providers SET rate_limited_until = ? WHERE label = ?",
                [until.isoformat(), provider_label],
            )
            await db.commit()

    async def on_quality_feedback(self, provider_label: str, *, usable: bool) -> None:
        if usable:
            return
        db_path = str(db_storage.DB_PATH)
        async with _open(db_path) as db:
            await db.execute(
                "UPDATE llmbroker_providers"
                " SET execution_fail_count = execution_fail_count + 1"
                " WHERE label = ?",
                [provider_label],
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


def _toml_stats_path(providers_toml: Path) -> Path:
    if providers_toml.exists():
        with providers_toml.open("rb") as fh:
            data = tomllib.load(fh)
        if "stats_path" in data:
            return Path(data["stats_path"])
    return providers_toml.with_name("llmbroker_stats.json")


class TomlLLMBrokerStorage:
    """CLI/standalone BrokerStorage — reads toml directly, logs via Python logging, no DB."""

    def __init__(self, providers_toml: Path | None = None) -> None:
        self._providers_toml = providers_toml if providers_toml is not None else _LLM_PROVIDERS_TOML

    async def load_providers(self) -> list[ProviderConfig]:
        raw = _providers_from_toml(self._providers_toml)
        if not raw:
            logger.warning("no LLM providers found in %s", self._providers_toml.name)
        return [
            ProviderConfig(
                label=p["label"],
                base_url=p["base_url"],
                api_key=p["api_key"],
                model=p["model"],
                rate_limit_sec=p.get("rate_limit_sec", 60),
                rate_limited_until=None,
            )
            for p in raw
        ]

    async def on_call_logged(self, event: CallEvent) -> None:
        logger.info(
            "llmbroker provider=%s status=%s latency=%dms",
            event.provider_label,
            event.status,
            event.latency_ms,
        )

    async def on_rate_limited(self, provider_label: str, until: datetime) -> None:
        logger.warning("llmbroker provider %s rate-limited until %s", provider_label, until)

    async def on_quality_feedback(self, provider_label: str, *, usable: bool) -> None:
        if usable:
            return
        stats_path = _toml_stats_path(self._providers_toml)
        if stats_path.exists():
            with stats_path.open() as fh:
                data = json.load(fh)
        else:
            data = {}
        entry = data.setdefault(provider_label, {})
        entry["execution_fail_count"] = entry.get("execution_fail_count", 0) + 1
        tmp = stats_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.rename(stats_path)


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
