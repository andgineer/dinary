# Extract `llmbroker` into its own top-level package

## Goal

Move `LLMBroker` and everything it directly depends on into a new package
`src/llmbroker/`, sibling to `src/dinary` and `src/dinary_analytics`, with
**zero imports of `dinary.*`**. This is a structural move only — no behavior
change for dinary, no new broker features. The "Selection Strategy /
Throttling / Auto-Recovery / Fail statistics / Free providers DB" requirements
from the brief are the **end goal for a future standalone PyPI package** and
are captured as a roadmap doc (Step 8), not implemented now.

## Core design principle — the package must not depend on a concrete DB

The whole point of the package is to be embeddable into *any* host with *any*
storage. Therefore:

- **Core (`broker.py`, `chat.py`) depends only on the `BrokerStorage`
  Protocol** — zero DB imports, no `aiosqlite`, no SQL strings. This is
  already true today and must stay true.
- **The host owns persistence policy.** A host with its own database (Postgres,
  a non-SQLite store, or no DB at all) simply implements `BrokerStorage` and
  never touches the package's SQLite code.
- **SQLite is one shipped *adapter*, not a dependency.** `sqlite_storage.py`
  is the *only* module in the package that imports `aiosqlite` / writes SQL.
  It exists so SQLite hosts (like dinary) get a ready-made, embeddable
  implementation — but importing `llmbroker` (the core) must never drag in a
  DB driver. When the package later gets its own `pyproject.toml`, `aiosqlite`
  becomes an **optional extra** (`llmbroker[sqlite]`); for now, the isolation
  is enforced purely by import discipline (core modules never import
  `sqlite_storage`).
- **`TomlLLMBrokerStorage` (read-only config + JSON sidecar)** is the
  zero-database path for CLI / standalone use.

## Why this is low-risk

`llmbroker.py` and `llm_chat.py` already have **no `dinary.db` imports** —
the existing docstring says they were written "intentionally isolated for
future extraction as a standalone package."

`llm_storage.py`'s SQLite tables (`llmbroker_providers`, `llmbroker_call_log`)
turn out to have **no FK into dinary's schema either** — migration `0005`
already replaced the integer `provider_id` FK with a plain `provider_label`
TEXT column, and `execution_id` is a bare TEXT correlation id, not a SQL FK.
The only actual dinary coupling in `llm_storage.py` is `SqliteLLMBrokerStorage`
reading `dinary.db.storage.DB_PATH` as a global instead of taking a `db_path`
constructor argument. Once that's parameterized, the whole storage layer is
embeddable as-is.

`src/` is already on `sys.path` via the editable install (verified:
`uv run python -c "import dinary_analytics"` works with no extra
`pyproject.toml` config). So `src/llmbroker/` becomes importable as
`import llmbroker` with **no pyproject/build changes** in this phase.

---

## Target layout

```
src/llmbroker/
  __init__.py         # public API re-exports (see below)
  chat.py             # from adapters/llm_chat.py — no changes needed
  broker.py           # from adapters/llmbroker.py — fix one import
  toml_storage.py     # generic TOML storage (from adapters/llm_storage.py)
  sqlite_storage.py   # embeddable SQLite storage (from adapters/llm_storage.py)
```

```
tests/llmbroker/
  test_chat.py          # from tests/services/test_llm_chat.py
  test_broker.py        # from tests/services/test_llmbroker.py
  test_toml_storage.py  # new — TOML-only tests from tests/services/test_llm_storage.py
  test_sqlite_storage.py # new — SQLite tests from tests/services/test_llm_storage.py
```

`src/dinary/adapters/llm_storage.py`, `llm_chat.py`, `llmbroker.py` and
`tests/services/test_llm_storage.py`, `test_llm_chat.py`, `test_llmbroker.py`
are all **deleted** — nothing dinary-specific remains in any of them.

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

---

## Step 3 — split `src/dinary/adapters/llm_storage.py`

Current file mixes:
- **Generic, TOML-backed**: `_providers_from_toml`, `_label_from_base_url`,
  `_toml_stats_path`, `TomlLLMBrokerStorage`.
- **Generic, SQLite-backed but dinary-coupled-by-accident**:
  `SqliteLLMBrokerStorage`, `_open`, `_PRAGMAS` — coupled only via
  `db_storage.DB_PATH`.
- **dinary deploy-path constants**: `_DEPLOY_DIR`, `_LLM_PROVIDERS_TOML`
  (`.deploy/llm_providers.toml` — dinary's deploy convention, not part of
  the package).

### 3a. New `src/llmbroker/toml_storage.py`

Move `_providers_from_toml`, `_label_from_base_url`, `_toml_stats_path`,
`TomlLLMBrokerStorage`. Rename the underscore-prefixed helpers to public
names since they're now part of the package's API:

- `_providers_from_toml` → `providers_from_toml`
- `_label_from_base_url` → `label_from_base_url`
- `_toml_stats_path` → `toml_stats_path`

`TomlLLMBrokerStorage` constructor: drop the `.deploy/`-aware default —
require an explicit `providers_toml: Path`, or default to
`Path("llm_providers.toml")` (cwd-relative) for CLI ergonomics. Callers in
dinary always pass an explicit path (see Step 5 / `tasks/receipt.py`).

Imports needed: `from llmbroker.chat import ProviderConfig`,
`from llmbroker.broker import CallEvent`.

### 3b. New `src/llmbroker/sqlite_storage.py`

Move `SqliteLLMBrokerStorage`, `_open`, `_PRAGMAS`. Two changes:

**1. Explicit `db_path`** — constructor takes the DB path instead of reading
a dinary global:

```python
class SqliteLLMBrokerStorage:
    def __init__(self, db_path: Path, providers_toml: Path | None = None) -> None:
        self._db_path = db_path
        self._providers_toml = providers_toml
        self._schema_ready = False
```

Every method's `db_path = str(db_storage.DB_PATH)` becomes
`db_path = str(self._db_path)`.

**2. Self-managed schema** — add `ensure_schema(db: aiosqlite.Connection)`:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS llmbroker_providers (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    label                  TEXT NOT NULL,
    base_url               TEXT NOT NULL,
    api_key                TEXT NOT NULL,
    model                  TEXT NOT NULL,
    is_enabled             BOOLEAN NOT NULL DEFAULT 1,
    rate_limited_until     TIMESTAMP,
    default_rate_limit_sec INTEGER NOT NULL DEFAULT 60,
    created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    execution_fail_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llmbroker_call_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id   TEXT,
    provider_label TEXT,
    called_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status         TEXT NOT NULL,
    latency_ms     INTEGER,
    error_detail   TEXT
);
"""


async def ensure_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(_SCHEMA)
    await db.commit()
```

This matches the shape dinary's migrations `0004`/`0005` already produced —
on dinary's DB it's a no-op; on a fresh host DB it bootstraps both tables
from scratch.

Call it once per instance, lazily, on first use. Add a small private helper
that wraps `_open` + the one-time `ensure_schema`:

```python
@asynccontextmanager
async def _connect(self) -> AsyncGenerator[aiosqlite.Connection]:
    async with _open(str(self._db_path)) as db:
        if not self._schema_ready:
            await ensure_schema(db)
            self._schema_ready = True
        yield db
```

Replace the four `async with _open(db_path) as db:` call sites in
`load_providers`, `on_call_logged`, `on_rate_limited`, `on_quality_feedback`
with `async with self._connect() as db:`.

`_seed` (TOML seeding on first run) is unchanged apart from calling the
renamed `providers_from_toml` from `toml_storage.py`.

Imports needed: `from llmbroker.broker import CallEvent`,
`from llmbroker.chat import ProviderConfig`,
`from llmbroker.toml_storage import providers_from_toml`.

### 3c. dinary side

`src/dinary/adapters/llm_storage.py` is **deleted entirely** — nothing
dinary-specific remains (see Step 5 for where `_DEPLOY_DIR`/
`_LLM_PROVIDERS_TOML` go).

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
from llmbroker.sqlite_storage import SqliteLLMBrokerStorage, ensure_schema
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
    "SqliteLLMBrokerStorage",
    "TomlLLMBrokerStorage",
    "build_chat_request",
    "complete_with_tools",
    "ensure_schema",
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

## Step 5 — fix imports and wiring across `dinary`

| File | Change |
|---|---|
| `src/dinary/main.py` | see below |
| `src/dinary/background/classification/task.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import LLMBroker` |
| `src/dinary/background/classification/store_resolver.py` | same |
| `src/dinary/background/classification/receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Execution, LLMBroker` |
| `src/dinary_analytics/llm.py` | `from dinary.adapters.llm_chat import (...)` → `from llmbroker import (...)` |
| `tasks/receipt.py` | see below |

### `src/dinary/main.py`

```python
# before
from dinary.adapters.llm_storage import SqliteLLMBrokerStorage
from dinary.adapters.llmbroker import LLMBroker
...
broker = LLMBroker(SqliteLLMBrokerStorage())

# after
from llmbroker import LLMBroker, SqliteLLMBrokerStorage
...
broker = LLMBroker(SqliteLLMBrokerStorage(db_path=storage.DB_PATH, providers_toml=_LLM_PROVIDERS_TOML))
```

`main.py` already has `_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent`
(repo root). Add next to it:

```python
_DEPLOY_DIR = _PROJECT_ROOT / ".deploy"
_LLM_PROVIDERS_TOML = _DEPLOY_DIR / "llm_providers.toml"
```

(This is exactly what `llm_storage.py` used to compute — it just moves to
`main.py`, the one place that wires dinary's deploy layout to the broker.)

### `tasks/receipt.py`

```python
# before
from dinary.adapters.llm_storage import TomlLLMBrokerStorage
from dinary.adapters.llmbroker import LLMBroker
...
broker = LLMBroker(TomlLLMBrokerStorage())

# after
from llmbroker import LLMBroker, TomlLLMBrokerStorage
...
_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"
broker = LLMBroker(TomlLLMBrokerStorage(providers_toml=_PROVIDERS_TOML))
```

After this, `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tasks/` must return nothing.

---

## Step 6 — tests

Mirror the package's DB-independence in the tests: **`tests/llmbroker/` must
not import `dinary.*`** (it may import the shared `conftest.NullStorage`, which
is plain test infra). Any test that genuinely needs both `dinary` and
`llmbroker` (e.g. the schema-equivalence check in 6d) lives under
`tests/services/`, not `tests/llmbroker/`.

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

### 6d. New `tests/llmbroker/test_sqlite_storage.py`
Move the remaining classes from `tests/services/test_llm_storage.py`
(`TestLoadProviders`, `TestOnCallLogged`, `TestOnRateLimited`,
`TestOnQualityFeedback` minus the TOML tests moved in 6c) and adapt them to
the new constructor:

- Replace `SqliteLLMBrokerStorage()` → `SqliteLLMBrokerStorage(db_path=<tmp sqlite file>)`.
- These tests no longer need dinary's `fresh_db` fixture / yoyo migrations —
  `ensure_schema()` creates the tables. Use a plain `tmp_path / "test.db"` and
  rely on `ensure_schema` (called lazily on first storage call).
- Add a new `TestEnsureSchema` covering: fresh file gets both tables; calling
  twice is a no-op (idempotent); existing data survives a second call.
- **Add a schema-equivalence test** (guards `ensure_schema` ↔ yoyo drift,
  since both now produce these tables): create one DB via dinary's yoyo
  migrations and another via `llmbroker.ensure_schema`, then assert
  `PRAGMA table_info(llmbroker_providers)` and
  `PRAGMA table_info(llmbroker_call_log)` match (column names, types,
  defaults, notnull) — ignoring declared column *order*, which is cosmetic in
  SQLite. This test needs both `dinary.db.db_migrations` and `llmbroker`, so
  it can live in `tests/services/` (dinary side) rather than
  `tests/llmbroker/` (which must stay dinary-free).
- The `_label_from_base_url` import and `TestLabelFromBaseUrl` class do not
  belong here (moved to 6c).
- `test_example_toml_is_valid` (loads `.deploy.example/llm_providers.toml`)
  moves here too — it exercises `SqliteLLMBrokerStorage(db_path=..., providers_toml=example)`.

Imports: `from llmbroker import CallEvent, ProviderConfig, SqliteLLMBrokerStorage, ensure_schema`.

### 6e. Delete `tests/services/test_llm_storage.py`
Everything in it has moved to 6c/6d.

### 6f. `tests/conftest.py`
- `from dinary.adapters.llm_chat import ProviderConfig` + `from dinary.adapters.llmbroker import CallEvent` + `from dinary.adapters.llm_storage import SqliteLLMBrokerStorage` → `from llmbroker import CallEvent, ProviderConfig, SqliteLLMBrokerStorage`.
- `NullStorage`, `_REAL_LLM_SEED = SqliteLLMBrokerStorage._seed`, `_disable_llm_seed`,
  `real_llm_seed` fixtures: unchanged logic, just the new import.
- Check where the app-under-test's `SqliteLLMBrokerStorage` instance gets its
  `db_path` — `create_app()`'s lifespan now calls
  `SqliteLLMBrokerStorage(db_path=storage.DB_PATH, providers_toml=_LLM_PROVIDERS_TOML)`
  (Step 5), and `db`/`fresh_db` fixtures already monkeypatch `storage.DB_PATH`
  to the temp DB — so this flows through unchanged.

### 6g. Mechanical import updates (no logic change)
| File | Change |
|---|---|
| `tests/test_main.py` | `from dinary.adapters.llm_chat import ProviderConfig` + `from dinary.adapters.llm_storage import SqliteLLMBrokerStorage` → `from llmbroker import ProviderConfig, SqliteLLMBrokerStorage` |
| `tests/services/test_store_resolver.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import LLMBroker` |
| `tests/services/test_receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Execution, LLMBroker` |
| `tests/services/test_receipt_classification.py` | same |
| `tests/api/test_receipt_pipeline_e2e.py` | same |
| `tests/tasks/test_receipt_drain.py` | same |
| `tests/tasks/test_receipt_pipeline.py` | same |
| `tests/analytics/test_llm.py` | `from dinary.adapters.llm_chat import AllProvidersBusyError, AllProvidersFailedError` → `from llmbroker import AllProvidersBusyError, AllProvidersFailedError` |

`tests/api/test_api_delete_receipt.py` only references the `llmbroker_call_log`
**table name** (a contract now owned by `llmbroker.ensure_schema`) — no
import change.

---

## Step 7 — specs

### `specs/reference/llm-providers.md`
Trim to dinary-specific concerns only: provider pool rationale, prompt design
principles, models to avoid. Remove the "Broker design" and "Storage
implementations" sections describing the engine's internals (queue-based
round robin, `BrokerStorage` Protocol, `TomlLLMBrokerStorage`,
`SqliteLLMBrokerStorage`) — that's now the `llmbroker` package's own concern.

Replace with one short paragraph: dinary runs the `llmbroker` package's
`LLMBroker` with its `SqliteLLMBrokerStorage`, pointed at dinary's own SQLite
file (`storage.DB_PATH`) and seeded from `.deploy/llm_providers.toml`.
`llmbroker` creates and owns the `llmbroker_providers` / `llmbroker_call_log`
tables in that file (`ensure_schema`); dinary's migrations `0004`/`0005`
created these tables historically and are left untouched, but any future
schema changes to them are made inside `llmbroker`, not via new dinary
migrations.

Per spec rules, do not link to `src/llmbroker/README.md` (specs/reference
may only link other specs).

### `specs/reference/architecture.md`
Add `src/llmbroker/` to the source-layout listing as a sibling package
("standalone LLM provider broker — round-robin failover, rate-limit handling,
embeddable SQLite/TOML storage; no `dinary` imports; will move to its own
repo/PyPI package").

---

## Step 8 — `src/llmbroker/README.md` (new)

This is the package's own doc — its future PyPI README — separate from
dinary's `specs/`. It records both what exists today and the target feature
set the package is being shaped toward.

### 8.1 Current capabilities (as extracted in this plan)

- Round-robin provider queue; at most one in-flight request per provider.
- Per-provider 429/503 cooldown honoring the `Retry-After` header, falling
  back to a per-provider `rate_limit_sec`.
- Pluggable `BrokerStorage` Protocol; **core has zero DB dependency**.
- Shipped adapters: `TomlLLMBrokerStorage` (read-only config + JSON sidecar
  for fail counts, no database) and `SqliteLLMBrokerStorage` (self-managed
  schema via `ensure_schema`, embeddable into any host SQLite file via
  `db_path`).

### 8.2 Roadmap — target requirements (NOT implemented in this phase)

These are the end-state requirements from the project brief. They are recorded
here so the extracted package has a clear direction; none are built now.

**1. Selection strategy**
- *Optimal:* use the first available provider with **0 wait time**.
- *Fallback:* if all are busy, select the one with the **minimal remaining
  wait**.

**2. Throttling logic (HTTP 429)**
- *Wait:* respect the `Retry-After` header, else use `current_delay`.
- *Escalate:* on consecutive failures, increase the delay (e.g. ×2) up to a
  **Max Delay**.
- *Offlining:* if a request still fails after reaching Max Delay, mark the
  provider **Offline** and trigger a **Healthcheck Alarm**.

**3. Auto-recovery (probing)**
- *Offline sleep:* the provider stays inactive for a set duration.
- *The probe:* after sleep expires, send a **single test request**.
- *Success:* reset to the provider's **Initial Delay** and return to active
  rotation.
- *Failure:* trigger the Healthcheck Alarm again and restart Offline Sleep
  (with optional increment).

**4. Optimization (on success)**
- *Reduce:* on every successful execution, decrease `current_delay` until it
  reaches the **Min Delay** (the provider's floor).

**5. Fail statistics**
- Collect provider failures: store informative details about provider
  performance to support decisions about retiring providers, detecting an
  insufficient number of providers for the request rate, etc.
- Include API-key-expiration diagnostics.

**6. Free-providers DB**
- Maintain an up-to-date AI-providers DB in the repo to choose from — with
  latency, limits, and quality estimations. Open format question: YAML?
  release artifact?
- A ready-to-use prompt to refresh the DB in the repo (e.g. sourced from
  `https://shir-man.com/free-llm/`).
- An `inv` command to seed providers from the DB according to the user's
  requirements.

**Logic state machine**

| Current state | Event        | New state | Delay adjustment          |
|---------------|--------------|-----------|---------------------------|
| Available     | Error 429    | Waiting   | `current_delay` (up to Max)|
| Waiting       | Success      | Available | Decrease delay            |
| Waiting       | Fail @ Max   | Offline   | Start Offline Sleep / Alarm|
| Offline       | Sleep End    | Probing   | Send test request         |
| Probing       | Success      | Available | Reset to Initial Delay    |
| Probing       | Failure      | Offline   | Restart Sleep / Alarm     |

### 8.3 Open design questions (decide when the roadmap work starts)

- Do we need an **individual per-provider Initial Delay**, or one global value?
- Should the **optimal delay be computed from provider performance**, or keep
  it KISS with a fixed schedule? (Lean KISS first.)
- **Storage format** for the standalone package: local pydantic model →
  YAML/TOML, with optional partial load/save overrides (per-field operations
  against the host store, not just a full-file dump). How does this compose
  with `SqliteLLMBrokerStorage`'s `ensure_schema`/versioning if the schema
  needs a second revision?
- **Package / distribution name** — candidates: `circuit-ai`, `llm-hydra`,
  `ai-hydra`, `hydrai`, `llm-router`, `gratis-ai-relay`. Decide at publish time.

### 8.4 Status

Currently developed in-tree inside the `dinary` monorepo at `src/llmbroker/`.
Extraction to its own repository / PyPI package is future work, once the
roadmap above is implemented and a distribution name is chosen.

---

## Step 9 — verification

1. `uv run inv pre` → 0 errors (pyrefly must resolve `llmbroker` imports —
   `pyproject.toml`'s `search_path = ["src", "."]` already covers it).
2. `uv run pytest` → all green, including new `tests/llmbroker/`.
3. `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tests/ tasks/` → empty.
4. Sanity: `uv run python -c "import llmbroker; print(llmbroker.LLMBroker, llmbroker.SqliteLLMBrokerStorage)"`.
5. Manual smoke: `uv run inv dev`, confirm the server starts and
   `llmbroker_providers`/`llmbroker_call_log` are still readable via the
   admin LLM API (existing dinary DB — `ensure_schema` must no-op cleanly
   against it).

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
- Changing dinary's admin API (`api/controllers/llm.py`, `api/llm.py`) — it
  keeps talking to `llmbroker_providers`/`llmbroker_call_log` via dinary's own
  `db.storage.transaction()` with raw SQL, same as today; only the schema's
  *owner* changes (Step 7), not its shape or dinary's access path.
  **Known limitation:** because dinary still reads/writes these tables with
  raw SQL, the table *shape* remains a de-facto contract dinary depends on —
  so `llmbroker` is not yet free to change that shape unilaterally. Fully
  decoupling this (routing dinary's admin through a `BrokerStorage`
  read/admin interface so the schema becomes private to the package) is
  deferred to the roadmap, not done here.
