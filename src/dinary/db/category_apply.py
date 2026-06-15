"""Apply a category template onto the live catalog rows.

Template definitions (Phase 1 seed) are read-only; applying one rewrites,
for every category code the template places, ``categories.group_id`` /
``is_active`` / ``name`` and the corresponding ``category_groups.name`` /
``sort_order``, bumps ``app_metadata.catalog_version``, and records
``app_metadata.active_template``. ``is_hidden`` and ``is_retired`` are never
touched. Categories whose code is absent from the template's
``visible``/``hidden`` (i.e. user-created ``u_``-prefixed categories) are
left untouched.
"""

import json
import sqlite3

from dinary.db import catalog, storage

DEFAULT_LANG = "ru"


def apply_template(con: sqlite3.Connection, template_code: str, lang: str) -> None:
    """Project ``template_code``'s definition, rendered in ``lang``, onto the catalog."""
    row = con.execute(
        "SELECT definition_json FROM category_templates WHERE code = ?",
        [template_code],
    ).fetchone()
    if row is None:
        msg = f"Unknown category template: {template_code!r}"
        raise ValueError(msg)
    definition = json.loads(row[0])
    translations = load_category_translations(con)

    placement: dict[str, tuple[str, bool]] = {}
    for group_code, codes in definition["visible"].items():
        for code in codes:
            placement[code] = (group_code, True)
    for group_code, codes in definition["hidden"].items():
        for code in codes:
            placement[code] = (group_code, False)

    with storage.transaction(con):
        used_codes = {
            str(code)
            for (code,) in con.execute(
                "SELECT DISTINCT c.code FROM categories c JOIN expenses e ON e.category_id = c.id",
            ).fetchall()
        }

        for sort_order, (group_code, group_names) in enumerate(definition["groups"].items()):
            name = group_names.get(lang, group_names.get(DEFAULT_LANG, group_code))
            con.execute(
                "UPDATE category_groups SET name = ?, sort_order = ? WHERE code = ?",
                [name, sort_order, group_code],
            )

        for code, (group_code, is_visible) in placement.items():
            name = resolve_category_name(translations, definition, code, lang)
            is_active = 1 if (is_visible or code in used_codes) else 0
            con.execute(
                "UPDATE categories SET "
                "group_id = (SELECT id FROM category_groups WHERE code = ?), "
                "is_active = ?, name = ? "
                "WHERE code = ?",
                [group_code, is_active, name, code],
            )

        catalog.set_catalog_version(con, catalog.get_catalog_version(con) + 1)
        con.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('active_template', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [template_code],
        )


def load_category_translations(con: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Load every ``category_translations`` row into ``{code: {lang: name}}``."""
    translations: dict[str, dict[str, str]] = {}
    for code, lang, name in con.execute("SELECT code, lang, name FROM category_translations"):
        translations.setdefault(code, {})[lang] = name
    return translations


def resolve_category_name(
    translations: dict[str, dict[str, str]],
    definition: dict,
    code: str,
    lang: str,
) -> str:
    """Resolve ``code``'s baked name: template rename > vocabulary translation > code."""
    renames = definition.get("renames", {})
    if code in renames and lang in renames[code]:
        return str(renames[code][lang])

    names = translations.get(code, {})
    return names.get(lang, names.get(DEFAULT_LANG, code))
