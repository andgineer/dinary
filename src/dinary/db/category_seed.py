"""Seed and reconcile the category catalog from packaged template files.

``src/dinary/category_templates/`` ships the category vocabulary
(``categories.yml``) and one factory template per onboarding choice
(``*.yaml``). ``seed_category_templates`` loads them into ``categories`` /
``category_groups`` / ``category_translations`` / ``category_templates`` by
``code``: insert new factory codes, update existing rows in place, and retire
factory codes that dropped out of the vocabulary. ``u_``-prefixed
(user-created) codes are never touched.

``migrate_personal_catalog`` is a one-off for the pre-existing personal DB:
it backfills ``code`` onto categories/groups that predate this schema (by
name, via the hardcoded maps below), then runs the normal seed and applies
the ``active`` template in Russian.

``bootstrap_categories`` is the single entry point, called on every app
boot, that picks between the two.
"""

import json
import sqlite3

from dinary.category_templates import loader
from dinary.category_templates.loader import Template
from dinary.db import storage
from dinary.db.category_apply import apply_template

_DEFAULT_LANG = "ru"

# Onboarding display order (specs/reference/category-templates.md): Simple
# first "so almost anyone finds a fit fast". Not load_templates()'s
# alphabetical-by-filename order, which is a parsing convenience only.
TEMPLATE_SORT_ORDER = {"simple": 0, "active": 1, "family": 2, "freelancer": 3}

# Pre-existing personal DB: category name -> factory code (db/category_seed.py
# only touches non-u_ rows, so this map never collides with user categories).
CATEGORY_MAP = {
    "алкоголь": "alcohol",
    "деликатесы": "delicacies",
    "еда": "groceries",
    "фрукты": "fruit",
    "кафе": "cafe",
    "интернет": "internet",
    "коммунальные": "utilities",
    "мобильник": "mobile",
    "сервисы": "subscriptions",
    "аренда": "rent",
    "бытовая техника": "appliances",
    "мебель": "furniture",
    "ремонт": "repairs",
    "хозтовары": "household_goods",
    "обучение": "education",
    "продуктивность": "productivity",
    "ЗОЖ": "wellness",
    "гигиена": "hygiene",
    "лекарства": "pharmacy",
    "медицина": "doctor",
    "карманные": "pocket_money",
    "одежда": "clothing",
    "подарки": "gifts",
    "велосипед": "cycling",
    "лыжи": "skiing",
    "спорт": "sport",
    "машина": "car",
    "топливо": "fuel",
    "транспорт": "transit",
    "гаджеты": "gadgets",
    "инструменты": "tools",
    "развлечения": "entertainment",
    "электроника": "electronics",
    "налог": "tax",
    "штрафы": "fines",
}

# Pre-existing personal DB: group name -> factory group code.
GROUP_MAP = {
    "Государство": "government",
    "Еда": "food",
    "ЖКХ и сервисы": "utilities",
    "Жильё": "housing",
    "Знания и продуктивность": "growth",
    "Красота и ЗОЖ": "beauty",
    "Медицина": "health",
    "Семья и личное": "personal",
    "Спорт": "sport",
    "Транспорт": "transport",
    "Хобби и отдых": "hobbies",
}


def seed_category_templates(con: sqlite3.Connection) -> None:
    """Idempotent fresh-seed / reconcile of the factory vocabulary and templates.

    Reconciles by ``code``: never deletes, never renumbers ``id``. Rows whose
    ``code`` starts with ``u_`` (user-created) are never touched.
    """
    vocabulary = loader.load_vocabulary()
    templates = loader.load_templates()
    loader.validate(vocabulary, templates)

    with storage.transaction(con):
        _upsert_translations(con, vocabulary)
        _upsert_categories(con, vocabulary)
        _upsert_category_groups(con, templates)
        _upsert_category_templates(con, templates)
        _retire_vanished(con, vocabulary, templates)


def _upsert_translations(con: sqlite3.Connection, vocabulary: dict[str, dict[str, str]]) -> None:
    for code, names in vocabulary.items():
        for lang, name in names.items():
            con.execute(
                "INSERT INTO category_translations (code, lang, name) VALUES (?, ?, ?) "
                "ON CONFLICT(code, lang) DO UPDATE SET name = excluded.name",
                [code, lang, name],
            )


def _upsert_categories(con: sqlite3.Connection, vocabulary: dict[str, dict[str, str]]) -> None:
    for code, names in vocabulary.items():
        existing = con.execute("SELECT id FROM categories WHERE code = ?", [code]).fetchone()
        if existing is None:
            default_name = names.get(_DEFAULT_LANG, code)
            con.execute(
                "INSERT INTO categories "
                "(name, group_id, is_active, code, is_hidden, is_retired) "
                "VALUES (?, NULL, 0, ?, 0, 0)",
                [default_name, code],
            )
        else:
            con.execute(
                "UPDATE categories SET code = ?, is_retired = 0 WHERE id = ?",
                [code, existing[0]],
            )


def _upsert_category_groups(con: sqlite3.Connection, templates: list[Template]) -> None:
    """Ensure a ``category_groups`` row per group code used across templates.

    ``name`` comes from the first template (in ``load_templates()`` order) that
    declares the group; ``apply_template`` re-bakes the per-template name on apply.
    """
    seen: dict[str, dict[str, str]] = {}
    for template in templates:
        for group_code, names in template.groups.items():
            seen.setdefault(group_code, names)

    for group_code, names in seen.items():
        existing = con.execute(
            "SELECT id FROM category_groups WHERE code = ?",
            [group_code],
        ).fetchone()
        if existing is None:
            default_name = names.get(_DEFAULT_LANG, group_code)
            con.execute(
                "INSERT INTO category_groups (name, sort_order, is_active, code) "
                "VALUES (?, 0, 1, ?)",
                [default_name, group_code],
            )


def _upsert_category_templates(con: sqlite3.Connection, templates: list[Template]) -> None:
    for template in templates:
        definition = {
            "names": template.names,
            "taglines": template.taglines,
            "groups": template.groups,
            "renames": template.renames,
            "visible": template.visible,
            "hidden": template.hidden,
        }
        # Group order in "groups" is the template's display order, consumed by
        # apply_template's sort_order assignment — sort_keys would scramble it.
        definition_json = json.dumps(definition, ensure_ascii=False)
        con.execute(
            "INSERT INTO category_templates (code, origin, sort_order, definition_json) "
            "VALUES (?, 'factory', ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "sort_order = excluded.sort_order, definition_json = excluded.definition_json",
            [template.code, TEMPLATE_SORT_ORDER[template.code], definition_json],
        )


def _retire_vanished(
    con: sqlite3.Connection,
    vocabulary: dict[str, dict[str, str]],
    templates: list[Template],
) -> None:
    """Retire factory categories/templates whose code dropped out of the source files."""
    vocab_codes = list(vocabulary)
    placeholders = ",".join("?" for _ in vocab_codes)
    con.execute(
        "UPDATE categories SET is_active = 0, is_retired = 1 "  # noqa: S608
        f"WHERE code IS NOT NULL AND code NOT LIKE 'u_%' AND code NOT IN ({placeholders})",
        vocab_codes,
    )

    template_codes = [t.code for t in templates]
    placeholders = ",".join("?" for _ in template_codes)
    vanished = [
        row[0]
        for row in con.execute(
            "SELECT code FROM category_templates WHERE origin = 'factory' "  # noqa: S608
            f"AND code NOT IN ({placeholders})",
            template_codes,
        ).fetchall()
    ]
    if not vanished:
        return

    active = con.execute(
        "SELECT value FROM app_metadata WHERE key = 'active_template'",
    ).fetchone()
    if active is not None and active[0] in vanished:
        con.execute("DELETE FROM app_metadata WHERE key = 'active_template'")

    placeholders = ",".join("?" for _ in vanished)
    con.execute(f"DELETE FROM category_templates WHERE code IN ({placeholders})", vanished)  # noqa: S608


def migrate_personal_catalog(con: sqlite3.Connection) -> None:
    """One-off: backfill factory codes onto the pre-existing personal catalog.

    Guarded: returns immediately if any ``categories.code`` is already set
    (``bootstrap_categories`` itself never reaches this on a re-run, since by
    then every row has a code).
    """
    already_migrated = con.execute(
        "SELECT 1 FROM categories WHERE code IS NOT NULL LIMIT 1",
    ).fetchone()
    if already_migrated is not None:
        return

    with storage.transaction(con):
        _backfill_category_codes(con)
        _backfill_group_codes(con)

    seed_category_templates(con)
    apply_template(con, "active", "ru")


def _backfill_category_codes(con: sqlite3.Connection) -> None:
    rows = con.execute("SELECT id, name FROM categories WHERE code IS NULL").fetchall()
    unknown = sorted({name for _id, name in rows if name not in CATEGORY_MAP})
    if unknown:
        msg = f"migrate_personal_catalog: unrecognised category names: {unknown}"
        raise ValueError(msg)
    for cat_id, name in rows:
        con.execute("UPDATE categories SET code = ? WHERE id = ?", [CATEGORY_MAP[name], cat_id])


def _backfill_group_codes(con: sqlite3.Connection) -> None:
    rows = con.execute("SELECT id, name FROM category_groups WHERE code IS NULL").fetchall()
    unknown = sorted({name for _id, name in rows if name not in GROUP_MAP})
    if unknown:
        msg = f"migrate_personal_catalog: unrecognised group names: {unknown}"
        raise ValueError(msg)
    for group_id, name in rows:
        con.execute(
            "UPDATE category_groups SET code = ? WHERE id = ?",
            [GROUP_MAP[name], group_id],
        )


def bootstrap_categories(con: sqlite3.Connection) -> None:
    """Select fresh-seed / reconcile vs. one-off personal migration.

    A non-empty ``categories`` table with at least one ``NULL`` code means
    the pre-existing personal DB; everything else (empty, or every row
    already coded) goes through the normal seed/reconcile path.
    """
    has_rows = con.execute("SELECT 1 FROM categories LIMIT 1").fetchone()
    has_null_code = con.execute("SELECT 1 FROM categories WHERE code IS NULL LIMIT 1").fetchone()
    if has_rows and has_null_code:
        migrate_personal_catalog(con)
    else:
        seed_category_templates(con)
