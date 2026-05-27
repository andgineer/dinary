-- Expand income table: add id PK (AUTOINCREMENT), income_date, comment,
-- currency_original, amount_original.  Drop the (year, month) PRIMARY KEY so
-- multiple records per calendar month are allowed.
--
-- Historical rows: income_date = 5th of the month, amount_original = amount,
-- currency_original = stored accounting currency from app_metadata.

CREATE TABLE income_new (
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

INSERT INTO income_new (year, month, income_date, amount, amount_original, currency_original)
SELECT
    year,
    month,
    year || '-' || printf('%02d', month) || '-05',
    amount,
    amount,
    (SELECT value FROM app_metadata WHERE key = 'accounting_currency')
FROM income;

DROP TABLE income;
ALTER TABLE income_new RENAME TO income;

-- income_logging_jobs is keyed by (year, month) for per-month sheet rows.
-- The FK to income is removed because income no longer has a unique year+month
-- constraint; orphaned jobs are cleaned up by the drain's "orphan" path.
CREATE TABLE income_logging_jobs (
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    PRIMARY KEY (year, month),
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);

-- Convert events.auto_tags from JSON name arrays to JSON id arrays.
-- Requires SQLite >= 3.38.0 (2022-02-22) for json_each and json_group_array.
-- Names with no matching tags row are dropped (same as the runtime drop path).
-- WARNING: rollback after this migration is not safe — the old app code would
-- query WHERE name IN ([3]) and silently drop every auto-tag.
UPDATE events
SET auto_tags = (
    SELECT json_group_array(DISTINCT t.id)
    FROM json_each(events.auto_tags) AS je
    JOIN tags t ON t.name = je.value
)
WHERE auto_tags IS NOT NULL
  AND auto_tags != ''
  AND auto_tags != '[]';

-- Rebuild tags with AUTOINCREMENT so deleted IDs are never reused.
-- SQLite 3.26+ enforces FK constraints on DROP TABLE when child rows exist, so
-- we stage all three child tables in TEMP tables, clear them, drop-and-rebuild
-- tags, then restore.  All steps are inside yoyo's surrounding transaction, so
-- any failure rolls back everything including the TEMP table writes.
CREATE TEMP TABLE _tmp_tags AS SELECT * FROM tags;
CREATE TEMP TABLE _tmp_expense_tags AS SELECT * FROM expense_tags;
CREATE TEMP TABLE _tmp_sheet_mapping_tags AS SELECT * FROM sheet_mapping_tags;
CREATE TEMP TABLE _tmp_import_mapping_tags AS SELECT * FROM import_mapping_tags;

DELETE FROM import_mapping_tags;
DELETE FROM sheet_mapping_tags;
DELETE FROM expense_tags;

DROP TABLE tags;

CREATE TABLE tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1
);
INSERT INTO tags SELECT * FROM _tmp_tags;

INSERT INTO expense_tags SELECT * FROM _tmp_expense_tags;
INSERT INTO sheet_mapping_tags SELECT * FROM _tmp_sheet_mapping_tags;
INSERT INTO import_mapping_tags SELECT * FROM _tmp_import_mapping_tags;

-- Rebuild events with AUTOINCREMENT.  Child tables (expenses, sheet_mapping,
-- import_mapping) reference events via nullable event_id; NULL them out, drop
-- and rebuild events, then restore the original values.
CREATE TEMP TABLE _tmp_events AS SELECT * FROM events;
CREATE TEMP TABLE _tmp_import_event AS SELECT id, event_id FROM import_mapping WHERE event_id IS NOT NULL;
CREATE TEMP TABLE _tmp_sheet_event AS SELECT row_order, event_id FROM sheet_mapping WHERE event_id IS NOT NULL;
CREATE TEMP TABLE _tmp_expense_event AS SELECT id, event_id FROM expenses WHERE event_id IS NOT NULL;

UPDATE import_mapping SET event_id = NULL WHERE event_id IS NOT NULL;
UPDATE sheet_mapping SET event_id = NULL WHERE event_id IS NOT NULL;
UPDATE expenses SET event_id = NULL WHERE event_id IS NOT NULL;

DROP TABLE events;

CREATE TABLE events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT 1,
    auto_tags           TEXT NOT NULL DEFAULT '[]'
);
INSERT INTO events SELECT * FROM _tmp_events;

UPDATE import_mapping SET event_id = (
    SELECT event_id FROM _tmp_import_event WHERE _tmp_import_event.id = import_mapping.id
) WHERE id IN (SELECT id FROM _tmp_import_event);
UPDATE sheet_mapping SET event_id = (
    SELECT event_id FROM _tmp_sheet_event WHERE _tmp_sheet_event.row_order = sheet_mapping.row_order
) WHERE row_order IN (SELECT row_order FROM _tmp_sheet_event);
UPDATE expenses SET event_id = (
    SELECT event_id FROM _tmp_expense_event WHERE _tmp_expense_event.id = expenses.id
) WHERE id IN (SELECT id FROM _tmp_expense_event);

-- Add retry backoff columns to receipt_classification_jobs.
ALTER TABLE receipt_classification_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE receipt_classification_jobs ADD COLUMN retry_after TIMESTAMP;
