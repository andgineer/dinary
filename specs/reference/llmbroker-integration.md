# llmbroker integration

dinary uses `llmbroker` as an external PyPI dependency for all LLM access. One
`AsyncBroker` instance lives for the full FastAPI application lifetime.

## Wiring

`AsyncBroker` is constructed in the FastAPI lifespan with:

- `llmbroker.sqlite.Registry` on `storage.DB_PATH` — provider configs managed at
  runtime through the admin API
- `llmbroker.sqlite.Telemetry` on the same database — append-only call journal
- No `shared_state` — dinary is single-process; per-LLM live state stays in broker memory

The broker's `ensure_schema` owns all `llmbroker_*` tables. dinary never issues SQL
against them directly; all reads and writes go through the broker object.

## Initial provider seeding

On a fresh install the `llmbroker_registry` table is empty. The operator seeds it once:

```bash
# Fetch a curated provider list (requires Phase 2 of llmbroker — not yet released):
python -m llmbroker preset freetier > .deploy/llm_providers.toml

# Sync into the DB:
python -m llmbroker sync .deploy/llm_providers.toml \
    --into sqlite:.deploy/dinary.db --policy if_empty
```

The lifespan does **not** auto-seed — seeding is an explicit one-time operator step
so admin edits survive restarts. After the initial seed, the operator uses the admin
UI to add/remove providers; `llm_providers.toml` is not consulted at runtime.

Pulling an updated preset list without clobbering admin edits:

```bash
python -m llmbroker sync .deploy/llm_providers.toml \
    --into sqlite:.deploy/dinary.db --policy add
```

## Preset distribution

llmbroker ships curated provider lists in `presets/` at the root of its repository
(not bundled in the wheel). The `preset <name>` CLI command (Phase 2 of llmbroker)
fetches from the repository default branch:

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
