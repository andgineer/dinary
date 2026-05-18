"""Store resolution: PIB-keyed cache → LLM chain-name identification → DB upsert."""

import sqlite3

from dinary.adapters.llm_client import ProviderPool


async def resolve_store(
    conn: sqlite3.Connection,
    pool: ProviderPool,
    store_pib: str,
    store_name_raw: str,
) -> int:
    """Return store_id for the given receipt store.

    1. PIB cache lookup (no LLM call).
    2. On miss: ask LLM for canonical chain name.
    3. Chain name lookup — if found, UPDATE pib on that row and return.
    4. Both miss: INSERT new stores row.
    """
    if store_pib:
        row = conn.execute(
            "SELECT id FROM stores WHERE pib = ?",
            [store_pib],
        ).fetchone()
        if row:
            return int(row[0])

    chain_name = await pool.get_chain_name(conn, store_name_raw)
    chain_name = chain_name.strip() or store_name_raw.strip()

    row = conn.execute(
        "SELECT id FROM stores WHERE chain_name = ?",
        [chain_name],
    ).fetchone()
    if row:
        store_id = int(row[0])
        if store_pib:
            conn.execute(
                "UPDATE stores SET pib = ? WHERE id = ?",
                [store_pib, store_id],
            )
        return store_id

    conn.execute(
        "INSERT OR IGNORE INTO stores (chain_name, pib) VALUES (?, ?)",
        [chain_name, store_pib or None],
    )
    row = conn.execute("SELECT id FROM stores WHERE chain_name = ?", [chain_name]).fetchone()
    return int(row[0])
