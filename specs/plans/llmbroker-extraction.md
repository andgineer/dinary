# Extract `llmbroker` into a standalone, host-agnostic package

## Goal

Turn the LLM broker into a self-contained package `src/llmbroker/` (sibling to
`src/dinary` and `src/dinary_analytics`, **zero `dinary.*` imports**) that is a
**complete LLM-provider broker for any application** — any database, or none —
not a dinary-internal helper. The package provides one thing: LLM access over a
*cluster of configured LLMs* (each an `(base_url, model, api_key)` endpoint),
rotating away from ones that are momentarily unavailable (429/503), and
accumulating enough signal to decide which to drop or add.

The design optimizes two things at once:

- **Dead-simple typical use.** Copy an example LLMs file, put keys in env vars,
  write one constructor line. A typical host writes **no integration code** and
  **never puts a secret in source**.
- **Full universality.** Any storage, any config/import source, any secret
  backend, single-process or clustered — each is a shipped *battery*, and the rare
  host with a non-standard requirement implements **one small port**, reusing
  shipped implementations for everything else.
- **It tunes itself.** The package does not just log calls — a background
  optimizer reads telemetry (per LLM *and per operation*) and **acts**:
  auto-adjusts cooldowns/delays, offlines and re-probes bad LLMs, and routes each
  operation to the LLMs that empirically handle it best. The goal is "it just
  works" — not a feed of advice about free LLMs the user will never read. A human
  is bothered only by what only a human can fix (pool under-provisioned, API key
  dead).

There is **no goal to minimize the diff**. We rename and reshape freely to reach
the ideal API; dinary becomes just one more caller.

---

## Trajectory — vendored through all phases, standalone PyPI package only when complete

`llmbroker` lives inside dinary's `src/` **as a staging area for the whole build-out**,
not just Phase 1. The import name and the PyPI distribution name are both **`llmbroker`**
(already reserved, cemented — no rename). **Nothing usable is published to PyPI until all
phases (the `Optimizer` and the LLM-judge included) are implemented.** Phase 1 exists only
to extract the package in-tree and **prove it inside dinary** as dinary's real LLM path —
it is internal-only, never an external release. Because the package is unpublished until
complete, the P1 surface may lock the *shape* of features that do not work yet (the
default-on `optimize=True`, the full `LifecyclePhase` FSM, the version-aware `ensure_schema`
upgrade seam) purely as forward-compatibility hygiene: by the time any external user can
`pip install llmbroker`, every locked knob is real, so there is no published do-nothing
surface and no version churn from late-arriving features. Only once the phases are done is
the package git-extracted into its **own repository**, given its own `pyproject.toml`,
published, and from then on **developed and versioned independently** of dinary — which
then consumes it as an ordinary pinned dependency, not as in-tree source.

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

| Concept | Port (interface) | Required? | Default battery | What it is |
|---|---|---|---|---|
| **config** | `RegistryProtocol` | **yes** | — | where the LLM configuration is stored / loaded |
| **secrets** | `SecretsProtocol` | no | `Secrets()` (env) | how `api_key_ref` references resolve to real keys |
| **shared state** | `SharedStateProtocol` | no — **opt-in, cluster only** | none (single process keeps state in-memory internally) | cross-instance sync of per-LLM live state (cooldown, fail count, offline) — supply it only to make several `llmbroker` copies agree |
| **telemetry** | `TelemetryProtocol` | no | `Telemetry()` (log) | append-only journal of calls — to see what happened and decide which LLMs to keep |

**`SharedState` is opt-in and exists only for clusters.** The broker always
keeps per-LLM live state (cooldown/fail/offline) in memory internally — that is a
private detail, not a user-facing port. You pass `shared_state=` *only* to share
that state across several `llmbroker` instances; there is deliberately no "local"
variant, because the absence of the parameter already means "single process,
nothing to coordinate". A database does not call for it — persisting ephemeral
cooldown for one process buys nothing (a stale cooldown after a restart is worse
than re-learning from a live 429). So the "DB" axis is purely `Registry` (config)
+ `Telemetry` (log); `SharedState` is orthogonal and only about multi-instance
sync.

**Naming convention.** The bare name is the **default concrete battery**, built
by direct construction — `llmbroker.Registry("llms.toml")` (file),
`llmbroker.Secrets()` (env), `llmbroker.Telemetry()` (log) — the `httpx.Client` /
`pathlib.Path` idiom (no factory functions, no classmethods). A *variant* of a
zero-dep battery gets a descriptive prefix: `DictSecrets`, `NoTelemetry`,
`JsonlTelemetry`. A **dependency** backend is `llmbroker.<backend>.<Port>`
(`llmbroker.sqlite.Registry`, `llmbroker.redis.SharedState`) — the submodule
namespace already says the backend, so there is no `SqliteRegistry` stutter. The
**interface** a custom backend implements is `<Port>Protocol` (`RegistryProtocol`,
`SecretsProtocol`, `SharedStateProtocol`, `TelemetryProtocol`). When a port has **capability
layers** (a minimal contract the broker needs plus a richer one a host admin UI or
the Optimizer needs), each layer is its own protocol named
`<Capability><Port>Protocol` — `MutableRegistryProtocol(RegistryProtocol)`,
`QueryableTelemetryProtocol(TelemetryProtocol)`. `Protocol` is the **invariant suffix** marking
"this is a structural interface to implement" — it reads as exactly that, never as a base
class to inherit; the capability is an ordinary adjective prefix on
the port noun. So the rule is uniform — every protocol ends in `Protocol`, never a bare
`MutableRegistry` (that would mix a suffix and a prefix scheme, and the bare names
are reserved for batteries anyway). The default telemetry `llmbroker.Telemetry()` is
Python `logging` so call data is never silently lost; `llmbroker.NoTelemetry()` is
the explicit opt-out.

**Why the bare name is the default battery, not the interface** (rejected
alternatives, so this is not re-litigated after extraction). Dependency-carrying
backends (sqlite/redis/postgres) **must** be submodules in any scheme — otherwise
`import llmbroker` pulls every optional driver — so the only open choice is naming
the *zero-dep defaults* and the *protocols*; the bare name `Registry` can go to one
or the other, not both.

- **Giving the protocol the bare name and prefixing the default** (`Registry` =
  Protocol, `FileRegistry`/`EnvSecrets`/`LogTelemetry` = defaults) is rejected on
  three counts. (1) It makes the most-guessable name a trap: a newcomer writes
  `llmbroker.Secrets()` expecting the env default and gets
  `TypeError: Protocols cannot be instantiated`. In the chosen scheme
  `llmbroker.Secrets()`/`Registry(path)`/`Telemetry()` *are* the obvious defaults.
  (2) It lengthens the **common** Rung-0 path (every host types `FileRegistry`) to
  tidy the **rare** custom-backend path (`RegistryProtocol`, seen only by someone
  writing a backend) — backwards: spend the short name where it is used most. It
  even makes the default longer than a dep backend (`FileRegistry` vs
  `sqlite.Registry`). (3) `<Port>Protocol` is the unambiguous Python marker for a
  structural interface — the suffix reads as "implement this", not "inherit this",
  which a bare `Registry` or a `Base`-suffixed name would blur.
- **Making the zero-dep defaults submodules too** (`llmbroker.toml.Registry`,
  `llmbroker.env.Secrets`) is rejected: it forces the 90% file/env/log user to learn
  a submodule for a stdlib-only thing and falsely implies a dependency. Symmetry for
  its own sake at the cost of the common case.

The payoff is **one rule across all ports — bare name = the sensible default**
(`Registry`/`Secrets`/`Telemetry`), learned once and applied everywhere; variants
get a descriptive prefix, the interface gets `Protocol`, a dep backend gets a submodule.

| Port interface | Reads / writes |
|---|---|
| `RegistryProtocol.load()` | `list[LLMConfig]` |
| `SharedStateProtocol.read()` / `.write(name, state)` | `dict[str, LLMState]` / saves one `LLMState` |
| `TelemetryProtocol.record(call)` | `Call` |
| `SecretsProtocol.resolve(ref)` | `str` (the resolved secret) |

The entity is the **`LLM`** — a configured `(base_url, model, api_key)` endpoint the
broker can call. The word **provider** is reserved for the *upstream vendor* (the
`base_url` host, e.g. Groq) — one provider can back several `LLM` entries
(different models), which is exactly why the config store is a `Registry`, not a
flat `Providers` list.

Each `LLM` is identified by an immutable **`name`** (the convention of `k8s
metadata.name` / `docker --name` — a human-authored unique id) used for every
reference (telemetry, shared state, routing) and as the `Broker` Mapping
key. The stored config (`LLMConfig`) holds an `api_key_ref` — an env-var name /
secret path, **never** the secret — and the broker resolves it via `Secrets` into a
**private** map (`_resolved_keys`) keyed by `name`; the resolved secret never lands
on a public object, so `LLMConfig` is safe to expose as-is.

---

## The usage ladder (this is the README and the doc structure)

Documentation reads as a staircase, **not** as "orthogonal axes". Each rung is a
shipped battery; a reader stops at the first rung that fits.

**One battery rule, no exception list:** everything dependency-free is a
top-level class you construct directly with only `import llmbroker`
(`llmbroker.Registry(path)`, `llmbroker.Secrets()`, `llmbroker.Telemetry()`,
`llmbroker.JsonlTelemetry(path)`, …); a backend that carries an external
dependency is its own **submodule** you import explicitly — `import
llmbroker.sqlite` is *where* the optional dependency is pulled. Construct
submodule classes **fully qualified** (`llmbroker.sqlite.Registry(...)`);
**never** `from llmbroker import sqlite` (the bare `sqlite` shadows the reader's
stdlib mental model — an antipattern). The dividing line is just "does it have a
dependency", so there is no list of "which submodules are eager" to memorize.

### Rung 0 — copy, set env, one line (embedded, in-memory)

1. Copy a shipped example: `llms.example.toml` → `llms.toml` (pick the variant
   for your goal — see "example files").
2. Generate a `.env` skeleton so you never hand-type key names:
   ```bash
   python -m llmbroker env-template llms.toml > .env   # then fill in the values
   ```
3. In your app — one registry, no backend menu:
   ```python
   import llmbroker

   llms = llmbroker.Broker(registry=llmbroker.Registry("llms.toml"))
   reply = (await llms.ask("Summarize this receipt: ...", operation="summary")).text
   ```
   `llmbroker.Registry(path)` loads the config file and dispatches by extension
   (`.toml` / `.json`, both stdlib-parsed); an unknown extension is a clear error.

State is in-memory, telemetry goes to the log, keys come from env. Nothing to
implement, no secret in source. `ask` is the simplest call — it wraps a bare
string as one user message (`chat` is the full messages API). When every LLM is
momentarily busy it raises `NoLLMAvailable`; the README example shows handling
that. The broker starts its background machinery lazily on the first `await
ask`/`chat`, so this one-liner needs no `start()` and no `async with` (see
"Lifecycle"); a throwaway script on the default log telemetry can simply exit.

### Rung 1 — "if you have a database"

Persist config and telemetry, build an admin UI on the DB table. Connecting to
the store and **populating** it are separate steps (see "Seeding a DB store" —
the constructor never auto-seeds). The one-line sugar `import_if_empty` covers the
common "fill on first run" case. **`shared_state` is not part of this** — a single
process keeps cooldown state in memory internally; there is nothing to share.

```python
import llmbroker
import llmbroker.sqlite          # dep-carrying → explicit import (llmbroker.Registry needs no import)

registry = await llmbroker.sqlite.Registry("broker.db").import_if_empty(
    llmbroker.Registry("llms.toml"),
)
llms = llmbroker.Broker(
    registry=registry,
    telemetry=llmbroker.sqlite.Telemetry("broker.db"),
    # no shared_state= → single process (Rung 2 adds it for clusters)
)
```

A host that hand-manages the DB just never imports; a host that wants our updated
catalog re-runs an explicit `import_from(..., on_conflict="update")`.

### Rung 2 — "if you run a cluster"

Add `shared_state=`; the instances then agree automatically (shared cooldown,
shared fail counts). Nothing else changes:

```python
import llmbroker.redis
shared_state=llmbroker.redis.SharedState("redis://...")   # or llmbroker.postgres.SharedState(dsn) / llmbroker.mongodb.SharedState(uri)
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

## Ports and the public surface (the universality contract)

Narrow Protocols. A host implements one **only** to support a backend we do not
ship.

```python
class LifecyclePhase(Enum):   # the catalogue of lifecycle phase codes
    AVAILABLE = "available"   # in rotation
    COOLING = "cooling"       # cooling after 429/503 until cooldown_until
    OFFLINE = "offline"       # repeatedly failed; sleeping before a probe (Optimizer, P4)
    PROBING = "probing"       # sending a test request to check recovery (Optimizer, P4)


# A snapshot of one LLM's live state, its field values fixed at the moment it is
# built — it is NOT stored and held. The broker builds a fresh one every time you
# read llms[name].state, and builds one each time it saves to the shared store in a
# cluster. Plain fields only (no live properties), so it can be saved to and loaded
# from redis/postgres.
@dataclass(frozen=True, slots=True)
class LLMState:
    phase: LifecyclePhase = LifecyclePhase.AVAILABLE   # AVAILABLE/COOLING computed from cooldown_until vs now; OFFLINE/PROBING set by the Optimizer
    cooldown_until: datetime | None = None             # when the COOLING/OFFLINE sleep ends
    fail_count: int = 0


@dataclass(frozen=True, slots=True)
class LLMConfig:                         # pure stored config — no secret, safe to expose
    name: str                            # immutable identifier; every reference uses it; the Mapping key
    base_url: str
    model: str
    api_key_ref: str                     # env-var name / secret path; resolved via Secrets (broker-side)


class CallStatus(Enum):
    OK = "ok"                       # HTTP 200 — quality is judged separately via quality_score
    RATE_LIMITED = "rate_limited"   # 429
    UNAVAILABLE = "unavailable"     # 503
    ERROR = "error"                 # any other transport/protocol failure


@dataclass(frozen=True, slots=True)
class Usage:                             # resource use the provider reported for one call
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    extra: dict[str, int] | None = None  # provider-specific extras (cached / reasoning tokens, …)


@dataclass(frozen=True, slots=True)
class Call:
    id: str                              # broker-assigned uuid; PK of llmbroker_calls; the row record_quality updates
    llm_name: str                        # the LLMConfig.name that served this call
    operation: str | None
    trace_id: str | None
    status: CallStatus                   # coarse transport outcome — the axis routing reacts to
    http_status: int | None = None       # exact code (500/timeout → None); captured now, unrecoverable later
    latency_ms: int | None = None
    error_detail: str | None = None
    usage: Usage | None = None           # token counts the provider returned, when present
    quality_score: float | None = None   # 0..1; NULL = not judged (the common case)


@dataclass(frozen=True, slots=True)
class TelemetryStats:                    # admin read-model, derived from Call rows
    call_count: int
    last_status: CallStatus | None
    last_at: datetime | None


# Port interfaces are named `<Capability><Port>Protocol`; a custom backend implements
# the level it supports. The bare names (Registry/Secrets/Telemetry) are the
# default concrete batteries. `Protocol` is the invariant suffix marking "this is a
# structural interface"; a capability is an adjective prefix (see "Naming convention").

# Minimal contract the broker needs — load the config. The file battery
# (llmbroker.Registry) implements exactly this.
class RegistryProtocol(Protocol):
    async def load(self) -> list[LLMConfig]: ...


# Admin extension the host admin UI types against (DB batteries implement it; the
# broker never calls it). A typed contract, not "optional methods" — a host admin
# function annotates `MutableRegistryProtocol` and gets full type checking on CRUD over
# ANY backend that supports it, with no concrete-type lock-in.
@runtime_checkable
class MutableRegistryProtocol(RegistryProtocol, Protocol):
    async def get(self, name: str) -> LLMConfig | None: ...
    async def add(self, cfg: LLMConfig) -> None: ...
    async def update(self, name: str, **fields) -> None: ...
    async def remove(self, name: str) -> None: ...


class SecretsProtocol(Protocol):
    async def resolve(self, ref: str) -> str: ...


# Optional, opt-in — only for clusters (several broker copies sharing one
# redis/postgres store so they agree on each LLM's state). A plain read/write store
# of the whole LLMState — the broker builds the value it writes at write time.
class SharedStateProtocol(Protocol):
    async def read(self) -> dict[str, LLMState]: ...                # current state of every LLM in the store
    async def write(self, name: str, state: LLMState) -> None: ...  # save one LLM's whole state (phase included)


# Minimal contract — record a call, and attach a quality score to one already recorded.
# Both default log/none batteries implement exactly this. `record_quality` is on the
# minimal contract (not the queryable layer) so EVERY backend has a quality write path;
# how it lands differs by capability: a queryable backend UPDATEs the call row by id, an
# append-only backend appends a distinct, clearly-labelled quality record (never a Call).
class TelemetryProtocol(Protocol):
    async def record(self, call: Call) -> None: ...
    async def record_quality(self, call_id: str, score: float) -> None: ...


# Read/aggregation extension (queryable batteries — sqlite/jsonl/postgres — implement
# it; default log/none do not). Powers a host admin UI AND the Optimizer warm-start,
# so neither needs raw SQL. `@runtime_checkable` so the Optimizer can `isinstance`
# the telemetry to decide warm-start vs cold-boot — no hasattr sniffing.
@runtime_checkable
class QueryableTelemetryProtocol(TelemetryProtocol, Protocol):
    async def stats(self, *, since: datetime) -> dict[str, TelemetryStats]: ...
    async def recent(self, *, limit: int) -> list[Call]: ...
    async def purge(self, *, before: datetime) -> int: ...  # retention — drop rows older than `before`
```

**The data types — who's who** (the README carries this same table):

| Type | Axis | Role |
|---|---|---|
| `LLMConfig` | config | a stored `(name, base_url, model, api_key_ref)` row; what `RegistryProtocol.load()` returns; no secret |
| `LLM` | facade | the `Mapping` value `llms[name]`; bundles `.config` + `.state` + `.stats()`, one handle |
| `LifecyclePhase` | enum | the FSM label: Available / Cooling / Offline / Probing |
| `LLMState` | live | a snapshot of one LLM's runtime state `(phase, cooldown_until, fail_count)`, built on read; also what `SharedStateProtocol.read()`/`write()` stores in a cluster |
| `Usage` | event | token counts the provider reported for one call `(prompt_tokens, completion_tokens, total_tokens, extra)`; on `Result.usage` and `Call.usage` |
| `TelemetryStats` | aggregate | per-LLM `(call_count, last_status, last_at)` derived from `Call` rows; `QueryableTelemetryProtocol.stats()` / `LLM.stats()` |
| `Call` | event | one telemetry record (`id`, `llm_name`, `operation`, `status`, `usage`, `quality_score`, …); `id` is the uuid `record_quality` updates by |

### The `Broker` is a `Mapping`, the value is the `LLM` facade

```python
class LLM:                       # facade returned by llms[name] — a thin bundle, no copied fields
    config: LLMConfig            # sync — the pure stored config (name/base_url/model/api_key_ref)
    @property
    def state(self) -> LLMState: ...   # sync — built FRESH on every access from the broker's live
                                       #   internals (cooldown timestamp, fail counter, optimizer phase);
                                       #   nothing stored, so the phase is always current
    async def stats(self, *, since: datetime) -> TelemetryStats: ...   # async — telemetry I/O + window
    # the resolved secret is NOT here and NOT on config — it lives in the broker's
    # private _resolved_keys map, keyed by name, and never leaves the broker.


class Broker(Mapping[str, LLM]):
    def __init__(
        self,
        *,
        registry,                    # RegistryProtocol (e.g. llmbroker.Registry("llms.toml") or llmbroker.sqlite.Registry(...))
        secrets=None,                # SecretsProtocol (default llmbroker.Secrets() — env); broker resolves api_key_ref → _resolved_keys
        shared_state=None,           # SharedStateProtocol — opt-in, cluster only
        telemetry=None,              # TelemetryProtocol (default llmbroker.Telemetry() — log)
        optimize: "bool | Optimizer" = True,   # True ≡ Optimizer() (judge off); see "Autonomous optimization"
    ): ...                           # __init__ is cheap & side-effect-free; background loops start lazily

    async def aclose(self) -> None: ...        # cancel background loops, close owned ports
    async def __aenter__(self) -> "Broker": ...  # returns self; teardown sugar over aclose()
    async def __aexit__(self, *exc) -> None: ...

    # Two entry points, always raise (never return a sentinel). `wait` bounds only how
    # long to wait for a free LLM slot: None = wait indefinitely (default), 0 = do not
    # wait (raise NoLLMAvailable at once if nothing is free now), N = wait up to N seconds
    # then raise NoLLMAvailable. AllLLMsFailed is raised when a slot was obtained but the
    # LLM(s) errored. `wait` is named to stay distinct from a future per-request provider
    # timeout — it is capacity wait, not response timeout.
    async def ask(
        self, prompt: str, *,
        operation: str | None = None,
        trace_id: str | None = None,
        wait: float | None = None,
    ) -> Result: ...

    async def chat(
        self, messages: list[dict], *,
        operation: str | None = None,
        trace_id: str | None = None,
        wait: float | None = None,
    ) -> Result: ...
    # __getitem__/__iter__/__len__ → the LLM facade by name
    # NB: no per-call provider passthrough — see "Provider-specific parameters" below
```

```python
llms = llmbroker.Broker(registry=llmbroker.sqlite.Registry("broker.db"))
groq = llms["groq-llama"]
print(groq.config.model, groq.state.phase)   # objects, not unrolled fields — symmetric with stats()
for name in llms:
    print(name, llms[name].state.phase)
```

- **The schema is private; the API is the public contract.** No host issues raw
  SQL against `llmbroker_registry`/`llmbroker_calls` — config goes through the
  `MutableRegistryProtocol` admin surface, live state through the `Broker` Mapping
  (`llms[name].state` …), and call-log aggregation/retention through the
  `QueryableTelemetryProtocol` read surface above. This is what lets the package own and evolve its
  schema independently after extraction; a host admin UI is built entirely on
  typed methods, and it works identically over any backend (sqlite/postgres/
  mongodb), which a fixed table shape never could.
- **`Broker` is a read-only `Mapping[str, LLM]`.** Indexing the broker is one
  level (`llms[name]`, never `llms.llms[name]`); the `LLM` facade is a thin bundle
  of `.config` (the cached `LLMConfig`) + `.state` (a fresh `LLMState` built on each
  read) + `.stats()` — no copied fields. The same `LLMState` value is what
  `SharedState.read()`/`.write()` stores in a cluster.
  **Why the broker *is* the Mapping (not a `.pool`/`.llms` sub-attribute, and not
  renamed `Pool` or `Client`).** The host's variable is `llms` regardless of the class
  name (it is the domain-meaningful name; `pool` alone — "pool of what?" — never gets
  written), so both roles read naturally under it: `llms.chat(...)` (the call) and
  `llms[name].state`, `name in llms`, `len(llms)` (inspection). A `.pool` sub-attribute
  buys nothing over direct indexing, and `.llms` would force the `llms.llms[name]`
  stutter. `Broker` names the object's **primary** role — the thing you call to route
  completions across LLMs — and rhymes with the package name `llmbroker`; `Pool` would
  overweight the secondary collection role (`pool.chat()` reads wrong), and `Client`
  (the single-endpoint idiom of `openai.OpenAI`/`httpx.Client`) reads *worse* under the
  Mapping role — `client[name]`, `len(client)` ask "client of what?", and a single
  "client" that is itself a collection of LLMs is a category error. **A `Mapping` that
  also performs I/O is admittedly unusual** (most mappings are passive); it is justified
  because there is genuinely **one** object here with one host variable, and both
  readings of it are honest — calling it (`llms.chat`) and inspecting it (`llms[name]`).
  Splitting them into two objects would only manufacture the `llms.pool[name]` stutter
  the single-object design exists to avoid. So the class stays `Broker`, the Mapping is
  the broker itself, and indexing is one level.
- **One rule governs sync vs async: a member is `async` iff it performs I/O.**
  In-memory / cached access is sync — `llms[name]`, `LLM.config`/`.state`,
  `Result.text`/`.usage`, `name in llms`, `len(llms)`. Anything touching the
  network, a file, or the DB is async — `ask`/`chat`, every `RegistryProtocol.*`, every
  `TelemetryProtocol.*`, `LLM.stats()`, and `Result.record_quality()` (it writes the
  score to telemetry). This makes the mix predictable rather than arbitrary. The async
  `Broker` is the **core** because the concurrency model (per-LLM queue slot, one
  in-flight request, cooldown re-enqueue) is asyncio; a sync-only host gets the
  deferred `SyncBroker` facade (see "Explicitly out of scope"), not a half-sync
  core.
- **Mutation lives on the registry, never on the broker Mapping.** You cannot
  `llms[new_key] = …` — the broker only knows already-loaded LLMs, and `add` has
  no key to index yet. `add`/`update`/`remove` are async because the registry is
  I/O-backed (file/db); a sync `registry[key]` would lie, so the `RegistryProtocol` port
  is **async methods, not a Mapping**. The two read views answer different
  questions and span different (overlapping) sets — `registry` = *all configured*
  (incl. freshly added / filtered-out), `llms` = *currently managed* + health —
  so both existing is correct, not redundant.
- **Layered protocols, not "optional methods".** A custom backend implements the
  level it supports, and each level is a real, type-checked contract. `RegistryProtocol`
  (just `load()`) is all the broker needs; `MutableRegistryProtocol(RegistryProtocol)` adds
  `get`/`add`/`update`/`remove` — the **host admin UI** types against it and drives
  CRUD over any backend with full static checking, never the broker. Likewise
  `TelemetryProtocol` (just `record()`) vs `QueryableTelemetryProtocol(TelemetryProtocol)`
  (`stats`/`recent`/`purge`) for the call-log read side; the default `Telemetry()`
  (log) / `NoTelemetry()` implement only `TelemetryProtocol`. This mirrors
  `Sequence`/`MutableSequence`: a consumer annotates the capability it requires
  rather than sniffing `hasattr`, and a host that swaps sqlite→postgres keeps its
  admin code's types unchanged. The richer protocols are `@runtime_checkable` so the
  Optimizer can `isinstance(telemetry, QueryableTelemetryProtocol)` to choose warm-start
  vs cold-boot.
- `TelemetryStats` is a small read-model for admin aggregates (per-LLM `call_count`
  over the window, `last_status`, `last_at`) — derived from `Call` rows, never a
  stored table of its own. `QueryableTelemetryProtocol.stats(since=...)` returns
  `dict[str, TelemetryStats]`; the `LLM.stats(since=...)` facade returns one.
- `SharedState` is **optional and cluster-only** — omit `shared_state=` and the
  broker uses its private in-memory state. There is no public "in-memory
  SharedState" object; single-process is the absence of the parameter.
- `SharedState` is a plain read/write store of the whole `LLMState`: `write(name,
  state)` saves one LLM's state, `read()` returns every LLM's current state. The
  broker builds the `LLMState` value at the moment it writes (e.g. on a 429 it
  computes the new state and saves it). Writing the **whole** state — not granular
  events — is what lets every phase, including the Optimizer's Offline/Probing,
  propagate to other copies with no extra method.
- `LLMState.phase` models the **full** Optimizer state machine
  (Available/Cooling/Offline/Probing) from day one. P1 only ever sets
  Available/Cooling (429/503 cooldown); Offline/Probing are populated by the
  Optimizer (P4). The phase is part of the saved state now so the P3
  `SharedState` backends (redis/postgres/mongodb) carry it **without** a later
  Protocol-breaking change — a copy writes the whole `LLMState`, phase included.
- `Call` captures token `usage` (objective, read from the response, when present)
  and `quality_score` from P1 because telemetry is
  **append-only** — a column added later starts with no history, which is exactly
  the data the Optimizer needs. `quality_score` is **orthogonal to `status`**:
  `status` is the transport outcome (an HTTP-200 answer is `status=CallStatus.OK`),
  `quality_score` is whether that answer was usable. **Cost is deliberately not
  stored** — it is `tokens × a price table`, a host/Optimizer concern derived later
  from the tokens, not a raw signal to journal. The **source** of `quality_score`
  (host `score()` vs the P5 LLM-judge) is **not** a separate column in P1: until
  the judge exists every score is a host `score()` ground truth, so pre-judge rows
  are unambiguous and a `quality_source` column can be added with the judge (P5)
  with no lost history.
- `Broker(registry=...)` takes a `RegistryProtocol`; build one with `llmbroker.Registry(path)`
  (file) or `llmbroker.sqlite.Registry(...)`. Programmatic config goes through the
  admin `add()` or a custom `RegistryProtocol` implementation; `import_from` takes a
  `RegistryProtocol` source too. The kwarg matches the port, like `secrets=`/`shared_state=`/
  `telemetry=` taking a `SecretsProtocol`/`SharedStateProtocol`/`TelemetryProtocol`.
- **Two entry points, each with one clean type — no polymorphic parameter.**
  `chat` is the full API and always takes a chat messages array; `ask` is a thin
  convenience for the dominant single-user-turn case. Both return a `Result`
  handle exposing `.text`, `.usage`, and `.record_quality(...)`. Rung 0 is
  `llms.ask("Summarize …")`; anything beyond one user turn (system prompt,
  multi-turn history, assistant context) goes through `chat(messages)`. Keeping
  `messages` a single honest type avoids the `str | list` chameleon — the
  convenience lives in a separate, unambiguous method, not in an overloaded arg.
  There is **no per-call provider passthrough** — the broker does not know which
  provider will serve a call, so raw provider body fields have no place in its API
  (see "Provider-specific parameters").
- **One pair of methods, a numeric `wait`, always raising — no `try_*` twins, no
  `wait` *flag*.** There is no honest "blocking vs non-blocking" split to make: even
  the so-called blocking call goes `await` and waits on the chosen LLM, which can
  itself stall and end in a timeout or error, so a second method buys no different
  contract. The only real question is *what to do while no LLM slot is free*, and that
  is a duration, not a mode: `wait: float | None` — `None` waits indefinitely (default),
  `0` does not wait, `N` waits up to N seconds — after which the call **raises**
  `NoLLMAvailable`. This is exactly the `lock.acquire(timeout=)` / `queue.get(timeout=)`
  idiom: a numeric bound that **raises** on expiry, so the return type never shifts.
  Note this is **not** the rejected boolean `wait=`: that flag was bad because it would
  flip the *return contract* (raise vs sentinel) — a numeric `wait` that always raises
  keeps one contract and never returns `None`. Best-effort, skippable work ("enrich if a
  slot is spare, else move on") is `chat(..., wait=0)` inside `try/except
  NoLLMAvailable` — one obvious branch, no second method to learn.
- Both `ask` and `chat` take an opaque `trace_id` (correlation) and an
  `operation: str | None` (a host-defined category — e.g. `"receipt_classification"`,
  `"summary"`). `operation` is what lets the `Optimizer` tune and route per
  operation, so it is captured from day one even though the Optimizer is built
  later. **The word `operation` is deliberate and collision-free**: HTTP's term for a
  request kind is "method" (and Python's is "method" too), so `operation` does not clash
  with either — it is an unclaimed, immediately legible name for "the kind of work this
  call is", exactly the host-defined routing/tuning axis the Optimizer keys on.
- **`ask`/`chat` raise rather than returning a sentinel.** A `BrokerError`
  hierarchy — `NoLLMAvailable` (no LLM slot came free within `wait`) and
  `AllLLMsFailed` (a slot was obtained but each tried LLM errored) — replaces a
  `str | None` return, so "no capacity" is never confused with an empty answer and
  callers distinguish "retry later" from "all dead". `NoLLMAvailable` means "`wait`
  elapsed and the pool is still busy" — with `wait=0` that is immediate, with
  `wait=None` it never fires (the call waits out cooldowns). `AllLLMsFailed` is
  orthogonal: it fires whenever an LLM was actually tried and errored, regardless of
  `wait`, because that is a real failure, not a capacity skip.

`Result.record_quality(value: float)` — `async`, since it writes to telemetry; the
verb is honest about the side effect, parallel to `TelemetryProtocol.record(call)`, and
avoids "rate" colliding with rate-limiting. It does **not** emit a second `Call`.
Quality attaches to the **existing** call: every `Call` carries a broker-assigned
`id` (a uuid set at call time, the primary key in `llmbroker_calls`), and the `Result`
holds that id, so the quality score is routed to the original row. **The id is a uuid,
not a DB sequence/autoincrement, on purpose:** it must exist the instant the broker
creates the `Call` (so it can ride the in-memory `Result` for a later
`record_quality`) — a sequence is assigned only at `INSERT`, forcing a `RETURNING`/
`lastrowid` round-trip and back-threading — and it must mean the same thing across
**every** telemetry backend, including ones with no sequence at all (the log battery's
`quality call=<id>` line, jsonl, mongo) and a clustered multi-writer postgres where
broker-side uuids never collide and need no central id authority. The 16-byte / index-
locality cost is negligible for a retention-`purge`d event table; UUIDv7 is a drop-in if
ordering ever matters. `record_quality`
records the score into the broker's live state (mirrored to shared state if present)
and then calls `telemetry.record_quality(call_id, value)` — a method on
`TelemetryProtocol` whose two implementations diverge by what the backend can do:

- **Queryable backends** (`sqlite`/`jsonl`/`postgres`) `UPDATE llmbroker_calls SET
  quality_score=? WHERE id=?` — the score lands **on the original call row**. No new
  row, so `call_count`/aggregates never double-count.
- **Append-only backends** (`Telemetry()` log / `NoTelemetry()`), which cannot update a
  past line, append a **distinct, clearly-labelled quality record** (`quality call=<id>
  score=<v>`) — explicitly *not* a `Call` clone, and never tallied as a call.

A host marks an unusable answer with `record_quality(0.0)`; the P5 LLM-judge reuses the
**same** method to fill sampled non-binary scores, so there is one write path into
`quality_score`. `Call` carries `operation` alongside `trace_id` and `id`, so quality,
tokens, and latency are all attributed per (llm, operation) against one canonical row.

---

## Lifecycle — construct cheap, start lazily, close explicitly

The broker owns background machinery (per-LLM `asyncio.Queue` slots, the refresh
loop, the P4 `Optimizer` loop) and, through its ports, open resources (sqlite/
redis connections). The lifecycle keeps the Rung-0 one-liner trivial while making
clean shutdown unambiguous.

- **`Broker(...)` is cheap and side-effect-free.** The constructor only stores
  config and ports — no loop work, no connections, no background tasks. It is safe
  to construct outside a running event loop.
- **Background loops and port connections start lazily on the first `await
  ask`/`chat`.** So Rung 0 needs no `start()` and no `async with`.
- **Teardown is `await llms.aclose()`** — it does two things: (1) cancels the
  broker's background loops (always — a running event loop holds strong refs to those
  tasks, so they are never GC-collected on their own and the task closures keep the
  broker alive), and (2) closes the **resource-holding** ports it owns (calling each
  port's optional `aclose()`). In P1 the only resource-holding port is
  `llmbroker.sqlite.*` (the aiosqlite worker thread + connection + the DB file fd, none
  of which GC reclaims promptly); P3 adds the redis/postgres/mongodb sockets. The
  zero-resource ports — file `Registry`, `Secrets`/`DictSecrets`, log `Telemetry`,
  `NoTelemetry` — have a no-op `aclose()`, so a TOML+log broker's teardown is *only* the
  task cancellation.
- **Ports are owned by exactly one broker; resource ports are not shared.** The broker
  owns and closes every port handed to it. A resource port (`sqlite`/`redis`/`postgres`)
  belongs to one broker — if two brokers must talk to the same DB, each is given its own
  port on the same path/URL (sqlite allows several connections to one file; redis several
  pools to one server). This is not enforced in code (a port is just an object you could
  pass twice) and does not need to be: the constructor takes a path/URL, not a live
  connector, so the obvious wiring already gives each broker its own; and sharing a
  resource port is self-evidently wrong — whichever broker shuts down first would close
  the connection out from under the other (a *premature*-close bug, which no ownership
  trick fixes). Zero-resource ports (`Secrets`, log `Telemetry`) may be shared freely —
  their `aclose()` is a no-op. As cheap hygiene, every port's `aclose()` is **idempotent**
  (a second call is a no-op, never an error).
- **`async with` is teardown sugar over `aclose()`**, not a second way to start.
  `__aenter__` returns `self`. Because the constructor is multi-line, the idiom is
  **two-step** — never the constructor in the `with` header:

  ```python
  llms = llmbroker.Broker(registry=..., telemetry=...)
  async with llms:        # `as` is redundant; teardown guaranteed on exit
      ...
  ```

- **Three levels, matched to the consumer:**
  1. **Throwaway script on the default log telemetry** — no teardown needed; the
     one-liner runs and the process exits (nothing buffered, no connection to flush).
  2. **Script/test with a DB or network battery** — use `async with llms:` for
     deterministic cleanup (flush the last telemetry writes, close connections, stop
     tasks leaking between tests).
  3. **Long-lived app (dinary/FastAPI)** — construct once, `await llms.aclose()` on
     shutdown (FastAPI lifespan); see "dinary wiring".

  The rule of thumb: **the moment a DB/network battery is attached, or the process
  does not immediately exit, close the broker.**

---

## Secrets — universal, trivial for the simple case

Stored config holds an `api_key_ref`, not a secret. The `Secrets` resolver turns a
ref into the actual key. Default reads env vars, so the simplest case is just "set
env vars".

```toml
# llms.toml
[[llms]]
name        = "groq-llama"       # immutable identifier; referenced everywhere; the Mapping key
base_url    = "..."
model       = "..."
api_key_ref = "GROQ_API_KEY"     # env-var name for llmbroker.Secrets() (env); a secret path for a vault resolver
```

```python
llmbroker.Broker(registry=llmbroker.Registry("llms.toml"))                          # default llmbroker.Secrets() (env): from os.environ
llmbroker.Broker(registry=..., secrets=llmbroker.DictSecrets({...}))                # explicit map (tests / pre-loaded keys)
llmbroker.Broker(registry=..., secrets=my_vault_resolver)                           # secret manager: implements .resolve(ref)
```

- **Resolution is a `Broker` concern, not a `Registry` one.** The registry returns
  **pure** `LLMConfig` (with `api_key_ref`, no secret); the broker calls
  `secrets.resolve(api_key_ref)` for each entry at load/refresh and keeps the
  resolved keys in its **private** `_resolved_keys` map (keyed by `name`). So the
  resolved secret never rides a public object — `secrets=` lives on the `Broker`
  only, never on the registry constructor.
- Shipped: `llmbroker.Secrets()` (env, the default), `llmbroker.DictSecrets(mapping)`
  — both zero-dependency, so they are top-level classes, not a backend submodule. A
  plain `Callable[[str], Awaitable[str]] | Callable[[str], str]` is accepted and
  adapted, so a secret-manager integration is one small function.
- Keys are resolved at load/refresh; rotated secrets are picked up on the next
  refresh tick.

### Example files + `env-template` (so key names are never hand-typed)

- Ship `llms.example.toml` (and goal-specific variants, e.g. a broad free-tier
  set vs a quality-first set; identical format, different LLM list) for users to
  copy.
- Ship a matching `.env.example` listing every `api_key_ref` the example TOML
  references, with blank values.
- Ship `python -m llmbroker env-template <toml> > .env`: scans any TOML for
  `api_key_ref` and emits a `.env` skeleton — the robust answer for custom files.

---

## Seeding a DB store — explicit import, never a constructor side-effect

A backend `registry(…)` factory only **connects**; it never populates. Once a DB
table exists it is authoritative — nothing auto-mutates it, so there is no "what
happens if the seed changed?" ambiguity. Filling and updating it is a
**separate, explicit operation** with a chosen conflict policy:

```python
reg = llmbroker.sqlite.Registry("broker.db")
await reg.import_from(llmbroker.Registry("llms.toml"), on_conflict="skip")
```

`import_from(source, *, on_conflict=...)` takes any read-only `RegistryProtocol` source
(e.g. `llmbroker.Registry("llms.toml")`). Policies map to the real user journeys:

| `on_conflict` | Effect | Journey |
|---|---|---|
| `skip` (default) | insert LLMs missing by name; never touch existing rows | protect hand-edits; safe re-runs |
| `update` | upsert — insert new + overwrite existing fields from the source | pull our updated recommended catalog into the DB |
| `replace` | wipe the table, then insert the source | full reset to the source |

A host that decides to hand-manage the DB simply stops importing; a host that
wants our catalog changes re-runs `import_from(..., on_conflict="update")`. On a
fresh (empty) DB all three policies behave identically.

**One-line sugar for simple use:** `import_if_empty(source)` imports only when the
store is empty and returns the registry, so first-run fill composes inline
without any constructor magic:

```python
registry = await llmbroker.sqlite.Registry("broker.db").import_if_empty(
    llmbroker.Registry("llms.toml"),
)
```

Because `import_if_empty` never acts on a non-empty store, changing the source
later cannot surprise an existing DB — it only ever fills a blank one. The same
operations are on the CLI for ops:

```bash
python -m llmbroker import llms.toml --into sqlite:broker.db --on-conflict update
```

`import_from`/`import_if_empty` are built on the `MutableRegistryProtocol` admin surface
(`add`/`update`/`remove`); `llmbroker.Registry` (file is the store) implements only
`RegistryProtocol` and needs neither — edit the file.

---

## Cluster coordination — how `SharedState` meets the in-memory queue

The broker keeps its single-process machinery: one `asyncio.Queue` slot per LLM,
at most one in-flight request per LLM, `loop.call_later` re-enqueue after a 429
cooldown, and its **private in-memory** per-LLM live state. `SharedState`, when
supplied, layers on without the core knowing whether it is clustered:

- **On 429/503:** the broker updates its in-memory cooldown **and** schedules its
  own local `call_later` re-enqueue; if `shared_state=` is set it also builds the
  new `LLMState` and calls `write(name, state)` so *other* copies learn.
- **On the refresh tick** (existing refresh loop, now tightened/configurable):
  with `SharedState`, the broker calls `read()` and reconciles its local queue
  against the shared state — dropping LLMs other copies marked cooling/offline,
  re-adding those whose cooldown passed. **Clustering rides on the refresh loop
  that already exists.**
- **No `shared_state=` (default):** everything stays in the process's own memory —
  behavior identical to today (local `call_later`), zero infra, zero races.
- **Shared backends** (`llmbroker.redis`/`postgres`/`mongodb` `.SharedState(...)`)
  exist **only for clusters**: `read()` returns the shared state; bounded races (two
  copies briefly both see an LLM free) cost at most one redundant 429. There is no
  `sqlite` `SharedState` — SQLite is not a cross-node store, and single-process
  needs no externalized state.

Reconcile granularity = refresh interval (eventual consistency). A precise
"local timer driven by the shared cooldown value" and redis pub/sub for
near-instant propagation are noted as **future optimizations**, not built now.

---

## Autonomous optimization — the `Optimizer`

Showing per-LLM advice is not the goal — **nobody will study what is happening
with yet another free LLM, or care which vendor backs it.** The goal is that the
cluster **tunes itself and routes work optimally, invisibly.** The package ships
an `Optimizer`: a background control loop (like the refresh loop) that reads
telemetry and *acts*, not just reports.

```python
llms = llmbroker.Broker(
    registry=llmbroker.sqlite.Registry("broker.db"),
    telemetry=llmbroker.sqlite.Telemetry("broker.db"),   # queryable → warm-start + analysis (optimizer runs on any backend)
    optimize=True,                                        # default-on; learns from the live event stream
)
```

**The control surface — one knob for the AI part.** `optimize` takes `bool | Optimizer`:

```python
optimize=True                              # default: delay tuning + routing, ZERO extra LLM calls (active from P4)
optimize=False                             # broker stays reactive (round-robin + 429/503 cooldown), no learning
optimize=llmbroker.Optimizer(judge=0.05)   # the above + LLM-as-judge scores 5% of answers (active from P5)
```

`Optimizer(judge: float = 0.0)` — `judge` is the **sampling fraction** the
LLM-as-judge scores (`0.0` = off). `True` ≡ `Optimizer()` (`judge=0.0`), `False`
≡ no optimizer. So the default self-tuning is **free** (no extra LLM traffic), and
token-spending judging is **never** enabled implicitly — only when a host sets
`judge>0`.

**P1 ships only the shape, not the behavior.** P1 fixes the parameter
(`optimize: bool | Optimizer = True`, `Optimizer(judge=0.0)`) so the default is
locked now and P4 can switch the engine on with **no API change**. In P1 the
Optimizer loop does not exist: `optimize=True` runs nothing, and the broker is
**reactive regardless** — round-robin selection + per-LLM 429/503 cooldown. The
delay tuning + routing land in P4, the judge in P5. So `optimize=True` in P1 is an
honest reservation, not a working feature; do not document it as one.

**Why `bool | Optimizer`, not `Optimizer | None`** (so this is not re-litigated).
`bool | Optimizer` is a precise, fully type-checked union (not `Any`) and the
oldest of ergonomic Python idioms for a config knob — `True` = sensible default,
`False` = off, an object = custom (cf. `stdout=PIPE | None`, `retry: bool |
RetryConfig`). It is **not** the `str | list` polymorphism rejected for
`ask`/`chat`: that ban is about a *data* argument on the hot path, where a
shape-shifting `messages` complicates every call and the implementation. `optimize`
is a **construction-time switch**, where the `True/False` shortcut is the win.
`Optimizer | None` was considered and rejected: it buys nothing here, forces a
non-`None` default (`= Optimizer()`) and hence a frozen-config dance to make the
shared default instance safe, and makes `None`=off clash with the `None`=default-on
of the real ports (`telemetry=`/`secrets=`).

**The Optimizer's working state is a live in-memory aggregate, not journal data.**
It feeds off the **live event stream** — every `Telemetry.record(call)` updates
rolling per-(llm, operation) stats in memory (the Optimizer interposes at the
`record()` seam, e.g. as a `Telemetry` decorator, so this works with *any* backend
including `log()`/`none()`). The append-only journal (`Call` rows) stays the
durable source of truth; the Optimizer's rankings/tuning are a derived projection
of it. That projection **may** be checkpointed to its **own** table for a fast warm
start — but is never written back into the append-only `llmbroker_calls` (mixing a
mutable projection into an event log is a category error). Whether to checkpoint or
simply recompute from the journal on start is a **P4 open question**, not a P1
lock. Either way, `Call` must be rich from day one: a column added later starts
with no history, and historical warm-start/backfill is exactly what a queryable
backend buys.

**What it does automatically (the point):**

- **Parameter tuning** — per-LLM cooldown/delay: escalate on repeated 429s up to a
  max, decrease on sustained success, offline an LLM that keeps failing and probe
  it for recovery. The tuning state model:

  | Current state | Event       | New state | Delay adjustment            |
  |---------------|-------------|-----------|-----------------------------|
  | Available     | Error 429   | Cooling   | `current_delay` (up to Max)  |
  | Cooling       | Success     | Available | Decrease delay              |
  | Cooling       | Fail @ Max  | Offline   | Start Offline Sleep / Alarm  |
  | Offline       | Sleep End   | Probing   | Send test request           |
  | Probing       | Success     | Available | Reset to Initial Delay      |
  | Probing       | Failure     | Offline   | Restart Sleep / Alarm        |

- **Operation routing** — bias selection of each `operation` toward the LLMs that
  empirically handle it best. The policy is **tiered / lexicographic, not a
  weighted-sum scalar** (`w·quality + w·latency + w·cost` is untunable — the terms
  are not commensurable, and a latency win must never "buy back" a quality loss):
  1. **Availability gate** — candidates are LLMs not in cooldown (the FSM already
     drops Cooling/Offline); residual flakiness is a soft tiebreak.
  2. **Quality floor gate** — drop LLMs whose per-`operation` usable-rate is below a
     floor. Quality is a gate, not a tradeable term.
  3. **Objective ranking — the objective lives with the `operation`.** A background
     batch type (e.g. `receipt_classification`) ranks the gated set by quality; an
     interactive type ranks by latency. There is no single global weighting that is
     right for both.
  4. **Tokens = a budget constraint, not a quality axis.** For an identical prompt
     token counts barely differ; what matters is rate-limit budget (TPM)
     consumption → throughput headroom (a less verbose LLM yields more calls before
     a 429) and `$` when paid tiers are mixed. Tokens break ties / enforce a budget;
     they never trade against quality.

  Estimates are **confidence-aware** (bandit-style): a minimum sample count before
  an LLM's stats override round-robin, an exploration reserve so deprioritized LLMs
  keep being sampled (else their stats go stale and recovery/decay is invisible),
  and a Bayesian usable-rate for the **sparse** quality signal. The broker exposes
  a pluggable **selection policy**; the default is round-robin, and the Optimizer
  swaps in the per-`operation` ranking it maintains from telemetry. Concrete
  thresholds and the bandit flavor are a P4 open question; the tiering and the
  per-`operation`-objective principle are the decided shape.
- **Pool hygiene** — automatically deprioritize/retire consistently-useless LLMs.
  Nothing for a human to read.

**What it may use an LLM for** (optional, sampled, never on the hot path):

- **Quality judging** — **off unless the host sets `Optimizer(judge>0)`** — sample
  that fraction of outputs per (llm, operation) and score them with an LLM-as-judge,
  closing the quality loop *without* the host having to call `record_quality()`. The
  judge call goes through the broker itself (dogfooding) under a low-priority
  `operation` and **degrades gracefully** if no LLM is free — it is optional
  intelligence, never required for the broker to function, and never on by default.
- **Ambiguous tuning/routing judgement** when threshold rules are inconclusive.

**The only thing surfaced to a human** is what a human alone can fix:
`Optimizer.alerts()` returns the rare actionable items — *the whole pool is
under-provisioned for your request rate*, *this API key looks dead* — not a feed
of trivia about individual free LLMs.

**Telemetry backend and what still works.** Two layers act independently:

- **Broker core (always on, no history):** the reactive 429/503 cooldown —
  Available↔Cooling, live `call_later` re-enqueue — runs regardless of telemetry
  backend. It reacts to live responses, not to stored history.
- **Optimizer (learned):** delay tuning, the Offline→Probing→Active recovery, and
  per-`operation` routing. It learns from the **live event stream** (in-memory
  rolling aggregates), so it is **not** gated on a queryable backend — with the
  default `Telemetry()` (log) / `NoTelemetry()` it simply boots **cold** and learns
  from live traffic.
  A **queryable** backend (`sqlite`/`jsonl`/`postgres`) is an accelerator, not a
  gate: it warm-starts those aggregates after a restart and enables ad-hoc
  analysis. This is why `operation` (and tokens/quality) are captured from P1 —
  you cannot warm-start or back-fill data you never recorded.

---

## Shipped batteries

Zero-dependency batteries live at the top level / on the port type (no import
beyond `llmbroker`). A backend that carries an external dependency is a
**submodule** you import explicitly — that import *is* the dependency.

| Port (interface) | Top-level zero-dep classes | Dependency submodules | Phase |
|---|---|---|---|
| `RegistryProtocol` | `llmbroker.Registry(path)` (file: `.toml`/`.json`) | `llmbroker.sqlite.Registry`, `llmbroker.postgres.Registry`, `llmbroker.mongodb.Registry` | registry/sqlite: P1 · pg/mongo: P3 |
| `SecretsProtocol` | `llmbroker.Secrets()` (env, default), `llmbroker.DictSecrets()`, callable adapter | — | P1 |
| `SharedStateProtocol` | — (default = absent, internal in-memory) | `llmbroker.redis.SharedState`, `llmbroker.postgres.SharedState`, `llmbroker.mongodb.SharedState` | seam: P1 · backends: P3 |
| `TelemetryProtocol` | `llmbroker.Telemetry()` (log, default), `llmbroker.NoTelemetry()`, `llmbroker.JsonlTelemetry(path)` | `llmbroker.sqlite.Telemetry`, `llmbroker.postgres.Telemetry`, `llmbroker.mongodb.Telemetry` | log/none/jsonl/sqlite: P1 · pg/mongo: P3 |

Composition is explicit; there is **no `from_sqlite`-style fused factory** (it
would hide the storage choice, the explicit import step, and the shared-state/
telemetry wiring). The constructor + the top-level/submodule factories are the
whole API:

```python
import llmbroker
import llmbroker.sqlite
import llmbroker.redis

llmbroker.Broker(
    registry=llmbroker.sqlite.Registry("broker.db"),       # populate separately via import_from / import_if_empty
    shared_state=llmbroker.redis.SharedState("redis://..."),  # omit for single process
    telemetry=llmbroker.sqlite.Telemetry("broker.db"),
)
```

### Backend submodules and lazy dependencies

- **The one rule: a backend is a submodule you `import` exactly when it carries an
  external dependency.** Dependency-free batteries are top-level / on the port type
  and need only `import llmbroker` — `llmbroker.Registry(path)` (file loader,
  stdlib `tomllib`/`json` by extension), `llmbroker.Secrets()`/`DictSecrets()`,
  `llmbroker.Telemetry()`/`NoTelemetry()`/`JsonlTelemetry(path)`. There is **no**
  "eager submodule" concept and no list to memorize: if it has a dependency it is a
  submodule, if it does not it is top-level.
- Dependency-carrying backends (`sqlite`/`postgres`/`redis`/`mongodb`) are
  submodules; `llmbroker/__init__.py` never imports them, so `import llmbroker`
  stays free of every optional driver. A host does `import llmbroker.sqlite`, and
  *only then* is the driver imported.
- Each backend submodule imports its driver at module top level (`import
  aiosqlite` inside `llmbroker/sqlite.py`, `import redis` inside
  `llmbroker/redis.py`, …). Python 3 absolute imports resolve these to the real
  top-level packages, not to the same-named submodule, so there is no shadowing.
- With a future `pyproject.toml`, each dependency submodule becomes an optional
  extra (`llmbroker[sqlite]`, `llmbroker[redis]`, `llmbroker[postgres]`, …) — one
  extra per submodule.

### The `sqlite` battery owns its schema

`llmbroker.sqlite` self-manages its tables via `ensure_schema(db)`:
`llmbroker.sqlite.Registry` owns the config table `llmbroker_registry`,
`llmbroker.sqlite.Telemetry` owns `llmbroker_calls`. Its primary key is the `Call.id`
uuid (so `record_quality` can `UPDATE … WHERE id=?`). The `llmbroker_calls` schema
includes nullable token/quality columns — `prompt_tokens`, `completion_tokens`,
`total_tokens`, `usage_extra` (JSON), and `quality_score` — so the Optimizer has the
**full** `Usage` and quality history from day one (see the `Call` rationale in "Ports").
The battery persists all of `Call.usage`: the scalar token counts to their columns and
`Usage.extra` to `usage_extra` as JSON; nothing about `Usage` is dropped on persist (the
Optimizer's TPM-budget reasoning needs `total_tokens`). `ensure_schema` is the **single authority** for
the package's schema: no host migration ever builds, alters, or owns these tables
(see "Coexisting with host migration tools").

**The package maintains its own schema across releases, non-destructively.**
`ensure_schema` is idempotent and **version-aware**: it creates missing tables
and, on a DB whose `llmbroker_*` tables predate the running package version,
applies the package's own **additive, data-preserving** migrations (e.g.
`ALTER TABLE … ADD COLUMN`) — never a drop, never data loss. The schema version is
tracked in an `llmbroker_`-prefixed marker the package owns
(`llmbroker_schema_version` row / `PRAGMA user_version`), so a future release can
evolve the shape on its own cadence without touching the host's migrations. P1
ships only the initial `CREATE` plus that version marker; the upgrade path is the
seam later releases hang ALTERs off of.

dinary is the **one exception**, and only because of its pre-extraction history.
Its `llmbroker_*` tables were built by yoyo migrations `0004`/`0005` in an older
shape (a `llmbroker_providers` config table carrying the legacy
`rate_limited_until` / `execution_fail_count` columns). dinary is the package's
single local instance and that table data is disposable, so dinary's Phase 1
migration simply **drops** those tables and hands ownership to the package, which
rebuilds the current shape via `ensure_schema` on the next start (see "dinary
wiring"). This DROP is a one-off dinary cleanup of its own pre-extraction tables —
**not** the package's general upgrade story, which is the non-destructive path
above. The new `llmbroker_registry` config schema defines `name`/`base_url`/
`model`/`api_key_ref` and no `rate_limited_until`/`execution_fail_count` (live
state is in-memory now).

---

## Coexisting with host migration tools

`llmbroker` owns its tables — `llmbroker.sqlite` creates and **non-destructively
evolves** them via `ensure_schema` (see "The sqlite battery owns its schema"). The
host application almost always runs its **own** migration tool over the **same**
database. Two failure modes follow, and the package must prevent both:

1. **Name collision** — an `llmbroker` object clashing with a host object or a
   migration tool's bookkeeping table.
2. **Ownership fight** — a host autogenerate/diff tool seeing the `llmbroker`
   tables as "unknown" and emitting a `DROP` (or demanding they be modeled in the
   host's schema).

### Rule 1 — every DB object carries the `llmbroker_` prefix

Tables (`llmbroker_registry`, `llmbroker_calls`), the schema-version marker, **and
every index, unique-constraint, and trigger** the battery creates are named
`llmbroker_*`. This makes the package's whole footprint filterable by a single
prefix and collision-safe:

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

`llmbroker.alembic` is a backend-style integration submodule (analogous to
`llmbroker.sqlite`): one submodule per external tool. It ships a tiny predicate
that returns `False` for any object whose name begins with `llmbroker_`. Hosts wire
it into their `alembic/env.py`:

```python
import llmbroker.alembic

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    include_object=llmbroker.alembic.include_object,   # autogenerate ignores every llmbroker_* object
)
```

If the host already passes its own `include_object`, the two compose (logical
AND — skip when either says skip). The hook imports nothing from Alembic — it only
inspects the object name — so `import llmbroker.alembic` never pulls in a migration
framework. The README documents this snippet and the per-tool table above as the
"running llmbroker alongside your migrations" section.

---

## Implementation phases

### Phase 1 — extraction + core architecture (do now)

Create `src/llmbroker/` with the broker core (incl. its lazy-start / `aclose()` /
`async with` lifecycle — see "Lifecycle"), the ports, and the file (`.toml`/
`.json`) + `sqlite` `Registry` + `Secrets`/`DictSecrets` + internal in-memory live
state + `Telemetry`/`NoTelemetry`/`JsonlTelemetry`/`sqlite.Telemetry` batteries —
enough to serve Rung 0/1 and carry dinary with unchanged request-path behavior. The
`SharedStateProtocol` port (the cluster seam) is defined in P1; its backends land in
P3. Also capture the Optimizer's future inputs on every call — `operation`
(`ask`/`chat`), full token `usage` (from the response), and a `quality_score` written
back onto the call row by `record_quality` (matched by `Call.id`) — so the data exists
before the `Optimizer` control loop, which itself lands in Phase 4. P1 also ships the
host-coexistence surface: every DB object is `llmbroker_`-prefixed, `ensure_schema`
is version-aware (initial create now; additive data-preserving ALTERs hang off the
version marker in later releases), and `llmbroker.alembic.include_object` is
exported (see "Coexisting with host migration tools"). Because the DB schema is
**private**, P1 also ships the admin API that replaces raw SQL — the
`MutableRegistryProtocol` admin surface (`get`/`add`/`update`/`remove`) and the
`QueryableTelemetryProtocol` read surface (`stats`/`recent`/`purge`) — and reworks dinary's admin to consume it.
dinary's side gets the one-off drop migration that hands schema ownership to the
package.

```
src/llmbroker/
  __init__.py            # top-level surface — ONLY what an app uses:
                         #             Broker, LLM, LifecyclePhase, Result, Optimizer,
                         #             Registry/Secrets/DictSecrets/Telemetry/NoTelemetry/JsonlTelemetry,
                         #             BrokerError/NoLLMAvailable/AllLLMsFailed.
                         #             Protocols (RegistryProtocol/MutableRegistryProtocol/SecretsProtocol/SharedStateProtocol/
                         #             TelemetryProtocol/QueryableTelemetryProtocol) and DTOs (LLMConfig/LLMState/
                         #             Usage/Call/CallStatus/TelemetryStats) are NOT exported here — backend/admin
                         #             authors import them from their defining modules (registry.py/secrets.py/
                         #             shared_state.py/telemetry.py/models.py).
                         #             NEVER imports a dep-carrying backend submodule (sqlite/redis/postgres/mongodb).
  chat.py                # from adapters/llm_chat.py — LLMConfig moves to models.py; receives the resolved
                         #             key from the broker (not off a public field); parses response usage → Usage for Call; else verbatim
  broker.py              # from adapters/llmbroker.py — Broker(Mapping[str, LLM]), the LLM facade,
                         #             cheap __init__ + lazy start + aclose()/__aenter__/__aexit__ (Lifecycle),
                         #             private _resolved_keys (name→secret) + internal LLMState + shared-state reconcile,
                         #             ask() sugar + `wait` capacity bound, tokens/quality_score into Call
  models.py              # LLMConfig (config: name/base_url/model/api_key_ref — no secret),
                         #             LifecyclePhase (enum), LLMState (live state + SharedState wire DTO),
                         #             Usage (provider token report), Call (llm_name/usage/…), CallStatus,
                         #             TelemetryStats (call_count/last_status/last_at)
  state.py               # private in-memory per-LLM live state (always-on; not a public port) → LLMState
  schema.py              # ensure_schema for the sqlite battery: version-aware (creates + applies additive,
                         #             data-preserving ALTERs against an llmbroker_-prefixed version marker);
                         #             llmbroker_registry + llmbroker_calls, all objects llmbroker_-prefixed
  registry.py            # RegistryProtocol + MutableRegistryProtocol (admin layer) Protocols
                         #             + llmbroker.Registry file class (.toml/.json by extension; returns
                         #             pure LLMConfig — broker resolves api_key_ref)  [core, zero-dep: tomllib/json]
  secrets.py             # SecretsProtocol Protocol, llmbroker.Secrets() (env, default), DictSecrets(), callable adapter  [core]
  shared_state.py        # SharedStateProtocol Protocol (cluster seam; backends in postgres/redis/mongodb submodules)  [core]
  telemetry.py           # TelemetryProtocol + QueryableTelemetryProtocol (read layer) Protocols,
                         #             llmbroker.Telemetry() (log, default), NoTelemetry(), JsonlTelemetry(path)  [core]
  sqlite.py              # llmbroker.sqlite.Registry (config; admin CRUD; import_from/import_if_empty)
                         #             + llmbroker.sqlite.Telemetry (llmbroker_calls; record + read surface)  [aiosqlite]
  alembic.py             # llmbroker.alembic.include_object — host migration-tool coexistence (dependency-free)
  cli.py                 # python -m llmbroker env-template <config> | import <config> --into ... --on-conflict ...
  data/
    llms.example.toml
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
  reinstall, and deploy runs from source via `uv run` (not a built wheel). Caveat
  for later: `pyproject.toml` has no explicit `[tool.hatch.build.targets.wheel]`
  package list, so if a distributable wheel is ever built, `llmbroker` must be
  added there — not a concern for the source-based deploy now, but note it before
  any packaging work.
- `llmbroker.py` / `llm_chat.py` have **no `dinary.db` imports** today.
- `llm_storage.py`'s tables have **no FK into dinary's schema** — migration `0005`
  replaced the integer `provider_id` FK with a plain `provider_label` TEXT;
  `execution_id` is a bare TEXT correlation id. The only real coupling is
  `SqliteLLMBrokerStorage` reading `dinary.db.storage.DB_PATH` as a global instead
  of a `db_path` argument.

`src/dinary/adapters/llm_storage.py`, `llm_chat.py`, `llmbroker.py` are
**deleted**. The old SQLite/TOML storage split maps onto the new batteries:
SQLite → `llmbroker.sqlite.Registry` + `llmbroker.sqlite.Telemetry`, **no
`shared_state=`** (live state stays in the broker's internal memory); TOML →
`llmbroker.Registry` + `llmbroker.Telemetry()` (log), no shared state. Per-LLM cooldown/fail
counts are **no longer persisted** (internal in-memory now); the old JSON-sidecar
fail counter is dropped. The config record loses `rate_limited_until` (now an
`LLMState` field); the per-row identifier is `name` (dinary's old `provider_label`
maps onto it). The `api_key` columns/fields become `api_key_ref`, resolved by the
broker via `Secrets` into its private `_resolved_keys` map.

### Phase 2 — example variants + catalog refresh

Add goal-specific `llms.*.example.toml` variants. Optional: an `inv`/CLI command
to refresh the example set from a documented source (e.g. a prompt sourced from
`https://shir-man.com/free-llm/`) with latency/limits/quality notes.

### Phase 3 — cluster + DB batteries

`llmbroker.redis`/`postgres`/`mongodb` `.shared_state`; `llmbroker.postgres`/
`mongodb` `.registry` (with the optional admin CRUD); `llmbroker.postgres`/
`mongodb` `.telemetry`. Each behind an optional dependency extra.
Reconcile-via-refresh as specified; pub/sub and precise-timer left as documented
optimizations.

### Phase 4 — the `Optimizer` (autonomous control loop)

The core value, built once telemetry capture (P1) exists. The Optimizer learns
from the **live event stream** (in-memory rolling aggregates at the
`Telemetry.record()` seam), so it runs on any backend; the **queryable read
surface** (`stats`/`recent` — already shipped in P1 for the admin UI, on
`llmbroker.sqlite`/`jsonl` and `postgres` from P3) is for **warm-start after a
restart and ad-hoc analysis**, not a precondition. The Optimizer reuses that same
read surface rather than introducing its own, deciding warm-start vs cold-boot with
`isinstance(telemetry, QueryableTelemetryProtocol)` (the `@runtime_checkable` layer) —
not `hasattr`. Add a pluggable **selection policy**
seam to the broker (default round-robin). Build the background `Optimizer` that:
computes per-(llm, operation) stats; auto-tunes cooldowns/delays and runs the
offline→probe→active recovery (the state model in "Autonomous optimization");
maintains a per-`operation` routing ranking the broker selection consults; and
exposes `alerts()` for the human-only items (under-provisioned, dead key).
Selection strategy: first 0-wait LLM, else minimal remaining wait — biased by the
routing ranking. Default-on (`optimize=True` ≡ `Optimizer(judge=0.0)`); with the
default `Telemetry()` (log) / `NoTelemetry()` it boots cold (no warm-start) and the
broker keeps its reactive round-robin cooldown until the Optimizer has learned from
live traffic. The LLM-as-judge is enabled only by `optimize=Optimizer(judge>0)`.

### Phase 5 — LLM-in-the-loop deepening (future, not scheduled here)

The Optimizer's *optional* use of an LLM: LLM-as-judge quality scoring on sampled
outputs per (llm, operation) to close the quality loop without host `score()`, and
LLM judgement for ambiguous tuning/routing. Always sampled, off the hot path,
dogfooded through the broker under a low-priority `operation`, and gracefully
skipped when no LLM is free. Plus richer fail statistics (API-key-expiration
diagnostics) and per-LLM Initial/Min/Max delay tuning.

---

## dinary wiring (Phase 1)

dinary is single-process, so it uses explicit composition over its one SQLite file
(`storage.DB_PATH`) for **config + telemetry only**; no `shared_state=` (live state
stays in the broker's internal memory). The config table is populated by an
explicit `import_if_empty` during startup bootstrap (next to `bootstrap_categories`),
not by a constructor side-effect — so a fresh deploy auto-fills once, and
hand-edits or deletions in the table are never clobbered on later restarts.

```python
# src/dinary/main.py — inside the FastAPI lifespan
import llmbroker
import llmbroker.sqlite          # dep-carrying → explicit (file registry is zero-dep, already available)
...
registry = llmbroker.sqlite.Registry(storage.DB_PATH)
llms = llmbroker.Broker(
    registry=registry,
    telemetry=llmbroker.sqlite.Telemetry(storage.DB_PATH),
    # no shared_state= — dinary runs one process, live state stays in memory
)

# in the async startup bootstrap (alongside bootstrap_categories):
await registry.import_if_empty(llmbroker.Registry(_LLM_PROVIDERS_TOML))
...
# on shutdown (end of the lifespan): stop background loops, close connections
await llms.aclose()
```

dinary holds the broker for the whole app lifetime, so it constructs once in the
FastAPI lifespan and calls `await llms.aclose()` on shutdown (the long-lived-app
level of "Lifecycle") rather than wrapping requests in `async with`.

Pulling an updated `.deploy/llm_providers.toml` into an existing DB is then a
deliberate op (`import_from(..., on_conflict="update")` via an `inv` task), never
automatic.

**Admin goes API-only (decided): dinary issues no raw SQL against
`llmbroker_*`.** The schema is now private to the package (see "Ports"), so
dinary's admin (`api/controllers/llm.py`, `api/llm.py`) is reworked to reach every
piece of data through a typed `llmbroker` API:

- **Config / CRUD** → `registry.load()` and the `MutableRegistryProtocol` admin surface
  (`get`/`add`/`update`/`remove`), replacing the raw `db.storage.transaction()`
  SELECT/INSERT/UPDATE/DELETE over the old config table.
- **Live cooldown/fail** → the `Broker` Mapping (`llms[name].state.phase`,
  `.state.cooldown_until`, `.state.fail_count`). The `rate_limited_until`/`execution_fail_count`
  columns are gone after the drop migration; live state is the only source.
- **`used_today`/`last_status` aggregation** → the `QueryableTelemetryProtocol` read
  surface (`stats(since=...)`), replacing the raw aggregation query over `llmbroker_calls`.

The webapp admin LLM page keeps its existing shape: `llm_status()` returns the same
payload keys (`rate_limited_until`, `execution_fail_count`, `used_today`,
`last_status`), now assembled from `llms[name].state` (`cooldown_until`/`fail_count`) +
`telemetry.stats()` instead of table columns, so **no frontend change** is
required. After this rework **no dinary code names `llmbroker_*` tables**: the
per-receipt delete (`tests/api/test_api_delete_receipt.py` currently names
`llmbroker_calls` in a cascade) **stops touching the call log entirely** — the
broker's append-only journal is bounded by `Telemetry.purge(before=...)` retention.

`_DEPLOY_DIR`/`_LLM_PROVIDERS_TOML` move next to the existing `_PROJECT_ROOT` in
`main.py`. dinary's `.deploy/llm_providers.toml` switches to `[[llms]]` sections
with `name`/`base_url`/`model`/`api_key_ref` fields, and its keys move to env / the
deploy secret store (a migration note for ops).

**The drop migration** (next free number after `0006_category_templates` — confirm
at implementation time)**:** **drop** the old `llmbroker_*` objects (the legacy
`llmbroker_providers` config table, `llmbroker_calls`, and any legacy indexes) so
that `llmbroker`'s `ensure_schema` becomes their sole creator and owner. On the
next startup the sqlite battery recreates `llmbroker_registry` (config columns
`name`/`base_url`/`model`/`api_key_ref`, **without** the legacy
`rate_limited_until`/`execution_fail_count`) and `llmbroker_calls` (PK `id`, plus the
`prompt_tokens`/`completion_tokens`/`total_tokens`/`usage_extra`/`quality_score`
columns), and the startup
`import_if_empty` re-fills `llmbroker_registry` from `.deploy/llm_providers.toml`.
This **discards existing local `llmbroker_calls` history once** — acceptable and
intentional: dinary is the package's single local instance, that table data is
disposable, and config is re-imported from the TOML. This DROP is a **one-off
cleanup of dinary's pre-extraction tables**, not how the package upgrades in
general — post-extraction `ensure_schema` evolves its schema non-destructively (see
"The sqlite battery owns its schema"), and yoyo never touches `llmbroker_*` again.
The migration rides the existing migrations deploy machinery (`tasks/deploy.py`
already ships `src/dinary/db/migrations/`), so no deploy change.

| File | Change |
|---|---|
| `src/dinary/background/classification/task.py` | `from dinary.adapters.llmbroker import LLMBroker` → `from llmbroker import Broker`; rename `LLMBroker` references to `Broker` |
| `src/dinary/background/classification/store_resolver.py` | same |
| `src/dinary/background/classification/receipt_classifier.py` | `from dinary.adapters.llmbroker import Execution, LLMBroker` → `from llmbroker import Broker, Result` (rename `LLMBroker`→`Broker`, `Execution`→`Result`). The main `classify` call: `broker.execute(messages, execution_id=…)` → `broker.chat(messages, operation="receipt_classification", trace_id=…)` (default `wait=None` blocks until served), and `if execution.output is None` (broker_unavailable) becomes a `try/except NoLLMAvailable` around it. `get_chain_name`'s `broker.execute(…, wait=False)` → `broker.chat(…, operation="chain_name", wait=0)` inside `try/except NoLLMAvailable: return store_name_raw` — the same graceful skip, now one method with `wait=0` instead of a separate `try_chat` |
| `src/dinary_analytics/llm.py` | `from dinary.adapters.llm_chat import (AllProvidersBusyError, AllProvidersFailedError, ProviderConfig, complete_with_tools)` → errors `NoLLMAvailable`/`AllLLMsFailed` from top-level `llmbroker`, but `LLMConfig` (was `ProviderConfig`) from `llmbroker.models` and chat helpers from `llmbroker.chat` — they are not top-level |
| `tasks/receipt.py` | `LLMBroker(TomlLLMBrokerStorage())` → `Broker(registry=llmbroker.Registry(_PROVIDERS_TOML))` (file registry is zero-dep — available from `import llmbroker`, no extra import) and `_PROVIDERS_TOML = Path(__file__).resolve().parents[1] / ".deploy" / "llm_providers.toml"` |
| `src/dinary/api/controllers/llm.py` | drop all raw SQL over `llmbroker_*`; config CRUD typed against `MutableRegistryProtocol` (`load`/`get`/`add`/`update`/`remove`), aggregation against `QueryableTelemetryProtocol` (`stats`), live cooldown/fail via the `Broker` Mapping (`llms[name].state`) |
| `src/dinary/api/llm.py` | surface live state via the `Broker` Mapping; `llm_status()` assembles the unchanged payload keys from the API surfaces above |

After: `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tests/ tasks/` returns nothing.

---

## Tests (Phase 1)

`tests/llmbroker/` must not import `dinary.*`. Port the existing suites:

- `tests/services/test_llm_chat.py` → `tests/llmbroker/test_chat.py`
  (`patch("dinary.adapters.llm_chat.httpx.Client")` → `patch("llmbroker.chat.httpx.Client")`).
- `tests/services/test_llmbroker.py` → `tests/llmbroker/test_broker.py`; add coverage
  for the `Mapping` surface — `llms[name]` returns an `LLM` facade whose `state.phase`
  is `COOLING` while cooling and `AVAILABLE` once cooldown passes; `name in llms`,
  `len(llms)`, iteration; the facade exposes `.config`/`.state` (objects, not unrolled
  fields); the resolved secret is not on `.config` and not reachable via the facade.
  Cover the **`wait` contract**: with every LLM cooling, `chat(..., wait=0)` raises
  `NoLLMAvailable` at once, `chat(..., wait=0.1)` raises after ~0.1s, and a call whose
  cooldown clears within the wait succeeds; when an LLM is tried and errors, `chat`
  raises `AllLLMsFailed` regardless of `wait`. Also cover the **lifecycle**: `Broker(...)` constructs without a running
  loop and starts no background task until the first `await ask`/`chat`; `aclose()`
  cancels the background loops and calls the ports' `aclose()`; `async with llms:`
  is equivalent to `aclose()` on exit; and `optimize` accepts both `True`/`False`
  and an `Optimizer(judge=...)` instance (shape only — no judge loop in P1).
- `tests/services/test_llm_storage.py` splits into `test_registry_toml.py`,
  `test_registry_sqlite.py`, `test_telemetry.py` (and `test_state.py`), each
  adapting `SqliteLLMBrokerStorage()` → the new battery with an explicit
  `db_path = tmp_path / "test.db"` and `ensure_schema` (no yoyo migrations in
  package tests). The `_label_from_base_url` logic is gone — `name` is authored, not
  derived. The old SQLite cooldown/fail-count persistence tests are dropped (live
  state is internal in-memory); cooldown/fail behavior is covered by `test_state.py`.
- New `test_secrets.py`: `llmbroker.Secrets()` (env) resolves from `os.environ`;
  `llmbroker.DictSecrets()` from a map; the broker resolves `api_key_ref` into its
  private `_resolved_keys` (not onto `LLMConfig`); missing ref raises a clear error.
- New `test_state.py`: the broker's internal live state — a cooling LLM reports
  `LifecyclePhase.COOLING` (and is absent from rotation) until cooldown passes; idempotent
  records.
- New `test_cli_env_template.py`: scanning a TOML emits the expected `.env` skeleton
  (all `api_key_ref` names, blank values, no secrets).
- `test_broker.py` / `test_telemetry.py` assert that `operation` and the **full** token
  `usage` (`Call.usage.prompt_tokens`/`.completion_tokens`/`.total_tokens`, and `.extra`
  round-tripped through `usage_extra` JSON) flow into the recorded `Call`, and that the
  `Call.id` uuid is populated. Quality is asserted **without a second row**: after one
  `chat`, `Result.record_quality(0.0)` on a `sqlite.Telemetry` leaves `call_count`
  unchanged and sets `quality_score=0.0` on the **same** row (matched by `id`); on the
  log `Telemetry()` it appends a distinct quality record, **not** a `Call`. `Result.usage`
  exposes the same `Usage`.
- **Drop-migration test** (dinary-side, `tests/services/`, needs
  `dinary.db.db_migrations`): after applying migrations through the drop, the legacy
  `llmbroker_providers` and `llmbroker_calls` are **absent** (`PRAGMA table_info`
  empty / `sqlite_master` has no such table). No more yoyo-vs-`ensure_schema`
  equivalence test — yoyo no longer builds the package schema.
- **`ensure_schema` rebuild test** (package-side, `tests/llmbroker/`): on an empty DB
  (or one just dropped), `ensure_schema` creates `llmbroker_registry` (with `name`/
  `base_url`/`model`/`api_key_ref`, **without** `rate_limited_until`/
  `execution_fail_count`) and `llmbroker_calls` (PK `id`, plus `prompt_tokens`/
  `completion_tokens`/`total_tokens`/`usage_extra`/`quality_score`); every created
  object name starts with `llmbroker_`; running it twice is a no-op. The version-aware additive-upgrade path
  is exercised when the first ALTER actually ships (no ALTERs exist in P1 beyond the
  initial create).
- New `test_alembic.py` (package-side): `llmbroker.alembic.include_object` returns
  `False` for any `llmbroker_*` object name and `True` otherwise; composing with a
  host predicate skips when either says skip.
- `test_telemetry.py` covers the read surface: `stats(since=...)` aggregates
  `call_count`/`last_status`/`last_at` per LLM from recorded `Call`s; `recent(limit=...)`
  returns latest events; `purge(before=...)` deletes rows older than the cutoff and
  returns the count; the default `Telemetry()` (log) / `NoTelemetry()` do **not** expose the read surface.
- **Protocol-layer membership** (`test_telemetry.py` / `test_registry_*`): assert the
  `@runtime_checkable` layers classify batteries correctly —
  `isinstance(llmbroker.sqlite.Telemetry(...), QueryableTelemetryProtocol)` is `True`
  while `isinstance(llmbroker.Telemetry(), QueryableTelemetryProtocol)` is `False` (it is
  still a `TelemetryProtocol`); `isinstance(llmbroker.sqlite.Registry(...),
  MutableRegistryProtocol)` is `True` while the file `llmbroker.Registry(...)` is a
  `RegistryProtocol` but **not** a `MutableRegistryProtocol`.
- `tests/api/test_admin_llm.py`: rewrite for the **API-only** admin — assert the
  controller issues **no raw SQL** over `llmbroker_*` and that the `llm_status`
  payload is assembled from the API: `rate_limited_until`/`execution_fail_count`
  from the `Broker` Mapping (`llms[name].state.cooldown_until`/`.state.fail_count`),
  `used_today`/`last_status` from `QueryableTelemetryProtocol.stats()`; config CRUD round-trips
  through the `MutableRegistryProtocol` admin surface. The existing assertion that
  `execution_fail_count` is present in each entry stays.
- Mechanical import updates in dinary-side tests referencing the broker:
  `test_main.py`, `test_store_resolver.py`, `test_receipt_classifier.py`,
  `test_receipt_classification.py`, `test_receipt_pipeline_e2e.py`,
  `test_receipt_drain.py`, `test_receipt_pipeline.py`, `test_llm.py`,
  `tests/conftest.py` (the `NullStorage`/`real_llm_seed` fixtures keep their logic;
  the fixture that pre-populated config now calls
  `llmbroker.sqlite.Registry(...).import_from(...)` explicitly instead of relying on
  constructor seeding).
- `tests/api/test_api_delete_receipt.py` currently names `llmbroker_calls` in raw
  SQL; update it so the per-receipt delete **no longer touches the call log** (the
  cascade is removed — retention via `Telemetry.purge(before=...)` bounds growth),
  so no dinary code names the package's tables.
- New `test_registry_sqlite.py` covers `import_from` policies (`skip` leaves
  existing rows; `update` upserts; `replace` wipes-then-inserts) and `import_if_empty`
  (fills an empty store, no-ops on a populated one).

Every new battery, the `Secrets` resolvers, `ask()`, the import operations, and the
`env-template`/`import` CLI ship with tests in the phase that introduces them.

---

## Specs (Phase 1)

- `specs/reference/llm-providers.md`: trim to dinary-specific concerns (LLM pool
  rationale, prompt design, models to avoid). Remove broker-internals sections
  (queue round-robin, storage Protocol, …). Add one paragraph: dinary runs
  `llmbroker` via explicit `llmbroker.sqlite.Registry` + `llmbroker.sqlite.Telemetry`
  over `storage.DB_PATH` (no `shared_state=` — one process, live state in memory),
  with config imported (once, if empty) from `.deploy/llm_providers.toml`, keys via
  `api_key_ref` + env; the sqlite battery owns `llmbroker_registry`/`llmbroker_calls`
  (`ensure_schema`); migrations `0004`/`0005` created the tables historically, a new
  migration drops them so `llmbroker`'s `ensure_schema` owns the schema (recreated on
  next start as `llmbroker_registry` with `name`/`base_url`/`model`/
  `api_key_ref` and `llmbroker_calls` keyed by `id` with the `prompt_tokens`/
  `completion_tokens`/`total_tokens`/`usage_extra`/`quality_score` columns, without the
  legacy `rate_limited_until`/`execution_fail_count`). Note that the package coexists with dinary's yoyo
  migrations via the `llmbroker_` object prefix — yoyo never touches those tables
  after the drop. The schema is **private to the package**: dinary's admin reaches
  config, live state, and call-log aggregates through the `llmbroker` API (no raw SQL
  over those tables). Per spec rules, do not link the package README (specs link only
  specs).
- `specs/reference/architecture.md`: add `src/llmbroker/` to the source layout —
  "standalone, host-agnostic LLM broker; round-robin failover, rate-limit handling;
  pluggable `Registry`/`Secrets`/`Telemetry` + opt-in `SharedState` for clusters;
  batteries for TOML/SQLite/Postgres/redis/MongoDB; owns its own `llmbroker_`-prefixed
  schema (`ensure_schema`, version-aware) and coexists with host migration tools
  (Alembic `include_object` hook, prefix filtering); no `dinary` imports; will move to
  its own repo/PyPI package."

---

## Package README (`src/llmbroker/README.md`)

The Rung 0→2 ladder above is the README. It records current capabilities
(round-robin queue, one in-flight request per LLM, per-LLM 429/503 cooldown honoring
`Retry-After`, the `Broker` as a `Mapping[str, LLM]`, pluggable
`RegistryProtocol`/`SecretsProtocol`/`TelemetryProtocol` plus opt-in `SharedStateProtocol` for
clusters, the `MutableRegistryProtocol`/`QueryableTelemetryProtocol` admin layers, secrets
indirection so no key lives in config, `operation`-tagged
telemetry, the `llmbroker_`-prefixed self-owned schema and
`llmbroker.alembic.include_object` coexistence hook) and the `Optimizer` roadmap
(autonomous self-tuning + operation routing; optional LLM-in-the-loop quality judging
behind `Optimizer(judge>0)`). **The README must keep this boundary sharp: describe
the reactive behavior as what ships, and the Optimizer/judge as roadmap — never as
working features.** Concretely, the README states that in P1 the broker is reactive
only (round-robin + 429/503 cooldown), `optimize=True` is the reserved default that
runs no optimizer until P4 (so the constructor reads the same before and after), and
`LifecyclePhase.OFFLINE`/`PROBING` are reserved codes that **never occur** in P1 —
`llms[name].state.phase` is only ever `AVAILABLE` or `COOLING` until the Optimizer
lands. The types are locked now purely so P4/P5 add no breaking change. It documents the **admin API** — config CRUD via the
`MutableRegistryProtocol` admin surface, call-log aggregates via the `QueryableTelemetryProtocol`
read surface (`stats`/`recent`/`purge`), and live state via the `Broker` Mapping (`llms[name].state`) — as
the way to build an admin UI, noting the **DB schema is private** (no raw SQL). It
documents the **naming convention** (bare name = default battery
`llmbroker.Registry`/`Secrets`/`Telemetry`; variant = `DictSecrets`/`NoTelemetry`/
`JsonlTelemetry`; dependency backend = `llmbroker.<backend>.<Port>`; interface =
`<Port>Protocol`, with capability layers as `<Capability><Port>Protocol`
— `MutableRegistryProtocol`, `QueryableTelemetryProtocol`) and the one battery rule plainly: dependency-free batteries are
top-level classes needing only `import llmbroker` (`llmbroker.Registry(path)`,
`llmbroker.Secrets()`, `llmbroker.Telemetry()`/`JsonlTelemetry(path)`); a backend with
an external dependency is a submodule imported explicitly (`import llmbroker.sqlite`,
constructed fully qualified as `llmbroker.sqlite.Registry(...)`); **never `from
llmbroker import sqlite`.** It documents the **lifecycle** (cheap constructor, lazy
start on first call, `aclose()` / `async with` for teardown, broker owns its ports;
see "Lifecycle"). It documents the async `Broker` as the core (with the deferred
`SyncBroker` facade for sync-only hosts) and the rule that a member is `async` iff it
does I/O. It includes the
"running llmbroker alongside
your migrations" section (the per-tool table + the Alembic snippet from "Coexisting
with host migration tools"). It states plainly that `llmbroker` is a **library, not a
server** — wrap it in your own web framework if you need an HTTP gateway. Both the
import name and the distribution name are `llmbroker`.

---

## Verification

1. `uv run inv pre` → "All checks passed!" + `0 errors`.
2. `uv run pytest` → all green, incl. `tests/llmbroker/`.
3. `grep -rn "dinary.adapters.llm_chat\|dinary.adapters.llmbroker\|dinary.adapters.llm_storage" src/ tests/ tasks/` → empty.
4. `uv run python -c "import llmbroker, llmbroker.sqlite, llmbroker.alembic; print(llmbroker.Broker, llmbroker.LLM, llmbroker.LifecyclePhase, llmbroker.Result, llmbroker.Registry, llmbroker.JsonlTelemetry, llmbroker.NoTelemetry, llmbroker.DictSecrets, llmbroker.Secrets, llmbroker.NoLLMAvailable, llmbroker.alembic.include_object)"`.
   Also assert: `import llmbroker` alone does **not** import `aiosqlite` (no dep-carrying submodule pulled); the protocols/DTOs are **not** top-level (`hasattr(llmbroker, "RegistryProtocol")` is `False`) and import from their modules (`from llmbroker.registry import RegistryProtocol, MutableRegistryProtocol; from llmbroker.telemetry import TelemetryProtocol, QueryableTelemetryProtocol; from llmbroker.shared_state import SharedStateProtocol; from llmbroker.models import LLMConfig, LLMState, Usage, Call`).
5. `uv run python -m llmbroker env-template src/llmbroker/data/llms.example.toml` prints a `.env` skeleton.
6. Smoke: applying the drop migration leaves no legacy `llmbroker_providers`/
   `llmbroker_calls` tables; `uv run inv dev` then starts, `ensure_schema` creates
   `llmbroker_registry` + `llmbroker_calls` (current shape, all objects
   `llmbroker_`-prefixed) and `import_if_empty` fills `llmbroker_registry`; a second
   start no-ops both; the admin LLM API reads `llmbroker_registry`/`llmbroker_calls`
   and overlays live state from the `Broker` Mapping.

---

## Open design questions (decide when the phase needs them)

- **Single-process state durability** (P1): we drop all single-process live-state
  persistence (the old SQLite columns + the TOML JSON sidecar) on the principle that
  ephemeral cooldown is not worth persisting. The Optimizer's learned state is a live
  in-memory aggregate of the event stream (see "Autonomous optimization"); whether it
  is checkpointed to its own table for a fast warm start or simply recomputed from the
  journal is a **P4 decision**, deferred. The P1 invariant is only that the
  append-only `Call` journal stays rich enough to reconstruct it. If a real need for
  restart-resilient cooldown on one node ever appears, add an opt-in file-backed state
  — not shipped now.
- **Example-file variants** (P2): how many goal-specific TOMLs, and the
  refresh-from-source workflow / format of latency/limits/quality notes.
- **`SharedState` write semantics under concurrency** (P3): the P1 seam is a
  whole-`LLMState` `write(name, state)` (last-writer-wins). Whether to keep that or
  split into per-field updates (e.g. an atomic `fail_count` increment) to avoid two
  copies clobbering each other's fields is the deferred decision.
- **Optimizer design** (P4): the read API on queryable telemetry (warm-start + ad-hoc
  analysis); whether the in-memory aggregate is checkpointed to its own table or
  recomputed from the journal on start; the broker's selection-policy seam; how the
  routing ranking is computed and how aggressively it overrides round-robin.
- **LLM-in-the-loop cost/safety** (P5): sampling rate for LLM-as-judge quality
  scoring; the judge prompt/rubric per operation; guarding token spend; how the judge
  avoids starving real traffic on a busy pool. When the judge lands, add a
  `quality_source` column to `Call` (host `score()` = ground truth vs judge = noisier)
  so the router can weight the two by confidence; pre-P5 rows are all host-sourced, so
  nothing is lost by deferring it.
- **Per-LLM Initial/Min/Max delay** (P5): individual vs one global; computed vs fixed
  KISS schedule (lean KISS first).
- **Optional `Telemetry` read-surface shape** (P1, decided minimally): the methods
  shipped now are exactly what dinary's admin needs (`stats`, `recent`, `purge`);
  richer query/filtering (date ranges, per-`operation` breakdowns, pagination) is added
  when a consumer needs it, without breaking the existing signatures.

---

## Explicitly out of scope (this plan)

- **Performing the extraction itself** — giving `src/llmbroker/` its own
  `pyproject.toml`, repo, and PyPI release. That happens **only once all phases are
  implemented** (see "Trajectory"), not after Phase 1 and not inside this plan; through
  every phase the package stays in-tree and internal to dinary. The PyPI name `llmbroker`
  is already reserved.
- **Any HTTP / server layer.** `llmbroker` is a library; a microservice gateway is a
  host concern, built on the host's own web framework.
- The `Optimizer` itself (P4) and its LLM-in-the-loop deepening (P5) — only the
  `operation` data capture and the selection-policy seam are designed now.
- **Token streaming (`stream()`)** — a real capability a universal LLM broker will
  eventually need (chat UIs, agents), but deliberately **not built in P1**. This is a
  recorded gap, not an oversight: `chat` returns a `Result` handle (not a bare string),
  so a later `stream()` can hang off the same object and finalize `usage`/`quality_score`
  on stream completion — no Protocol break. Defer until a consumer needs it.
- **Typed `Message` for `chat`** — `chat` takes `messages: list[dict]`, a deliberate
  honest pass-through of the provider wire format (content parts, tool calls, `name`,
  …), which a strict `TypedDict` would either over-constrain or pointlessly widen. A
  documentation/IDE-aid `Message` TypedDict is a possible later ergonomic addition;
  the `list[dict]` input stays accepted, so adding it is non-breaking. Not built now.
- **Per-call model override** — `ask`/`chat` take **no** `model=` parameter. The
  model is part of an `LLM`'s identity (`LLMConfig.model`), not a per-call knob:
  the broker selects the `LLM` — hence the model — at call time, so a `model=` arg
  would be sent to whichever provider rotation happened to pick, which is
  meaningless. A host that wants a specific model configures it as its own `LLM`
  entry and routes to it via `operation`. (dinary never had this override — the
  model has always come from the provider config.)
- **Provider-specific parameters** — `ask`/`chat` take **no** per-call provider
  passthrough (no `provider_params`, no `**kwargs`). A raw body dict (temperature,
  tools, `response_format`, …) is provider-shaped, and the broker selects the LLM —
  hence the provider — at call time, so it cannot know whose schema a passthrough
  targets; routing the same dict to a different provider would silently send the
  wrong fields. If a real need to influence requests appears, the likely design is
  **`llmbroker`'s own provider-agnostic knobs** (e.g. a normalized `temperature`/
  `max_tokens`/`tools` surface) that each provider adapter **translates** into its
  wire format, **not** a raw passthrough. Deferred until a consumer needs it; the
  shape (where the knobs live, how adapters map them) is decided then.
- **Sync facade (`SyncBroker`)** — a blocking client for hosts not on asyncio
  (Flask sync, CLI, Django sync). The async `Broker` stays the core because the
  concurrency model (per-LLM queue slot, one in-flight request, cooldown
  re-enqueue) is asyncio. `SyncBroker` wraps a `Broker` running on a **dedicated
  background event-loop thread**; its sync `ask()`/`chat()` submit the coroutine
  and block, so the pool's concurrency persists across calls (unlike a per-call
  `asyncio.run`, which would tear down and rebuild the pool every time). This is
  the established two-client pattern (`httpx.Client`/`AsyncClient`,
  `openai.OpenAI`/`AsyncOpenAI`). Purely additive — no Protocol break — so it lands
  when a sync consumer appears; dinary is async (FastAPI) and needs only the async
  core now. unasync-style codegen is rejected for this package: the asyncio
  concurrency core cannot be produced by stripping `await`.
- Renaming the import or distribution name `llmbroker`.
- A standalone HTTP admin surface in the package. dinary's admin **is** reworked in P1
  to be API-only (see "dinary wiring") — config CRUD through the `MutableRegistryProtocol`
  admin surface, aggregation through `QueryableTelemetryProtocol.stats()`, live state through the `Broker`
  Mapping — but it remains dinary's own FastAPI endpoints consuming the library;
  `llmbroker` ships no admin HTTP layer of its own.
