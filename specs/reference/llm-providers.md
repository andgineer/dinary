# LLM Provider Strategy

The provider list is defined solely in the preset file and mirrored into the broker on
startup — see [llmbroker-integration.md](llmbroker-integration.md). This document covers
which providers to pick and why.

## Provider identity

Each provider's name (from the preset file) is its stable identifier: telemetry, quality
history, and the user-disable latch all key on it. Renaming a provider in the preset
orphans its history, so treat the name as fixed once a provider is in use. The name is
also what the admin screen shows, so keep it short and human-readable
(e.g. `groq-llama-3.3-70b`).

## Provider pool rationale

Provider selection (validated against real Serbian fiscal receipts):

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
processes jobs at a steady low-volume pace; at normal load the pool is
never exhausted. Rate-limit cooldown is tracked per provider so the primary is
preferred again as soon as its window resets.

## Quality tracking

llmbroker keeps a rolling quality window per provider for the receipt-classification
operation, fed by the ratings described in
[llmbroker-integration.md](llmbroker-integration.md): accepted replies count positive,
malformed replies count negative, and user corrections feed a delayed verdict on the
model that created the corrected rule. When a model's window drops far enough it is
demoted for that operation (deprioritised in routing); positive ratings let it recover.
The admin screen surfaces both the demotion flag and a numeric quality indicator so
operators can spot unreliable providers without digging through server logs.

## Prompt design principles

- Single batch call per receipt: all normalised item names plus store name plus
  the full active category list in one request. One call per receipt avoids per-item
  latency and allows the model to use item context (a receipt from a clothing store
  disambiguates borderline items).
- Category IDs are passed as `id: group: name` tuples. The group context helps
  models correctly assign items to broadly-named categories (e.g. "fruit" as the
  catch-all for all produce, not just fruit).
- Alternatives are always requested unconditionally. See
  [classification-pipeline.md](classification-pipeline.md) for the rationale.
- The model is explicitly instructed not to guess tags — only assign tags from the
  provided active set.

## Models to avoid

Cerebras / llama3.1-8b failed to classify non-food items (Serbian vocabulary for
clothing) and returned unclassified, causing receipt total mismatches. Not suitable
regardless of its rate-limit advantages.
