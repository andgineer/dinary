# inv sql — Interactive SQL Runner

Invoked via `inv sql` (locally or over `--remote`).

## Read-only default

The module opens the SQLite DB with a `file:...?mode=ro` URI so `UPDATE` /
`DELETE` / `INSERT` statements error out at the SQLite layer. An operator
peeking at prod cannot accidentally mutate the ledger by typoing a query.

`--write` is the explicit opt-in for mutation (one-off fixups, ad-hoc
cleanups). It is deliberately absent from the `--remote` path so the opt-in
can never ride an SSH pipe into a snapshot that gets torn down on exit.

## Output formats

| Flag | Output |
|------|--------|
| _(default)_ | Rich table to stdout, one-line footer with row count |
| `--csv` | CSV with header row, suitable for piping into `wc` / `csvkit` / a spreadsheet |
| `--json` | Single envelope `{"columns": [...], "rows": [[...], ...], "row_count": N}` |

Non-primitive values (`Decimal`, `date`, `datetime`) in `--json` are
stringified. Callers that need typed JSON should cast in SQL
(`CAST(amount AS REAL)`).

## Remote dispatch

`--remote` goes through the same `_remote_snapshot_cmd` wrapper as
`inv report-*`. A `/tmp` snapshot of the live DB is opened read-only on the
server (via `sqlite3 .backup`, not a raw file copy, to honour the WAL), the
JSON envelope comes back over SSH, and the local process renders it.

Reading a snapshot rather than the live file avoids any interaction with the
in-flight Litestream replication and keeps Cyrillic / box-drawing bytes intact
across the wire.
