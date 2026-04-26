# .deploy.example — deploy config template

Copy this directory to `.deploy/` and fill in your values:

```bash
cp -r .deploy.example .deploy
# edit .deploy/.env
```

`.deploy/` is gitignored; the operator's local copy is the authoritative
source of deploy configuration across every server they run.

## Files

- **`.env`** — required runtime config (`DINARY_DEPLOY_HOST`, optional
  `DINARY_SHEET_LOGGING_SPREADSHEET`, etc.). `inv deploy` reads this
  locally and syncs it to the server as `/home/ubuntu/dinary/.deploy/.env`.
- **`import_sources.json`** — OPTIONAL. Only needed if you run
  `inv import-*` tasks to import historical expenses from your own
  Google Sheets. Non-import users can leave this file alone — it is
  never read at runtime. See the `imports/` directory at the repo
  root for details on the schema and workflows.
- **`litestream.yml`** — OPTIONAL. Litestream (v0.5.x) replicator
  config for hot off-site backup of `data/dinary.db` to an SFTP
  target (typically a second Oracle Cloud Free Tier VM). Copy to
  `.deploy/litestream.yml`, fill in the SFTP `host`/`user`/`path`
  fields, then run `inv setup-replica` once. See
  [`docs/src/en/operations.md`](../docs/src/en/operations.md)
  for the end-to-end replica bootstrap workflow. `inv setup-server` does
  NOT auto-run `inv setup-replica` even when the config is
  present locally — the sidecar requires an SFTP host whose
  `authorized_keys` already trusts VM 1's ed25519 key, a
  cross-host relationship the bootstrap script cannot arrange on
  your behalf.

If you copy `import_sources.json` as-is and never run `inv import-*`,
nothing breaks: the file is only consulted by import tasks, which
non-import users never invoke. Same for `litestream.yml`: absent
means no replication, which is a valid single-VM deployment.
