# Deploy to Oracle Cloud (replace existing, no DB)

## Context

- Oracle Cloud Free Tier VM is already running a previous version of dinary-server
- Tailscale tunnel is configured, systemd service `dinary` is active
- There is no existing DuckDB data to preserve (no `config.duckdb`, no `budget_*.duckdb`)
- New version on `main` uses `yoyo` migrations instead of inline DDL, dropped `ibis`/`pandas`

## Pre-deploy checks (on laptop)

```bash
# 1. Ensure .env is configured
cat .env
# Should show DINARY_DEPLOY_HOST, DINARY_GOOGLE_SHEETS_SPREADSHEET_ID

# 2. Verify SSH access
inv ssh
# Ctrl-D to exit

# 3. Verify local tests pass
uv run pytest tests/ -q

# 4. Verify main is pushed
git log --oneline -3 origin/main
# Should show "yoyo migrations" as latest
```

## Deploy

```bash
inv deploy
```

This runs the following steps automatically via SSH:

1. **Pre-deploy backup** of remote `data/` (will be empty or missing — that is expected)
2. `git pull` on the server to get latest `main`
3. `uv sync --no-dev` to install/remove dependencies (installs `yoyo-migrations`, removes `ibis`/`pandas`/`pyarrow` if present)
4. `mkdir -p data/`
5. **Apply config migrations** — creates fresh `config.duckdb` with `yoyo` version tracking
6. Render `__VERSION__` into static assets
7. `systemctl restart dinary`
8. Health check: `curl localhost:8000/api/health`

## Post-deploy verification

Run these from the laptop, in order:

### Step 1: Service health

```bash
inv status
```

Expected: `dinary.service` active (running), Tailscale serve active.

### Step 2: Health endpoint

```bash
inv ssh
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Expected: `{"status": "ok", "version": "<short git hash>"}`.

### Step 3: Logs — no errors on startup

```bash
inv logs --lines=30
```

Look for:
- no Python tracebacks
- no import errors (especially no `ibis` / `pandas` references)
- migration log line if present

### Step 4: Seed config from Google Sheets

Since there is no existing DB, seed reference data:

```bash
inv seed-config
```

Expected: JSON summary with `category_groups > 0`, `categories > 0`, `mappings_created > 0`.

### Step 5: Verify categories API

```bash
inv ssh
curl -s http://localhost:8000/api/categories | python3 -m json.tool
```

Expected: list of category objects from seeded data.

### Step 6: Test expense creation (from phone or curl)

```bash
inv ssh
curl -s -X POST http://localhost:8000/api/expenses \
  -H 'Content-Type: application/json' \
  -d '{
    "expense_id": "deploy-test-1",
    "amount": 100,
    "currency": "RSD",
    "category": "<a known category from step 5>",
    "group": "<its group>",
    "date": "2026-04-16",
    "comment": "deploy smoke test"
  }' | python3 -m json.tool
```

Expected: `{"status": "created", ...}`.

### Step 7: Verify budget DB was created

```bash
inv ssh
ls -la ~/dinary-server/data/
```

Expected: `config.duckdb` and `budget_2026.duckdb` present.

### Step 8: Verify yoyo tracking tables

```bash
inv ssh
cd ~/dinary-server && source ~/.local/bin/env
uv run python -c "
import duckdb
for f in ['data/config.duckdb', 'data/budget_2026.duckdb']:
    con = duckdb.connect(f, read_only=True)
    rows = con.execute('SELECT * FROM _yoyo_migration').fetchall()
    print(f'{f}: {len(rows)} migration(s) applied')
    con.close()
"
```

Expected: 1 migration applied in each file.

### Step 9: PWA smoke test

Open the Tailscale URL on phone. Verify the app loads, categories are visible, and a test expense can be submitted through the UI.

### Step 10: Verify sync (optional)

```bash
inv sync
```

Expected: `Synced 1 months` (or however many dirty months exist after the test expense).

## Rollback

If something goes wrong:

```bash
# Deploy the previous known-good commit
inv deploy --ref=<previous commit hash>
```

Since there is no data to lose (no pre-existing DB), rollback is just redeploying the old code. The `data/` directory can be safely deleted and recreated.

## After successful deploy

- Delete the test expense row from Google Sheets if sync wrote it
- Or leave it as a smoke test record
