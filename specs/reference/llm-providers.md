# LLM Provider Strategy

The provider pool is llmbroker's curated free-tier list, merged into the broker's
registry on startup — see [llmbroker-integration.md](llmbroker-integration.md). This
document covers what dinary relies on that pool for.

## Provider identity

Each provider's name is its stable identifier: telemetry, quality history, and the
user-disable latch all key on it. A provider renamed upstream orphans its history and
starts over as a new entry. The name is also what the admin screen shows.

## Provider pool rationale

Which endpoints are worth pooling is llmbroker's curation, not dinary's: the list is
multi-provider and free-tier only, one model per provider, and it changes without a
dinary release. What dinary requires of it is a pool wide enough that a single
provider's rate limit never stops classification — the failover below is the whole
reason a pool exists rather than one configured model.

A key is needed per provider, and the pool routes over whichever keys are present:
a provider with no key stays inactive rather than failing anything. Keys and how to
obtain them are in [llmbroker-integration.md](llmbroker-integration.md).

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
call that created the corrected rule. When a model's window drops far enough it is
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
