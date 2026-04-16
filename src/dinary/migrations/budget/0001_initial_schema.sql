CREATE TABLE expenses (
    id             TEXT PRIMARY KEY,
    datetime       TIMESTAMP NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    amount         DECIMAL(10,2) NOT NULL,
    currency       TEXT DEFAULT 'RSD',
    category_id    INTEGER NOT NULL,
    beneficiary_id INTEGER,
    event_id       INTEGER,
    comment        TEXT,
    source         TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE expense_tags (
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (expense_id, tag_id)
);

CREATE TABLE sheet_sync_jobs (
    year  INTEGER,
    month INTEGER,
    PRIMARY KEY (year, month)
);
