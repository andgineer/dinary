# llmbroker integration

dinary uses `llmbroker` as an external PyPI dependency for all LLM access. One
`AsyncBroker` instance lives for the full FastAPI application lifetime.

## Broker design

`LLMBroker` knows nothing about receipts or categories. It accepts OpenAI-style
messages and returns a string. This isolation is intentional: the broker is
designed to be extractable as a standalone package. All receipt and category
business logic lives in the classification layer above it. See
[classification-pipeline.md](classification-pipeline.md) for how the pipeline
uses the broker.

The broker depends on a Protocol for storage, not a concrete DB module. This
keeps the broker's hot path (provider selection, HTTP call, rate-limit tracking)
free of any SQLite dependency.

Rate-limit state is tracked in memory for immediate response and persisted to DB
for survival across restarts. The in-memory path avoids a DB write on every
provider call; the persistence path ensures a restarted process doesn't hammer a
provider that was already cooling down.

## Storage implementations

The server process uses SQLite as the backing store for provider configuration,
call telemetry, and API key secrets, all managed by llmbroker. Providers absent
from the registry are added from `.deploy/llms.toml` on each startup; existing
registry entries are never overwritten.

The CLI path (for example, `inv classify-receipt`) reads providers directly from
TOML, logs calls via Python's logging module, and writes nothing to a database.
This path is appropriate for tasks where no long-lived database process is running.

## Wiring

`AsyncBroker` is constructed in the FastAPI lifespan with:

- `llmbroker.sqlite.Registry` on `storage.DB_PATH` — provider configs managed at
  runtime through the admin API
- `llmbroker.sqlite.Telemetry` on the same database — append-only call journal
- `llmbroker.sqlite.Secrets` on the same database — persists API keys resolved from env vars
- No `shared_state` — dinary is single-process; per-LLM live state stays in broker memory

The broker's `ensure_schema` owns all `llmbroker_*` tables. dinary never issues SQL
against them directly; all reads and writes go through the broker object.

## Initial provider seeding

The broker is constructed with `seed=llmbroker.Registry(".deploy/llms.toml")` and
`seed_policy=llmbroker.SeedPolicy.ADD`. `ensure_pool()` is called eagerly in the lifespan.
On first startup this adds all providers from the TOML file; on restart it adds only
providers absent from the registry (existing entries are untouched).

`.deploy/llms.toml` uses `[[llms]]` entries with `name`, `base_url`, `model`, and
`api_key_ref`. When seeding, for each provider whose `api_key_ref` is not yet in
the sqlite secrets store, the broker reads the value from env vars (set in
`.deploy/.env`) and persists it via `llmbroker.sqlite.Secrets`. Existing sqlite
secrets are never overwritten — the sqlite value wins. Once stored, env vars are
not consulted again at runtime; the broker resolves secrets only from its configured
`secrets=` backend.

`SeedPolicy.ADD` on restart with providers already in the registry still attempts to
fill any missing secrets from env vars, so a secret that was absent on the first boot
(env var added later) is picked up on the next restart.

## Provider config file

`.deploy/llms.toml` is the local operator config (gitignored). `.deploy.example/llms.toml`
is the committed template. The file is synced to the server on each `inv deploy`.

## Preset distribution

llmbroker ships curated provider lists in `presets/` at the root of its repository
(not bundled in the wheel). The `preset <name>` CLI command fetches from the
repository default branch:

```
https://raw.githubusercontent.com/andgineer/llmbroker/main/presets/<name>.toml
```

A preset update is a plain commit to the llmbroker repo — independent of any
package version. dinary does not keep a local copy of preset files.

## Version pinning

dinary pins `llmbroker` with an exact version (`==`). llmbroker is evolving
actively; breaking API changes between minor versions are expected. Bump
deliberately: update the pin, run the full test suite, commit.

## Admin API

All admin reads and writes go through the `AsyncBroker` instance on `app.state.llms`:

- Provider list and live state: `await llms.snapshot()`
- Add / remove a provider: `llms.add(...)` / `llms.remove(...)`
- Call history: `await llms.calls(limit=...)` (requires `sqlite.Telemetry`)

## DB schema ownership

`llmbroker_*` tables are created and evolved by `ensure_schema`, not by dinary
migrations. Migration `0007` drops the legacy pre-extraction `llmbroker_providers`
and `llmbroker_call_log` tables, transferring schema ownership to the package. No
dinary migration ever creates or alters `llmbroker_*` tables after that point.
