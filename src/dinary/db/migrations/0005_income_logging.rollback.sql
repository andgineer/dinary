DROP TABLE IF EXISTS income_logging_jobs;

CREATE TABLE income_old (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);

INSERT INTO income_old (year, month, amount)
SELECT year, month, SUM(amount)
FROM income
GROUP BY year, month;

DROP TABLE income;
ALTER TABLE income_old RENAME TO income;

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
