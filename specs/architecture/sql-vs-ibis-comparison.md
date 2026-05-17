# SQL Files vs Ibis PoC

## Scope

This note compares the `sql-files-poc` and `ibis-poc` branches across three axes:

- Runtime performance
- Memory footprint
- Maintainability

The goal is to decide which direction is more appropriate for the DuckDB repository layer in this project.

## Executive Summary

`sql-files-poc` is the better default direction.

It is significantly faster on the measured read-heavy workloads, uses substantially less memory, and keeps the hot path simpler by relying on direct DuckDB SQL instead of an additional `Ibis -> pandas -> dataclass` execution/mapping layer.

The main advantage of `ibis-poc` is not Ibis itself, but the introduction of versioned schema migrations via `yoyo`. That migration approach is worth keeping and can be adopted independently of Ibis.

Recommended target architecture:

- Query layer: plain SQL files
- Schema evolution: `yoyo` migrations
- Runtime dependency surface: avoid `ibis`/`pandas` in the request hot path

## Measured Results

Measurements were taken on the same machine using a synthetic but representative workload:

- 5,000 category mappings
- 20,000 expenses
- 1,000 inserts
- identical benchmark logic on both branches

| Metric | `sql-files-poc` | `ibis-poc` | Relative result |
|---|---:|---:|---:|
| Cold import time | 0.09 s | 0.56 s | `ibis-poc` is 6.2x slower |
| Cold import RSS | 41.1 MB | 133.8 MB | `ibis-poc` uses 3.3x more memory |
| `resolve_mapping` x5000 | 1.99 s | 19.89 s | `ibis-poc` is 10.0x slower |
| `reverse_lookup_mapping` x5000 | 1.36 s | 18.83 s | `ibis-poc` is 13.9x slower |
| `get_month_expenses` x20 | 1.13 s | 6.56 s | `ibis-poc` is 5.8x slower |
| `insert_expense` x1000 | 3.35 s | 6.27 s | `ibis-poc` is 1.9x slower |
| Peak RSS for full workload | 136.5 MB | 297.9 MB | `ibis-poc` uses 2.2x more memory |
| `.venv` size | 107 MB | 340 MB | `ibis-poc` environment is 3.2x larger |

## Why `ibis-poc` Costs More

The extra cost does not come from DuckDB itself. It comes from the extra execution and object-conversion layers:

- `ibis-poc` builds Ibis expressions instead of executing raw SQL directly
- query results are materialized through `expr.execute()`
- results are then mapped from pandas objects into dataclasses
- the dependency set is larger and imports are heavier

This is especially expensive for:

- cold start
- small read queries
- repeated lookup-style operations

In this codebase, those are exactly the operations that matter most.

## Maintainability Comparison

### `sql-files-poc` strengths

- SQL is explicit and easy to inspect in dedicated `.sql` files
- DuckDB behavior is transparent and easy to debug
- lower dependency surface
- simpler profiling and performance reasoning
- fewer abstractions between application code and database execution

### `sql-files-poc` weaknesses

- schema creation is embedded in Python strings instead of versioned migrations
- evolving existing databases safely is harder
- deployment and upgrade workflows are less structured

### `ibis-poc` strengths

- introduces versioned migrations using `yoyo`
- schema evolution becomes reproducible and operationally safer
- some query composition is done in Python rather than in raw SQL text

### `ibis-poc` weaknesses

- read path becomes much slower and heavier
- debugging generated query behavior is less direct than reading SQL files
- abstraction is incomplete anyway, because several write operations still require raw SQL
- dependency and environment size increase substantially

## Recommendation

Do not adopt Ibis as the main query layer for this project.

Instead:

1. Keep the direct SQL approach from `sql-files-poc`
2. Port the `yoyo` migration approach from `ibis-poc`
3. Keep runtime dependencies lean and avoid pandas-backed query execution in the hot path

This gives the best combination of:

- performance
- memory efficiency
- operational safety
- long-term maintainability

## Practical Decision

If only one branch should be used as the base going forward, choose `sql-files-poc`.

Then selectively cherry-pick or reimplement the following idea from `ibis-poc`:

- versioned DuckDB migrations with `yoyo`

That combination captures the main benefit of `ibis-poc` without paying its runtime and memory costs.
