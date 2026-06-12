# Extract `llmbroker` into a standalone, host-agnostic package

## Goal

Turn `LLMBroker` into a self-contained package `src/llmbroker/` (sibling to
`src/dinary` and `src/dinary_analytics`, **zero `dinary.*` imports**) that is a
**complete LLM-provider broker for any application** — any database, or none —
not a dinary-internal helper. The package provides one function: LLM access
over a *cluster of providers*, rotating away from ones that are momentarily
unavailable (429/503), and accumulating enough signal to decide which providers
to drop or add.

The design optimizes two things at once:

- **Dead-simple typical use.** Copy an example providers file, put keys in env
  vars, write one constructor line. A typical host writes **no integration
  code** and **never puts a secret in source**.
- **Full universality.** Any storage, any provider/import source, any secret
  backend, single-process or clustered — each is a shipped *battery*, and the rare host
  with a non-standard requirement implements **one small port**, reusing shipped
  implementations for everything else.
- **It tunes itself.** The package does not just log calls — a background
  optimizer reads telemetry (per provider *and per task type*) and **acts**:
  auto-adjusts cooldowns/delays, offlines and re-probes bad providers, and routes
  each task type to the providers that empirically handle it best. The goal is
  "it just works" — not a feed of advice about free providers the user will never
  read. A human is bothered only by what only a human can fix (pool
  under-provisioned, API key dead).

There is **no goal to minimize the diff**. We rename and reshape freely to reach
the ideal API; dinary becomes just one more caller.

---

## The mental model

A host wires up to four things; only the first is mandatory, the rest have
working defaults:

| Concept | Port | Required? | Default battery | What it is |
|---|---|---|---|---|
| **providers** | `Registry` | **yes** (or a `list[ProviderConfig]`) | — | where the LLM-provider configuration is stored / loaded |
| **secrets** | `Secrets` | no | `Secrets.env()` | how `api_key_ref` references resolve to real keys |
| **coordination** | `Coordination` | no — **opt-in, cluster only** | none (single process keeps state in-memory internally) | cross-instance sync of per-provider live state (cooldown, fail count, offline) — supply it only to make several `llmbroker` copies agree |
| **telemetry** | `Telemetry` | no | `Telemetry.log()` | append-only journal of calls — to see what happened and decide which providers to keep |

**`Coordination` is opt-in and exists only for clusters.** The broker always
keeps per-provider live state (cooldown/fail/offline) in memory internally — that
is a private detail, not a user-facing port. You pass `coordination=` *only* to
share that state across several `llmbroker` instances; there is deliberately no
"local" variant, because the absence of the parameter already means "single
process, nothing to coordinate". A database does not call for it — persisting
ephemeral cooldown for one process buys nothing (a stale cooldown after a restart
is worse than re-learning from a live 429). So the "DB" axis is purely `Registry`
(config) + `Telemetry` (log); `Coordination` is orthogonal and only about
multi-instance sync.

Names are bare (no `Provider` prefix) because the package namespace already
supplies context — the `httpx.Client` / `sqlalchemy.Engine` idiom. Accessed as
`llmbroker.Registry`, `llmbroker.Coordination`, `llmbroker.Telemetry`,
`llmbroker.Secrets`. `Telemetry` default is `log()` (Python `logging`) so call
data is never silently lost; `Telemetry.none()` is the explicit opt-out.

Dataclasses keep descriptive names so short ports pair with clear data:

| Port | Reads / writes |
|---|---|
| `Registry.load()` | `list[ProviderConfig]` |
| `Coordination.snapshot()` | `dict[str, ProviderHealth]` |
| `Telemetry.record(event)` | `CallEvent` |
| `Secrets.resolve(ref)` | `str` (the key) |

`provider` (an `(base_url, api_key, model)` endpoint) stays the public term —
the widely-understood industry word. `ProviderConfig.api_key` holds the
**resolved** key in memory only; the stored form (TOML row / DB column) holds an
`api_key_ref`, never the secret.

---

## The usage ladder (this is the README and the doc structure)

Documentation reads as a staircase, **not** as "orthogonal axes". Each rung is a
shipped battery; a reader stops at the first rung that fits.

### Rung 0 — copy, set env, one line (embedded, in-memory)

1. Copy a shipped example: `providers.example.toml` → `providers.toml` (pick the
   variant for your goal — see "example files").
2. Generate a `.env` skeleton so you never hand-type key names:
   ```bash
   python -m llmbroker env-template providers.toml > .env   # then fill in the values
   ```
3. In your app:
   ```python
   import llmbroker

   broker = llmbroker.LLMBroker(registry=llmbroker.Registry.toml("providers.toml"))
   reply = await broker.complete("Summarize this receipt: ...")
   ```

State is in-memory, telemetry goes to the log, keys come from env. Nothing to
implement, no secret in source. (`complete()` returns `str | None` — `None` when
every provider is momentarily busy; the README example shows handling that.)

### Rung 1 — "if you have a database"

Persist provider config and telemetry, build an admin UI on the DB table.
Connecting to the store and **populating** it are separate steps (see "Seeding a
DB store" — the constructor never auto-seeds). The one-line sugar
`import_if_empty` covers the common "fill on first run" case. **`coordination` is
not part of this** — a single process keeps cooldown state in memory internally;
there is nothing to share.

```python
registry = await llmbroker.Registry.sqlite("broker.db").import_if_empty(
    llmbroker.Registry.toml("providers.toml"),
)
broker = llmbroker.LLMBroker(
    registry=registry,
    telemetry=llmbroker.Telemetry.sqlite("broker.db"),
    # no coordination= → single process (Rung 2 adds it for clusters)
)
```

A host that hand-manages the DB just never imports; a host that wants our updated
catalog re-runs an explicit `import_from(..., on_conflict="update")`.

### Rung 2 — "if you run a cluster"

Add `coordination=`; the instances then agree automatically (shared cooldown,
shared fail counts). Nothing else changes:

```python
coordination=llmbroker.Coordination.redis("redis://...")   # or .postgres(dsn) / .mongodb(uri)
```

The broker core is **never cluster-aware** — clustering lives entirely inside the
`Coordination` implementation (see "Cluster coordination"). Omit `coordination=`
and you are single-process; there is no "local" variant to write.

### Need an HTTP service?

`llmbroker` is a **library, not a server** — it deliberately ships no HTTP layer.
If you want a standalone gateway, embed the broker in whatever web framework you
already use (FastAPI / Flask / Django) and expose your own endpoint. That is a
host concern, outside the package's scope.

---

## Ports (the universality contract)

Narrow Protocols. A host implements one **only** to support a backend we do not
ship.

```python
class ProviderState(Enum):
    AVAILABLE = "available"   # in rotation
    WAITING = "waiting"       # cooling after 429/503 until cooldown_until
    OFFLINE = "offline"       # repeatedly failed; sleeping before a probe (Optimizer, P4)
    PROBING = "probing"       # sending a test request to check recovery (Optimizer, P4)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    state: ProviderState = ProviderState.AVAILABLE
    cooldown_until: datetime | None = None   # end of the WAITING/OFFLINE sleep
    fail_count: int = 0


@dataclass(frozen=True, slots=True)
class CallEvent:
    provider_label: str
    task_type: str | None
    execution_id: Any | None
    status: str                          # transport outcome: "ok" | "429" | "503" | "error"
    latency_ms: int | None = None
    error_detail: str | None = None
    prompt_tokens: int | None = None     # from the response `usage`, when the provider returns it
    completion_tokens: int | None = None
    quality_score: float | None = None   # 0..1; NULL = not judged (the common case)


class Registry(Protocol):
    async def load(self) -> list[ProviderConfig]: ...
    # Optional admin surface (DB batteries implement it; the broker never calls it):
    # async def add(self, p: ProviderConfig) -> None: ...
    # async def update(self, label: str, **fields) -> None: ...
    # async def remove(self, label: str) -> None: ...


class Secrets(Protocol):
    async def resolve(self, ref: str) -> str: ...


# Optional, opt-in — only for clusters. The broker maintains the same shape
# in-memory internally; a Coordination backend mirrors/syncs it across instances.
class Coordination(Protocol):
    async def snapshot(self) -> dict[str, ProviderHealth]: ...
    async def record_rate_limited(self, label: str, until: datetime) -> None: ...
    async def record_failure(self, label: str) -> None: ...
    async def record_success(self, label: str) -> None: ...


class Telemetry(Protocol):
    async def record(self, event: CallEvent) -> None: ...
```

- **Only `Registry.load()` is mandatory** for a custom backend; the admin
  methods are an optional mixin for a host's admin UI, never called by the broker.
- `Coordination` is **optional and cluster-only** — omit `coordination=` and the
  broker uses its private in-memory state. There is no public "in-memory
  Coordination" object; single-process is the absence of the parameter.
- `record_success` is a no-op for current backends; it exists so the roadmap
  `Optimizer` (delay decrease, offline→probe→active) can hang off the same
  interface without a breaking change.
- `ProviderHealth.state` models the **full** Optimizer state machine
  (Available/Waiting/Offline/Probing) from day one. P1 only ever sets
  Available/Waiting (429/503 cooldown); Offline/Probing are populated by the
  Optimizer (P4). The field is locked into the contract now so the P3
  `Coordination` backends (redis/postgres/mongodb) sync it **without** a later
  Protocol-breaking change — a node writes the whole `ProviderHealth` (state
  included), so optimizer-driven transitions propagate over `snapshot()` with no
  new method.
- `CallEvent` captures `prompt_tokens`/`completion_tokens` (objective, read from
  the response `usage` when present) and `quality_score` from P1 because
  telemetry is **append-only** — a column added later starts with no history,
  which is exactly the data the Optimizer needs. `quality_score` is **orthogonal
  to `status`**: `status` is the transport outcome (an HTTP-200 answer is
  `status="ok"`), `quality_score` is whether that answer was usable. **Cost is
  deliberately not stored** — it is `tokens × a price table`, a host/Optimizer
  concern derived later from the tokens, not a raw signal to journal. The
  **source** of `quality_score` (host `mark_failed` vs the P5 LLM-judge) is **not**
  a separate column in P1: until the judge exists every score is a host
  `mark_failed` ground truth, so pre-judge rows are unambiguous and a
  `quality_source` column can be added with the judge (P5) with no lost history —
  unlike `tokens`/`quality_score` themselves, whose per-row values are
  unrecoverable if not captured now.
- `LLMBroker(registry=...)` accepts a `Registry` **or** a `list[ProviderConfig]`
  (wrapped as a read-only in-memory registry; `Registry.of([...])` makes that
  explicit). Same for any `import_from` source. The kwarg matches the type, like
  `secrets=Secrets…`, `coordination=Coordination…`, `telemetry=Telemetry…`.
- `complete(prompt: str, **kw) -> str | None` is a thin convenience wrapping
  `execute([{ "role": "user", "content": prompt }], **kw)` for the friendly path;
  `execute(messages=...)` stays the full API.
- `execute`/`complete` take **both** an opaque `execution_id` (correlation) and a
  `task_type: str | None` (a host-defined category — e.g. `"receipt_classification"`,
  `"summary"`). `task_type` is what lets the `Optimizer` tune and route per type,
  so it is captured from day one even though the Optimizer is built later.

`Execution.mark_failed()` (quality feedback on an HTTP-200 but unusable answer)
records a failure into the broker's live state (mirrored to `Coordination` if
present) and emits a `CallEvent` to telemetry with `status="ok"` but
`quality_score=0.0` — the call succeeded at the transport layer, the answer was
rejected, so quality is attributed separately from the HTTP outcome. The P5
LLM-judge fills sampled non-binary scores into the same field.
`CallEvent` carries `task_type` alongside `execution_id`, so quality, tokens, and
latency can all be attributed per (provider, task type).

---

## Secrets — universal, trivial for the simple case

Stored provider config holds an `api_key_ref`, not a key. The `Secrets` resolver
turns a ref into a key at load/refresh time. Default reads env vars, so the
simplest case is just "set env vars".

```toml
# providers.toml
[[providers]]
label       = "Groq"
base_url    = "..."
model       = "..."
api_key_ref = "GROQ_API_KEY"     # env-var name for Secrets.env(); a secret path for a vault resolver
```

```python
Registry.toml("providers.toml")                              # default Secrets.env(): from os.environ
Registry.toml("providers.toml", secrets=Secrets.dict({...})) # explicit map (tests / pre-loaded keys)
Registry.toml("providers.toml", secrets=my_vault_resolver)   # secret manager: implements .resolve(ref)
```

- Shipped: `Secrets.env()` (default), `Secrets.dict(mapping)`. A plain
  `Callable[[str], Awaitable[str]] | Callable[[str], str]` is accepted and
  adapted, so a secret-manager integration is one small function.
- `secrets=` is a parameter of **any** Registry that materializes configs (TOML
  or DB) — the DB stores `api_key_ref` too, resolved the same way.
- Keys are resolved at load/refresh; rotated secrets are picked up on the next
  refresh tick.

### Example files + `env-template` (so key names are never hand-typed)

- Ship `providers.example.toml` (and goal-specific variants, e.g. a broad
  free-tier set vs a quality-first set; identical format, different provider
  list) for users to copy.
- Ship a matching `.env.example` listing every `api_key_ref` the example TOML
  references, with blank values.
- Ship `python -m llmbroker env-template <toml> > .env`: scans any TOML for
  `api_key_ref` and emits a `.env` skeleton — the robust answer for custom files.

---

## Seeding a DB store — explicit import, never a constructor side-effect

A `Registry.<db>(…)` constructor only **connects**; it never populates. Once a DB
table exists it is authoritative — nothing auto-mutates it, so there is no
"what happens if the seed changed?" ambiguity. Filling and updating it is a
**separate, explicit operation** with a chosen conflict policy:

```python
reg = Registry.sqlite("broker.db")
await reg.import_from(Registry.toml("providers.toml"), on_conflict="skip")
```

`import_from(source, *, on_conflict=...)` takes any read-only provider source (a
`Registry` like `Registry.toml(...)`, or a `list[ProviderConfig]`). Policies map
to the real user journeys:

| `on_conflict` | Effect | Journey |
|---|---|---|
| `skip` (default) | insert providers missing by label; never touch existing rows | protect hand-edits; safe re-runs |
| `update` | upsert — insert new + overwrite existing fields from the source | pull our updated recommended catalog into the DB |
| `replace` | wipe the table, then insert the source | full reset to the source |

A host that decides to hand-manage the DB simply stops importing; a host that
wants our catalog changes re-runs `import_from(..., on_conflict="update")`. On a
fresh (empty) DB all three policies behave identically.

**One-line sugar for simple use:** `import_if_empty(source)` imports only when the
store is empty and returns the registry, so first-run fill composes inline
without any constructor magic:

```python
registry = await Registry.sqlite("broker.db").import_if_empty(Registry.toml("providers.toml"))
```

Because `import_if_empty` never acts on a non-empty store, changing the source
later cannot surprise an existing DB — it only ever fills a blank one. The same
operations are on the CLI for ops:

```bash
python -m llmbroker import providers.toml --into sqlite:broker.db --on-conflict update
```

`import_from`/`import_if_empty` are built on the optional `Registry` admin surface
(`add`/`update`/`remove`); `Registry.toml` (file is the store) needs neither —
edit the file.

---

## Cluster coordination — how `Coordination` meets the in-memory queue

The broker keeps its single-process machinery: one `asyncio.Queue` slot per
provider, at most one in-flight request per provider, `loop.call_later`
re-enqueue after a 429 cooldown, and its **private in-memory** per-provider live
state. `Coordination`, when supplied, layers on without the core knowing whether
it is clustered:

- **On 429/503:** the broker updates its in-memory cooldown **and** schedules its
  own local `call_later` re-enqueue; if `coordination=` is set it also
  `record_rate_limited(...)` so *other* nodes learn.
- **On the refresh tick** (existing `_run_refresh`, now tightened/configurable):
  with `Coordination`, the broker calls `snapshot()` and reconciles its local
  queue against shared state — dropping providers other nodes marked
  cooling/offline, re-adding those whose cooldown passed. **Clustering rides on the
  refresh loop that already exists.**
- **No `coordination=` (default):** everything stays in the process's own memory —
  behavior identical to today (local `call_later`), zero infra, zero races.
- **Shared backends** (`Coordination.redis`/`postgres`/`mongodb`) exist **only for
  clusters**: `snapshot()` reads shared state; bounded races (two nodes briefly
  both see a provider free) cost at most one redundant 429. There is no `sqlite`
  coordination — SQLite is not a cross-node store, and single-process needs no
  externalized state.

Reconcile granularity = refresh interval (eventual consistency). A precise
"local timer driven by the shared cooldown value" and redis pub/sub for
near-instant propagation are noted as **future optimizations**, not built now.

---

## Autonomous optimization — the `Optimizer`

Showing per-provider advice is not the goal — **nobody will study what is
happening with yet another free provider, or care which vendor backs it.** The
goal is that the cluster **tunes itself and routes work optimally, invisibly.**
The package ships an `Optimizer`: a background control loop (like the provider
refresh) that reads telemetry and *acts*, not just reports.

```python
broker = llmbroker.LLMBroker(
    registry=Registry.sqlite("broker.db"),
    telemetry=Telemetry.sqlite("broker.db"),   # queryable → warm-start + analysis (optimizer runs on any backend)
    optimize=True,                              # default-on; learns from the live event stream
)
```

**The Optimizer's working state is a live in-memory aggregate, not journal data.**
It feeds off the **live event stream** — every `Telemetry.record(event)` updates
rolling per-(provider, task_type) stats in memory (the Optimizer interposes at the
`record()` seam, e.g. as a `Telemetry` decorator, so this works with *any* backend
including `log()`/`none()`). The append-only journal (`CallEvent` rows) stays the
durable source of truth; the Optimizer's rankings/tuning are a derived projection
of it. That projection **may** be checkpointed to its **own** table for a fast warm
start — but is never written back into the append-only `call_log` (mixing a mutable
projection into an event log is a category error). Whether to checkpoint or simply
recompute from the journal on start is a **P4 open question**, not a P1 lock. Either
way, `CallEvent` must be rich from day one: a column added later starts with no
history, and historical warm-start/backfill is exactly what a queryable backend
buys.

**What it does automatically (the point):**

- **Parameter tuning** — per-provider cooldown/delay: escalate on repeated 429s up
  to a max, decrease on sustained success, offline a provider that keeps failing
  and probe it for recovery. The tuning state model:

  | Current state | Event       | New state | Delay adjustment            |
  |---------------|-------------|-----------|-----------------------------|
  | Available     | Error 429   | Waiting   | `current_delay` (up to Max)  |
  | Waiting       | Success     | Available | Decrease delay              |
  | Waiting       | Fail @ Max  | Offline   | Start Offline Sleep / Alarm  |
  | Offline       | Sleep End   | Probing   | Send test request           |
  | Probing       | Success     | Available | Reset to Initial Delay      |
  | Probing       | Failure     | Offline   | Restart Sleep / Alarm        |

- **Task routing** — bias selection of each `task_type` toward the providers that
  empirically handle it best. The policy is **tiered / lexicographic, not a
  weighted-sum scalar** (`w·quality + w·latency + w·cost` is untunable — the terms
  are not commensurable, and a latency win must never "buy back" a quality loss):
  1. **Availability gate** — candidates are providers not in cooldown (the FSM
     already drops Waiting/Offline); residual flakiness is a soft tiebreak.
  2. **Quality floor gate** — drop providers whose per-`task_type` usable-rate is
     below a floor. Quality is a gate, not a tradeable term.
  3. **Objective ranking — the objective lives with the `task_type`.** A
     background batch type (e.g. `receipt_classification`) ranks the gated set by
     quality; an interactive type ranks by latency. There is no single global
     weighting that is right for both.
  4. **Tokens = a budget constraint, not a quality axis.** For an identical prompt
     token counts barely differ; what matters is rate-limit budget (TPM)
     consumption → throughput headroom (a less verbose provider yields more calls
     before a 429) and `$` when paid tiers are mixed. Tokens break ties / enforce a
     budget; they never trade against quality.

  Estimates are **confidence-aware** (bandit-style): a minimum sample count before
  a provider's stats override round-robin, an exploration reserve so deprioritized
  providers keep being sampled (else their stats go stale and recovery/decay is
  invisible), and a Bayesian usable-rate for the **sparse** quality signal. The
  broker exposes a pluggable **selection policy**; the default is round-robin, and
  the Optimizer swaps in the per-`task_type` ranking it maintains from telemetry.
  Concrete thresholds and the bandit flavor are a P4 open question; the tiering and
  the per-`task_type`-objective principle are the decided shape.
- **Pool hygiene** — automatically deprioritize/retire consistently-useless
  providers. Nothing for a human to read.

**What it may use an LLM for** (optional, sampled, never on the hot path):

- **Quality judging** — sample outputs per (provider, task_type) and score them
  with an LLM-as-judge, closing the quality loop *without* the host having to call
  `mark_failed`. The judge call goes through the broker itself (dogfooding) under a
  low-priority `task_type` and **degrades gracefully** if no provider is free — it
  is optional intelligence, never required for the broker to function.
- **Ambiguous tuning/routing judgement** when threshold rules are inconclusive.

**The only thing surfaced to a human** is what a human alone can fix:
`Optimizer.alerts()` returns the rare actionable items — *the whole pool is
under-provisioned for your request rate*, *this API key looks dead* — not a feed
of trivia about individual free providers.

**Telemetry backend and what still works.** Two layers act independently:

- **Broker core (always on, no history):** the reactive 429/503 cooldown —
  Available↔Waiting, live `call_later` re-enqueue — runs regardless of telemetry
  backend. It reacts to live responses, not to stored history.
- **Optimizer (learned):** delay tuning, the Offline→Probing→Active recovery, and
  per-`task_type` routing. It learns from the **live event stream** (in-memory
  rolling aggregates), so it is **not** gated on a queryable backend — with
  `Telemetry.log()`/`none()` it simply boots **cold** and learns from live traffic.
  A **queryable** backend (`sqlite`/`jsonl`/`postgres`) is an accelerator, not a
  gate: it warm-starts those aggregates after a restart and enables ad-hoc
  analysis. This is why `task_type` (and tokens/quality) are captured from P1 —
  you cannot warm-start or back-fill data you never recorded.

---

## Shipped batteries

| Port | Batteries | Phase |
|---|---|---|
| `Registry` | `list`, `toml`, `sqlite`, `postgres`, `mongodb` | list/toml/sqlite: P1 · pg/mongo: P3 |
| `Secrets` | `env` (default), `dict`, callable adapter | P1 |
| `Coordination` | **opt-in, cluster-only** (default = absent, internal in-memory): `redis`, `postgres`, `mongodb` | seam: P1 · backends: P3 |
| `Telemetry` | `log` (default), `none`, `jsonl`, `sqlite`, `postgres`, `mongodb` | log/none/jsonl/sqlite: P1 · pg/mongo: P3 |

Composition is explicit; there is **no `from_sqlite`-style fused factory** (it
would hide the storage choice, the explicit import step, and the coordination/
telemetry wiring). The constructor + the per-backend classmethods are the whole API:

```python
LLMBroker(
    registry=Registry.sqlite("broker.db"),   # populate separately via import_from / import_if_empty
    coordination=Coordination.redis("redis://..."),   # omit for single process
    telemetry=Telemetry.sqlite("broker.db"),
)
```

### SQLite batteries own their schema

The `sqlite` batteries self-manage their tables via `ensure_schema(db)`
(idempotent, `CREATE TABLE IF NOT EXISTS`): `Registry.sqlite` owns the config
table `llmbroker_providers`, `Telemetry.sqlite` owns `llmbroker_call_log`. The
`call_log` schema gains three nullable columns — `prompt_tokens`,
`completion_tokens`, `quality_score` — so the Optimizer has token and quality
history from day one (see the `CallEvent` rationale in "Ports"). Because
`ensure_schema` only ever `CREATE TABLE IF NOT EXISTS` and never `ALTER`, an
**existing** dinary DB will not pick those columns up on its own; dinary ships a
new migration (**`0007`** — the next free number after `0006_category_templates`;
verify the highest number in `src/dinary/db/migrations/` at implementation time, as
more may land first) that `ALTER TABLE llmbroker_call_log ADD COLUMN` for the
three, so the yoyo-built and `ensure_schema`-built schemas stay equivalent and the
schema-equivalence test covers them. On a fresh host DB `ensure_schema` bootstraps
the full shape from scratch. The
`rate_limited_until` / `execution_fail_count` columns in dinary's table are
**legacy** — `Registry.sqlite`'s config schema defines neither (live state is
in-memory now) and tolerates them as extra columns. `aiosqlite` is imported only
inside the sqlite batteries — importing
`llmbroker` core never drags in a DB driver. With a future `pyproject.toml`,
each backend becomes an optional extra (`llmbroker[sqlite]`, `llmbroker[redis]`,
`llmbroker[postgres]`, …).

---

## Implementation phases

### Phase 1 — extraction + core architecture (do now)

Create `src/llmbroker/` with the broker core, the ports, and the
`list`/`toml`/`sqlite` registry + `Secrets.env`/`dict` + internal in-memory
provider state + `log`/`none`/`jsonl`/`sqlite` telemetry batteries — enough to
serve Rung 0/1 and carry dinary with unchanged request-path behavior. The
`Coordination` port (the cluster seam) is defined in P1; its backends land in P3.
Also capture the Optimizer's future inputs on every call — `task_type`
(`execute`/`complete`), `prompt_tokens`/`completion_tokens` (from the response
`usage`), and `quality_score` (`mark_failed` → 0.0) into `CallEvent` — so the
data exists before the `Optimizer` control loop, which itself lands in Phase 4.

```
src/llmbroker/
  __init__.py            # public API: LLMBroker, Registry, Secrets, Coordination, Telemetry,
                         #             ProviderConfig, ProviderHealth, CallEvent, Execution
  chat.py                # from adapters/llm_chat.py — ProviderConfig moves to models.py;
                         #             response parsing also surfaces `usage` tokens for CallEvent; else verbatim
  broker.py              # from adapters/llmbroker.py — ports renamed, internal state + Coordination reconcile,
                         #             complete(), tokens/quality_score into CallEvent
  models.py              # ProviderConfig (config only — rate_limited_until moves to ProviderHealth),
                         #             ProviderState, ProviderHealth (full state machine),
                         #             CallEvent (task_type + prompt/completion tokens + quality_score)
  state.py               # private in-memory per-provider live state (always-on; not a public port)
  schema.py              # ensure_schema for sqlite batteries (config + call-log tables)
  registry/
    __init__.py          # Registry Protocol + list/in-memory wrapper
    toml.py              # Registry.toml  (reads providers + resolves api_key_ref)
    sqlite.py            # Registry.sqlite (config columns; admin CRUD; import_from/import_if_empty)
  secrets.py             # Secrets Protocol, Secrets.env() (default), Secrets.dict(), callable adapter
  coordination.py        # Coordination Protocol (cluster seam; redis/postgres/mongodb backends in P3)
  telemetry/
    __init__.py          # Telemetry Protocol
    log.py               # Telemetry.log() (default), Telemetry.none()
    jsonl.py             # Telemetry.jsonl()
    sqlite.py            # Telemetry.sqlite() (llmbroker_call_log)
  cli.py                 # python -m llmbroker env-template <toml> | import <toml> --into ... --on-conflict ...
  data/
    providers.example.toml
    .env.example
```

```
tests/llmbroker/         # must NOT import dinary.*
  test_chat.py
  test_broker.py
  test_registry_toml.py
  test_registry_sqlite.py
  test_secrets.py
  test_state.py
  test_telemetry.py
  test_cli_env_template.py
```

Facts that make P1 low-risk:

- `src/` is already importable (editable install); `import llmbroker` needs **no
  pyproject/build change**. The editable install is a plain `.pth` that puts
  `src/` on `sys.path`, so a new top-level `src/llmbroker/` is importable with no
  reinstall, and deploy runs from source via `uv run` (not a built wheel).
  Caveat for later: `pyproject.toml` has no explicit
  `[tool.hatch.build.targets.wheel]` package list, so if a distributable wheel is
  ever built, `llmbroker` must be added there — not a concern for the
  source-based deploy now, but note it before any packaging work.
- `llmbroker.py` / `llm_chat.py` have **no `dinary.db` imports** today.
- `llm_storage.py`'s tables have **no FK into dinary's schema** — migration
  `0005` replaced the integer `provider_id` FK with a plain `provider_label`
  TEXT; `execution_id` is a bare TEXT correlation id. The only real coupling is
  `SqliteLLMBrokerStorage` reading `dinary.db.storage.DB_PATH` as a global
  instead of a `db_path` argument.

`src/dinary/adapters/llm_storage.py`, `llm_chat.py`, `llmbroker.py` are
**deleted**. The old SQLite/TOML storage split maps onto the new batteries:
SQLite → `Registry.sqlite` + `Telemetry.sqlite`, **no `coordination=`** (live
state stays in the broker's internal memory); TOML → `Registry.toml` +
`Telemetry.log`, no coordination. Per-provider cooldown/fail counts are **no
longer persisted** (internal in-memory now); the old JSON-sidecar fail counter is
dropped. `ProviderConfig` loses `rate_limited_until` (now a `ProviderHealth`
field). The `api_key` columns/fields become `api_key_ref` resolved via `Secrets`.

### Phase 2 — example variants + catalog refresh

Add goal-specific `providers.*.example.toml` variants. Optional: an `inv`/CLI
command to refresh the example set from a documented source (e.g. a prompt
sourced from `https://shir-man.com/free-llm/`) with latency/limits/quality notes.

### Phase 3 — cluster + DB batteries

`Coordination.redis`/`postgres`/`mongodb`; `Registry.postgres`/`mongodb` (with the
optional admin CRUD); `Telemetry.postgres`/`mongodb`. Each behind an optional
dependency extra. Reconcile-via-refresh as specified; pub/sub and precise-timer
left as documented optimizations.

### Phase 4 — the `Optimizer` (autonomous control loop)

The core value, built once telemetry capture (P1) exists. The Optimizer learns
from the **live event stream** (in-memory rolling aggregates at the
`Telemetry.record()` seam), so it runs on any backend; the read surface on
`Telemetry.sqlite`/`jsonl` (and `postgres` from P3) is for **warm-start after a
restart and ad-hoc analysis**, not a precondition. Add a pluggable **selection
policy** seam to the broker (default round-robin). Build the background `Optimizer`
that: computes per-(provider, task_type) stats; auto-tunes cooldowns/delays and
runs the offline→probe→active recovery (the state model in "Autonomous
optimization"); maintains a per-`task_type` routing ranking the broker selection
consults; and exposes `alerts()` for the human-only items (under-provisioned, dead
key). Selection strategy: first 0-wait provider, else minimal remaining wait —
biased by the routing ranking. Default-on; with `Telemetry.log()`/`none()` it boots
cold (no warm-start) and the broker keeps its reactive round-robin cooldown until
the Optimizer has learned from live traffic.

### Phase 5 — LLM-in-the-loop deepening (future, not scheduled here)

The Optimizer's *optional* use of an LLM: LLM-as-judge quality scoring on sampled
outputs per (provider, task_type) to close the quality loop without host
`mark_failed`, and LLM judgement for ambiguous tuning/routing. Always sampled,
off the hot path, dogfooded through the broker under a low-priority `task_type`,
and gracefully skipped when no provider is free. Plus richer fail statistics
(API-key-expiration diagnostics) and per-provider Initial/Min/Max delay tuning.

---

## dinary wiring (Phase 1)

dinary is single-process, so it uses explicit composition over its one SQLite
file (`storage.DB_PATH`) for **config + telemetry only**; no `coordination=`
(live state stays in the broker's internal memory). The provider table is
populated by an explicit `import_if_empty` during startup bootstrap (next to
`bootstrap_categories`), not by a constructor side-effect — so a fresh deploy
auto-fills once, and hand-edits or deletions in the table are never clobbered on
later restarts.

```python
# src/dinary/main.py
from llmbroker import LLMBroker, Registry, Telemetry
...
registry = Registry.sqlite(storage.DB_PATH)
broker = LLMBroker(
    registry=registry,
    telemetry=Telemetry.sqlite(storage.DB_PATH),
    # no coordination= — dinary runs one process, live state stays in memory
)

# in the async startup bootstrap (alongside bootstrap_categories):
await registry.import_if_empty(Registry.toml(_LLM_PROVIDERS_TOML))
```

Pulling an updated `.deploy/llm_providers.toml` into an existing DB is then a
deliberate op (`import_from(..., on_conflict="update")` via an `inv` task), never
automatic.

**Deliberate behavior change:** per-provider `rate_limited_until` /
`execution_fail_count` are no longer written to `llmbroker_providers` (live state
is internal in-memory now), so the columns would go stale. **Decided:** dinary
adds a small read-only endpoint exposing the broker's in-memory live state
(`broker.provider_health()`), and `api/controllers/llm.py:llm_status()` is changed
to **overlay** live cooldown/fail from it onto the per-provider rows instead of
reading the now-stale `rate_limited_until`/`execution_fail_count` columns. This is
the one bounded read-path change in dinary's admin (everything else — CRUD,
`used_today`/`last_status` aggregation over `llmbroker_call_log` — is untouched).
The two columns remain in the schema as legacy; nothing writes them. The webapp
admin LLM page keeps its existing shape: it consumes the same `llm_status` payload
keys (`rate_limited_until`, `execution_fail_count`), now fed from live state, so no
frontend change is required.

`_DEPLOY_DIR`/`_LLM_PROVIDERS_TOML` move next to the existing `_PROJECT_ROOT` in
`main.py`. dinary's `.deploy/llm_providers.toml` gains `api_key_ref` fields and
its keys move to env / the deploy secret store (a migration note for ops).

**Schema migration `0007`** (next free number after `0006_category_templates` —
confirm at implementation time)**:** add `prompt_tokens`/`completion_tokens`/`quality_score`
(all nullable) to `llmbroker_call_log` so dinary's existing DB matches the
`Telemetry.sqlite` `ensure_schema` shape and starts capturing the Optimizer's
token/quality history. It rides the existing migrations deploy machinery
(`tasks/deploy.py` already ships `src/dinary/db/migrations/`), so no deploy change.

| File | Change |
|---|---|
| `src/dinary/background/classification/task.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import LLMBroker` |
| `src/dinary/background/classification/store_resolver.py` | same |
| `src/dinary/background/classification/receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Execution, LLMBroker`; pass `task_type="receipt_classification"` to `execute()` so the Optimizer can tune/route per task type |
| `src/dinary_analytics/llm.py` | `from dinary.adapters.llm_chat import (...)` → `from llmbroker import (...)` |
| `tasks/receipt.py` | `LLMBroker(TomlLLMBrokerStorage())` → `LLMBroker(registry=Registry.toml(_PROVIDERS_TOML))` with `_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"` |

After: `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tests/ tasks/` returns nothing.

---

## Tests (Phase 1)

`tests/llmbroker/` must not import `dinary.*`. Port the existing suites:

- `tests/services/test_llm_chat.py` → `tests/llmbroker/test_chat.py`
  (`patch("dinary.adapters.llm_chat.httpx.Client")` → `patch("llmbroker.chat.httpx.Client")`).
- `tests/services/test_llmbroker.py` → `tests/llmbroker/test_broker.py`.
- `tests/services/test_llm_storage.py` splits into `test_registry_toml.py`,
  `test_registry_sqlite.py`, `test_telemetry.py` (and `test_state.py`),
  each adapting `SqliteLLMBrokerStorage()` → the new battery with an explicit
  `db_path = tmp_path / "test.db"` and `ensure_schema` (no yoyo migrations in
  package tests). The `_label_from_base_url` logic stays with `Registry.toml`.
  The old SQLite cooldown/fail-count persistence tests are dropped (live state is
  internal in-memory); cooldown/fail behavior is covered by `test_state.py`.
- New `test_secrets.py`: `Secrets.env()` resolves from `os.environ`;
  `Secrets.dict()` from a map; `api_key_ref` materializes into
  `ProviderConfig.api_key`; missing ref raises a clear error.
- New `test_state.py`: the broker's internal live state — cooling provider absent
  from its snapshot until cooldown passes; idempotent records.
- New `test_cli_env_template.py`: scanning a TOML emits the expected `.env`
  skeleton (all `api_key_ref` names, blank values, no secrets).
- `test_broker.py` / `test_telemetry.py` assert that `task_type`,
  `prompt_tokens`/`completion_tokens` (from a stubbed response `usage`), and
  `quality_score` flow into the recorded `CallEvent`: `task_type` from
  `execute`/`complete`, tokens parsed from the response, and `quality_score=0.0`
  emitted by `Execution.mark_failed()` (with `status` still `"ok"`).
- **Schema-equivalence test** stays dinary-side (`tests/services/`, needs both
  `dinary.db.db_migrations` and `llmbroker`): yoyo-built vs `ensure_schema`-built
  DBs agree on `PRAGMA table_info(llmbroker_providers|llmbroker_call_log)`
  (names, types, defaults, notnull; ignore column order). **Ignore the legacy
  `rate_limited_until`/`execution_fail_count` columns** — `ensure_schema`
  deliberately omits them (live state is in-memory now) while the yoyo schema
  still has them, so the comparison must exclude those two or it will never
  agree. Conversely, with the new call-log migration applied, the new
  `prompt_tokens`/`completion_tokens`/`quality_score` columns are present on both
  sides and **must** match.
- `tests/api/test_admin_llm.py`: update for the `llm_status` change — assert that
  `rate_limited_until`/`execution_fail_count` in the payload now reflect the
  broker's live `provider_health()` (not the stale columns), and add coverage for
  the new read-only `provider_health()` endpoint. The existing assertion that
  `execution_fail_count` is present in each provider entry stays.
- Mechanical import updates in dinary-side tests referencing the broker:
  `test_main.py`, `test_store_resolver.py`, `test_receipt_classifier.py`,
  `test_receipt_classification.py`, `test_receipt_pipeline_e2e.py`,
  `test_receipt_drain.py`, `test_receipt_pipeline.py`, `test_llm.py`,
  `tests/conftest.py` (the `NullStorage`/`real_llm_seed` fixtures keep their
  logic; the fixture that pre-populated providers now calls
  `Registry.sqlite(...).import_from(...)` explicitly instead of relying on
  constructor seeding).
- `tests/api/test_api_delete_receipt.py` references only the `llmbroker_call_log`
  table name — no import change.
- New `test_registry_sqlite.py` covers `import_from` policies (`skip` leaves
  existing rows; `update` upserts; `replace` wipes-then-inserts) and
  `import_if_empty` (fills an empty store, no-ops on a populated one).

Every new battery, the `Secrets` resolvers, `complete()`, the import operations,
and the `env-template`/`import` CLI ship with tests in the phase that introduces
them.

---

## Specs (Phase 1)

- `specs/reference/llm-providers.md`: trim to dinary-specific concerns (provider
  pool rationale, prompt design, models to avoid). Remove broker-internals
  sections (queue round-robin, storage Protocol, …). Add one paragraph: dinary
  runs `llmbroker` via explicit `Registry.sqlite` + `Telemetry.sqlite` over
  `storage.DB_PATH` (no `coordination=` — one process, live state in memory), with
  providers imported (once, if empty) from `.deploy/llm_providers.toml`, keys via
  `api_key_ref` + env; the sqlite
  batteries own `llmbroker_providers`/`llmbroker_call_log` (`ensure_schema`);
  migrations `0004`/`0005` created the tables historically, a new migration adds
  the `prompt_tokens`/`completion_tokens`/`quality_score` call-log columns, and the
  `rate_limited_until`/`execution_fail_count` columns are now legacy/unused. Per
  spec rules, do not link the package README (specs link only specs).
- `specs/reference/architecture.md`: add `src/llmbroker/` to the source layout —
  "standalone, host-agnostic LLM-provider broker; round-robin failover,
  rate-limit handling; pluggable `Registry`/`Secrets`/`Telemetry` + opt-in
  `Coordination` for clusters; batteries for TOML/SQLite/Postgres/redis/MongoDB;
  no `dinary` imports; will move to its own repo/PyPI package."

---

## Package README (`src/llmbroker/README.md`)

The Rung 0→2 ladder above is the README. It records current capabilities
(round-robin queue, one in-flight request per provider, per-provider 429/503
cooldown honoring `Retry-After`, pluggable `Registry`/`Secrets`/`Telemetry` plus
opt-in `Coordination` for clusters, `Secrets` indirection so no key lives in
config, `task_type`-tagged telemetry) and the
`Optimizer` roadmap (autonomous self-tuning + task routing; optional LLM-in-the-
loop quality judging). It states plainly that `llmbroker` is a **library, not a
server** — wrap it in your own web framework if you need an HTTP gateway.
Distribution name deferred (`circuit-ai`, `llm-hydra`, `llm-router`, … — decide at
publish); the import name stays `llmbroker`.

---

## Verification

1. `uv run inv pre` → "All checks passed!" + `0 errors`.
2. `uv run pytest` → all green, incl. `tests/llmbroker/`.
3. `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tests/ tasks/` → empty.
4. `uv run python -c "import llmbroker; print(llmbroker.Registry.toml, llmbroker.Coordination, llmbroker.Secrets.env)"`.
5. `uv run python -m llmbroker env-template src/llmbroker/data/providers.example.toml` prints a `.env` skeleton.
6. Smoke: `uv run inv dev` starts; on a fresh DB `import_if_empty` fills
   `llmbroker_providers`; on a second start it no-ops; admin LLM API still reads
   `llmbroker_providers`/`llmbroker_call_log` (`ensure_schema` no-ops on the
   existing DB).

---

## Open design questions (decide when the phase needs them)

- **Single-process state durability** (P1): we drop all single-process live-state
  persistence (the old SQLite columns + the TOML JSON sidecar) on the principle
  that ephemeral cooldown is not worth persisting. The Optimizer's learned state
  is a live in-memory aggregate of the event stream (see "Autonomous
  optimization"); whether it is checkpointed to its own table for a fast warm
  start or simply recomputed from the journal is a **P4 decision**, deferred. The
  P1 invariant is only that the append-only `CallEvent` journal stays rich enough
  to reconstruct it. If a real need for restart-resilient cooldown on one node ever
  appears, add an opt-in file-backed state — not shipped now.
- **Example-file variants** (P2): how many goal-specific TOMLs, and the
  refresh-from-source workflow / format of latency/limits/quality notes.
- **`Coordination` write semantics under concurrency** (P3): partial per-field
  writes vs whole-snapshot; consistency model for concurrent `record_*` across
  nodes.
- **Optimizer design** (P4): the read API on queryable telemetry (warm-start +
  ad-hoc analysis); whether the in-memory aggregate is checkpointed to its own
  table or recomputed from the journal on start; the broker's selection-policy
  seam; how the routing ranking is computed and how aggressively it overrides
  round-robin.
- **LLM-in-the-loop cost/safety** (P5): sampling rate for LLM-as-judge quality
  scoring; the judge prompt/rubric per task type; guarding token spend; how the
  judge avoids starving real traffic on a busy pool. When the judge lands, add a
  `quality_source` column to `CallEvent` (host `mark_failed` = ground truth vs
  judge = noisier) so the router can weight the two by confidence; pre-P5 rows are
  all host-sourced, so nothing is lost by deferring it.
- **Per-provider Initial/Min/Max delay** (P5): individual vs one global; computed
  vs fixed KISS schedule (lean KISS first).
- **Dinary admin path** (later): dinary still reads/writes the two tables with raw
  SQL, so their shape is a de-facto contract — the package is not yet free to
  change it unilaterally. Routing dinary's admin through `Registry`'s admin
  surface (schema private to the package) is future work, not in this plan.

---

## Explicitly out of scope (this plan)

- Giving `src/llmbroker/` its own `pyproject.toml` / separate repo / PyPI publish.
- **Any HTTP / server layer.** `llmbroker` is a library; a microservice gateway
  is a host concern, built on the host's own web framework.
- The `Optimizer` itself (P4) and its LLM-in-the-loop deepening (P5) — only the
  `task_type` data capture and the selection-policy seam are designed now.
- Renaming the import name `llmbroker`.
- Reworking dinary's admin API surface (`api/controllers/llm.py`, `api/llm.py`) —
  CRUD and the call-log aggregation keep talking to the two tables via dinary's own
  `db.storage.transaction()`; only the schema's *owner* changes, not its shape. Two
  bounded, decided exceptions (see "dinary wiring"): a small read-only endpoint
  surfacing `broker.provider_health()`, and `llm_status()` overlaying live
  cooldown/fail from it in place of the now-stale columns. Routing CRUD through
  `Registry`'s admin surface remains out of scope.
