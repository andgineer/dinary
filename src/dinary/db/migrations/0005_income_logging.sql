CREATE TABLE income_logging_jobs (
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    PRIMARY KEY (year, month),
    FOREIGN KEY (year, month) REFERENCES income (year, month) ON DELETE CASCADE,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);
