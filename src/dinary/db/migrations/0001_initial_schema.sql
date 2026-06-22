-- Baseline schema for data/dinary.db (SQLite).

CREATE TABLE app_currencies (
    code     TEXT PRIMARY KEY,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE app_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE "categories" (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            group_id    INTEGER REFERENCES category_groups(id),
            is_active   BOOLEAN NOT NULL DEFAULT 1,
            sheet_name  TEXT,
            sheet_group TEXT,
            code        TEXT,
            is_hidden   BOOLEAN NOT NULL DEFAULT 0,
            is_retired  BOOLEAN NOT NULL DEFAULT 0
        );
CREATE TABLE "category_groups" (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            is_active  BOOLEAN NOT NULL DEFAULT 1,
            code       TEXT
        );
CREATE TABLE category_templates (
                    id              INTEGER PRIMARY KEY,
                    code            TEXT NOT NULL UNIQUE,
                    origin          TEXT NOT NULL CHECK (origin IN ('factory', 'custom')),
                    sort_order      INTEGER NOT NULL DEFAULT 0,
                    definition_json TEXT NOT NULL
                );
CREATE TABLE category_translations (
                    code TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    name TEXT NOT NULL,
                    PRIMARY KEY (code, lang)
                );
CREATE TABLE classification_rules (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id             INTEGER REFERENCES shop_chains(id),
    item_name_normalized TEXT NOT NULL,
    category_id          INTEGER NOT NULL REFERENCES categories(id),
    confidence_level     INTEGER NOT NULL,
    source               TEXT NOT NULL CHECK (source IN ('llm', 'user_correction')),
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
, alternative_category_ids TEXT, tag_ids TEXT NOT NULL DEFAULT '[]');
CREATE TABLE events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT 1,
    auto_tags           TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE exchange_rates (
    date             DATE NOT NULL,
    source_currency  TEXT NOT NULL,
    target_currency  TEXT NOT NULL,
    rate             DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (date, source_currency, target_currency)
);
CREATE TABLE expense_tags (
    expense_id INTEGER NOT NULL REFERENCES expenses(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (expense_id, tag_id)
);
CREATE TABLE expenses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
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
, receipt_id       INTEGER REFERENCES receipts(id), store_id         INTEGER REFERENCES stores(id), confidence_level INTEGER, rule_id          INTEGER REFERENCES classification_rules(id));
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
CREATE TABLE "income" (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    year             INTEGER NOT NULL,
    month            INTEGER NOT NULL,
    income_date      DATE NOT NULL,
    amount           DECIMAL(12,2) NOT NULL,
    amount_original  DECIMAL(12,2) NOT NULL,
    currency_original TEXT NOT NULL,
    comment          TEXT,
    CHECK (month BETWEEN 1 AND 12)
);
CREATE TABLE income_logging_jobs (
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    PRIMARY KEY (year, month),
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);
CREATE TABLE receipt_classification_jobs (
    receipt_id   INTEGER PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'pending',
    claim_token  TEXT,
    claimed_at   TIMESTAMP,
    last_error   TEXT, retry_count INTEGER NOT NULL DEFAULT 0, retry_after TIMESTAMP,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);
CREATE TABLE receipt_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id       INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    name_raw         TEXT NOT NULL,
    name_normalized  TEXT,
    unit_price       DECIMAL(12,4) NOT NULL DEFAULT 0,
    quantity         DECIMAL(12,4) NOT NULL DEFAULT 0,
    total_price      DECIMAL(12,2) NOT NULL DEFAULT 0,
    tax_label        TEXT NOT NULL DEFAULT '',
    expense_id       INTEGER REFERENCES expenses(id) ON DELETE SET NULL
);
CREATE TABLE receipts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    client_receipt_id     TEXT UNIQUE NOT NULL,
    url                   TEXT NOT NULL,
    store_id              INTEGER REFERENCES stores(id),
    store_name_raw        TEXT NOT NULL DEFAULT '',
    store_pib_raw         TEXT NOT NULL DEFAULT '',
    total_amount          DECIMAL(12,2) NOT NULL DEFAULT 0,
    invoice_number        TEXT NOT NULL DEFAULT '',
    purchase_datetime     TEXT,
    parsed_at             TIMESTAMP,
    used_journal_fallback BOOLEAN NOT NULL DEFAULT 0,
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sheet_logging_jobs (
    expense_id  INTEGER PRIMARY KEY REFERENCES expenses(id),
    status      TEXT NOT NULL DEFAULT 'pending',
    claim_token TEXT,
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);
CREATE TABLE sheet_mapping (
    row_order      INTEGER PRIMARY KEY,
    category_id    INTEGER REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL
);
CREATE TABLE sheet_mapping_tags (
    mapping_row_order INTEGER NOT NULL,
    tag_id            INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_row_order, tag_id)
);
CREATE TABLE shop_chains (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE stores (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    chain_id INTEGER REFERENCES shop_chains(id),
    pib      TEXT UNIQUE
);
CREATE TABLE tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX classification_rules_chain_item
    ON classification_rules (chain_id, item_name_normalized)
    WHERE chain_id IS NOT NULL;
CREATE UNIQUE INDEX classification_rules_null_item
    ON classification_rules (item_name_normalized)
    WHERE chain_id IS NULL;
CREATE INDEX idx_cr_chain_name
    ON classification_rules (chain_id, item_name_normalized);
CREATE INDEX ix_expenses_category_id ON expenses(category_id);
CREATE INDEX receipt_items_expense_id    ON receipt_items (expense_id);
CREATE INDEX receipt_items_name_norm     ON receipt_items (name_normalized);
CREATE INDEX receipt_items_receipt_id    ON receipt_items (receipt_id);
CREATE INDEX receipts_store_id           ON receipts (store_id);
CREATE UNIQUE INDEX stores_name_no_pib ON stores (name) WHERE pib IS NULL;
CREATE UNIQUE INDEX ux_categories_code ON categories(code);
CREATE UNIQUE INDEX ux_category_groups_code ON category_groups(code);

INSERT INTO app_metadata (key, value) VALUES ('catalog_version', '1');
