## Database Migrations

Schema versioning for DuckDB via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/).

### Layout

- `config/` — migrations for `config.duckdb` (reference/dimension data)
- `budget/` — migrations for `budget_YYYY.duckdb` (transactional data, one file per year)

### Adding a new migration

1. Create a numbered `.sql` file, e.g. `config/0002_add_column.sql`.
2. Optionally add a matching `.rollback.sql` for reversibility.
3. Migrations run automatically on app startup and during `inv deploy`.
   Manual: `inv migrate-config` or `inv migrate-budget --year=2026`.
