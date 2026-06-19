"""Store resolution: PIB/name cache lookup → LLM chain-name normalisation → DB upsert."""

import logging
import sqlite3

from dinary.background.classification.receipt_classifier import get_chain_name
from dinary.db import storage
from dinary.db.storage import transaction
from llmbroker import AsyncBroker

logger = logging.getLogger(__name__)


def _upsert_chain(conn: sqlite3.Connection, chain_name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO shop_chains (name) VALUES (?)", [chain_name])
    row = conn.execute("SELECT id FROM shop_chains WHERE name = ?", [chain_name]).fetchone()
    return int(row["id"])


def _select_store(
    conn: sqlite3.Connection,
    store_pib: str,
    store_name_raw: str,
) -> sqlite3.Row | None:
    if store_pib:
        return conn.execute(
            "SELECT id, chain_id FROM stores WHERE pib = ?",
            [store_pib],
        ).fetchone()
    return conn.execute(
        "SELECT id, chain_id FROM stores WHERE name = ? AND pib IS NULL",
        [store_name_raw],
    ).fetchone()


async def resolve_store(
    broker: AsyncBroker,
    store_pib: str,
    store_name_raw: str,
) -> tuple[int, int | None]:
    """Return (store_id, chain_id) for the given receipt store.

    chain_id may be None for stores without a resolved chain (legacy data).
    With PIB:    SELECT by pib → return if found; else LLM → insert → SELECT by pib.
    Without PIB: log error; SELECT by name → return if found; else LLM → insert → SELECT by name.
    """
    with storage.connection() as conn:
        if not store_pib:
            logger.error(
                "resolve_store: no PIB on receipt for store %r — falling back to name lookup",
                store_name_raw,
            )
        row = _select_store(conn, store_pib, store_name_raw)
        if row:
            return int(row["id"]), row["chain_id"]

    chain_name = await get_chain_name(broker, store_name_raw)
    chain_name = chain_name.strip() or store_name_raw.strip()

    with storage.connection() as conn, transaction(conn):
        chain_id = _upsert_chain(conn, chain_name)
        conn.execute(
            "INSERT OR IGNORE INTO stores (name, chain_id, pib) VALUES (?, ?, ?)",
            [store_name_raw, chain_id, store_pib or None],
        )
        row = _select_store(conn, store_pib, store_name_raw)
        if row is None:
            raise RuntimeError(
                f"Failed to resolve store: name={store_name_raw!r}, pib={store_pib!r}",
            )
        return int(row["id"]), row["chain_id"]
