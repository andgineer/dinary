-- Unified schema for data/dinary.duckdb.
-- Replaces the former config.duckdb + budget_YYYY.duckdb split.

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

CREATE TABLE events (
    id                  INTEGER PRIMARY KEY,
    name                TEXT UNIQUE NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE
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

CREATE TABLE logging_mapping (
    id             INTEGER PRIMARY KEY,
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE logging_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES logging_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
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
