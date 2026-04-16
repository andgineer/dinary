"""Seed config.duckdb from Google Sheets category data.

Reads the current (category, group) pairs from the sheet and creates
the 5D reference data + mapping table in config.duckdb.
"""

import logging
from datetime import date

from dinary.services import duckdb_repo
from dinary.services.duckdb_repo import SYNTHETIC_EVENT_PREFIX, TRAVEL_GROUP
from dinary.services.sheets import get_categories

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


def seed_from_sheet(year: int | None = None) -> dict:
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

        for r in duckdb_repo.load_id_name_rows(con, "category_groups"):
            group_ids[r.name] = r.id

        if group_ids:
            logger.info("Config DB already has data, merging new entries")

        for r in duckdb_repo.load_category_refs(con):
            cat_ids[(r.name, r.group_id)] = r.id
        for r in duckdb_repo.load_id_name_rows(con, "family_members"):
            beneficiary_ids[r.name] = r.id
        for r in duckdb_repo.load_id_name_rows(con, "tags"):
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
                duckdb_repo.insert_row(con, "category_groups", {"id": gid, "name": name})
                group_ids[name] = gid
            return group_ids[name]

        def ensure_category(cat_name: str, group_name: str) -> int:
            nonlocal next_cat_id
            gid = ensure_group(group_name)
            key = (cat_name, gid)
            if key not in cat_ids:
                cid = next_cat_id
                next_cat_id += 1
                duckdb_repo.insert_row(con, "categories", {"id": cid, "name": cat_name, "group_id": gid})
                cat_ids[key] = cid
            return cat_ids[key]

        def ensure_beneficiary(name: str) -> int:
            nonlocal next_ben_id
            if name not in beneficiary_ids:
                bid = next_ben_id
                next_ben_id += 1
                duckdb_repo.insert_row(con, "family_members", {"id": bid, "name": name})
                beneficiary_ids[name] = bid
            return beneficiary_ids[name]

        def ensure_tag(name: str) -> int:
            nonlocal next_tag_id
            if name not in tag_ids:
                tid = next_tag_id
                next_tag_id += 1
                duckdb_repo.insert_row(con, "tags", {"id": tid, "name": name})
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
            beneficiary_id = ensure_beneficiary(BENEFICIARY_GROUPS[sheet_group]) if sheet_group in BENEFICIARY_GROUPS else None
            event_id = None
            store_id = None
            resolved_tag_ids: list[int] = []

            if sheet_group in TAG_GROUPS:
                resolved_tag_ids = [ensure_tag(TAG_GROUPS[sheet_group])]

            if not duckdb_repo.row_exists(con, "sheet_category_mapping", sheet_category=sheet_cat, sheet_group=sheet_group):
                duckdb_repo.insert_row(con, "sheet_category_mapping", {
                    "sheet_category": sheet_cat,
                    "sheet_group": sheet_group,
                    "category_id": category_id,
                    "beneficiary_id": beneficiary_id,
                    "event_id": event_id,
                    "store_id": store_id,
                    "tag_ids": sorted(resolved_tag_ids) if resolved_tag_ids else None,
                })
                mapping_count += 1

        event_name = f"{SYNTHETIC_EVENT_PREFIX}{year}"
        if not duckdb_repo.row_exists(con, "events", name=event_name):
            new_event_id = duckdb_repo.max_id(con, "events") + 1
            duckdb_repo.insert_row(con, "events", {
                "id": new_event_id,
                "name": event_name,
                "date_from": date(year, 1, 1),
                "date_to": date(year, 12, 31),
            })
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
        duckdb_repo.close_connection(con)
