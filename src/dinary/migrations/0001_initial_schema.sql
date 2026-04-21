-- Unified schema for data/dinary.duckdb.
-- Replaces the former config.duckdb + budget_YYYY.duckdb split and absorbs
-- the phase-2 runtime mapping tables under their final name `sheet_mapping`.

-- =========================================================================
-- Catalog tables (formerly in config.duckdb)
-- =========================================================================

CREATE TABLE category_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    sort_order INTEGER NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE categories (
    id        INTEGER PRIMARY KEY,
    name      TEXT UNIQUE NOT NULL,
    group_id  INTEGER REFERENCES category_groups(id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sheet_name  TEXT,
    sheet_group TEXT
);

-- ``auto_tags`` is a JSON array of tag names (e.g. '["отпуск"]'). When a
-- runtime expense attaches an event, the listed tags are unioned into the
-- expense's tag set both at POST time and during historical import. Empty
-- array (default) means the event contributes no automatic tags.
CREATE TABLE events (
    id                  INTEGER PRIMARY KEY,
    name                TEXT UNIQUE NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    auto_tags           TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE tags (
    id        INTEGER PRIMARY KEY,
    name      TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE exchange_rates (
    currency TEXT NOT NULL,
    date     DATE NOT NULL,
    rate     DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (currency, date)
);

CREATE TABLE import_sources (
    year                  INTEGER PRIMARY KEY,
    spreadsheet_id        TEXT NOT NULL,
    worksheet_name        TEXT NOT NULL DEFAULT '',
    layout_key            TEXT NOT NULL DEFAULT 'default',
    notes                 TEXT,
    income_worksheet_name TEXT DEFAULT '',
    income_layout_key     TEXT DEFAULT ''
);

CREATE TABLE import_mapping (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL DEFAULT 0,
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT '',
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    UNIQUE (year, sheet_category, sheet_group)
);

CREATE TABLE import_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES import_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
);

-- ``sheet_mapping`` is the ordered, five-column runtime map that projects a
-- 3D expense (category, event, tags) onto the 2D sheet columns
-- (Расходы, Конверт). Evaluated top-to-bottom by ``row_order``:
--
--   * ``category_id IS NULL`` / ``event_id IS NULL`` mean "wildcard" (any).
--     A row with explicit ids only matches when every id matches.
--   * ``sheet_mapping_tags`` is a required-set filter: the row matches only
--     when every listed tag is present on the expense.
--   * ``sheet_category`` / ``sheet_group`` carry two semantically distinct
--     states: literal ``'*'`` ("don't decide, inherit from a later row" —
--     the map-tab parser also normalises empty / whitespace-only cells
--     to ``'*'``, so the two surface shapes mean the same thing) and any
--     other value (explicit assignment — including the empty string,
--     though that would have to be inserted directly into the table
--     since the parser can never produce it). The resolver takes the
--     first non-``*`` value per column independently.
--
-- Final fallbacks (applied only when no row assigned a value for a column):
-- ``sheet_category`` defaults to the category's canonical name;
-- ``sheet_group`` defaults to the empty string.
CREATE TABLE sheet_mapping (
    row_order      INTEGER PRIMARY KEY,
    category_id    INTEGER REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL
);

CREATE TABLE sheet_mapping_tags (
    mapping_row_order INTEGER NOT NULL REFERENCES sheet_mapping(row_order),
    tag_id            INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_row_order, tag_id)
);

CREATE TABLE app_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO app_metadata (key, value) VALUES ('catalog_version', '1');

-- =========================================================================
-- Ledger tables (formerly in budget_YYYY.duckdb)
-- =========================================================================

CREATE SEQUENCE expenses_id_seq;

CREATE TABLE expenses (
    id                 INTEGER PRIMARY KEY DEFAULT nextval('expenses_id_seq'),
    client_expense_id  TEXT UNIQUE,
    datetime           TIMESTAMP NOT NULL,
    amount             DECIMAL(12,2) NOT NULL,
    amount_original    DECIMAL(12,2) NOT NULL,
    currency_original  TEXT NOT NULL,
    category_id        INTEGER NOT NULL REFERENCES categories(id),
    event_id           INTEGER REFERENCES events(id),
    comment            TEXT,
    sheet_category     TEXT,
    sheet_group        TEXT
);

CREATE TABLE expense_tags (
    expense_id INTEGER NOT NULL REFERENCES expenses(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (expense_id, tag_id)
);

CREATE TABLE sheet_logging_jobs (
    expense_id  INTEGER PRIMARY KEY REFERENCES expenses(id),
    status      TEXT NOT NULL DEFAULT 'pending',
    claim_token TEXT,
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);

CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);
