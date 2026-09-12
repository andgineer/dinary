"""LLM provider business logic — read-only status plus a persistent user disable.

The provider list is owned by llmbroker's curated model list, merged into the
broker's registry on startup. There is no add/edit/delete path here; the only
mutation is the user disable/enable latch, which llmbroker persists.
"""

import logging
from datetime import UTC, datetime

import llmbroker
from fastapi import HTTPException
from llmbroker.models import LLMSnapshot, PoolSnapshot

from dinary.background.classification.receipt_classifier import CLASSIFICATION_OPERATION

logger = logging.getLogger(__name__)


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
        "demoted": CLASSIFICATION_OPERATION in snap.demoted_operations,
        "quality_bound": optimizer.wilson_bound(name, CLASSIFICATION_OPERATION),
        # llmbroker leaves the hint empty where its registry carries no key
        # metadata; the field stays "a hint or nothing" rather than an empty string.
        "help": None if snap.has_key else (key_help.get(snap.config.api_key_ref) or None),
    }


async def _pool_snapshot(llms: llmbroker.AsyncBroker) -> PoolSnapshot | None:
    """``None`` when there is no pool to report on, which a read-only status view
    answers with an empty list rather than a 500. Two cases reach it: a registry
    nothing has been synced into, and a broker already closed by a shutdown that
    raced this request — llmbroker raises a bare ``RuntimeError`` for the latter,
    and every ``LLMBrokerError`` derives from it.
    """
    try:
        return await llms.snapshot()
    except llmbroker.SchemaVersionError:
        # A database left at another release's store schema is a deployment fault,
        # and reporting it as "nothing synced yet" would send the operator to the
        # one screen that cannot fix it.
        raise
    except RuntimeError:
        logger.warning("llm pool unavailable — reporting an empty pool", exc_info=True)
        return None


async def llm_status(llms: llmbroker.AsyncBroker, optimizer: llmbroker.Optimizer) -> dict:
    snapshot = await _pool_snapshot(llms)
    providers: list[dict] = []
    if snapshot is not None:
        key_help = {pending.api_key_ref: pending.help for pending in snapshot.missing_keys}
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
    snapshot = await _pool_snapshot(llms)
    if snapshot is None or name not in snapshot:
        raise HTTPException(status_code=404, detail="Provider not found")
    if disabled:
        await llms.disable_llm(name)
    else:
        await llms.enable_llm(name)
