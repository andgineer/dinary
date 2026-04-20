CREATE TABLE category_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE categories (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    group_id INTEGER NOT NULL REFERENCES category_groups(id)
);

-- `name` is UNIQUE because seed_classification_catalog and
-- imports/expense_import.py both look events up by name, and
-- `INSERT ... ON CONFLICT DO NOTHING`
-- in the seed path needs the unique constraint to dedupe re-runs (the PK
-- on id only catches collisions when the deterministic id assignment
-- happens to align, which is fragile).
CREATE TABLE events (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT true,
    CHECK (date_to >= date_from)
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE sheet_mapping (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL DEFAULT 0,
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT '',
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    UNIQUE (year, sheet_category, sheet_group)
);

CREATE TABLE sheet_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES sheet_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
);

CREATE TABLE expense_id_registry (
    expense_id TEXT PRIMARY KEY,
    year       INTEGER NOT NULL
);

CREATE TABLE sheet_import_sources (
    year                  INTEGER PRIMARY KEY,
    spreadsheet_id        TEXT NOT NULL,
    worksheet_name        TEXT NOT NULL DEFAULT '',
    layout_key            TEXT NOT NULL DEFAULT 'default',
    notes                 TEXT,
    income_worksheet_name TEXT DEFAULT '',
    income_layout_key     TEXT DEFAULT ''
);

CREATE TABLE exchange_rates (
    date     DATE NOT NULL,
    currency TEXT NOT NULL,
    rate     DECIMAL(10,4) NOT NULL,
    PRIMARY KEY (date, currency)
);

CREATE TABLE app_metadata (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    catalog_version INTEGER NOT NULL DEFAULT 1 CHECK (catalog_version >= 1)
);
-- Defensive INSERT OR IGNORE so that a re-run of the migration on a non-wiped DB
-- (e.g. an accidental yoyo replay) does not break on the singleton PK.
INSERT OR IGNORE INTO app_metadata (id, catalog_version) VALUES (1, 1);
