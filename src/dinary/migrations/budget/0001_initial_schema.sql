CREATE TABLE expenses (
    id                TEXT PRIMARY KEY,
    datetime          TIMESTAMP NOT NULL,
    name              TEXT NOT NULL DEFAULT '',
    amount            DECIMAL(10,2) NOT NULL,
    amount_original   DECIMAL(10,2) NOT NULL,
    currency_original TEXT NOT NULL DEFAULT 'RSD',
    category_id       INTEGER NOT NULL,
    beneficiary_id    INTEGER,
    event_id          INTEGER,
    sphere_of_life_id INTEGER,
    comment           TEXT,
    source            TEXT NOT NULL DEFAULT 'manual',
    source_type       TEXT NOT NULL DEFAULT '',
    source_envelope   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE sheet_sync_jobs (
    year  INTEGER,
    month INTEGER,
    PRIMARY KEY (year, month)
);
