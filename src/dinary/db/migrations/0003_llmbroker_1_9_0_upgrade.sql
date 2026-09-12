-- Schema side of the llmbroker 1.3.0 -> 1.9.0 upgrade: drop the broker tables the
-- new release cannot reuse, then rate a corrected classification rule by the call
-- that produced it.
--
-- llmbroker 1.9.0 carries store schema version 7 and migrates nothing: a file left
-- at version 5 raises SchemaVersionError on the first query. Three of its four
-- tables go; the reason differs for each.
--
-- llmbroker_registry, because a row written by 1.3.0 has no `from_preset` marker
-- in its metadata, which 1.9.0 reads as "the installation stated this itself" —
-- an entry no sync may ever remove. The pre-upgrade providers would outlive every
-- model-list merge and stay in the pool forever. They re-sync from the curated
-- list on the next startup.
--
-- llmbroker_calls, because its columns changed. Telemetry and the quality windows
-- derived from it start empty.
--
-- llmbroker_disabled, because a verdict is keyed by provider name and the curated
-- list shares no name with the list dinary used before: every stored row is dead
-- on arrival, nothing ever reaps one, and a later curated revision reusing a name
-- would silently re-attach an old verdict to a different model.
--
-- llmbroker_secrets is deliberately left alone. Its table definition is unchanged
-- and its api_key_ref names carry over, so API keys stored in the database
-- survive: .deploy/.env only ever seeded them, and a key rotated in the database
-- afterwards has no other copy.

DROP TABLE IF EXISTS llmbroker_registry;
DROP TABLE IF EXISTS llmbroker_calls;
DROP TABLE IF EXISTS llmbroker_disabled;

-- 1.3.0 kept its schema version in the file-global PRAGMA user_version; 1.9.0
-- reads it only to detect exactly this case and refuses anything but 0 or 7.
-- DROP TABLE does not clear a header value, so reset it here.
PRAGMA user_version = 0;

-- A delayed rating now names the call it rates, not the model that answered:
-- llmbroker resolves it against the journal, which no longer accepts a bare
-- model name. Existing rows keep no call id and are never rated.
ALTER TABLE classification_rules DROP COLUMN llm_name;
ALTER TABLE classification_rules ADD COLUMN llm_call_id TEXT;
