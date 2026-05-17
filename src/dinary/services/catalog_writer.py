"""Catalog write infrastructure: canonical state hash and version bump.

See specs/reference/catalog-api.md.
"""

import hashlib
import logging
import sqlite3

from dinary.services.catalog import get_catalog_version, set_catalog_version

logger = logging.getLogger(__name__)


def hash_catalog_state(con: sqlite3.Connection) -> str:
    """Return a hex sha256 over the canonical catalog state."""
    return _hash_state(con)


def _canonical_state(con: sqlite3.Connection) -> bytes:
    """Serialise the full catalog to a deterministic byte string.

    Ordered by ``id`` so reordering a response body doesn't leak into
    the hash. Primary keys are included so a retire-and-recreate under
    the same name registers as a change.
    """
    parts: list[str] = []

    for row in con.execute(
        "SELECT id, name, sort_order, is_active FROM category_groups ORDER BY id",
    ).fetchall():
        parts.append(f"g|{row[0]}|{row[1]}|{row[2]}|{int(bool(row[3]))}")

    for row in con.execute(
        "SELECT id, name, group_id, is_active,"
        " COALESCE(sheet_name, ''), COALESCE(sheet_group, '')"
        " FROM categories ORDER BY id",
    ).fetchall():
        parts.append(
            f"c|{row[0]}|{row[1]}|{row[2]}|{int(bool(row[3]))}|{row[4]}|{row[5]}",
        )

    for row in con.execute(
        "SELECT id, name, date_from, date_to, auto_attach_enabled, is_active,"
        " COALESCE(auto_tags, '[]')"
        " FROM events ORDER BY id",
    ).fetchall():
        parts.append(
            f"e|{row[0]}|{row[1]}|{row[2]}|{row[3]}|{int(bool(row[4]))}|"
            f"{int(bool(row[5]))}|{row[6]}",
        )

    for row in con.execute(
        "SELECT id, name, is_active FROM tags ORDER BY id",
    ).fetchall():
        parts.append(f"t|{row[0]}|{row[1]}|{int(bool(row[2]))}")

    return "\n".join(parts).encode("utf-8")


def _hash_state(con: sqlite3.Connection) -> str:
    return hashlib.sha256(_canonical_state(con)).hexdigest()


def _commit_with_bump(
    con: sqlite3.Connection,
    before_hash: str,
    *,
    context: str,
) -> bool:
    """Finalise a mutation: bump ``catalog_version`` only if state changed, then COMMIT.

    Returns ``True`` when the version was bumped, ``False`` on no-op.
    """
    after_hash = _hash_state(con)
    bumped = False
    if before_hash != after_hash:
        previous = get_catalog_version(con)
        set_catalog_version(con, previous + 1)
        bumped = True
    con.execute("COMMIT")
    logger.info(
        "catalog_writer %s: %s",
        context,
        "bumped catalog_version" if bumped else "no-op (hash unchanged)",
    )
    return bumped


def _next_id(con: sqlite3.Connection, table: str) -> int:
    row = con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()  # noqa: S608
    return int(row[0]) if row else 1
