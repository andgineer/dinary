"""Repository helpers for the saved-currencies table.

The PWA picker shows whatever codes the operator has saved here,
plus a world-currency search and a manage-mode that ``add`` /
``remove``s rows. The default ``app_currency`` is always present
(seeded on first boot, deletion guarded by the API layer) so the
picker is never empty.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


_ISO_CODE_LEN = 3


def _normalise_code(code: str) -> str:
    if not isinstance(code, str):
        msg = "currency code must be a string"
        raise TypeError(msg)
    code = code.strip().upper()
    if len(code) != _ISO_CODE_LEN or not code.isalpha():
        msg = f"invalid ISO-4217 code: {code!r}"
        raise ValueError(msg)
    return code


def list_currencies(con: sqlite3.Connection) -> list[str]:
    """Return saved currency codes in alphabetical order (uppercased)."""
    rows = con.execute(
        "SELECT code FROM app_currencies ORDER BY code",
    ).fetchall()
    return [str(r[0]) for r in rows]


def add_currency(con: sqlite3.Connection, code: str) -> str:
    """Insert ``code`` (idempotent). Returns the canonical (uppercased) code."""
    canonical = _normalise_code(code)
    con.execute(
        "INSERT OR IGNORE INTO app_currencies (code) VALUES (?)",
        [canonical],
    )
    return canonical


def remove_currency(con: sqlite3.Connection, code: str) -> bool:
    """Delete ``code``. Returns True iff a row was removed."""
    canonical = _normalise_code(code)
    cur = con.execute(
        "DELETE FROM app_currencies WHERE code = ?",
        [canonical],
    )
    return cur.rowcount > 0


def has_currency(con: sqlite3.Connection, code: str) -> bool:
    canonical = _normalise_code(code)
    row = con.execute(
        "SELECT 1 FROM app_currencies WHERE code = ?",
        [canonical],
    ).fetchone()
    return row is not None


def seed_default_if_empty(con: sqlite3.Connection, default_code: str) -> None:
    """Insert ``default_code`` if the table is empty.

    Called from ``db.init_db`` so a fresh DB starts with the
    operator's chosen ``app_currency`` already in the picker. No-op
    once any row exists, so operators are free to add/remove
    currencies later without the seed re-asserting itself on reboot.
    """
    canonical = _normalise_code(default_code)
    row = con.execute("SELECT 1 FROM app_currencies LIMIT 1").fetchone()
    if row is not None:
        return
    con.execute(
        "INSERT INTO app_currencies (code) VALUES (?)",
        [canonical],
    )
