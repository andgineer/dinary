"""Seed config.duckdb from Google Sheets category data.

Reads the current (category, group) pairs from the sheet and creates
the 4D reference data + mapping table in config.duckdb.
"""

import logging
from datetime import date

import duckdb

from dinary.services import duckdb_repo
from dinary.services.duckdb_repo import SYNTHETIC_EVENT_PREFIX
from dinary.services.sheets import get_categories
from dinary.services.sql_loader import fetchall_as, load_sql

logger = logging.getLogger(__name__)

BENEFICIARY_ENVELOPES = {
    "собака": "собака",
    "ребенок": "Аня",
    "лариса": "Лариса",
}

TAG_ENVELOPES = {
    "релокация": "релокация",
    "профессиональное": "профессиональное",
}

SUBSCRIPTION_ENVELOPE = "приложения"
SUBSCRIPTION_TAG = "подписка"

ENTRY_GROUPS: list[tuple[str, list[str]]] = [
    ("Еда", ["еда"]),
    ("Дом", ["бытовые", "аренда", "обустройство"]),
    ("Покупки", ["одежда", "гаджеты"]),
    ("Связь и приложения", ["мобильник", "интернет", "приложения"]),
    ("Здоровье и уход", ["медицина", "лекарства", "БАД", "стрижка"]),
    ("Спорт", ["спорт", "велосипед"]),
    ("Досуг", ["развлечения", "кафе"]),
    ("Транспорт", ["транспорт", "машина", "топливо"]),
    ("Развитие", ["обучение"]),
    ("Семья и личное", ["карманные", "булавки"]),
    ("Платежи", ["налог"]),
]

LEGACY_FOOD_CATEGORY = "еда&бытовые"


def seed_from_sheet(year: int | None = None) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Read categories from Google Sheets and populate config.duckdb.

    Returns a summary of what was created.
    """
    if year is None:
        year = date.today().year

    cats = get_categories()
    if not cats:
        raise ValueError("No categories found in Google Sheets")

    duckdb_repo.init_config_db()
    con = duckdb_repo.get_config_connection(read_only=False)

    try:
        con.execute("BEGIN")

        cat_ids: dict[str, int] = {}
        beneficiary_ids: dict[str, int] = {}
        tag_ids: dict[str, int] = {}

        for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_categories.sql")):
            cat_ids[r.name] = r.id
        for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_members.sql")):
            beneficiary_ids[r.name] = r.id
        for r in fetchall_as(duckdb_repo.IdNameRow, con, load_sql("seed_load_tags.sql")):
            tag_ids[r.name] = r.id

        if cat_ids:
            logger.info("Config DB already has data, merging new entries")

        next_cat_id = max(cat_ids.values(), default=0) + 1
        next_ben_id = max(beneficiary_ids.values(), default=0) + 1
        next_tag_id = max(tag_ids.values(), default=0) + 1

        def ensure_category(cat_name: str) -> int:
            nonlocal next_cat_id
            if cat_name not in cat_ids:
                cid = next_cat_id
                next_cat_id += 1
                con.execute(
                    "INSERT INTO categories (id, name) VALUES (?, ?)",
                    [cid, cat_name],
                )
                cat_ids[cat_name] = cid
            return cat_ids[cat_name]

        def ensure_beneficiary(name: str) -> int:
            nonlocal next_ben_id
            if name not in beneficiary_ids:
                bid = next_ben_id
                next_ben_id += 1
                con.execute(
                    "INSERT INTO family_members (id, name) VALUES (?, ?)",
                    [bid, name],
                )
                beneficiary_ids[name] = bid
            return beneficiary_ids[name]

        def ensure_tag(name: str) -> int:
            nonlocal next_tag_id
            if name not in tag_ids:
                tid = next_tag_id
                next_tag_id += 1
                con.execute(
                    "INSERT INTO tags (id, name) VALUES (?, ?)",
                    [tid, name],
                )
                tag_ids[name] = tid
            return tag_ids[name]

        mapping_count = 0

        for c in cats:
            source_type = c.name
            source_envelope = c.group or ""

            if source_type == LEGACY_FOOD_CATEGORY:
                category_id = ensure_category("еда")
            else:
                category_id = ensure_category(source_type)

            beneficiary_id = (
                ensure_beneficiary(BENEFICIARY_ENVELOPES[source_envelope])
                if source_envelope in BENEFICIARY_ENVELOPES
                else None
            )
            event_id = None
            resolved_tag_ids: list[int] = []

            if source_envelope in TAG_ENVELOPES:
                resolved_tag_ids = [ensure_tag(TAG_ENVELOPES[source_envelope])]

            if source_envelope == SUBSCRIPTION_ENVELOPE:
                resolved_tag_ids = [ensure_tag(SUBSCRIPTION_TAG)]

            existing_mapping = con.execute(
                "SELECT 1 FROM source_type_mapping "
                "WHERE year = 0 AND source_type = ? AND source_envelope = ?",
                [source_type, source_envelope],
            ).fetchone()

            if not existing_mapping:
                con.execute(
                    """
                    INSERT INTO source_type_mapping
                    (year, source_type, source_envelope, category_id,
                     beneficiary_id, event_id, tag_ids)
                    VALUES (0, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        source_type,
                        source_envelope,
                        category_id,
                        beneficiary_id,
                        event_id,
                        sorted(resolved_tag_ids) if resolved_tag_ids else None,
                    ],
                )
                mapping_count += 1

        event_name = f"{SYNTHETIC_EVENT_PREFIX}{year}"
        existing_event = con.execute(
            "SELECT 1 FROM events WHERE name = ?",
            [event_name],
        ).fetchone()
        if not existing_event:
            max_row = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            max_event_id = max_row[0] if max_row else 0
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
                [max_event_id + 1, event_name, date(year, 1, 1), date(year, 12, 31)],
            )
            logger.info("Created synthetic travel event: %s", event_name)

        summary = {
            "categories": len(cat_ids),
            "beneficiaries": len(beneficiary_ids),
            "tags": len(tag_ids),
            "mappings_created": mapping_count,
        }
        con.execute("COMMIT")
        logger.info("Seed complete: %s", summary)
        return summary

    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def rebuild_taxonomy(con: duckdb.DuckDBPyConnection) -> int:
    """Create the entry_groups taxonomy from ENTRY_GROUPS constant.

    Returns the number of membership rows created.
    """
    con.execute("DELETE FROM category_taxonomy_membership")
    con.execute("DELETE FROM category_taxonomy_nodes")
    con.execute("DELETE FROM category_taxonomies")

    con.execute(
        "INSERT INTO category_taxonomies (id, key, title)"
        " VALUES (1, 'entry_groups', 'Entry Groups')",
    )

    memberships = 0
    for sort_order, (group_title, category_names) in enumerate(ENTRY_GROUPS, start=1):
        node_key = group_title.lower().replace(" ", "_")
        con.execute(
            "INSERT INTO category_taxonomy_nodes (id, taxonomy_id, key, title, sort_order) "
            "VALUES (?, 1, ?, ?, ?)",
            [sort_order, node_key, group_title, sort_order],
        )
        for cat_name in category_names:
            cat_row = con.execute(
                "SELECT id FROM categories WHERE name = ?",
                [cat_name],
            ).fetchone()
            if cat_row:
                con.execute(
                    "INSERT INTO category_taxonomy_membership (category_id, node_id) VALUES (?, ?)",
                    [cat_row[0], sort_order],
                )
                memberships += 1
            else:
                logger.warning("Category '%s' not in DB — skipping taxonomy link", cat_name)

    return memberships
