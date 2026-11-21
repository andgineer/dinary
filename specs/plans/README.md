# Implementation plans

Active plans and the order to implement them in. A plan that is fully implemented and merged is
deleted (anything spec-worthy moves into `specs/` first) and its row here goes with it.

## Order

| # | Plan | Blocked by | Notes |
|---|---|---|---|
| 1 | `analytics-ledger-visibility.md` | — | SQL written by a model reaches only the bookkeeping tables; the snapshot stays a full copy |
| 2 | `analytics-stores-and-items.md` | 1, for the per-column choice only | The analyst is told about stores, chains, receipts and receipt items |
| 3 | `analytics-baskets-by-category.md` | — | Baskets by category and group; LLM drill-down into a basket |
| 4 | `pwa-trend-reasons.md` | — | Each trend on the PWA Analytics screen names its reason and expands into a breakdown by category, event, shop and item |
| — | `analytics-llm-secrets.md` | — | Direction chosen (key entered once in the dashboard). Not scheduled until analytics runs on a second machine |
