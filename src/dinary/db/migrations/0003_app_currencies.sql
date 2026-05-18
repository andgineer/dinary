-- Currencies the operator has saved for the PWA picker. Stores
-- ISO-4217 codes uppercased; rates live in ``exchange_rates`` and
-- are joined at lookup time when the server needs them.
--
-- The default ``app_currency`` (env-seeded) is inserted on first
-- boot when this table is empty so the PWA picker is never empty.
-- Operators cannot delete the default code; the API enforces that.

CREATE TABLE app_currencies (
    code     TEXT PRIMARY KEY,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
