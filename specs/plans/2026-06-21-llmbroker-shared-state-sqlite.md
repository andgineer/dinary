# Plan: wire llmbroker state_store (sqlite) into the app broker

Date: 2026-06-21
Trigger: **only when llmbroker ships a sqlite `StateStore` battery.** Today
`state_store` is protocol-only upstream (no backend yet). Until the
`llmbroker.sqlite.StateStore` class exists and is released, do nothing.

> Upstream renamed this port `shared_state` → `state_store` (the name is no
> longer cluster-only). Class `SharedState` → `StateStore`. This plan uses the
> new names.

## Background

dinary runs a single app-lifetime broker in `src/dinary/main.py` `_lifespan`:

```python
llms = llmbroker.AsyncBroker(
    registry=llmbroker.sqlite.Registry(storage.DB_PATH),
    telemetry=llmbroker.sqlite.Telemetry(storage.DB_PATH),
    secrets=llmbroker.sqlite.Secrets(storage.DB_PATH),
    seed=llmbroker.Registry(_PROJECT_ROOT / ".deploy" / "llms.toml"),
    seed_policy=llmbroker.SeedPolicy.ADD,
)
```

There is **no `state_store`**, so cooldown / health state lives only in the
broker's in-process `InMemoryState`. Consequences:

- on restart (deploy, crash) all cooldowns are lost — a freshly started process
  hammers a provider that was rate-limited a second earlier;
- if dinary ever runs more than one worker process, each has its own blind copy
  of the state.

This is exactly the "state must survive **between requests**, not just between
cluster nodes" point. dinary's load is low and it already has a sqlite DB, so a
sqlite-backed `state_store` is the right minimal fix — no Redis.

## Scope

- **Single-tenant only. Multi-user is NOT needed in dinary** — do not pass
  `user_id` anywhere. The upstream per-user `user_id` feature is irrelevant
  here; the sqlite `StateStore` is used in its default (unscoped) mode.

## Changes

### `src/dinary/main.py` — `_lifespan`

Add one argument to the existing `AsyncBroker(...)` call:

```python
    state_store=llmbroker.sqlite.StateStore(storage.DB_PATH),
```

Same DB file as the other batteries, no new infrastructure. Effect:

- cooldowns persist across restarts and are visible to every worker;
- `snapshot()` already merges `state_store` (once the upstream broker-list
  interface lands — see `2026-06-21-llmbroker-broker-interface.md`), so the
  provider admin views reflect the shared cooldown state automatically.

### Schema

No dinary migration. Per `0007_drop_legacy_llmbroker`, `llmbroker.ensure_schema`
owns all `llmbroker_*` tables; the `StateStore` table ships in llmbroker's own
schema version bump and is created when the sqlite battery initializes. Just
confirm `ensure_schema` runs (it does, via the sqlite batteries on first use).

### Out of scope

- The analytics sync broker (`src/dinary_analytics/llm.py`) uses a **file**
  registry and no sqlite DB — it has no persistent store to share and stays as
  is.
- The `tasks/receipt.py` operator CLI is ephemeral (file registry) — unchanged.

## Tests

- One test over a temp DB: write a cooldown through one `AsyncBroker`, construct
  a second `AsyncBroker` on the same `DB_PATH`, and assert the second sees the
  cooldown via `snapshot()` (simulates restart / second worker).

## Done gate

- dinary's standard gate: full test suite green + lint/type-check clean.
