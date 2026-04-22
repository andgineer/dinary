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

If you copy `import_sources.json` as-is and never run `inv import-*`,
nothing breaks: the file is only consulted by import tasks, which
non-import users never invoke.
