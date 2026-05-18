"""Receipt repository: DB operations for receipts, items, and drain jobs."""

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from dinary.adapters.serbian_receipt_parser import ParsedReceipt


@dataclass(frozen=True, slots=True)
class ItemClassification:
    """Classification output for one receipt item."""

    category_id: int | None
    confidence_level: int | None
    expense_id: int | None


@dataclass(slots=True)
class ReceiptJobRow:
    receipt_id: int
    url: str
    store_name_raw: str
    store_pib_raw: str
    invoice_number: str
    parsed_at: str | None
    used_journal_fallback: bool
    claim_token: str


@dataclass(slots=True)
class ReceiptItemRow:
    id: int
    name_raw: str
    name_normalized: str | None
    unit_price: float
    quantity: float
    total_price: float
    tax_label: str
    category_id: int | None
    confidence_level: int | None
    expense_id: int | None


def insert_receipt(conn: sqlite3.Connection, client_receipt_id: str, url: str) -> int:
    """Insert a bare receipt row (URL only, not yet parsed). Returns receipt_id."""
    conn.execute(
        "INSERT INTO receipts (client_receipt_id, url) VALUES (?, ?)",
        [client_receipt_id, url],
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def get_receipt_by_client_id(
    conn: sqlite3.Connection,
    client_receipt_id: str,
) -> tuple[int, str] | None:
    """Return (receipt_id, url) if the client_receipt_id already exists."""
    row = conn.execute(
        "SELECT id, url FROM receipts WHERE client_receipt_id = ?",
        [client_receipt_id],
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), str(row[1])


def insert_job(conn: sqlite3.Connection, receipt_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO receipt_classification_jobs (receipt_id) VALUES (?)",
        [receipt_id],
    )


def claim_next_job(conn: sqlite3.Connection, stale_minutes: int = 10) -> ReceiptJobRow | None:
    """Claim the oldest pending (or stale in_progress) job. Returns None if none available."""
    stale_cutoff = f"datetime('now', '-{stale_minutes} minutes')"
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            f"""
            SELECT r.id, r.url, r.store_name_raw, r.store_pib_raw,
                   r.invoice_number, r.parsed_at, r.used_journal_fallback
              FROM receipt_classification_jobs j
              JOIN receipts r ON r.id = j.receipt_id
             WHERE j.status = 'pending'
                OR (j.status = 'in_progress' AND j.claimed_at < {stale_cutoff})
             ORDER BY r.created_at
             LIMIT 1
            """,  # noqa: S608
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None

        receipt_id = int(row[0])
        token = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            UPDATE receipt_classification_jobs
               SET status = 'in_progress', claim_token = ?, claimed_at = ?
             WHERE receipt_id = ?
            """,
            [token, now, receipt_id],
        )
        result = ReceiptJobRow(
            receipt_id=receipt_id,
            url=str(row[1]),
            store_name_raw=str(row[2]),
            store_pib_raw=str(row[3]),
            invoice_number=str(row[4]),
            parsed_at=str(row[5]) if row[5] else None,
            used_journal_fallback=bool(row[6]),
            claim_token=token,
        )
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def save_parsed_receipt(
    conn: sqlite3.Connection,
    receipt_id: int,
    parsed: ParsedReceipt,
) -> None:
    """Store parsed receipt metadata and items atomically."""
    now = datetime.now(UTC).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE receipts
               SET store_name_raw = ?, store_pib_raw = ?, total_amount = ?,
                   invoice_number = ?, purchase_datetime = ?,
                   parsed_at = ?, used_journal_fallback = ?
             WHERE id = ?
            """,
            [
                parsed.store_name,
                parsed.store_pib,
                parsed.total_amount,
                parsed.invoice_number,
                parsed.purchase_datetime,
                now,
                1 if parsed.used_journal_fallback else 0,
                receipt_id,
            ],
        )
        for item in parsed.items:
            conn.execute(
                """
                INSERT INTO receipt_items
                       (receipt_id, name_raw, unit_price, quantity, total_price, tax_label)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    receipt_id,
                    item.name_raw,
                    item.unit_price,
                    item.quantity,
                    item.total_price,
                    item.tax_label,
                ],
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_receipt_items(conn: sqlite3.Connection, receipt_id: int) -> list[ReceiptItemRow]:
    rows = conn.execute(
        """
        SELECT id, name_raw, name_normalized, unit_price, quantity, total_price,
               tax_label, category_id, confidence_level, expense_id
          FROM receipt_items
         WHERE receipt_id = ?
         ORDER BY id
        """,
        [receipt_id],
    ).fetchall()
    return [
        ReceiptItemRow(
            id=int(r[0]),
            name_raw=str(r[1]),
            name_normalized=str(r[2]) if r[2] else None,
            unit_price=float(r[3]),
            quantity=float(r[4]),
            total_price=float(r[5]),
            tax_label=str(r[6]),
            category_id=int(r[7]) if r[7] is not None else None,
            confidence_level=int(r[8]) if r[8] is not None else None,
            expense_id=int(r[9]) if r[9] is not None else None,
        )
        for r in rows
    ]


def update_receipt_item(
    conn: sqlite3.Connection,
    item_id: int,
    name_normalized: str,
    classification: ItemClassification,
) -> None:
    conn.execute(
        """
        UPDATE receipt_items
           SET name_normalized = ?, category_id = ?, confidence_level = ?, expense_id = ?
         WHERE id = ?
        """,
        [
            name_normalized,
            classification.category_id,
            classification.confidence_level,
            classification.expense_id,
            item_id,
        ],
    )


def complete_job(conn: sqlite3.Connection, receipt_id: int) -> None:
    conn.execute(
        "DELETE FROM receipt_classification_jobs WHERE receipt_id = ?",
        [receipt_id],
    )


def poison_job(conn: sqlite3.Connection, receipt_id: int, error: str) -> None:
    conn.execute(
        """
        UPDATE receipt_classification_jobs
           SET status = 'poisoned', last_error = ?
         WHERE receipt_id = ?
        """,
        [error[:2000], receipt_id],
    )


def release_job(conn: sqlite3.Connection, receipt_id: int, claim_token: str) -> None:
    """Release a claimed job back to 'pending' for retry.

    Matches on claim_token so a stale release from a previous attempt cannot
    accidentally reset a job that has already been re-claimed by a new worker.
    """
    conn.execute(
        """
        UPDATE receipt_classification_jobs
           SET status = 'pending', claim_token = NULL, claimed_at = NULL
         WHERE receipt_id = ? AND claim_token = ?
        """,
        [receipt_id, claim_token],
    )


def trim_llm_call_log(conn: sqlite3.Connection, keep: int = 200) -> None:
    conn.execute(
        """
        DELETE FROM llm_call_log
         WHERE id NOT IN (
             SELECT id FROM llm_call_log ORDER BY id DESC LIMIT ?
         )
        """,
        [keep],
    )


def requeue_receipts(
    conn: sqlite3.Connection,
    receipt_ids: list[int],
    clear_rules: bool = False,
) -> None:
    """Reset classification state and re-queue jobs for the given receipt IDs."""
    if not receipt_ids:
        return
    placeholders = ",".join("?" * len(receipt_ids))
    # Clear FK references on items before deleting parent expenses.
    conn.execute(
        f"""
        UPDATE receipt_items
           SET category_id = NULL, confidence_level = NULL, expense_id = NULL
         WHERE receipt_id IN ({placeholders})
        """,  # noqa: S608
        receipt_ids,
    )
    conn.execute(
        f"DELETE FROM expenses WHERE receipt_id IN ({placeholders})",  # noqa: S608
        receipt_ids,
    )
    if clear_rules:
        # Delete rules scoped to the stores of the target receipts, plus
        # generic rules (store_id IS NULL) that map names found in those receipts.
        scoped_items = conn.execute(
            f"SELECT DISTINCT ri.name_normalized, rec.store_id"  # noqa: S608
            f"  FROM receipt_items ri"
            f"  JOIN receipts rec ON rec.id = ri.receipt_id"
            f" WHERE ri.receipt_id IN ({placeholders})"
            f"   AND ri.name_normalized IS NOT NULL",
            receipt_ids,
        ).fetchall()
        for name, item_store_id in scoped_items:
            conn.execute(
                "DELETE FROM classification_rules"
                " WHERE item_name_normalized = ?"
                "   AND (store_id IS NULL OR store_id IS ?)",
                [name, item_store_id],
            )
    conn.execute(
        f"""
        INSERT INTO receipt_classification_jobs (receipt_id)
             SELECT id FROM receipts WHERE id IN ({placeholders})
        ON CONFLICT(receipt_id) DO NOTHING
        """,  # noqa: S608
        receipt_ids,
    )


def count_pending_classification_jobs(conn: sqlite3.Connection) -> int:
    """Return the number of jobs with status 'pending' or 'in_progress'."""
    return conn.execute(
        "SELECT COUNT(*) FROM receipt_classification_jobs"
        " WHERE status IN ('pending', 'in_progress')",
    ).fetchone()[0]
