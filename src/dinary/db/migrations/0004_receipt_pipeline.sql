-- Receipt pipeline tables: stores, receipts, receipt_items,
-- classification_rules, receipt_classification_jobs, llm_providers, llm_call_log.
-- Also adds receipt_id / store_id / confidence_level to expenses.

CREATE TABLE stores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_name TEXT NOT NULL UNIQUE,
    pib        TEXT UNIQUE
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

CREATE TABLE receipt_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id       INTEGER NOT NULL REFERENCES receipts(id),
    name_raw         TEXT NOT NULL,
    name_normalized  TEXT,
    unit_price       DECIMAL(12,4) NOT NULL DEFAULT 0,
    quantity         DECIMAL(12,4) NOT NULL DEFAULT 0,
    total_price      DECIMAL(12,2) NOT NULL DEFAULT 0,
    tax_label        TEXT NOT NULL DEFAULT '',
    category_id      INTEGER REFERENCES categories(id),
    confidence_level INTEGER,
    expense_id       INTEGER REFERENCES expenses(id)
);

-- Chain-specific rules: (store_id, item_name_normalized) must be unique when store_id IS NOT NULL.
-- Generic rules:         (item_name_normalized) must be unique when store_id IS NULL.
-- Two partial unique indexes enforce both constraints (SQLite UNIQUE treats NULLs as distinct).
CREATE TABLE classification_rules (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id             INTEGER REFERENCES stores(id),
    item_name_normalized TEXT NOT NULL,
    category_id          INTEGER NOT NULL REFERENCES categories(id),
    confidence_level     INTEGER NOT NULL,
    source               TEXT NOT NULL CHECK (source IN ('llm', 'user_correction')),
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX classification_rules_store_item
    ON classification_rules (store_id, item_name_normalized)
    WHERE store_id IS NOT NULL;

CREATE UNIQUE INDEX classification_rules_null_item
    ON classification_rules (item_name_normalized)
    WHERE store_id IS NULL;

CREATE TABLE receipt_classification_jobs (
    receipt_id   INTEGER PRIMARY KEY REFERENCES receipts(id),
    status       TEXT NOT NULL DEFAULT 'pending',
    claim_token  TEXT,
    claimed_at   TIMESTAMP,
    last_error   TEXT,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);

CREATE TABLE llm_providers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    label              TEXT NOT NULL,
    base_url           TEXT NOT NULL,
    api_key            TEXT NOT NULL,
    model              TEXT NOT NULL,
    priority           INTEGER NOT NULL DEFAULT 0,
    is_enabled         BOOLEAN NOT NULL DEFAULT 1,
    rate_limited_until TIMESTAMP,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE llm_call_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER REFERENCES llm_providers(id),
    receipt_id  INTEGER REFERENCES receipts(id),
    called_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status      TEXT NOT NULL,
    latency_ms  INTEGER
);

ALTER TABLE expenses ADD COLUMN receipt_id       INTEGER REFERENCES receipts(id);
ALTER TABLE expenses ADD COLUMN store_id         INTEGER REFERENCES stores(id);
ALTER TABLE expenses ADD COLUMN confidence_level INTEGER;

CREATE INDEX receipt_items_receipt_id    ON receipt_items (receipt_id);
CREATE INDEX receipt_items_expense_id    ON receipt_items (expense_id);
CREATE INDEX receipt_items_name_norm     ON receipt_items (name_normalized);
CREATE INDEX receipts_store_id           ON receipts (store_id);
CREATE INDEX llm_call_log_provider_id    ON llm_call_log (provider_id);
