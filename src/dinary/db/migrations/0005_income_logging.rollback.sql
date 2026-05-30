ALTER TABLE llmbroker_providers DROP COLUMN execution_fail_count;
ALTER TABLE llmbroker_providers ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llmbroker_call_log ADD COLUMN provider_id INTEGER;
UPDATE llmbroker_call_log
   SET provider_id = (
       SELECT id FROM llmbroker_providers WHERE label = llmbroker_call_log.provider_label
   );
ALTER TABLE llmbroker_call_log DROP COLUMN provider_label;
ALTER TABLE llmbroker_call_log RENAME COLUMN execution_id TO context_id;
CREATE INDEX IF NOT EXISTS llmbroker_call_log_provider_id ON llmbroker_call_log (provider_id);

ALTER TABLE receipt_classification_jobs DROP COLUMN retry_after;
ALTER TABLE receipt_classification_jobs DROP COLUMN retry_count;

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
