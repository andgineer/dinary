-- One-time cleanup of the legacy llmbroker 0.0.11 schema.
--
-- There is a single dinary installation and no llmbroker data survives the
-- upgrade to llmbroker 1.3.0. Dropping the tables here — before the broker is
-- constructed in the app lifespan — lets the new llmbroker recreate its own
-- (llmbroker_-prefixed) schema from scratch on first use: the provider list
-- rebuilds from .deploy/llms.toml, API keys re-seed from .deploy/.env,
-- telemetry and quality windows start empty.
--
-- Going forward llmbroker owns and migrates its own tables; this is a
-- one-time legacy cleanup, not a pattern.

DROP TABLE IF EXISTS llmbroker_registry;
DROP TABLE IF EXISTS llmbroker_calls;
DROP TABLE IF EXISTS llmbroker_secrets;
DROP TABLE IF EXISTS llmbroker_state;
