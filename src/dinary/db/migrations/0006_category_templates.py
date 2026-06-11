"""Category templates: codes, visibility flags, template/translation storage.

Rebuilds ``categories`` and ``category_groups`` to drop their inline
``name`` UNIQUE constraints (a template-baked label may legitimately repeat
across categories) and add ``code`` / ``is_hidden`` / ``is_retired``
(``categories`` only) columns. Adds ``category_templates`` and
``category_translations`` for the template definitions and per-language
vocabulary, plus an index on ``expenses.category_id`` for the visibility
predicate.

This is a deliberate one-off, applied once against the single personal dev
DB: ``PRAGMA foreign_keys`` is a no-op while a transaction is open (the
project's ``SQLiteBackend.begin()`` opens one with ``BEGIN IMMEDIATE`` before
running migration steps), so this migration disables yoyo's transaction
wrapping (``__transactional__ = False``) and manages its own
``BEGIN``/``COMMIT`` around the table rebuilds, with the FK pragma toggled
in autocommit mode immediately outside that transaction.

No rollback: once ``apply_template`` has run, ``categories.name`` may contain
duplicates across templates, so restoring the ``name`` UNIQUE constraint
would fail. Revert by restoring a pre-migration DB backup.
"""

import sqlite3

from yoyo import step

__transactional__ = False


def _rebuild_categories(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE categories_new (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            group_id    INTEGER REFERENCES category_groups(id),
            is_active   BOOLEAN NOT NULL DEFAULT 1,
            sheet_name  TEXT,
            sheet_group TEXT,
            code        TEXT,
            is_hidden   BOOLEAN NOT NULL DEFAULT 0,
            is_retired  BOOLEAN NOT NULL DEFAULT 0
        )
        """,
    )
    conn.execute(
        """
        INSERT INTO categories_new
            (id, name, group_id, is_active, sheet_name, sheet_group,
             code, is_hidden, is_retired)
        SELECT id, name, group_id, is_active, sheet_name, sheet_group, NULL, 0, 0
        FROM categories
        """,
    )
    conn.execute("DROP TABLE categories")
    conn.execute("ALTER TABLE categories_new RENAME TO categories")
    conn.execute("CREATE UNIQUE INDEX ux_categories_code ON categories(code)")


def _rebuild_category_groups(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE category_groups_new (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            is_active  BOOLEAN NOT NULL DEFAULT 1,
            code       TEXT
        )
        """,
    )
    conn.execute(
        """
        INSERT INTO category_groups_new (id, name, sort_order, is_active, code)
        SELECT id, name, sort_order, is_active, NULL
        FROM category_groups
        """,
    )
    conn.execute("DROP TABLE category_groups")
    conn.execute("ALTER TABLE category_groups_new RENAME TO category_groups")
    conn.execute("CREATE UNIQUE INDEX ux_category_groups_code ON category_groups(code)")


def apply_step(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _rebuild_categories(conn)
            _rebuild_category_groups(conn)

            conn.execute("CREATE INDEX ix_expenses_category_id ON expenses(category_id)")

            conn.execute(
                """
                CREATE TABLE category_templates (
                    id              INTEGER PRIMARY KEY,
                    code            TEXT NOT NULL UNIQUE,
                    origin          TEXT NOT NULL CHECK (origin IN ('factory', 'custom')),
                    sort_order      INTEGER NOT NULL DEFAULT 0,
                    definition_json TEXT NOT NULL
                )
                """,
            )
            conn.execute(
                """
                CREATE TABLE category_translations (
                    code TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    name TEXT NOT NULL,
                    PRIMARY KEY (code, lang)
                )
                """,
            )

            fk_problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_problems:
                msg = f"foreign_key_check failed after category table rebuild: {fk_problems!r}"
                raise RuntimeError(msg)

            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


step(apply_step)
