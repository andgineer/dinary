# LLM Provider Strategy

## Broker design

`LLMBroker` knows nothing about receipts or categories. It accepts OpenAI-style
messages and returns a string. This isolation is intentional: the broker is
designed to be extractable as a standalone package. All receipt and category
business logic lives in the classification layer above it. See
[classification-pipeline.md](classification-pipeline.md) for how the pipeline
uses the broker.

The broker depends on a Protocol for storage, not a concrete DB module. This
keeps the broker's hot path (provider selection, HTTP call, rate-limit tracking)
free of any SQLite dependency.

Rate-limit state is tracked in memory for immediate response and persisted to DB
for survival across restarts. The in-memory path avoids a DB write on every
provider call; the persistence path ensures a restarted process doesn't hammer a
provider that was already cooling down.

## Provider identity

`label` is the stable identifier for a provider. It is used as the primary key in
call-log history, quality-failure stats, and any cross-session or cross-backend
linking. Once a provider is in use, its `label` must not be changed — doing so
orphans all historical records for that provider. The label is also what is shown
in the UI, so pick a short, human-readable name when adding a provider
(e.g. `"Groq"`, `"OpenRouter-GPT"`, `"Gemini"`).

## Storage implementations

`SqliteLLMBrokerStorage` is the production implementation: persists call events
and rate-limit state to SQLite, seeds providers from `.deploy/llm_providers.toml`
on startup when the table is empty.

`TomlLLMBrokerStorage` is the CLI/standalone path: reads providers from TOML
directly, logs calls via Python's logging module, writes nothing to a database.
Used by the `inv classify-receipt` task where no long-lived DB process is
running.

`NullStorage` (no-op) is test infrastructure only; it lives in `tests/conftest.py`,
not in production code.

## Provider pool rationale

Provider selection (tested 2026-05-07/08 against real Serbian fiscal receipts):

- **Groq / llama-3.3-70b-versatile** — primary. Best classification quality and
  speed. Correctly handles non-food items (clothing) and Serbian vocabulary without
  any Serbian-specific fine-tuning.
- **OpenRouter / gpt-oss-120b:free** — first fallback. Good quality; occasional
  conservative confidence on ambiguous items is acceptable.
- **OpenRouter / nemotron-3-super-120b-a12b:free** — second fallback. Similar
  quality to above. Same API key as the first fallback, so shares a rate limit
  bucket — they do not provide independent throughput.
- **Google Gemini / gemini-2.5-flash** — third fallback. Equivalent classification
  quality to Groq but capped at 20 RPM and intermittently returns 503 under load.
  Lower priority for these reasons; provides genuine independence from the
  OpenRouter bucket.

The two OpenRouter entries use the same API key. If both hit 429 simultaneously,
Gemini provides the only real fallback — that's why it stays in the pool despite
its limitations.

## Failover strategy

On 429 or 503: move immediately to the next provider without waiting. The drain
processes jobs at a steady personal-tracker pace; at normal load the pool is
never exhausted. Rate-limit cooldown is tracked per provider so the primary is
preferred again as soon as its window resets.

## Prompt design principles

- Single batch call per receipt: all normalised item names plus store name plus
  the full active category list in one request. One call per receipt avoids per-item
  latency and allows the model to use item context (a receipt from a clothing store
  disambiguates borderline items).
- Category IDs are passed as `id: group: name` tuples. The group context helps
  models correctly assign items to broadly-named categories (e.g. "фрукты" as the
  catch-all for all produce, not just fruit).
- Alternatives are always requested unconditionally. See
  [classification-pipeline.md](classification-pipeline.md) for the rationale.
- The model is explicitly instructed not to guess tags — only assign tags from the
  provided active set.

## Models to avoid

Cerebras / llama3.1-8b failed to classify non-food items (Serbian vocabulary for
clothing) and returned unclassified, causing receipt total mismatches. Not suitable
regardless of its rate-limit advantages.
