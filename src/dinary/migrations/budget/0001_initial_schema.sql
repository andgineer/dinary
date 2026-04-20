-- category_id, event_id: cross-DB references into config.duckdb. Not enforced by DuckDB;
-- application code + verify-sheet-equivalence enforce integrity.
CREATE TABLE expenses (
    id                TEXT PRIMARY KEY,
    datetime          TIMESTAMP NOT NULL,
    amount            DECIMAL(10,2) NOT NULL,
    amount_original   DECIMAL(10,2) NOT NULL,
    currency_original TEXT NOT NULL DEFAULT 'RSD',
    category_id       INTEGER NOT NULL,
    event_id          INTEGER,
    comment           TEXT,
    sheet_category    TEXT,
    sheet_group       TEXT
);

-- tag_id: cross-DB reference into config.duckdb.tags; enforced by application code.
CREATE TABLE expense_tags (
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (expense_id, tag_id)
);

-- sheet_sync_jobs: durable queue for "this expense still needs to be appended to Google Sheets".
-- Renamed to sheet_logging_jobs in migration 0002 (see 0002_rename_sheet_sync_jobs.sql).
-- This file keeps the original name so historical migration replays stay deterministic.
-- Producer: POST /api/expenses (inserts the queue row in the same DuckDB transaction as expenses).
-- Consumers (both run the same _drain_one_job code path):
--   1) async worker started by asyncio.create_task right before the API returns -- opportunistic fast path;
--   2) lifespan-managed periodic `drain_pending` task -- retries anything the async worker did not finish (process crash, network, etc.).
-- A row is deleted as soon as its single-row append succeeds; no full-month rebuild exists.
-- `claimed_at` implements lease-style crash recovery: a later worker may reclaim an
-- `in_progress` row once the claim is older than the configured timeout.
-- import-budget does NOT populate this table: historical rows live in Sheets already.
CREATE TABLE sheet_sync_jobs (
    expense_id TEXT PRIMARY KEY REFERENCES expenses(id),
    status     TEXT NOT NULL DEFAULT 'pending',
    claim_token TEXT,
    claimed_at TIMESTAMP,
    CHECK (status IN ('pending', 'in_progress'))
);

-- income: unlike expenses, `year` is kept as an explicit column so cross-year analytics
-- queries over ATTACHed budget_YYYY.duckdb files stay uniform. Intentional local exception
-- to the "year is implicit in file name" convention.
CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);
