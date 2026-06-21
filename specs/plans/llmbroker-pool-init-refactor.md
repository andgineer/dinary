# llmbroker — pool init refactor + duplicate-warning fix

## Problem

`sync_configs` emits the "api_key_ref could not be resolved" warning **twice** for
the same provider when a secret is genuinely missing (absent from both the sqlite
secrets table and the environment).

Root cause is ordering inside `sync_configs`:

```python
await self.ensure_started()   # builds pool, resolves secrets from an empty sqlite table → WARN #1
...
await self._seed_secrets(...) # only NOW writes env values into sqlite
await self._reconcile_pool()  # resolves again → WARN #2 when still missing
```

`ensure_started` resolves secrets **before** `_seed_secrets` has populated them. On a
fresh DB this is harmless (registry empty); on restart with persisted configs it
double-warns.

Secondary issues surfaced while diagnosing:
- `_start_lock` / `_started` names do not convey "the in-memory pool has been
  populated from the registry".
- `ensure_started` and `_reconcile_pool` duplicate the registry→pool build.
- `_reconcile_pool` calls `_require_mutable_registry()` although loading needs no
  mutability (breaks the read-only TOML-registry path conceptually).
- `sync_configs` name is misleading: it also seeds secrets and builds the pool.

## Design (agreed)

Pool build becomes a single lock-guarded method shared by the lazy path and `sync`.
Seeding happens before the (only) pool build, so resolution sees the secrets.

### Renames

| Old | New |
|---|---|
| `self._started` | `self._pool_initialized` |
| `self._start_lock` | `self._pool_lock` |
| `ensure_started` | `init_pool` (idempotent lazy initializer) |
| `_reconcile_pool` | `_refresh_pool` (lock-guarded; replaces both old bodies) |
| `sync_configs` | `sync` |

### Target shape (broker.py)

```python
def __init__(self, ...):
    ...
    self._pool_initialized = False     # was _started
    self._pool_lock = asyncio.Lock()   # was _start_lock

async def _refresh_pool(self) -> None:
    """Reconcile the in-memory pool with the registry: add new configs,
    drop removed ones, refill the availability queue. Lock-guarded."""
    async with self._pool_lock:
        configs = await self._registry.load()      # no _require_mutable_registry
        names = {c.name for c in configs}
        for name in list(self._configs):
            if name not in names:
                self._configs.pop(name, None)
                self._resolved_keys.pop(name, None)
        for cfg in configs:
            await self._add_to_pool(cfg)
        self._pool_initialized = True

async def init_pool(self) -> None:        # lazy, idempotent; called by chat/snapshot/add/remove
    if self._pool_initialized:
        return
    await self._refresh_pool()

async def sync(self, source, *, policy="mirror") -> None:
    registry = self._require_mutable_registry()
    source_configs = await source.load()
    existing = {c.name: c for c in await registry.load()}
    ... mutations per policy (unchanged) ...
    await self._seed_secrets(source_configs)
    await self._refresh_pool()            # builds pool post-seed AND sets _pool_initialized
```

`_add_to_pool` is unchanged — no `had_key` dedup needed, because `sync` no longer
resolves before seeding and `_pool_initialized` makes the first later `init_pool`
a no-op.

### Why the readiness check stays only in `init_pool`

`_refresh_pool` must always reconcile when called (registry may have changed in
`sync`). The `if self._pool_initialized: return` guard therefore lives in `init_pool`
(pre-lock fast path), not inside `_refresh_pool`. Two concurrent lazy `init_pool`
calls can both pass the guard and both run `_refresh_pool`; the second reconcile is
harmless (`is_new=False`, nothing re-queued, secret re-resolves silently).

### Lock placement

The lock moves into `_refresh_pool`, serializing both entry paths (`init_pool` and
`sync`). The previous double-checked-locking idiom in `ensure_started` collapses to
the single guard in `init_pool`.

## Implementation — llmbroker repo

File: `llmbroker/broker.py`

1. `__init__`: rename `_started`→`_pool_initialized`, `_start_lock`→`_pool_lock`.
2. Replace `ensure_started` body with the idempotent guard calling `_refresh_pool`;
   rename to `init_pool`.
3. Replace `_reconcile_pool` with `_refresh_pool` (lock-guarded, no
   `_require_mutable_registry`, sets `_pool_initialized`).
4. `sync_configs` → `sync`: drop the leading `ensure_started()` call; keep mutations
   + `_seed_secrets`; end with `await self._refresh_pool()`.
5. Update internal callers of `ensure_started` → `init_pool`: `chat`, `snapshot`,
   `add`, `remove` (current lines ~257, ~426, ~454, ~460).
6. Bump package version.

### Tests — llmbroker repo

Warning-count invariants (env var = `_KEY_REF`, sqlite registry + sqlite secrets):

- Fresh DB + env set → `sync` → **0** warnings.
- Fresh DB + env absent → `sync` → **1** warning.
- Restart (provider persisted, secret persisted) → `sync` → **0** warnings.
- Restart, secret absent everywhere (env + sqlite) → `sync` → **exactly 1** warning
  (the regression this refactor fixes).
- Restart, env set, sqlite secret missing → `sync` → **0** warnings (seed-then-build).
- After `sync`, a subsequent `chat()`/`init_pool` does **not** add a warning
  (`_pool_initialized` is set).

## Follow-up — dinary repo (after llmbroker release)

1. `pyproject.toml`: bump `llmbroker==0.0.4` → new version.
2. `src/dinary/main.py`: `await llms.sync_configs(...)` → `await llms.sync(...)`.
3. `tests/conftest.py`: `_REAL_BROKER_SYNC_CONFIGS` + `_disable_llm_broker_sync` +
   `real_broker_sync` patch `sync` instead of `sync_configs`.
4. `tests/llmbroker/test_sync_configs.py`: calls `sync`; add the
   "genuinely missing on restart → 1 warning" case.
5. `specs/reference/llmbroker-integration.md`: replace `sync_configs` references
   with `sync`.
6. Gate: `uv run inv pre` (0 errors) + `uv run pytest` (all pass).
