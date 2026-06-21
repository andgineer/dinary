# Plan: adapt to llmbroker's new broker interface

Date: 2026-06-21
Trigger: **only when the new llmbroker is released** (the version that drops the
broker dict interface and splits `add`/`update`). Until then, do nothing.

## Background

Upstream llmbroker removes the `Mapping` interface from `AsyncBroker` /
`Broker` and changes the admin surface:

- `broker[name]` / `for x in broker` / `len(broker)` — **removed**.
- `await broker.get(name) -> AsyncLLM` — lazy handle (`.config` free, `state()`/
  `metrics()` on demand), raises `KeyError` if absent.
- `await broker.count() -> int` — cheap provider count (no IO).
- `add(cfg)` is now **create-only**: raises `ValueError` if the name exists.
- new `update(cfg)`: modifies existing, raises `KeyError` if absent.
- `snapshot()` unchanged in signature (still `Mapping[str, LLMSnapshot]`), now
  also reflects cluster `shared_state`.

dinary touches the broker in exactly three places; the editor itself never used
the dict interface (it already goes through `snapshot()` / `add` / `remove`).

## Changes

### `pyproject.toml`

- Bump the pin `llmbroker==0.0.5` → the new released version. Re-lock (`uv lock`).

### `src/dinary/background/classification/task.py` (line ~474)

- `max_attempts = max(1, min(3, len(broker)))`
  → `max_attempts = max(1, min(3, await broker.count()))`
  (`_run_llm_pass` is already `async`).

### `src/dinary/api/controllers/llm.py`

1. **`add_provider`** — `add()` now rejects duplicates. Translate to HTTP 409:
   ```python
   try:
       await llms.add(cfg)
   except ValueError as exc:
       raise HTTPException(status_code=409, detail="Provider already exists") from exc
   ```

2. **`update_provider`** — two changes:
   - It currently pulls the full `snapshot()` only to read the old config. Switch
     to the lazy `get(name)` (no metrics/shared_state IO):
     ```python
     try:
         old = (await llms.get(name)).config
     except KeyError as exc:
         raise HTTPException(status_code=404, detail="Provider not found") from exc
     ```
   - Build `updated` from `old` as today, then call **`update`** instead of `add`:
     ```python
     await llms.update(updated)
     ```

3. **`delete_provider`** — no change. It needs all providers' `state.phase` for
   the "cannot delete the only enabled provider" guard, so it legitimately keeps
   `snapshot()`. `remove(name)` is unchanged upstream.

4. **`list_providers`, `llm_status`** — no change (full `snapshot()` is justified;
   they render per-provider runtime state + metrics).

## Tests

- `tests/api/test_admin_llm.py`:
  - Adding a provider whose name already exists must now expect **409** (today it
    silently overwrote). Add/adjust a case.
  - Update of an existing provider goes through the PATCH endpoint → `update()`;
    update of a missing name → 404.
- `tests/tasks/test_receipt_drain.py`, `tests/tasks/test_receipt_pipeline.py`,
  `tests/api/test_receipt_pipeline_e2e.py`: run against the new broker; if any
  fixture/double exposed `__len__`, give it `count()` instead. Most use real
  broker fixtures, so the `await broker.count()` swap should be transparent.

## Verification

- `uv lock && uv sync`
- run dinary's full check (pytest + lint/format/type per dinary's done gate).
