# llmbroker upgrade: preset mirror, delayed quality rating, read-only LLM screen

**Source of truth: https://github.com/andgineer/dinary/issues/24** — the deliverable is the
functionality described there. This plan is the suggested route; where the code has drifted from
what the plan assumes, the issue wins.

## Prerequisite

llmbroker must have shipped delayed quality ratings (https://github.com/andgineer/llmbroker/issues/9):
`AsyncBroker.record_quality(llm_name, operation, score, call_id=None)` and public
`llm_name` / `operation` / `call_id` on results. Update the pin in `pyproject.toml` from
`llmbroker==0.0.11` to that release and run `uv lock`. Nothing below compiles against 0.0.11 —
the broker constructor, snapshot shape, and exceptions all changed.

## Step 1 — Broker wiring in `src/dinary/main.py::_lifespan`

Replace the current construction (its kwargs `telemetry=`, `state_store=`, `seed=`,
`seed_policy=` and the classes `llmbroker.sqlite.Telemetry` / `StateStore` no longer exist) with
the one-line sqlite source plus an explicit preset mirror:

```python
opt = llmbroker.Optimizer()
llms = llmbroker.AsyncBroker(f"sqlite://{storage.DB_PATH}", optimize=opt)
_app.state.llms = llms
_app.state.llm_optimizer = opt
await llms.sync(_PROJECT_ROOT / ".deploy" / "llms.toml")
await llms.ensure_pool()
```

Ordering and rationale:

- Keep this right after `storage.init_db()` and `load_dotenv(...)`. `sync()` is a **total
  mirror** (add/update/delete) — the startup analogue of `db_migrations.migrate_db()` for the
  provider registry, which is exactly what issue #24 asks for. `load_dotenv` must run first:
  `sync()` seeds any secret that is not yet resolvable from the env vars into the DB-backed
  secrets store, so keys from `.deploy/.env` start resolving with no extra code. (Known
  behavior, do not fight it: an already-stored secret is preserved, so rotating a key requires
  updating the DB secret or clearing the row — not a goal of this change.)
- The explicit `Optimizer` instance is kept on `app.state` because the status endpoint reads
  `opt.wilson_bound(name, operation)` for the numeric quality indicator (Step 4). Passing
  `optimize=opt` gives the broker that same instance.
- llmbroker owns its `llmbroker_`-prefixed tables in the same DB file and migrates them itself —
  no dinary migration needed for them. Telemetry from 0.0.11 is not carried over; quality
  windows start empty.
- Deployment note (README / `.deploy.example/llms.toml` comment): providers previously added via
  the admin UI disappear unless present in `llms.toml`; regenerate the file before rolling out
  (`llmbroker preset freetier > .deploy/llms.toml`). Update the stale comment in
  `.deploy.example/llms.toml` that says the live deployment is managed via the admin UI.

## Step 2 — Rules remember the model that created them

- New migration in `src/dinary/db/migrations/`: `ALTER TABLE classification_rules ADD COLUMN
  llm_name TEXT` (nullable; existing rows stay NULL and are simply never rated).
- `RuleSpec` (`src/dinary/db/classification_rules.py`): add `llm_name: str | None = None`;
  `create_or_update_rule` writes it on INSERT and UPDATE.
- Thread the value: `classify_receipt` already returns the `AsyncResult` in
  `ClassifyOutcome.execution`; in `src/dinary/background/classification/task.py` pass
  `outcome.execution.llm_name` down to `persist_classification_results`, which forwards it into
  `_write_single_item` → the `RuleSpec(..., source="llm", llm_name=...)` it builds
  (`src/dinary/background/classification/persist.py`). Rules created from user corrections keep
  `llm_name=NULL`.

## Step 3 — Quality signals

- **Immediate positive**: in `_run_llm_pass` (`task.py`), when the outcome is accepted
  (`not outcome.execution_failed` and results are used), call
  `await outcome.execution.record_quality(1.0)` — same guard-and-log pattern as the existing
  `record_quality(0.0)` on parse failure, which stays as is. This gives demoted models a path
  back up; today only zeros are ever recorded.
- **Delayed negative on user review**: in `correct_category_sync`
  (`src/dinary/api/controllers/expense_corrections.py`), before the rule upsert, read the
  existing rule for `(chain_id, item_name_normalized)`. If it has `source='llm'` and a non-NULL
  `llm_name`, decide the score: `0.5` when the corrected-to `category_id` is in the rule's
  `alternative_category_ids`, else `0.0`. Collect `(llm_name, score)` decisions inside the
  transaction; after the transaction commits, record them via
  `broker.record_quality(llm_name, "receipt_classification", score)`.
  `correct_category_sync` is sync and has no broker; the async endpoint that calls it does
  (`request.app.state.llms`). Suggested shape: `correct_category_sync` returns the pending
  ratings (or accepts a callback list), and the async controller awaits `record_quality` after.
  Dedup is structural: the upsert flips the rule to `source='user_correction'`, so a second
  correction of the same rule finds no `source='llm'` row and records nothing — matching the
  issue's "no repeated ratings" requirement. Rating failures must not fail the correction
  (log and continue).
- The operation string is the one already used by `classify_receipt`:
  `"receipt_classification"`.

## Step 4 — API: read-only status + disable

`src/dinary/api/llm.py` and `src/dinary/api/controllers/llm.py`:

- **Delete** `POST/PATCH/DELETE /api/llm/providers` routes and `add_provider` /
  `update_provider` / `delete_provider` (+ `ProviderIn` / `ProviderPatch`). The broker methods
  they call (`add`/`update`/`remove`) no longer exist.
- **Rewrite `llm_status`** for the new `LLMSnapshot` (raw facts; the old `snap.state.phase` /
  `snap.state.fail_count` are gone). Per provider expose: `name`, `model`, `base_url`,
  `disabled`, `has_key`, `cooldown_until`, derived `status` (precedence: `disabled` →
  `no_key` (has_key false) → `cooling` (cooldown_until in the future) → `available`), usage from
  `snap.metrics` (`call_count`, `last_status`, `last_at`), and quality:
  `demoted: "receipt_classification" in snap.demoted_operations` plus
  `quality_bound: app.state.llm_optimizer.wilson_bound(name, "receipt_classification")`
  (float or null). For `has_key=False` providers include the onboarding hint: load
  `key_info()` from a `llmbroker.Registry(.deploy/llms.toml)` and attach `help` for the
  provider's `api_key_ref`. Recompute the `health` summary from the derived statuses.
- **New endpoints**: `POST /api/llm/providers/{name}/disable` and `.../enable` calling
  `broker.disable_llm(name)` / `broker.enable_llm(name)`; 404 when the name is not in
  `snapshot()`. The latch persists across restarts and preset reloads (llmbroker stores it),
  and `snapshot()` reflects it as `disabled` — nothing to store on the dinary side.

## Step 5 — Web UI (`webapp/`)

- `views/LLMView.vue`: read-only dashboard — per-provider card with status badge (available /
  cooling / no key / disabled), usage counters, quality (demoted flag + numeric bound when
  present), a disable/enable toggle wired to the new endpoints, and the key-onboarding hint for
  `no_key` providers.
- Delete `components/ProviderSheet.vue` (the add/edit sheet) and every reference to it; prune
  `stores/llm.js` and `api/adminLlm.js` down to status + disable/enable calls.
- Update frontend tests (`cd webapp && npm test`) for the removed editing flows and the new
  status fields.

## Step 6 — Analytics chat (`src/dinary_analytics/llm.py`)

`llmbroker.AllLLMsFailedError` no longer exists (exceptions are `LLMRequestError` and its
subclass `NoLLMAvailableError`). Keep the `NoLLMAvailableError` branch, replace the
`AllLLMsFailedError` branch with `LLMRequestError`. The `Broker(registry=llmbroker.Registry(...))`
construction and `run_tool_loop` are still current. Update `tests/analytics/test_llm.py`
accordingly.

## Step 7 — Specs and docs

- Rewrite `specs/reference/llmbroker-integration.md` to the current state: preset file is the
  single source of the provider list, mirrored on startup; admin UI is read-only plus a
  persistent user disable; user corrections feed model quality back (delayed), accepted replies
  count positive. No signatures or field names (spec rules in CLAUDE.md).
- Check `specs/reference/architecture.md` and `specs/ui/` for mentions of provider editing; fix
  where stale.

## Step 8 — Tests and done gate

Python tests to add/adjust (backend): migration applies and `create_or_update_rule` round-trips
`llm_name`; accepted classification records a positive rating and parse failure still records
zero (fake broker/result, pattern of existing classification tests); correction of an
llm-sourced rule records `0.0`/`0.5` by the alternatives rule, second correction records
nothing, correction of a user-sourced or NULL-`llm_name` rule records nothing; status endpoint
shape incl. `disabled`/`no_key`/`cooling`/`available` derivation; disable/enable endpoints flip
routing and survive a broker rebuild. Update `test_webapp_api_contract.py` for the removed
provider-CRUD endpoints.

Done gate (CLAUDE.md): `uv run inv pre` clean, `uv run pytest` all green, `cd webapp && npm test`
green. Run `inv pre` after each discrete batch.

## Non-goals

- Any provider editing path besides `.deploy/llms.toml`.
- Key rotation UX for already-seeded secrets.
- Rating policy beyond issue #24 (no LLM-as-judge — that is llmbroker's issue #8 — and no decay).
