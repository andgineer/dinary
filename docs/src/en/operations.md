# Database Migrations and Backups

This page describes how `dinary` manages its DuckDB files and how to back them up safely.

## Database files

The server stores data in the `data/` directory:

- `config.duckdb` - reference data such as categories, groups, tags, stores, and mappings
- `budget_YYYY.duckdb` - yearly transactional data such as expenses and sync jobs

## Migrations

Schema changes are managed with `yoyo` migrations.

### When migrations run automatically

- On application startup, `config.duckdb` is migrated to the latest version automatically
- When a yearly budget database is opened for the first time, `budget_YYYY.duckdb` is created and migrated automatically
- During `inv deploy`, the deploy script applies config migrations before restarting the service

For a fresh installation, no manual migration step is usually required.

### Manual migration commands

If you want to apply migrations explicitly on the server:

```bash
inv migrate-config
inv migrate-budget --year=2026
```

Use `migrate-budget` for any year whose `budget_YYYY.duckdb` file already exists or is about to be created.

## Backup strategy

DuckDB stores everything in local files, so backup is file-based.

### What to back up

Back up the whole `data/` directory, including:

- `config.duckdb`
- all `budget_YYYY.duckdb` files
- `data/.deployed_version` if you want to keep deployment metadata

### Recommended times to back up

Create a backup:

- before running `inv deploy`
- before applying manual migrations in production
- before any manual edits to files in `data/`

### Backup command

To download server data locally:

```bash
inv backup
```

By default this copies the remote `data/` directory into `./backups/`.

You can also choose another destination:

```bash
inv backup --dest=./my-backups
```

## Restore

To restore from a backup:

1. Stop the running service
2. Replace the contents of `data/` with the backed up files
3. Start the service again
4. Run `inv migrate-config` and `inv migrate-budget --year=YYYY` if you want to force migration checks after restore

Because the database is file-based, restore is usually just a file copy operation.

## Practical guidance

- Keep at least one backup from before every deploy
- Treat `data/` as the source of truth
- Do not edit DuckDB files while the service is actively writing to them
- If you copy files manually, prefer doing it while the service is stopped
