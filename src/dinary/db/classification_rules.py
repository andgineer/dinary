"""Classification rules engine: (chain_id, item_name_normalized) → category.

Chain-specific rules take precedence over generic rules (chain_id IS NULL).
User corrections always store confidence_level=4.
"""

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime


@dataclasses.dataclass(frozen=True, slots=True)
class RuleHit:
    """Result of a successful rule lookup."""

    rule_id: int
    category_id: int
    confidence_level: int
    tag_ids: list[int]


@dataclasses.dataclass(frozen=True, slots=True)
class RuleSpec:
    """Classification assignment for one item."""

    category_id: int
    confidence_level: int
    source: str
    alternative_category_ids: tuple[int, ...] = ()
    tag_ids: tuple[int, ...] = ()


def classify_by_rules(
    conn: sqlite3.Connection,
    chain_id: int | None,
    item_name_normalized: str,
) -> RuleHit | None:
    """Return a RuleHit from stored rules, or None on miss.

    Chain-specific rule (chain_id IS NOT NULL) beats generic (chain_id IS NULL)
    for the same item name. Returns the stored confidence unchanged — no source
    penalty because no LLM call is involved.
    """
    saved_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, category_id, confidence_level, tag_ids
              FROM classification_rules
             WHERE (chain_id = ? OR chain_id IS NULL)
               AND item_name_normalized = ?
             ORDER BY chain_id NULLS LAST
             LIMIT 1
            """,
            [chain_id, item_name_normalized],
        ).fetchone()
    finally:
        conn.row_factory = saved_row_factory
    if row is None:
        return None
    tag_ids: list[int] = []
    if row["tag_ids"]:
        try:
            raw = json.loads(row["tag_ids"])
            tag_ids = [int(t) for t in raw if isinstance(t, (int, float))]
        except (json.JSONDecodeError, ValueError):
            pass
    return RuleHit(
        rule_id=int(row["id"]),
        category_id=int(row["category_id"]),
        confidence_level=int(row["confidence_level"]),
        tag_ids=tag_ids,
    )


def create_or_update_rule(
    conn: sqlite3.Connection,
    chain_id: int | None,
    item_name_normalized: str,
    spec: RuleSpec,
) -> int:
    """Upsert a classification rule.

    ``spec.source`` must be 'llm' or 'user_correction'.
    User corrections always set confidence_level=4 regardless of what is passed.
    Both sources persist alternative_category_ids and tag_ids from spec.
    """
    category_id = spec.category_id
    confidence_level = spec.confidence_level
    source = spec.source
    if source == "user_correction":
        confidence_level = 4

    now = datetime.now(UTC).isoformat()

    saved_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            """
            SELECT id, alternative_category_ids FROM classification_rules
             WHERE (chain_id IS ? OR (chain_id IS NULL AND ? IS NULL))
               AND item_name_normalized = ?
            """,
            [chain_id, chain_id, item_name_normalized],
        ).fetchone()
    finally:
        conn.row_factory = saved_row_factory

    if existing:
        conn.execute(
            """
            UPDATE classification_rules
               SET category_id = ?, confidence_level = ?, source = ?,
                   alternative_category_ids = ?, tag_ids = ?, updated_at = ?
             WHERE id = ?
            """,
            [
                category_id,
                confidence_level,
                source,
                json.dumps(list(spec.alternative_category_ids)),
                json.dumps(list(spec.tag_ids)),
                now,
                existing["id"],
            ],
        )
        return int(existing["id"])
    conn.execute(
        """
            INSERT INTO classification_rules
                   (chain_id, item_name_normalized, category_id,
                    confidence_level, source, alternative_category_ids, tag_ids,
                    created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
        [
            chain_id,
            item_name_normalized,
            category_id,
            confidence_level,
            source,
            json.dumps(list(spec.alternative_category_ids)),
            json.dumps(list(spec.tag_ids)),
            now,
            now,
        ],
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
