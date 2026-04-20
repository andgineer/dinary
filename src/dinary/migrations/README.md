## Database Migrations

Schema versioning for DuckDB via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/).

### Layout

Single migration stream against `data/dinary.duckdb` (unified catalog + ledger).

### Adding a new migration

1. Create a numbered `.sql` file, e.g. `0002_add_column.sql`.
2. Optionally add a matching `.rollback.sql` for reversibility.
3. Migrations run automatically on app startup and during `inv deploy`.
   Manual: `inv migrate`.
