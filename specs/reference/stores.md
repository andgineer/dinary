# Stores and Shop Chains

## Data model

Two tables separate physical store locations from retail brands:

| Table | Purpose |
|---|---|
| `stores` | One row per physical store location (unique PIB or unique raw name when no PIB) |
| `shop_chains` | One row per retail brand — many stores share one chain |

```
shop_chains : id, name (UNIQUE)
stores      : id, name, chain_id → shop_chains, pib (UNIQUE)
```

`stores.name` is the raw store name from the fiscal receipt (e.g. "LIDL SRBIJA KD").
`shop_chains.name` is the LLM-normalised brand (e.g. "Lidl").

A chain like "Lidl" has many store rows, each with a different PIB. `chain_name` is
never unique across stores — only `pib` is.

## PIB as the primary identifier

PIB (Serbian tax ID) is the canonical identifier for a store location. Every fiscal
receipt must carry a PIB; its absence is an error condition (logged at ERROR level).

Partial unique index `stores_name_no_pib ON stores (name) WHERE pib IS NULL` prevents
duplicate no-PIB records for the same raw name. SQLite's `TEXT UNIQUE` allows multiple
NULLs, so the explicit partial index is required.

## Store resolution (`background/classification/store_resolver.py`)

`resolve_store(broker, store_pib, store_name_raw)` returns a `store_id`:

1. **Cache lookup** (one connection, closed before any LLM call):
   - With PIB: `SELECT WHERE pib = ?`
   - Without PIB: log ERROR; `SELECT WHERE name = ? AND pib IS NULL`
   - Return immediately on hit.

2. **LLM normalisation** (no connection held):
   - `get_chain_name` via `broker.try_complete()` — returns raw name if no provider available.
   - Result is stripped; falls back to raw name if empty.

3. **Upsert** (one connection):
   - `INSERT OR IGNORE INTO shop_chains (name)` + `SELECT` → `chain_id`
   - `INSERT OR IGNORE INTO stores (name, chain_id, pib)`
   - `SELECT` by pib or name → return `store_id`

The INSERT + SELECT-by-pib pattern is safe under concurrent receipt processing:
if a racing task wins the INSERT, `SELECT WHERE pib = ?` still finds that row.

## Chain name normalisation

The LLM is prompted to return the canonical brand name in proper case, stripping
legal suffixes (d.o.o., k.d., a.d.), country/region words, and store-type words.
No existing chain list is passed to the LLM — providing it would anchor the model
to existing names and prevent correct identification of genuinely new chains.

Occasional LLM inconsistency (e.g. "Lidl" vs "Lidl Srbija" on different receipts)
produces duplicate `shop_chains` rows. Deduplication is deferred to a future
maintenance task — the analytical layer joins on `chain_id` so fixing duplicates
only requires updating `stores.chain_id` rows.

## Every store always has a chain

`resolve_store()` always calls `get_chain_name` before inserting a new store and
sets `chain_id` on the `stores` row at creation time. `stores.chain_id` is never
NULL in practice — the column is nullable only to avoid a circular FK dependency
during schema creation.

This guarantee means code that needs the chain for a store can always do a single
`JOIN stores s ON s.id = e.store_id JOIN shop_chains sc ON sc.id = s.chain_id`
without NULL-guarding the chain side.

## Classification rules attach to chains, not stores

`classification_rules.chain_id` references `shop_chains(id)` — **not** `stores(id)`.

A rule learned from one Lidl branch (e.g. "mleko" → Dairy) applies automatically
to all other Lidl branches. Writing rules at store granularity would waste LLM calls
and leave new branches without coverage until they accumulate their own history.

The lookup precedence in `classify_by_rules()`:
1. Chain-specific rule (`chain_id IS NOT NULL, chain_id = ?`) — highest priority.
2. Generic rule (`chain_id IS NULL`) — fallback when no chain rule exists.

When `resolve_store()` returns a `store_id`, callers must resolve `chain_id` from
`stores.chain_id` before calling `classify_by_rules()` or `create_or_update_rule()`.
