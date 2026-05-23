"""Classification rules engine: (store_id, item_name_normalized) → category.

Chain-specific rules take precedence over generic rules (store_id IS NULL).
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
    store_id: int | None,
    item_name_normalized: str,
) -> RuleHit | None:
    """Return a RuleHit from stored rules, or None on miss.

    Chain-specific rule (store_id IS NOT NULL) beats generic (store_id IS NULL)
    for the same item name. Returns the stored confidence unchanged — no source
    penalty because no LLM call is involved.
    """
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT id, category_id, confidence_level, tag_ids
          FROM classification_rules
         WHERE (store_id = ? OR store_id IS NULL)
           AND item_name_normalized = ?
         ORDER BY store_id NULLS LAST
         LIMIT 1
        """,
        [store_id, item_name_normalized],
    ).fetchone()
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
    store_id: int | None,
    item_name_normalized: str,
    spec: RuleSpec,
) -> int:
    """Upsert a classification rule.

    ``spec.source`` must be 'llm' or 'user_correction'.
    User corrections always set confidence_level=4 regardless of what is passed.
    For 'user_correction': overwrites tag_ids, leaves alternative_category_ids unchanged.
    For 'llm': persists alternative_category_ids and tag_ids.
    """
    category_id = spec.category_id
    confidence_level = spec.confidence_level
    source = spec.source
    if source == "user_correction":
        confidence_level = 4

    now = datetime.now(UTC).isoformat()

    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        """
        SELECT id, alternative_category_ids FROM classification_rules
         WHERE (store_id IS ? OR (store_id IS NULL AND ? IS NULL))
           AND item_name_normalized = ?
        """,
        [store_id, store_id, item_name_normalized],
    ).fetchone()

    if existing:
        if source == "user_correction":
            conn.execute(
                """
                UPDATE classification_rules
                   SET category_id = ?, confidence_level = ?, source = ?,
                       alternative_category_ids = '[]', tag_ids = ?, updated_at = ?
                 WHERE id = ?
                """,
                [
                    category_id,
                    confidence_level,
                    source,
                    json.dumps(list(spec.tag_ids)),
                    now,
                    existing["id"],
                ],
            )
        else:
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
                   (store_id, item_name_normalized, category_id,
                    confidence_level, source, alternative_category_ids, tag_ids,
                    created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
        [
            store_id,
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
