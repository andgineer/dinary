# Plan: the analyst can answer questions about stores and receipt items

## Why

The ledger records where money was spent and what was bought: 1 536 of the 2 049 expenses of
the last 12 months carry a store, and the replica holds 1 536 receipt items and 16 stores
(measured 2026-11-12). The analyst is told about none of it, so "how much did I spend at Lidl"
and "how much on coffee beans this year" — a pain point `architecture.md` names as a reason
the project exists — cannot be answered.

## Target behaviour

The dashboard chat and MCP clients can answer questions by store, by shop chain and by
receipt item, and can combine them with categories, events and tags.

## Design

Add four tables to `LEDGER_TABLES` in `src/dinary_analytics/connection.py`, each with its note.
`ledger_schema()` reads their real definitions from the replica, so no schema text is written
by hand.

| Table | Note for the model |
|---|---|
| `stores` | a shop; `chain_id` links shops of one chain; `pib` is the Serbian tax ID |
| `shop_chains` | a retail chain; group stores by it to answer "at Lidl" across branches |
| `receipts` | a scanned fiscal receipt; `total_amount` is in the receipt's own currency (RSD), not the accounting currency |
| `receipt_items` | one line of a receipt; `total_price` is in the receipt's currency; `expense_id` links the line to the expense it was booked into; `name_normalized` is the cleaned item name, `name_raw` the printed one |

Currency is the trap. `expenses.amount` is in the accounting currency, while
`receipts.total_amount` and `receipt_items.*_price` are as printed. The prompt must give the
rule: for money by store, sum `expenses.amount` joined through `expenses.store_id`; for money
by item, apportion the linked expense — `expenses.amount * receipt_items.total_price /
SUM(total_price) OVER (PARTITION BY expense_id)` — and never add receipt prices to expense
amounts. Verify on real data that the items of one expense sum to its `amount_original`
before relying on the apportioning; if they do not (discounts, rounding), say so in the note.

`receipts.url` is the fiscal-portal link of each receipt. It is not needed for analysis and is
sent to the model provider with any `SELECT *`. Decide in implementation between leaving it
and describing a narrower view; the recommended answer is to leave it out, which this plan
can do only after `analytics-ledger-visibility.md` (the sanitized cache can copy chosen
columns; a plain attach cannot hide one).

### Summary query

`queries/spending_summary.sql` gains `stores`: the top stores of the last 12 months by
`expenses.amount`, each with its chain name, amount and expense count, so the analyst sees them
before its first proposal and can offer follow-ups such as "compare supermarkets".

### Prompt

Two sentences in the dashboard prompt: the store and item tables exist and the currency rule
above. One new starter button, e.g. "Where do I shop most?".

## Changes

| File | Change |
|---|---|
| `src/dinary_analytics/connection.py` | four entries in `LEDGER_TABLES` |
| `src/dinary_analytics/queries/spending_summary.sql` | `stores` key |
| `src/dinary_analytics/notebooks/dashboard.py` | prompt sentences, `query_summary` docstring, starter button |
| `docs/src/en/analytics.md`, `docs/src/ru/analytics.md` | add a store question and an item question to the examples |

## Tests

- `test_connection.py`: `ledger_schema()` includes the four tables when the replica has them
  and omits them when it does not.
- `test_queries.py`: `stores` in the summary — ordering, chain name, a store without a chain,
  an expense without a store is not counted.
- `test_dashboard.py`: the prompt cell contains the currency rule.

## Order

After `analytics-ledger-visibility.md`. Until that lands the model can already read every
table in the replica, so this plan adds no exposure; it is ordered second only because the
column-level choice for `receipts.url` depends on the sanitized cache.

## When done

Update "What the model is told about the ledger" in `specs/reference/analytics-ai.md` to list
stores, shop chains, receipts and receipt items, and add the rule that money by store or item
is always taken from expense amounts.
