-- Return the database to the state a pre-1.9.0 llmbroker can open.
--
-- 1.9.0 leaves its tables at store schema version 7, which 1.3.0 refuses. It tracks
-- that version in its own marker table, so the marker has to go along with the
-- tables 0003 dropped and llmbroker rebuilt. Stored API keys stay, as they did on
-- the way up.

DROP TABLE IF EXISTS llmbroker_registry;
DROP TABLE IF EXISTS llmbroker_calls;
DROP TABLE IF EXISTS llmbroker_disabled;
DROP TABLE IF EXISTS llmbroker_schema_version;

-- Defensive rather than required: 1.9.0 writes the header only to clear a legacy
-- value it found, so on this path it is already 0.
PRAGMA user_version = 0;

-- Back to rating by model name. A recorded call id means nothing to the older
-- broker's journal, so the column is dropped rather than carried over.
ALTER TABLE classification_rules DROP COLUMN llm_call_id;
ALTER TABLE classification_rules ADD COLUMN llm_name TEXT;
