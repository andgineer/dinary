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

_DEFAULT_LANG = "ru"


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

    placement: dict[str, tuple[str, bool]] = {}
    for group_code, codes in definition["visible"].items():
        for code in codes:
            placement[code] = (group_code, True)
    for group_code, codes in definition["hidden"].items():
        for code in codes:
            placement[code] = (group_code, False)

    with storage.transaction(con):
        for sort_order, (group_code, group_names) in enumerate(definition["groups"].items()):
            name = group_names.get(lang, group_names.get(_DEFAULT_LANG, group_code))
            con.execute(
                "UPDATE category_groups SET name = ?, sort_order = ? WHERE code = ?",
                [name, sort_order, group_code],
            )

        for code, (group_code, is_visible) in placement.items():
            name = _resolve_name(con, definition, code, lang)
            con.execute(
                "UPDATE categories SET "
                "group_id = (SELECT id FROM category_groups WHERE code = ?), "
                "is_active = ?, name = ? "
                "WHERE code = ?",
                [group_code, 1 if is_visible else 0, name, code],
            )

        catalog.set_catalog_version(con, catalog.get_catalog_version(con) + 1)
        con.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('active_template', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [template_code],
        )


def _resolve_name(
    con: sqlite3.Connection,
    definition: dict,
    code: str,
    lang: str,
) -> str:
    """Resolve ``code``'s baked name: template rename > vocabulary translation > code."""
    renames = definition.get("renames", {})
    if code in renames and lang in renames[code]:
        return str(renames[code][lang])

    for try_lang in (lang, _DEFAULT_LANG):
        row = con.execute(
            "SELECT name FROM category_translations WHERE code = ? AND lang = ?",
            [code, try_lang],
        ).fetchone()
        if row is not None:
            return str(row[0])

    return code
