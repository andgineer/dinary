"""LLM provider business logic — read-only status plus a persistent user disable.

The provider list is owned by the preset file (`.deploy/llms.toml`), mirrored
into the broker on startup. There is no add/edit/delete path here; the only
mutation is the user disable/enable latch, which llmbroker persists.
"""

from datetime import UTC, datetime
from pathlib import Path

import llmbroker
from fastapi import HTTPException
from llmbroker.models import LLMSnapshot

_OPERATION = "receipt_classification"


def _derive_status(snap: LLMSnapshot, *, cooling: bool) -> str:
    """Precedence: disabled → no_key → cooling → available."""
    if snap.disabled:
        return "disabled"
    if not snap.has_key:
        return "no_key"
    if cooling:
        return "cooling"
    return "available"


def _snapshot_to_dict(
    name: str,
    snap: LLMSnapshot,
    optimizer: llmbroker.Optimizer,
    key_help: dict[str, str],
) -> dict:
    cooldown_until = snap.cooldown_until
    cooling = cooldown_until is not None and cooldown_until > datetime.now(UTC)
    status = _derive_status(snap, cooling=cooling)
    metrics = snap.metrics
    return {
        "name": name,
        "model": snap.config.model,
        "base_url": snap.config.base_url,
        "disabled": snap.disabled,
        "has_key": snap.has_key,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "status": status,
        "call_count": metrics.call_count if metrics else 0,
        "last_status": (metrics.last_status.value if metrics and metrics.last_status else None),
        "last_at": metrics.last_at.isoformat() if metrics and metrics.last_at else None,
        "demoted": _OPERATION in snap.demoted_operations,
        "quality_bound": optimizer.wilson_bound(name, _OPERATION),
        "help": None if snap.has_key else key_help.get(snap.config.api_key_ref),
    }


async def llm_status(
    llms: llmbroker.AsyncBroker,
    optimizer: llmbroker.Optimizer,
    providers_file: Path,
) -> dict:
    try:
        snapshot = await llms.snapshot()
    except RuntimeError:
        # llmbroker raises when the registry is empty (no providers configured yet);
        # a read-only status view should report an empty pool, not 500.
        snapshot = {}
    key_help: dict[str, str] = {}
    if any(not snap.has_key for snap in snapshot.values()) and providers_file.exists():
        key_info = await llmbroker.Registry(providers_file).key_info()
        key_help = {ref: info.help for ref, info in key_info.items()}

    providers = [
        _snapshot_to_dict(name, snap, optimizer, key_help) for name, snap in snapshot.items()
    ]
    total = len(providers)
    healthy = sum(1 for p in providers if p["status"] == "available")
    health = {
        "healthy": healthy,
        "total": total,
        "strategy": "failover" if total >= 2 else None,
    }
    return {"health": health, "providers": providers}


async def set_provider_disabled(name: str, *, disabled: bool, llms: llmbroker.AsyncBroker) -> None:
    try:
        snapshot = await llms.snapshot()
    except RuntimeError:
        snapshot = {}
    if name not in snapshot:
        raise HTTPException(status_code=404, detail="Provider not found")
    if disabled:
        await llms.disable_llm(name)
    else:
        await llms.enable_llm(name)
