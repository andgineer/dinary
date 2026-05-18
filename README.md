[![Build Status](https://github.com/andgineer/dinary/workflows/CI/badge.svg)](https://github.com/andgineer/dinary/actions)
[![Coverage](https://raw.githubusercontent.com/andgineer/dinary/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)
# Dinary - Your dinar diary

Track expenses, scan receipts, analyze spending with AI

# Documentation

[Dinary](https://andgineer.github.io/dinary/)

# Local development

The PWA is a Vue 3 + Pinia app under `webapp/`, built with Vite into
`_static/` (gitignored). FastAPI serves the built assets from the same
origin as the API, so on `http://127.0.0.1:8000` you see the same PWA
the production VM serves.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22+, then:

```bash
uv sync

# One-time: copy .deploy/.env from the template (.deploy/ is gitignored).
cp -r .deploy.example .deploy

# Build the Vue PWA into _static/ (gitignored). Run this:
#   * the very first time,
#   * after pulling commits that touch webapp/,
#   * after editing anything under webapp/ yourself.
# ``--skip-install`` skips ``npm ci`` (use after the first full build for
# faster iteration). The full build runs ``npm ci`` + ``vite build`` and
# writes data/.deployed_version + _static/version.json.
uv run inv build-static                 # full build (npm ci + vite build)
uv run inv build-static --skip-install  # faster: skips npm ci

# Run the FastAPI server. Same migration path as prod ``systemctl restart``:
#   * starts uvicorn on http://127.0.0.1:8000 with auto-reload,
#   * the FastAPI lifespan calls ``ledger_repo.init_db()`` which runs
#     yoyo migrations against the existing data/dinary.db (or creates
#     a fresh schema if no DB is there yet).
# As a safety net, ``inv dev`` will also run ``inv build-static`` itself
# if _static/index.html is missing — but always run the build explicitly
# whenever you change webapp/ so the served PWA matches your source.
uv run inv dev
```

To exercise migrations against a realistic dataset, replace `data/dinary.db`
with a fresh prod snapshot before starting:

```bash
uv run inv restore-primary
uv run inv dev
```

Pass `--reset` **only** when you want a clean slate — it wipes
`data/dinary.db` (plus its WAL/SHM sidecars), creates the schema from
scratch, and re-seeds the hardcoded 3D taxonomy (groups / categories /
events / tags). Use it for first-time bootstrap or after changing
`seed_config.py`. It is not the right way to test that migrations apply
cleanly to existing data:

```bash
uv run inv dev --reset
```

After editing anything under `webapp/`, rebuild `_static/`:

```bash
uv run inv build-static                # full rebuild (npm ci + vite build)
uv run inv build-static --skip-install # faster local rebuild after first run
```

While iterating on Vue/JS, you can run the Vite dev server with HMR
(it proxies `/api` calls to FastAPI on port 8000):

```bash
npm --prefix webapp run dev   # http://127.0.0.1:5173
```

Note: `vite dev` does **not** register a service worker, so offline
and PWA behavior must be tested against a real `_static/` build (run
`uv run inv build-static`, then hit FastAPI on port 8000).

`inv dev` **disables Google-Sheets logging by default** so test expenses
you create while debugging don't leak into the prod logging spreadsheet
(the env var from `.deploy/.env` is overridden just for this process).
Pass `--sheet-logging` if you specifically want to exercise the drain
loop end-to-end.

To point local dev at a copy of prod data instead of an empty DB:

```bash
uv run inv backup                         # snapshot prod into ~/Library/dinary/<ts>/
cp ~/Library/dinary/<ts>/data/dinary.db data/
uv run inv dev                            # NOT --reset; keep the snapshot
```

Credentials are read from `~/.config/gspread/service_account.json` (standard gspread location).
Don't have a service account key yet?
See [Google Sheets Setup](https://andgineer.github.io/dinary/google-sheets-setup/).

### Run tests

```bash
inv test
```

### Pre-commit hooks

Use [pre-commit](https://pre-commit.com/#install) for code quality:

    pre-commit install

### Scripts

Install [invoke](https://docs.pyinvoke.org/en/stable/) preferably with [uv tool](https://docs.astral.sh/uv/):

    uv tool install invoke

For a list of available scripts run:

    invoke --list

### Deploy to Oracle Cloud

Configure `.deploy/.env` (see `.deploy.example/.env`), then:

```bash
inv setup-server    # one-time: install deps, clone, create systemd services, upload creds
inv deploy          # pull latest code and restart
inv status --remote # check service status
inv logs --remote   # tail server logs
```

See [Oracle Cloud deployment guide](https://andgineer.github.io/dinary/deploy-oracle/) for details.

## Reports

* [Allure test report](https://andgineer.github.io/dinary/builds/tests/)
* [Codecov](https://app.codecov.io/gh/andgineer/dinary/tree/main/src%2Fdinary)
* [Coveralls](https://coveralls.io/github/andgineer/dinary)

> Created with cookiecutter using [template](https://github.com/andgineer/cookiecutter-python-package)
