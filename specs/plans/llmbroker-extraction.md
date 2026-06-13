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
  optimizer reads telemetry (per provider *and per operation*) and **acts**:
  auto-adjusts cooldowns/delays, offlines and re-probes bad providers, and routes
  each operation to the providers that empirically handle it best. The goal is
  "it just works" — not a feed of advice about free providers the user will never
  read. A human is bothered only by what only a human can fix (pool
  under-provisioned, API key dead).

There is **no goal to minimize the diff**. We rename and reshape freely to reach
the ideal API; dinary becomes just one more caller.

---

## Trajectory — vendored now, standalone PyPI package right after Phase 1

`llmbroker` lives inside dinary's `src/` **only as a staging area**. The PyPI
distribution name is **already reserved**. The moment Phase 1 lands and deploys
cleanly, the package is git-extracted into its **own repository**, given its own
`pyproject.toml`, published to PyPI, and from then on **developed and versioned
independently** of dinary — dinary consumes it as an ordinary pinned dependency,
not as in-tree source.

Every Phase-1 rule that feels strict exists to make that extraction a
non-event: **zero `dinary.*` imports**, tests under `tests/llmbroker/` that never
touch `dinary.*`, the package owning its own DB schema via `ensure_schema`, and
the `llmbroker_` table prefix + host-migration coexistence hooks (see "Coexisting
with host migration tools") so the package can evolve its tables on its own
release cadence without ever editing a dinary migration. Treat anything that
would couple the package back to dinary as a Phase-1 defect.

---

## The mental model

A host wires up to four things; only the first is mandatory, the rest have
working defaults:

| Concept | Port | Required? | Default battery | What it is |
|---|---|---|---|---|
| **providers** | `Registry` | **yes** (or a `list[ProviderConfig]`) | — | where the LLM-provider configuration is stored / loaded |
| **secrets** | `Secrets` | no | `Secrets.env()` | how `api_key_ref` references resolve to real keys |
| **shared state** | `SharedState` | no — **opt-in, cluster only** | none (single process keeps state in-memory internally) | cross-instance sync of per-provider live state (cooldown, fail count, offline) — supply it only to make several `llmbroker` copies agree |
| **telemetry** | `Telemetry` | no | `Telemetry.log()` | append-only journal of calls — to see what happened and decide which providers to keep |

**`SharedState` is opt-in and exists only for clusters.** The broker always
keeps per-provider live state (cooldown/fail/offline) in memory internally — that
is a private detail, not a user-facing port. You pass `shared_state=` *only* to
share that state across several `llmbroker` instances; there is deliberately no
"local" variant, because the absence of the parameter already means "single
process, nothing to coordinate". A database does not call for it — persisting
ephemeral cooldown for one process buys nothing (a stale cooldown after a restart
is worse than re-learning from a live 429). So the "DB" axis is purely `Registry`
(config) + `Telemetry` (log); `SharedState` is orthogonal and only about
multi-instance sync.

Names are bare (no `Provider` prefix) because the package namespace already
supplies context — the `httpx.Client` / `sqlalchemy.Engine` idiom. Accessed as
`llmbroker.Registry`, `llmbroker.SharedState`, `llmbroker.Telemetry`,
`llmbroker.Secrets`. `Telemetry` default is `log()` (Python `logging`) so call
data is never silently lost; `Telemetry.none()` is the explicit opt-out.

Dataclasses keep descriptive names so short ports pair with clear data:

| Port | Reads / writes |
|---|---|
| `Registry.load()` | `list[ProviderConfig]` |
| `SharedState.snapshot()` | `dict[str, ProviderHealth]` |
| `Telemetry.record(call)` | `Call` |
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

   llm = llmbroker.Broker(registry=llmbroker.Registry.toml("providers.toml"))
   reply = (await llm.ask("Summarize this receipt: ...", operation="summary")).text
   ```

State is in-memory, telemetry goes to the log, keys come from env. Nothing to
implement, no secret in source. `ask` is the simplest call — it wraps a bare
string as one user message (`chat` is the full messages API). When every
provider is momentarily busy it raises `NoProviderAvailable`; the README example
shows handling that.

### Rung 1 — "if you have a database"

Persist provider config and telemetry, build an admin UI on the DB table.
Connecting to the store and **populating** it are separate steps (see "Seeding a
DB store" — the constructor never auto-seeds). The one-line sugar
`import_if_empty` covers the common "fill on first run" case. **`shared_state` is
not part of this** — a single process keeps cooldown state in memory internally;
there is nothing to share.

```python
registry = await llmbroker.Registry.sqlite("broker.db").import_if_empty(
    llmbroker.Registry.toml("providers.toml"),
)
llm = llmbroker.Broker(
    registry=registry,
    telemetry=llmbroker.Telemetry.sqlite("broker.db"),
    # no shared_state= → single process (Rung 2 adds it for clusters)
)
```

A host that hand-manages the DB just never imports; a host that wants our updated
catalog re-runs an explicit `import_from(..., on_conflict="update")`.

### Rung 2 — "if you run a cluster"

Add `shared_state=`; the instances then agree automatically (shared cooldown,
shared fail counts). Nothing else changes:

```python
shared_state=llmbroker.SharedState.redis("redis://...")   # or .postgres(dsn) / .mongodb(uri)
```

The broker core is **never cluster-aware** — clustering lives entirely inside the
`SharedState` implementation (see "Cluster coordination"). Omit `shared_state=`
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


class CallStatus(Enum):
    OK = "ok"                       # HTTP 200 — quality is judged separately via quality_score
    RATE_LIMITED = "rate_limited"   # 429
    UNAVAILABLE = "unavailable"     # 503
    ERROR = "error"                 # any other transport/protocol failure


@dataclass(frozen=True, slots=True)
class Call:
    provider: str
    operation: str | None
    trace_id: str | None
    status: CallStatus                   # coarse transport outcome — the axis routing reacts to
    http_status: int | None = None       # exact code (500/timeout → None); captured now, unrecoverable later
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
# in-memory internally; a SharedState backend mirrors/syncs it across instances.
class SharedState(Protocol):
    async def snapshot(self) -> dict[str, ProviderHealth]: ...
    async def mark_rate_limited(self, label: str, until: datetime) -> None: ...
    async def mark_failure(self, label: str) -> None: ...
    async def mark_success(self, label: str) -> None: ...


class Telemetry(Protocol):
    async def record(self, call: Call) -> None: ...
    # Optional read/aggregation surface (queryable batteries — sqlite/jsonl/postgres —
    # implement it; log()/none() do not). Powers a host admin UI AND the Optimizer
    # warm-start, so neither needs raw SQL:
    # async def provider_stats(self, *, since: datetime) -> dict[str, ProviderStats]: ...
    # async def recent(self, *, limit: int) -> list[Call]: ...
    # async def purge(self, *, trace_id: str) -> None: ...  # cascade delete by correlation id
```

- **The schema is private; the API is the public contract.** No host issues raw
  SQL against `llmbroker_providers`/`llmbroker_calls` — config goes through the
  `Registry` admin surface, live state through `llm.provider_health()`, and
  call-log aggregation/cleanup through the `Telemetry` read surface above. This is
  what lets the package own and evolve its schema independently after extraction;
  a host admin UI is built entirely on typed methods, and it works identically over
  any backend (sqlite/postgres/mongodb), which a fixed table shape never could.
- **Only `Registry.load()` is mandatory** for a custom backend; the admin
  methods (`add`/`update`/`remove`) are an optional mixin the **host admin UI**
  drives, never the broker. The queryable `Telemetry` read surface
  (`provider_stats`/`recent`/`purge`) is the analogous optional mixin for the
  call-log side; `log()`/`none()` omit it.
- `ProviderStats` is a small read-model dataclass for admin aggregates
  (per-provider `call_count` over the window, `last_status`, `last_at`) — derived
  from `Call` rows, never a stored table of its own.
- `SharedState` is **optional and cluster-only** — omit `shared_state=` and the
  broker uses its private in-memory state. There is no public "in-memory
  SharedState" object; single-process is the absence of the parameter.
- `mark_success` is a no-op for current backends; it exists so the roadmap
  `Optimizer` (delay decrease, offline→probe→active) can hang off the same
  interface without a breaking change.
- `ProviderHealth.state` models the **full** Optimizer state machine
  (Available/Waiting/Offline/Probing) from day one. P1 only ever sets
  Available/Waiting (429/503 cooldown); Offline/Probing are populated by the
  Optimizer (P4). The field is locked into the contract now so the P3
  `SharedState` backends (redis/postgres/mongodb) sync it **without** a later
  Protocol-breaking change — a node writes the whole `ProviderHealth` (state
  included), so optimizer-driven transitions propagate over `snapshot()` with no
  new method.
- `Call` captures `prompt_tokens`/`completion_tokens` (objective, read from
  the response `usage` when present) and `quality_score` from P1 because
  telemetry is **append-only** — a column added later starts with no history,
  which is exactly the data the Optimizer needs. `quality_score` is **orthogonal
  to `status`**: `status` is the transport outcome (an HTTP-200 answer is
  `status=CallStatus.OK`), `quality_score` is whether that answer was usable. **Cost is
  deliberately not stored** — it is `tokens × a price table`, a host/Optimizer
  concern derived later from the tokens, not a raw signal to journal. The
  **source** of `quality_score` (host `score()` vs the P5 LLM-judge) is **not**
  a separate column in P1: until the judge exists every score is a host
  `score()` ground truth, so pre-judge rows are unambiguous and a
  `quality_source` column can be added with the judge (P5) with no lost history —
  unlike `tokens`/`quality_score` themselves, whose per-row values are
  unrecoverable if not captured now.
- `Broker(registry=...)` accepts a `Registry` **or** a `list[ProviderConfig]`
  (wrapped as a read-only in-memory registry; `Registry.of([...])` makes that
  explicit). Same for any `import_from` source. The kwarg matches the type, like
  `secrets=Secrets…`, `shared_state=SharedState…`, `telemetry=Telemetry…`.
- **Two entry points, each with one clean type — no polymorphic parameter.**
  `chat` is the full API and always takes a chat messages array; `ask` is a thin
  convenience for the dominant single-user-turn case. Both return a `Result`
  handle exposing `.text`, `.usage`, and `.score(...)`:
  ```python
  async def chat(
      messages: list[dict],          # full chat array; each message carries its own role
      *,
      model: str | None = None,      # per-call override of ProviderConfig.model
      operation: str | None = None,
      trace_id: str | None = None,
      **provider_params,             # temperature, tools, response_format… → into the request body as-is
  ) -> Result

  async def ask(prompt: str, **kw) -> Result
      # sugar: chat([{"role": "user", "content": prompt}], **kw)
  ```
  Rung 0 is `llm.ask("Summarize …")`; anything beyond one user turn (system
  prompt, multi-turn history, assistant context) goes through `chat(messages)`.
  Keeping `messages` a single honest type avoids the `str | list` chameleon — the
  convenience lives in a separate, unambiguous method, not in an overloaded arg.
- Both `ask` and `chat` take an opaque `trace_id` (correlation) and a
  `operation: str | None` (a host-defined category — e.g. `"receipt_classification"`,
  `"summary"`). `operation` is what lets the `Optimizer` tune and route per operation,
  so it is captured from day one even though the Optimizer is built later.
- **`ask`/`chat` raise rather than returning a sentinel.** A `BrokerError` hierarchy —
  `NoProviderAvailable` (every provider in cooldown, nothing to call) and
  `AllProvidersFailed` (each provider was tried and errored) — replaces a
  `str | None` return, so "no capacity" is never confused with an empty answer and
  callers distinguish "retry later" from "all dead".

`Result.score(value: float)` (quality feedback on an HTTP-200 but imperfect
answer) records the score into the broker's live state (mirrored to `SharedState`
if present) and emits a `Call` to telemetry with `status=CallStatus.OK` but
`quality_score=value` — the call succeeded at the transport layer, the answer is
judged separately, so quality is attributed apart from the HTTP outcome. A host
marks an unusable answer with `score(0.0)`; the P5 LLM-judge reuses the **same**
method to fill sampled non-binary scores, so there is one write path into
`quality_score`.
`Call` carries `operation` alongside `trace_id`, so quality, tokens, and
latency can all be attributed per (provider, operation).

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

## Cluster coordination — how `SharedState` meets the in-memory queue

The broker keeps its single-process machinery: one `asyncio.Queue` slot per
provider, at most one in-flight request per provider, `loop.call_later`
re-enqueue after a 429 cooldown, and its **private in-memory** per-provider live
state. `SharedState`, when supplied, layers on without the core knowing whether
it is clustered:

- **On 429/503:** the broker updates its in-memory cooldown **and** schedules its
  own local `call_later` re-enqueue; if `shared_state=` is set it also
  `mark_rate_limited(...)` so *other* nodes learn.
- **On the refresh tick** (existing `_run_refresh`, now tightened/configurable):
  with `SharedState`, the broker calls `snapshot()` and reconciles its local
  queue against shared state — dropping providers other nodes marked
  cooling/offline, re-adding those whose cooldown passed. **Clustering rides on the
  refresh loop that already exists.**
- **No `shared_state=` (default):** everything stays in the process's own memory —
  behavior identical to today (local `call_later`), zero infra, zero races.
- **Shared backends** (`SharedState.redis`/`postgres`/`mongodb`) exist **only for
  clusters**: `snapshot()` reads shared state; bounded races (two nodes briefly
  both see a provider free) cost at most one redundant 429. There is no `sqlite`
  `SharedState` — SQLite is not a cross-node store, and single-process needs no
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
llm = llmbroker.Broker(
    registry=Registry.sqlite("broker.db"),
    telemetry=Telemetry.sqlite("broker.db"),   # queryable → warm-start + analysis (optimizer runs on any backend)
    optimize=True,                              # default-on; learns from the live event stream
)
```

**The Optimizer's working state is a live in-memory aggregate, not journal data.**
It feeds off the **live event stream** — every `Telemetry.record(call)` updates
rolling per-(provider, operation) stats in memory (the Optimizer interposes at the
`record()` seam, e.g. as a `Telemetry` decorator, so this works with *any* backend
including `log()`/`none()`). The append-only journal (`Call` rows) stays the
durable source of truth; the Optimizer's rankings/tuning are a derived projection
of it. That projection **may** be checkpointed to its **own** table for a fast warm
start — but is never written back into the append-only `llmbroker_calls` (mixing a mutable
projection into an event log is a category error). Whether to checkpoint or simply
recompute from the journal on start is a **P4 open question**, not a P1 lock. Either
way, `Call` must be rich from day one: a column added later starts with no
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

- **Operation routing** — bias selection of each `operation` toward the providers that
  empirically handle it best. The policy is **tiered / lexicographic, not a
  weighted-sum scalar** (`w·quality + w·latency + w·cost` is untunable — the terms
  are not commensurable, and a latency win must never "buy back" a quality loss):
  1. **Availability gate** — candidates are providers not in cooldown (the FSM
     already drops Waiting/Offline); residual flakiness is a soft tiebreak.
  2. **Quality floor gate** — drop providers whose per-`operation` usable-rate is
     below a floor. Quality is a gate, not a tradeable term.
  3. **Objective ranking — the objective lives with the `operation`.** A
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
  the Optimizer swaps in the per-`operation` ranking it maintains from telemetry.
  Concrete thresholds and the bandit flavor are a P4 open question; the tiering and
  the per-`operation`-objective principle are the decided shape.
- **Pool hygiene** — automatically deprioritize/retire consistently-useless
  providers. Nothing for a human to read.

**What it may use an LLM for** (optional, sampled, never on the hot path):

- **Quality judging** — sample outputs per (provider, operation) and score them
  with an LLM-as-judge, closing the quality loop *without* the host having to call
  `score()`. The judge call goes through the broker itself (dogfooding) under a
  low-priority `operation` and **degrades gracefully** if no provider is free — it
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
  per-`operation` routing. It learns from the **live event stream** (in-memory
  rolling aggregates), so it is **not** gated on a queryable backend — with
  `Telemetry.log()`/`none()` it simply boots **cold** and learns from live traffic.
  A **queryable** backend (`sqlite`/`jsonl`/`postgres`) is an accelerator, not a
  gate: it warm-starts those aggregates after a restart and enables ad-hoc
  analysis. This is why `operation` (and tokens/quality) are captured from P1 —
  you cannot warm-start or back-fill data you never recorded.

---

## Shipped batteries

| Port | Batteries | Phase |
|---|---|---|
| `Registry` | `list`, `toml`, `sqlite`, `postgres`, `mongodb` | list/toml/sqlite: P1 · pg/mongo: P3 |
| `Secrets` | `env` (default), `dict`, callable adapter | P1 |
| `SharedState` | **opt-in, cluster-only** (default = absent, internal in-memory): `redis`, `postgres`, `mongodb` | seam: P1 · backends: P3 |
| `Telemetry` | `log` (default), `none`, `jsonl`, `sqlite`, `postgres`, `mongodb` | log/none/jsonl/sqlite: P1 · pg/mongo: P3 |

Composition is explicit; there is **no `from_sqlite`-style fused factory** (it
would hide the storage choice, the explicit import step, and the shared-state/
telemetry wiring). The constructor + the per-backend classmethods are the whole API:

```python
Broker(
    registry=Registry.sqlite("broker.db"),   # populate separately via import_from / import_if_empty
    shared_state=SharedState.redis("redis://..."),   # omit for single process
    telemetry=Telemetry.sqlite("broker.db"),
)
```

### SQLite batteries own their schema

The `sqlite` batteries self-manage their tables via `ensure_schema(db)`:
`Registry.sqlite` owns the config table `llmbroker_providers`, `Telemetry.sqlite`
owns `llmbroker_calls`. The `llmbroker_calls` schema includes three nullable columns
— `prompt_tokens`, `completion_tokens`, `quality_score` — so the Optimizer has
token and quality history from day one (see the `Call` rationale in
"Ports"). `ensure_schema` is the **single authority** for the package's schema:
no host migration ever builds, alters, or owns these tables (see "Coexisting with
host migration tools").

**The package maintains its own schema across releases, non-destructively.**
`ensure_schema` is idempotent and **version-aware**: it creates missing tables
and, on a DB whose `llmbroker_*` tables predate the running package version,
applies the package's own **additive, data-preserving** migrations (e.g.
`ALTER TABLE … ADD COLUMN`) — never a drop, never data loss. The schema version
is tracked in an `llmbroker_`-prefixed marker the package owns (a
`llmbroker_schema_version` row / `PRAGMA user_version`), so a future release can
evolve the shape on its own cadence without touching the host's migrations. P1
ships only the initial `CREATE` plus that version marker; the upgrade path is the
seam later releases hang ALTERs off of.

dinary is the **one exception**, and only because of its pre-extraction history.
Its `llmbroker_*` tables were built by yoyo migrations `0004`/`0005` in an older
shape (and carry the legacy `rate_limited_until` / `execution_fail_count`
columns). dinary is the package's single local instance and that table data is
disposable, so dinary's Phase 1 migration simply **drops** those tables and hands
ownership to the package, which rebuilds the current shape via `ensure_schema` on
the next start (see "dinary wiring"). This DROP is a one-off dinary cleanup of
its own pre-extraction tables — **not** the package's general upgrade story,
which is the non-destructive path above. `Registry.sqlite`'s config schema
defines no `rate_limited_until` / `execution_fail_count` columns (live state is
in-memory now), so after the rebuild those legacy columns are gone.

`aiosqlite` is imported only inside the sqlite batteries — importing `llmbroker`
core never drags in a DB driver. With a future `pyproject.toml`, each backend
becomes an optional extra (`llmbroker[sqlite]`, `llmbroker[redis]`,
`llmbroker[postgres]`, …).

---

## Coexisting with host migration tools

`llmbroker` owns its tables — the batteries create and **non-destructively
evolve** them via `ensure_schema` (see "SQLite batteries own their schema"). The
host application almost always runs its **own** migration tool over the **same**
database. Two failure modes follow, and the package must prevent both:

1. **Name collision** — an `llmbroker` object clashing with a host object or a
   migration tool's bookkeeping table.
2. **Ownership fight** — a host autogenerate/diff tool seeing the `llmbroker`
   tables as "unknown" and emitting a `DROP` (or demanding they be modeled in the
   host's schema).

### Rule 1 — every DB object carries the `llmbroker_` prefix

Tables (`llmbroker_providers`, `llmbroker_calls`), the schema-version marker,
**and every index, unique-constraint, and trigger** the batteries create are
named `llmbroker_*`. This makes the package's whole footprint filterable by a
single prefix and collision-safe:

- Django table names are `<app>_<model>` (`auth_user`); `llmbroker_` will not collide.
- It is clear of every tool's bookkeeping table — Alembic `alembic_version`,
  yoyo `_yoyo_*`, Flyway `flyway_schema_history`, Liquibase `databasechangelog`,
  Django `django_migrations`, Aerich `aerich`.

The prefix is a public contract: host operators filter on it, and the Alembic
hook below keys off it.

### Rule 2 — tell the host's tool to leave `llmbroker_*` alone

How depends on the tool's category:

| Host tool | Category | What the host does |
|---|---|---|
| **yoyo, Flyway, Liquibase, Dbmate** | forward-only SQL runners | Nothing to fight — they only run hand-written migrations and never autogenerate. The host simply never writes a migration touching `llmbroker_*`. (dinary's one-time P1 drop migration is the deliberate exception — see "dinary wiring".) |
| **Alembic, Flask-Migrate** | autogenerate (drift) | Pass the shipped `llmbroker.alembic.include_object` hook to `context.configure` so autogenerate skips `llmbroker_*` (Flask-Migrate *is* Alembic). |
| **Aerich** | autogenerate (Tortoise) | Tortoise only manages declared models, so it emits no drop for unmodeled tables; just never model the `llmbroker_*` tables. The prefix keeps Aerich's own `aerich` table clear. |
| **Migra** | schema-diff | `migra` emits diff SQL; exclude `llmbroker_*` statements from the generated script (or diff against a baseline that already contains them). |
| **Prisma Client, Django** | ORM-managed | Each manages only its own models; an unmodeled table is left untouched. Do **not** introspect the `llmbroker_*` tables into the ORM (`inspectdb` / `prisma db pull`); if introspected, mark them unmanaged (`managed = False`) / `@@ignore`. |

### The Alembic hook (shipped, P1)

The package ships a tiny, dependency-free predicate that returns `False` for any
object whose name begins with `llmbroker_`. Hosts wire it into their
`alembic/env.py`:

```python
from llmbroker import alembic

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    include_object=alembic.include_object,   # autogenerate ignores every llmbroker_* object
)
```

If the host already passes its own `include_object`, the two compose (logical
AND — skip when either says skip). The hook imports nothing from Alembic — it
only inspects the object name — so importing `llmbroker` never pulls in a
migration framework. The README documents this snippet and the per-tool table
above as the "running llmbroker alongside your migrations" section.

---

## Implementation phases

### Phase 1 — extraction + core architecture (do now)

Create `src/llmbroker/` with the broker core, the ports, and the
`list`/`toml`/`sqlite` registry + `Secrets.env`/`dict` + internal in-memory
provider state + `log`/`none`/`jsonl`/`sqlite` telemetry batteries — enough to
serve Rung 0/1 and carry dinary with unchanged request-path behavior. The
`SharedState` port (the cluster seam) is defined in P1; its backends land in P3.
Also capture the Optimizer's future inputs on every call — `operation`
(`ask`/`chat`), `prompt_tokens`/`completion_tokens` (from the response
`usage`), and `quality_score` (`score(0.0)` → 0.0) into `Call` — so the
data exists before the `Optimizer` control loop, which itself lands in Phase 4.
P1 also ships the host-coexistence surface: every DB object is `llmbroker_`-
prefixed, `ensure_schema` is version-aware (initial create now; additive
data-preserving ALTERs hang off the version marker in later releases), and
`llmbroker.alembic.include_object` is exported (see "Coexisting with host migration
tools"). Because the DB schema is **private**, P1 also ships the admin API that
replaces raw SQL — the `Registry` admin surface (`add`/`update`/`remove`) and the
queryable `Telemetry` read surface (`provider_stats`/`recent`/`purge`) — and
reworks dinary's admin to consume it. dinary's side gets the one-off `0007` drop
migration that hands schema ownership to the package.

```
src/llmbroker/
  __init__.py            # public API: Broker, Registry, Secrets, SharedState, Telemetry,
                         #             ProviderConfig, ProviderHealth, Call, CallStatus, Result,
                         #             BrokerError/NoProviderAvailable/AllProvidersFailed
  alembic.py             # include_object (llmbroker_-prefix predicate) — host migration-tool coexistence;
                         #             tool-named submodule, accessed as llmbroker.alembic.include_object
  chat.py                # from adapters/llm_chat.py — ProviderConfig moves to models.py;
                         #             response parsing also surfaces `usage` tokens for Call; else verbatim
  broker.py              # from adapters/llmbroker.py — ports renamed, internal state + SharedState reconcile,
                         #             ask() sugar, tokens/quality_score into Call
  models.py              # ProviderConfig (config only — rate_limited_until moves to ProviderHealth),
                         #             ProviderState, ProviderHealth (full state machine),
                         #             Call (operation + prompt/completion tokens + quality_score),
                         #             ProviderStats (admin read-model: call_count/last_status/last_at)
  state.py               # private in-memory per-provider live state (always-on; not a public port)
  schema.py              # ensure_schema for sqlite batteries: version-aware (creates + applies
                         #             additive, data-preserving ALTERs against an llmbroker_-prefixed
                         #             version marker); config + call-log tables, all objects llmbroker_-prefixed
  registry/
    __init__.py          # Registry Protocol + list/in-memory wrapper
    toml.py              # Registry.toml  (reads providers + resolves api_key_ref)
    sqlite.py            # Registry.sqlite (config columns; admin CRUD; import_from/import_if_empty)
  secrets.py             # Secrets Protocol, Secrets.env() (default), Secrets.dict(), callable adapter
  shared_state.py        # SharedState Protocol (cluster seam; redis/postgres/mongodb backends in P3)
  telemetry/
    __init__.py          # Telemetry Protocol (record + optional read surface: provider_stats/recent/purge)
    log.py               # Telemetry.log() (default), Telemetry.none() — record-only, no read surface
    jsonl.py             # Telemetry.jsonl() (record + read surface)
    sqlite.py            # Telemetry.sqlite() (llmbroker_calls; record + read surface: provider_stats/recent/purge)
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
  test_alembic.py
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
SQLite → `Registry.sqlite` + `Telemetry.sqlite`, **no `shared_state=`** (live
state stays in the broker's internal memory); TOML → `Registry.toml` +
`Telemetry.log`, no shared state. Per-provider cooldown/fail counts are **no
longer persisted** (internal in-memory now); the old JSON-sidecar fail counter is
dropped. `ProviderConfig` loses `rate_limited_until` (now a `ProviderHealth`
field). The `api_key` columns/fields become `api_key_ref` resolved via `Secrets`.

### Phase 2 — example variants + catalog refresh

Add goal-specific `providers.*.example.toml` variants. Optional: an `inv`/CLI
command to refresh the example set from a documented source (e.g. a prompt
sourced from `https://shir-man.com/free-llm/`) with latency/limits/quality notes.

### Phase 3 — cluster + DB batteries

`SharedState.redis`/`postgres`/`mongodb`; `Registry.postgres`/`mongodb` (with the
optional admin CRUD); `Telemetry.postgres`/`mongodb`. Each behind an optional
dependency extra. Reconcile-via-refresh as specified; pub/sub and precise-timer
left as documented optimizations.

### Phase 4 — the `Optimizer` (autonomous control loop)

The core value, built once telemetry capture (P1) exists. The Optimizer learns
from the **live event stream** (in-memory rolling aggregates at the
`Telemetry.record()` seam), so it runs on any backend; the **queryable read
surface** (`provider_stats`/`recent` — already shipped in P1 for the admin UI, on
`Telemetry.sqlite`/`jsonl` and `postgres` from P3) is for **warm-start after a
restart and ad-hoc analysis**, not a precondition. The Optimizer reuses that same
read surface rather than introducing its own. Add a pluggable **selection
policy** seam to the broker (default round-robin). Build the background `Optimizer`
that: computes per-(provider, operation) stats; auto-tunes cooldowns/delays and
runs the offline→probe→active recovery (the state model in "Autonomous
optimization"); maintains a per-`operation` routing ranking the broker selection
consults; and exposes `alerts()` for the human-only items (under-provisioned, dead
key). Selection strategy: first 0-wait provider, else minimal remaining wait —
biased by the routing ranking. Default-on; with `Telemetry.log()`/`none()` it boots
cold (no warm-start) and the broker keeps its reactive round-robin cooldown until
the Optimizer has learned from live traffic.

### Phase 5 — LLM-in-the-loop deepening (future, not scheduled here)

The Optimizer's *optional* use of an LLM: LLM-as-judge quality scoring on sampled
outputs per (provider, operation) to close the quality loop without host
`score()`, and LLM judgement for ambiguous tuning/routing. Always sampled,
off the hot path, dogfooded through the broker under a low-priority `operation`,
and gracefully skipped when no provider is free. Plus richer fail statistics
(API-key-expiration diagnostics) and per-provider Initial/Min/Max delay tuning.

---

## dinary wiring (Phase 1)

dinary is single-process, so it uses explicit composition over its one SQLite
file (`storage.DB_PATH`) for **config + telemetry only**; no `shared_state=`
(live state stays in the broker's internal memory). The provider table is
populated by an explicit `import_if_empty` during startup bootstrap (next to
`bootstrap_categories`), not by a constructor side-effect — so a fresh deploy
auto-fills once, and hand-edits or deletions in the table are never clobbered on
later restarts.

```python
# src/dinary/main.py
from llmbroker import Broker, Registry, Telemetry
...
registry = Registry.sqlite(storage.DB_PATH)
llm = Broker(
    registry=registry,
    telemetry=Telemetry.sqlite(storage.DB_PATH),
    # no shared_state= — dinary runs one process, live state stays in memory
)

# in the async startup bootstrap (alongside bootstrap_categories):
await registry.import_if_empty(Registry.toml(_LLM_PROVIDERS_TOML))
```

Pulling an updated `.deploy/llm_providers.toml` into an existing DB is then a
deliberate op (`import_from(..., on_conflict="update")` via an `inv` task), never
automatic.

**Admin goes API-only (decided): dinary issues no raw SQL against
`llmbroker_*`.** The schema is now private to the package (see "Ports"), so
dinary's admin (`api/controllers/llm.py`, `api/llm.py`) is reworked to reach
every piece of data through a typed `llmbroker` API:

- **Provider config / CRUD** → `registry.load()` and the `Registry` admin surface
  (`add`/`update`/`remove`), replacing the raw `db.storage.transaction()` SELECT/
  INSERT/UPDATE/DELETE over `llmbroker_providers`.
- **Live cooldown/fail** → a small read-only endpoint surfacing
  `llm.provider_health()`. The `rate_limited_until`/`execution_fail_count`
  columns are gone after `0007`; live state is the only source.
- **`used_today`/`last_status` aggregation** → the `Telemetry` read surface
  (`provider_stats(since=...)`), replacing the raw aggregation query over
  `llmbroker_calls`.

The webapp admin LLM page keeps its existing shape: `llm_status()` returns the
same payload keys (`rate_limited_until`, `execution_fail_count`, `used_today`,
`last_status`), now assembled from `provider_health()` + `provider_stats()`
instead of table columns, so **no frontend change** is required. Audit for any
remaining code that names the two tables in SQL (e.g.
`tests/api/test_api_delete_receipt.py`'s reference to `llmbroker_calls`,
likely a per-receipt cascade) and route it through `Telemetry.purge(trace_id=...)`
or drop it — after this rework **no dinary code names `llmbroker_*` tables**.

`_DEPLOY_DIR`/`_LLM_PROVIDERS_TOML` move next to the existing `_PROJECT_ROOT` in
`main.py`. dinary's `.deploy/llm_providers.toml` gains `api_key_ref` fields and
its keys move to env / the deploy secret store (a migration note for ops).

**Schema migration `0007`** (next free number after `0006_category_templates` —
confirm at implementation time)**:** **drop** the `llmbroker_*` objects
(`llmbroker_providers`, `llmbroker_calls`, and any legacy indexes) so that
`llmbroker`'s `ensure_schema` becomes their sole creator and owner. On the next
startup the sqlite batteries recreate both tables in their current shape —
including the `prompt_tokens`/`completion_tokens`/`quality_score` call-log
columns and **without** the legacy `rate_limited_until`/`execution_fail_count`
config columns — and the startup `import_if_empty` re-fills `llmbroker_providers`
from `.deploy/llm_providers.toml`. This **discards existing local
`llmbroker_calls` history once** — acceptable and intentional: dinary is the
package's single local instance, that table data is disposable, and provider
config is re-imported from the TOML. This DROP is a **one-off cleanup of dinary's
pre-extraction tables**, not how the package upgrades in general — post-extraction
`ensure_schema` evolves its schema non-destructively (see "SQLite batteries own
their schema"), and yoyo never touches `llmbroker_*` again. The migration rides
the existing migrations deploy machinery (`tasks/deploy.py` already ships
`src/dinary/db/migrations/`), so no deploy change.

| File | Change |
|---|---|
| `src/dinary/background/classification/task.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import Broker`; rename `LLMBroker` references to `Broker` |
| `src/dinary/background/classification/store_resolver.py` | same |
| `src/dinary/background/classification/receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Broker, Result` (rename `LLMBroker`→`Broker`, `Execution`→`Result`); pass `operation="receipt_classification"` to `chat()` so the Optimizer can tune/route per operation |
| `src/dinary_analytics/llm.py` | `from dinary.adapters.llm_chat import (...)` → `from llmbroker import (...)` |
| `tasks/receipt.py` | `LLMBroker(TomlLLMBrokerStorage())` → `Broker(registry=Registry.toml(_PROVIDERS_TOML))` with `_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"` |
| `src/dinary/api/controllers/llm.py` | drop all raw SQL over `llmbroker_*`; provider CRUD via `Registry` admin (`load`/`add`/`update`/`remove`), aggregation via `Telemetry.provider_stats()`, live cooldown/fail via `llm.provider_health()` |
| `src/dinary/api/llm.py` | add the read-only `provider_health()` endpoint; `llm_status()` assembles the unchanged payload keys from the API surfaces above |

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
- `test_broker.py` / `test_telemetry.py` assert that `operation`,
  `prompt_tokens`/`completion_tokens` (from a stubbed response `usage`), and
  `quality_score` flow into the recorded `Call`: `operation` from
  `ask`/`chat`, tokens parsed from the response, and `quality_score=0.0`
  emitted by `Result.score(0.0)` (with `status` still `CallStatus.OK`).
- **Migration `0007` drop test** (dinary-side, `tests/services/`, needs
  `dinary.db.db_migrations`): after applying migrations through `0007`,
  `llmbroker_providers` and `llmbroker_calls` are **absent** (`PRAGMA
  table_info` empty / `sqlite_master` has no such table). No more yoyo-vs-
  `ensure_schema` equivalence test — yoyo no longer builds the package schema, so
  there is no second source to reconcile.
- **`ensure_schema` rebuild test** (package-side, `tests/llmbroker/`): on an empty
  DB (or one just dropped), `ensure_schema` creates `llmbroker_providers` and
  `llmbroker_calls` with the `prompt_tokens`/`completion_tokens`/`quality_score`
  columns and **without** `rate_limited_until`/`execution_fail_count`; every
  created object name starts with `llmbroker_`; running it twice is a no-op. The
  version-aware additive-upgrade path is exercised when the first ALTER actually
  ships (no ALTERs exist in P1 beyond the initial create).
- New `test_alembic.py` (package-side): `alembic.include_object` returns `False`
  for any `llmbroker_*` object name and `True` otherwise; composing with a host
  predicate skips when either says skip.
- `test_telemetry.py` covers the read surface: `provider_stats(since=...)`
  aggregates `call_count`/`last_status`/`last_at` per provider from recorded
  `Call`s; `recent(limit=...)` returns latest events; `purge(trace_id=...)`
  deletes the matching rows; `log()`/`none()` do **not** expose the read surface.
- `tests/api/test_admin_llm.py`: rewrite for the **API-only** admin — assert the
  controller issues **no raw SQL** over `llmbroker_*` and that the `llm_status`
  payload is assembled from the API: `rate_limited_until`/`execution_fail_count`
  from `llm.provider_health()`, `used_today`/`last_status` from
  `Telemetry.provider_stats()`; provider CRUD round-trips through the `Registry`
  admin surface. Add coverage for the new read-only `provider_health()` endpoint.
  The existing assertion that `execution_fail_count` is present in each provider
  entry stays.
- Mechanical import updates in dinary-side tests referencing the broker:
  `test_main.py`, `test_store_resolver.py`, `test_receipt_classifier.py`,
  `test_receipt_classification.py`, `test_receipt_pipeline_e2e.py`,
  `test_receipt_drain.py`, `test_receipt_pipeline.py`, `test_llm.py`,
  `tests/conftest.py` (the `NullStorage`/`real_llm_seed` fixtures keep their
  logic; the fixture that pre-populated providers now calls
  `Registry.sqlite(...).import_from(...)` explicitly instead of relying on
  constructor seeding).
- `tests/api/test_api_delete_receipt.py` currently names `llmbroker_calls` in
  raw SQL; update it to drive `Telemetry.purge(trace_id=...)` (or drop the
  cascade) so no dinary code names the package's tables.
- New `test_registry_sqlite.py` covers `import_from` policies (`skip` leaves
  existing rows; `update` upserts; `replace` wipes-then-inserts) and
  `import_if_empty` (fills an empty store, no-ops on a populated one).

Every new battery, the `Secrets` resolvers, `ask()`, the import operations,
and the `env-template`/`import` CLI ship with tests in the phase that introduces
them.

---

## Specs (Phase 1)

- `specs/reference/llm-providers.md`: trim to dinary-specific concerns (provider
  pool rationale, prompt design, models to avoid). Remove broker-internals
  sections (queue round-robin, storage Protocol, …). Add one paragraph: dinary
  runs `llmbroker` via explicit `Registry.sqlite` + `Telemetry.sqlite` over
  `storage.DB_PATH` (no `shared_state=` — one process, live state in memory), with
  providers imported (once, if empty) from `.deploy/llm_providers.toml`, keys via
  `api_key_ref` + env; the sqlite
  batteries own `llmbroker_providers`/`llmbroker_calls` (`ensure_schema`);
  migrations `0004`/`0005` created the tables historically, a new migration drops
  them so `llmbroker`'s `ensure_schema` owns the schema (recreated on next start
  with the `prompt_tokens`/`completion_tokens`/`quality_score` call-log columns and
  without the legacy `rate_limited_until`/`execution_fail_count` config columns).
  Note that the package coexists with dinary's yoyo migrations via the `llmbroker_`
  object prefix — yoyo never touches those tables after the drop. The schema is
  **private to the package**: dinary's admin reaches provider config, live state,
  and call-log aggregates through the `llmbroker` API (no raw SQL over those
  tables). Per spec rules, do not link the package README (specs link only specs).
- `specs/reference/architecture.md`: add `src/llmbroker/` to the source layout —
  "standalone, host-agnostic LLM-provider broker; round-robin failover,
  rate-limit handling; pluggable `Registry`/`Secrets`/`Telemetry` + opt-in
  `SharedState` for clusters; batteries for TOML/SQLite/Postgres/redis/MongoDB;
  owns its own `llmbroker_`-prefixed schema (`ensure_schema`, version-aware) and
  coexists with host migration tools (Alembic `include_object` hook, prefix
  filtering); no `dinary` imports; will move to its own repo/PyPI package."

---

## Package README (`src/llmbroker/README.md`)

The Rung 0→2 ladder above is the README. It records current capabilities
(round-robin queue, one in-flight request per provider, per-provider 429/503
cooldown honoring `Retry-After`, pluggable `Registry`/`Secrets`/`Telemetry` plus
opt-in `SharedState` for clusters, `Secrets` indirection so no key lives in
config, `operation`-tagged telemetry, the `llmbroker_`-prefixed self-owned schema
and `llmbroker.alembic.include_object` coexistence hook) and the
`Optimizer` roadmap (autonomous self-tuning + operation routing; optional LLM-in-the-
loop quality judging). It documents the **admin API** — provider CRUD via the
`Registry` admin surface, call-log aggregates via the `Telemetry` read surface
(`provider_stats`/`recent`/`purge`), and live state via `llm.provider_health()`
— as the way to build an admin UI, noting the **DB schema is private** (no raw
SQL). It includes the "running llmbroker alongside your migrations" section (the
per-tool table + the Alembic snippet from "Coexisting with host migration
tools"). It states plainly that `llmbroker` is a **library,
not a server** — wrap it in your own web framework if you need an HTTP gateway.
Distribution name deferred (`circuit-ai`, `llm-hydra`, `llm-router`, … — decide at
publish); the import name stays `llmbroker`.

---

## Verification

1. `uv run inv pre` → "All checks passed!" + `0 errors`.
2. `uv run pytest` → all green, incl. `tests/llmbroker/`.
3. `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tests/ tasks/` → empty.
4. `uv run python -c "import llmbroker; print(llmbroker.Registry.toml, llmbroker.SharedState, llmbroker.Secrets.env, llmbroker.alembic.include_object)"`.
5. `uv run python -m llmbroker env-template src/llmbroker/data/providers.example.toml` prints a `.env` skeleton.
6. Smoke: applying migration `0007` leaves no `llmbroker_providers`/
   `llmbroker_calls` tables; `uv run inv dev` then starts, `ensure_schema`
   recreates both (current shape, all objects `llmbroker_`-prefixed) and
   `import_if_empty` fills `llmbroker_providers`; a second start no-ops both;
   admin LLM API reads `llmbroker_providers`/`llmbroker_calls` and overlays live
   `provider_health()`.

---

## Open design questions (decide when the phase needs them)

- **Single-process state durability** (P1): we drop all single-process live-state
  persistence (the old SQLite columns + the TOML JSON sidecar) on the principle
  that ephemeral cooldown is not worth persisting. The Optimizer's learned state
  is a live in-memory aggregate of the event stream (see "Autonomous
  optimization"); whether it is checkpointed to its own table for a fast warm
  start or simply recomputed from the journal is a **P4 decision**, deferred. The
  P1 invariant is only that the append-only `Call` journal stays rich enough
  to reconstruct it. If a real need for restart-resilient cooldown on one node ever
  appears, add an opt-in file-backed state — not shipped now.
- **Example-file variants** (P2): how many goal-specific TOMLs, and the
  refresh-from-source workflow / format of latency/limits/quality notes.
- **`SharedState` write semantics under concurrency** (P3): partial per-field
  writes vs whole-snapshot; consistency model for concurrent `mark_*` across
  nodes.
- **Optimizer design** (P4): the read API on queryable telemetry (warm-start +
  ad-hoc analysis); whether the in-memory aggregate is checkpointed to its own
  table or recomputed from the journal on start; the broker's selection-policy
  seam; how the routing ranking is computed and how aggressively it overrides
  round-robin.
- **LLM-in-the-loop cost/safety** (P5): sampling rate for LLM-as-judge quality
  scoring; the judge prompt/rubric per operation; guarding token spend; how the
  judge avoids starving real traffic on a busy pool. When the judge lands, add a
  `quality_source` column to `Call` (host `score()` = ground truth vs
  judge = noisier) so the router can weight the two by confidence; pre-P5 rows are
  all host-sourced, so nothing is lost by deferring it.
- **Per-provider Initial/Min/Max delay** (P5): individual vs one global; computed
  vs fixed KISS schedule (lean KISS first).
- **Optional `Telemetry` read-surface shape** (P1, decided minimally): the methods
  shipped now are exactly what dinary's admin needs (`provider_stats`, `recent`,
  `purge`); richer query/filtering (date ranges, per-`operation` breakdowns,
  pagination) is added when a consumer needs it, without breaking the existing
  signatures.

---

## Explicitly out of scope (this plan)

- **Performing the extraction itself** — giving `src/llmbroker/` its own
  `pyproject.toml`, repo, and PyPI release. That is the **planned next step once
  Phase 1 ships and deploys cleanly** (see "Trajectory"), not work done inside
  this plan; the PyPI name is already reserved.
- **Any HTTP / server layer.** `llmbroker` is a library; a microservice gateway
  is a host concern, built on the host's own web framework.
- The `Optimizer` itself (P4) and its LLM-in-the-loop deepening (P5) — only the
  `operation` data capture and the selection-policy seam are designed now.
- **Token streaming (`stream()`)** — a real capability a universal LLM broker will
  eventually need (chat UIs, agents), but deliberately **not built in P1**. This is
  a recorded gap, not an oversight: `chat` returns a `Result` handle (not a
  bare string), so a later `stream()` can hang off the same object and finalize
  `usage`/`quality_score` on stream completion — no Protocol break. Defer until a
  consumer needs it.
- Renaming the import name `llmbroker`.
- A standalone HTTP admin surface in the package. dinary's admin **is** reworked
  in P1 to be API-only (see "dinary wiring") — provider CRUD through the `Registry`
  admin surface, aggregation through `Telemetry.provider_stats()`, live state
  through `llm.provider_health()` — but it remains dinary's own FastAPI
  endpoints consuming the library; `llmbroker` ships no admin HTTP layer of its own.
