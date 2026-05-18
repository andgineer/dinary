## Database Migrations

Schema versioning for SQLite via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/),
driven by the `SQLiteBackend` in `src/dinary/services/db_migrations.py`.

### Layout

Single migration stream against `data/dinary.db` (unified catalog + ledger).

### Adding a new migration

1. Create a numbered `.sql` file, e.g. `0002_add_column.sql`.
2. Optionally add a matching `.rollback.sql` for reversibility.
3. Migrations run automatically on app startup and during `inv deploy`.
   Manual: `inv migrate`.
