"""Seed config.duckdb from Google Sheets category data.

Reads the current (category, group) pairs from the sheet and creates
the 5D reference data + mapping table in config.duckdb.
"""

import logging
from datetime import date

from dinary.services import duckdb_repo
from dinary.services.duckdb_repo import (
    SYNTHETIC_EVENT_PREFIX,
    TRAVEL_GROUP,
    CategoryRefRow,
    IdNameRow,
)
from dinary.services.sheets import get_categories
from dinary.services.sql_loader import fetchall_as, load_sql

logger = logging.getLogger(__name__)

BENEFICIARY_GROUPS = {
    "собака": "собака",
    "ребенок": "Аня",
    "лариса": "Лариса",
}

TAG_GROUPS = {
    "релокация": "релокация",
    "профессиональное": "профессиональное",
}


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

        group_ids: dict[str, int] = {}
        cat_ids: dict[tuple[str, int], int] = {}
        beneficiary_ids: dict[str, int] = {}
        tag_ids: dict[str, int] = {}

        for r in fetchall_as(IdNameRow, con, load_sql("seed_load_groups.sql")):
            group_ids[r.name] = r.id

        if group_ids:
            logger.info("Config DB already has data, merging new entries")

        for r in fetchall_as(CategoryRefRow, con, load_sql("seed_load_categories.sql")):
            cat_ids[(r.name, r.group_id)] = r.id
        for r in fetchall_as(IdNameRow, con, load_sql("seed_load_members.sql")):
            beneficiary_ids[r.name] = r.id
        for r in fetchall_as(IdNameRow, con, load_sql("seed_load_tags.sql")):
            tag_ids[r.name] = r.id

        next_group_id = max(group_ids.values(), default=0) + 1
        next_cat_id = max(cat_ids.values(), default=0) + 1
        next_ben_id = max(beneficiary_ids.values(), default=0) + 1
        next_tag_id = max(tag_ids.values(), default=0) + 1

        def ensure_group(name: str) -> int:
            nonlocal next_group_id
            if name not in group_ids:
                gid = next_group_id
                next_group_id += 1
                con.execute(
                    "INSERT INTO category_groups (id, name) VALUES (?, ?)",
                    [gid, name],
                )
                group_ids[name] = gid
            return group_ids[name]

        def ensure_category(cat_name: str, group_name: str) -> int:
            nonlocal next_cat_id
            gid = ensure_group(group_name)
            key = (cat_name, gid)
            if key not in cat_ids:
                cid = next_cat_id
                next_cat_id += 1
                con.execute(
                    "INSERT INTO categories (id, name, group_id) VALUES (?, ?, ?)",
                    [cid, cat_name, gid],
                )
                cat_ids[key] = cid
            return cat_ids[key]

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
            sheet_cat = c.name
            sheet_group = c.group or ""

            is_special = sheet_group in BENEFICIARY_GROUPS or sheet_group in TAG_GROUPS
            if sheet_group == TRAVEL_GROUP:
                group_name = TRAVEL_GROUP
            elif sheet_group and not is_special:
                group_name = sheet_group
            else:
                group_name = ""

            category_id = ensure_category(sheet_cat, group_name)
            beneficiary_id = (
                ensure_beneficiary(BENEFICIARY_GROUPS[sheet_group])
                if sheet_group in BENEFICIARY_GROUPS
                else None
            )
            event_id = None  # travel events resolved dynamically
            store_id = None
            resolved_tag_ids: list[int] = []

            if sheet_group in TAG_GROUPS:
                resolved_tag_ids = [ensure_tag(TAG_GROUPS[sheet_group])]

            existing_mapping = con.execute(
                "SELECT 1 FROM sheet_category_mapping "
                "WHERE year = 0 AND sheet_category = ? AND sheet_group = ?",
                [sheet_cat, sheet_group],
            ).fetchone()

            if not existing_mapping:
                con.execute(
                    """
                    INSERT INTO sheet_category_mapping
                    (year, sheet_category, sheet_group, category_id,
                     beneficiary_id, event_id, store_id, tag_ids)
                    VALUES (0, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        sheet_cat,
                        sheet_group,
                        category_id,
                        beneficiary_id,
                        event_id,
                        store_id,
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
            "category_groups": len(group_ids),
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
