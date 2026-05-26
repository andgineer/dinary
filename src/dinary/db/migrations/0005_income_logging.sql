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
