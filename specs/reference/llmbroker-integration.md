# llmbroker integration

dinary uses `llmbroker` as an external PyPI dependency for all LLM access. One
`AsyncBroker` instance lives for the full FastAPI application lifetime; a short-lived
synchronous `Broker` serves the analytics chat, which keeps its own model list and
keys and never touches the server's database.

## Broker design

The broker knows nothing about receipts or categories. It accepts OpenAI-style
messages and returns a reply. All receipt and category business logic lives in the
classification layer above it — see
[classification-pipeline.md](classification-pipeline.md) for how the pipeline uses
the broker.

The server process backs the broker with SQLite: the provider registry, call
telemetry, API-key secrets, the persistent user-disable latch, and the model-quality
learning window all live in `llmbroker_`-prefixed tables that the package creates and
migrates itself. dinary never issues SQL against those tables.

## Provider catalog: llmbroker's curated free-tier list

dinary follows llmbroker's curated free-tier model list and maintains no provider
file of its own. On every startup — at the same point where dinary's own schema is
migrated — that list is merged into the registry: providers added, changed, or
removed upstream appear, change, or disappear in the pool. There is no other write
path: no API and no UI can add, edit, or delete a provider.

The merge needs the network and is best-effort. A startup that cannot reach the
curated list serves the registry as it stands, and a deployment whose registry is
still empty starts anyway — everything that is not receipt classification keeps
working.

Configuration exists for an installation that must make no outbound connection of
its own: the list it follows, and the refresh interval, are both settings, and
switching the interval off stops every llmbroker clock.

## API keys

For the server, API keys live in the database. `.deploy/.env` is only the bootstrap
source: on startup, a key that is not yet resolvable in the database is seeded from its
env var, and from then on the database copy is authoritative — later env changes do not
overwrite it. Changing an already-seeded key is a manual database operation. Generate
the env-var stubs the curated list needs with `llmbroker env freetier`.

The analytics chat is the exception: it never touches the server's database, so its
broker resolves keys from its own process environment, and the dashboard launcher
exports every value it can resolve from `.deploy/.env` under the ref names the
curated list declares. A key changed only in the server's database therefore does not
reach analytics; its broker state (cooldowns, quality, the user disable) is likewise
separate, so a provider disabled on the LLM screen stays available to the chat.

A provider whose key cannot be resolved is reported on the LLM screen as such, with
whatever onboarding hint llmbroker supplies for that key. llmbroker supplies one only
where its registry carries key metadata, which a database-backed registry does not,
so the server's screen currently shows the state without the hint.

## Admin screen: read-only status plus a user disable

The LLM screen is read-only except for one control. For every provider it shows
availability (available, cooling down, no key, or disabled by the user), usage counters
and the last call status, and the model's quality of work — whether it is demoted for
receipt classification, plus a numeric quality indicator once ratings exist.

The one mutation is a persistent disable: the user can disable a provider and re-enable
it later. The verdict is stored by llmbroker, survives restarts and model-list merges, and
excludes the provider from routing until it is explicitly re-enabled.

## Model quality learning

Quality feedback for receipt classification flows back to the model that did the work:

- A classification reply that dinary accepts counts immediately as a positive rating.
- A malformed reply counts as a negative rating.
- A user category correction rates the model that created the corrected rule, across
  restarts and for as long as llmbroker still holds the call — its rating window is
  finite, and a correction that arrives after the call has left it is applied to the
  rule and rates nothing. The rule remembers the broker call that created it.
  Re-selecting the category the model already chose confirms it and is not rated at
  all. Correcting to one of the alternatives that model itself proposed earns partial
  credit; any other target is a full negative. Because the correction flips the rule
  away from its llm origin, a second correction of the same rule rates nothing.

**One call is one verdict.** A rating names the call it rates, and llmbroker keeps one
observation per call: a correction therefore replaces that receipt's acceptance rating
rather than adding a second one, and several corrections against the same receipt still
count once. So a receipt is rated by its final standing — accepted and never corrected,
or corrected — and a model is not penalised in proportion to how many items of one
receipt the user happened to revisit.

Rules created directly from user corrections carry no call and are never rated.

Rating is a side effect, never a precondition: a failure to record one is logged and
the correction stands.

## Version pinning

dinary pins `llmbroker` with an exact version (`==`). The package evolves actively and
breaking API changes between minor versions are expected. Bump deliberately: update the
pin, run the full test suite, commit.
