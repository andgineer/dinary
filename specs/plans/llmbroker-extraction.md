# Extract `llmbroker` into its own top-level package

## Goal

Move `LLMBroker` and everything it directly depends on into a new package
`src/llmbroker/`, sibling to `src/dinary` and `src/dinary_analytics`, with
**zero imports of `dinary.*`**. This is a structural move only — no behavior
change, no new features. The "Selection Strategy / Throttling / Auto-Recovery
/ Fail statistics / Free providers DB" requirements from the brief are the
**end goal for a future standalone PyPI package** and are captured as a
roadmap doc, not implemented now.

## Why this is low-risk

`llmbroker.py` and `llm_chat.py` already have **no `dinary.db` imports** —
the existing docstring says they were written "intentionally isolated for
future extraction as a standalone package." Only `llm_storage.py` mixes a
generic TOML-backed storage with a dinary-DB-coupled SQLite storage; that's
the only file that needs splitting.

`src/` is already on `sys.path` via the editable install (verified:
`uv run python -c "import dinary_analytics"` works with no extra
`pyproject.toml` config). So `src/llmbroker/` becomes importable as
`import llmbroker` with **no pyproject/build changes** in this phase.

---

## Target layout

```
src/llmbroker/
  __init__.py        # public API re-exports (see below)
  chat.py            # from adapters/llm_chat.py — no changes needed
  broker.py          # from adapters/llmbroker.py — fix one import
  toml_storage.py    # generic half of adapters/llm_storage.py
```

```
tests/llmbroker/
  test_chat.py        # from tests/services/test_llm_chat.py
  test_broker.py       # from tests/services/test_llmbroker.py
  test_toml_storage.py # new — extracted from tests/services/test_llm_storage.py
```

---

## Step 1 — `src/llmbroker/chat.py`

Move `src/dinary/adapters/llm_chat.py` → `src/llmbroker/chat.py` verbatim.
No internal imports to fix (file has none from `dinary`).

Symbols: `ProviderConfig`, `AllProvidersBusyError`, `AllProvidersFailedError`,
`is_rate_limit`, `retry_after_seconds`, `build_chat_request`,
`message_from_response`, `run_tool_step`, `complete_with_tools`,
`_run_tool_loop`, `_CHAT_PATH`.

## Step 2 — `src/llmbroker/broker.py`

Move `src/dinary/adapters/llmbroker.py` → `src/llmbroker/broker.py`.

Only change — the import block:

```python
# before
from dinary.adapters.llm_chat import (
    ProviderConfig,
    build_chat_request,
    is_rate_limit,
    message_from_response,
    retry_after_seconds,
)

# after
from llmbroker.chat import (
    ProviderConfig,
    build_chat_request,
    is_rate_limit,
    message_from_response,
    retry_after_seconds,
)
```

Also drop the now-stale module docstring sentence "intentionally isolated
for future extraction as a standalone package" (it's done — replace with a
short one-liner describing the module).

Symbols: `CallEvent`, `BrokerStorage` (Protocol), `Execution`, `LLMBroker`.

## Step 3 — split `src/dinary/adapters/llm_storage.py`

Current file mixes:
- **Generic** (no dinary deps): `_providers_from_toml`, `_label_from_base_url`,
  `_toml_stats_path`, `TomlLLMBrokerStorage`, plus module constants
  `_DEPLOY_DIR` / `_LLM_PROVIDERS_TOML` (only used as *defaults*).
- **Dinary-specific**: `SqliteLLMBrokerStorage`, `_open`, `_PRAGMAS`,
  `db_storage.DB_PATH`.

### 3a. New `src/llmbroker/toml_storage.py`

Move the generic part. Rename the underscore-prefixed helpers to public
names since they're now part of the package's API:

- `_providers_from_toml` → `providers_from_toml`
- `_label_from_base_url` → `label_from_base_url`
- `_toml_stats_path` → `toml_stats_path`

`TomlLLMBrokerStorage` itself is unchanged (constructor signature
`providers_toml: Path | None = None`).

The package must **not** hardcode dinary's `.deploy/` convention. Drop the
`_DEPLOY_DIR` / `_LLM_PROVIDERS_TOML` module-level path-discovery constants
from this module — `TomlLLMBrokerStorage.__init__` should require an explicit
`providers_toml: Path` (no default), or default to `Path("llm_providers.toml")`
(cwd-relative) for CLI ergonomics. Callers in dinary always pass an explicit
path anyway (see Step 3b / `tasks/receipt.py`).

Imports needed: `from llmbroker.chat import ProviderConfig`,
`from llmbroker.broker import CallEvent`.

### 3b. Trimmed `src/dinary/adapters/llm_storage.py`

Keep only:
- `_PRAGMAS`, `_open`
- `_DEPLOY_DIR`, `_LLM_PROVIDERS_TOML` (dinary's deploy-path convention —
  these stay here, not in the package)
- `SqliteLLMBrokerStorage`

Update its imports:

```python
# before
from dinary.adapters.llm_chat import ProviderConfig
from dinary.adapters.llmbroker import CallEvent
from dinary.db import storage as db_storage

# after
from dinary.db import storage as db_storage
from llmbroker import CallEvent, ProviderConfig
from llmbroker.toml_storage import providers_from_toml
```

`SqliteLLMBrokerStorage._seed` calls `_providers_from_toml(...)` →
`providers_from_toml(...)`.

Update module docstring (no longer "BrokerStorage implementations" plural —
just the SQLite one now; mention `TomlLLMBrokerStorage` lives in `llmbroker`).

### 3c. `tasks/receipt.py`

```python
# before
from dinary.adapters.llm_storage import TomlLLMBrokerStorage
from dinary.adapters.llmbroker import LLMBroker
...
broker = LLMBroker(TomlLLMBrokerStorage())

# after
from llmbroker import LLMBroker, TomlLLMBrokerStorage
...
broker = LLMBroker(TomlLLMBrokerStorage(providers_toml=<path to .deploy/llm_providers.toml>))
```

Since `TomlLLMBrokerStorage` no longer defaults to `.deploy/llm_providers.toml`,
`tasks/receipt.py` must pass that path explicitly — it already knows the repo
root (check existing `_DEPLOY_DIR`-style constant in `tasks/devtools` or
compute `Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"`).

---

## Step 4 — `src/llmbroker/__init__.py`

Public API surface (this is the package's intentional export point, not a
leftover re-export shim — it's the new home):

```python
from llmbroker.broker import BrokerStorage, CallEvent, Execution, LLMBroker
from llmbroker.chat import (
    AllProvidersBusyError,
    AllProvidersFailedError,
    ProviderConfig,
    build_chat_request,
    complete_with_tools,
    is_rate_limit,
    message_from_response,
    retry_after_seconds,
    run_tool_step,
)
from llmbroker.toml_storage import (
    TomlLLMBrokerStorage,
    label_from_base_url,
    providers_from_toml,
    toml_stats_path,
)

__all__ = [
    "AllProvidersBusyError",
    "AllProvidersFailedError",
    "BrokerStorage",
    "CallEvent",
    "Execution",
    "LLMBroker",
    "ProviderConfig",
    "TomlLLMBrokerStorage",
    "build_chat_request",
    "complete_with_tools",
    "is_rate_limit",
    "label_from_base_url",
    "message_from_response",
    "providers_from_toml",
    "retry_after_seconds",
    "run_tool_step",
    "toml_stats_path",
]
```

---

## Step 5 — fix imports across `dinary`

| File | Change |
|---|---|
| `src/dinary/main.py` | `from dinary.adapters.llm_storage import SqliteLLMBrokerStorage` stays; `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import LLMBroker` |
| `src/dinary/background/classification/task.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import LLMBroker` |
| `src/dinary/background/classification/store_resolver.py` | same |
| `src/dinary/background/classification/receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Execution, LLMBroker` |
| `src/dinary_analytics/llm.py` | `from dinary.adapters.llm_chat import (...)` → `from llmbroker import (...)` |
| `tasks/receipt.py` | see Step 3c |

After this, `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker" src/ tasks/` must return nothing.

---

## Step 6 — tests

### 6a. New `tests/llmbroker/test_chat.py`
Move `tests/services/test_llm_chat.py` content. Update imports:
`from dinary.adapters.llm_chat import (...)` → `from llmbroker import (...)` /
`from llmbroker.chat import (...)`.
The `patch("dinary.adapters.llm_chat.httpx.Client", ...)` target →
`patch("llmbroker.chat.httpx.Client", ...)`.
Delete the old file.

### 6b. New `tests/llmbroker/test_broker.py`
Move `tests/services/test_llmbroker.py` content. Update imports:
`from dinary.adapters.llm_chat import ProviderConfig` → `from llmbroker import ProviderConfig`,
`from dinary.adapters.llmbroker import CallEvent, Execution, LLMBroker` → `from llmbroker import CallEvent, Execution, LLMBroker`.
Keep `from conftest import NullStorage` — `tests/conftest.py` is rootdir-level
and stays importable from `tests/llmbroker/`.
Delete the old file.

### 6c. New `tests/llmbroker/test_toml_storage.py`
Extract from `tests/services/test_llm_storage.py`:
- `TestOnQualityFeedback.test_toml_writes_stats_json` (line ~377)
- `TestOnQualityFeedback.test_toml_stats_path_override` (line ~395)
- `TestLabelFromBaseUrl` (whole class, line ~417)

Update imports to `from llmbroker.toml_storage import (TomlLLMBrokerStorage, label_from_base_url)`.
Rename the asserted function from `_label_from_base_url` to `label_from_base_url`.

### 6d. Trim `tests/services/test_llm_storage.py`
- Remove the two `TomlLLMBrokerStorage` quality-feedback tests and the
  `TestLabelFromBaseUrl` class (moved to 6c).
- Update imports:
  ```python
  # before
  from dinary.adapters.llm_storage import (
      SqliteLLMBrokerStorage,
      TomlLLMBrokerStorage,
      _label_from_base_url,
  )
  from dinary.adapters.llmbroker import CallEvent

  # after
  from dinary.adapters.llm_storage import SqliteLLMBrokerStorage
  from llmbroker import CallEvent
  ```
- `TomlLLMBrokerStorage` is still referenced if any *Sqlite* test seeds via
  TOML — check; if only used for the moved tests, the import disappears
  entirely.
- Update module docstring title (no longer covers Toml storage).

### 6e. Mechanical import updates (no logic change)
| File | Change |
|---|---|
| `tests/conftest.py` | `from dinary.adapters.llm_chat import ProviderConfig` + `from dinary.adapters.llmbroker import CallEvent` → `from llmbroker import CallEvent, ProviderConfig` |
| `tests/test_main.py` | `from dinary.adapters.llm_chat import ProviderConfig` → `from llmbroker import ProviderConfig` |
| `tests/services/test_store_resolver.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import LLMBroker` |
| `tests/services/test_receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Execution, LLMBroker` |
| `tests/services/test_receipt_classification.py` | same |
| `tests/api/test_receipt_pipeline_e2e.py` | same |
| `tests/tasks/test_receipt_drain.py` | same |
| `tests/tasks/test_receipt_pipeline.py` | same |
| `tests/analytics/test_llm.py` | `from dinary.adapters.llm_chat import AllProvidersBusyError, AllProvidersFailedError` → `from llmbroker import AllProvidersBusyError, AllProvidersFailedError` |

`tests/api/test_api_delete_receipt.py` only references the `llmbroker_call_log`
**table name** (dinary's SQLite schema) — no import change.

---

## Step 7 — specs

### `specs/reference/llm-providers.md`
Trim to dinary-specific concerns only (provider pool rationale, prompt
design principles, models to avoid, `SqliteLLMBrokerStorage` as dinary's
adapter) — these are dinary's operational decisions, not the broker
engine's. Remove/rewrite the "Broker design" and "Storage implementations"
sections that describe the engine's internals (queue-based round robin,
`BrokerStorage` Protocol, `TomlLLMBrokerStorage`) — that's now the `llmbroker`
package's own concern. Replace with one short paragraph: dinary uses the
`llmbroker` package's `LLMBroker` via `SqliteLLMBrokerStorage`, which persists
provider config/call-log/rate-limit state in dinary's SQLite DB and seeds
from `.deploy/llm_providers.toml` on first run.

Per spec rules, do not link to `src/llmbroker/README.md` (specs/reference
may only link other specs).

### `specs/reference/architecture.md`
Add `src/llmbroker/` to the source-layout listing as a sibling package
("standalone LLM provider broker — round-robin failover, rate-limit handling;
no `dinary` imports; will move to its own repo/PyPI package").

---

## Step 8 — `src/llmbroker/README.md` (new)

This is the package's own doc — its future PyPI README — separate from
dinary's `specs/`. Capture:

1. One-line description + current capabilities (round-robin queue, per-provider
   429/503 cooldown via `Retry-After`, pluggable `BrokerStorage`, TOML +
   in-memory storage backends).
2. **Roadmap** section, verbatim from the brief, organized as:
   - Selection strategy (optimal / minimal-remaining-wait fallback)
   - Throttling logic (429 handling, escalating delay, offlining +
     healthcheck alarm)
   - Auto-recovery (offline sleep → probe → reset or re-offline)
   - Optimization (decay `current_delay` toward provider floor on success)
   - Fail statistics (per-provider performance history, retirement signals,
     API-key-expiry diagnostics)
   - Free-providers DB (community provider list with latency/limits/quality,
     `inv` seed command)
   - State machine table (Available / Waiting / Offline / Probing) from the brief
3. **Open design questions** (deferred to when this work starts):
   - Per-provider `initial_delay` vs. one global value
   - Performance-based optimal delay vs. KISS fixed schedule (lean KISS first)
   - Storage format: pydantic model → YAML/TOML, with optional partial
     load/save overrides (not just full-file dump)
   - Final PyPI distribution name (`llm-router`, `ai-hydra`, etc. — candidates
     listed, decide at publish time)
4. **Status**: currently developed in-tree inside the `dinary` monorepo at
   `src/llmbroker/`; extraction to its own repository/package is future work
   once the roadmap above is implemented.

---

## Step 9 — verification

1. `uv run inv pre` → 0 errors (pyrefly must resolve `llmbroker` imports —
   `pyproject.toml`'s `search_path = ["src", "."]` already covers it).
2. `uv run pytest` → all green, including new `tests/llmbroker/`.
3. `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker" src/ tests/ tasks/` → empty.
4. Sanity: `uv run python -c "import llmbroker; print(llmbroker.LLMBroker)"`.

---

## Explicitly out of scope for this phase

- Giving `src/llmbroker/` its own `pyproject.toml` / moving it to a separate
  git repo / publishing to PyPI — deferred until the roadmap features exist
  and a package name is chosen.
- Any of the roadmap features themselves (selection strategy beyond current
  round-robin, escalating-delay state machine, offline/probe auto-recovery,
  fail-statistics collection, free-providers DB).
- Renaming `LLMBroker`/the import name `llmbroker` to a future PyPI
  distribution name.
