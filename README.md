[![Build Status](https://github.com/andgineer/dinary/workflows/CI/badge.svg)](https://github.com/andgineer/dinary/actions)
[![Coverage](https://raw.githubusercontent.com/andgineer/dinary/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)
# Dinary

Track expenses, scan receipts, analyze spending with AI

# Documentation

[Dinary](https://andgineer.github.io/dinary/)

# Local development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync

# Create .deploy/.env from the template (one-time, .deploy/ is gitignored)
cp -r .deploy.example .deploy

# First time — or whenever you want a clean slate:
#   wipes data/dinary.duckdb, runs schema migrations, seeds the
#   hardcoded 3D taxonomy (groups / categories / events / tags), then
#   starts uvicorn on http://127.0.0.1:8000 with auto-reload.
uv run inv dev --reset

# Subsequent runs — DB is preserved, migrations still apply on startup
# via the FastAPI lifespan, so editing a migration and restarting is
# enough (no separate ``inv migrate`` needed for the local DB):
uv run inv dev
```

`inv dev` **disables Google-Sheets logging by default** so test expenses
you create while debugging don't leak into the prod logging spreadsheet
(the env var from `.deploy/.env` is overridden just for this process).
Pass `--sheet-logging` if you specifically want to exercise the drain
loop end-to-end.

To point local dev at a copy of prod data instead of an empty DB:

```bash
uv run inv backup                         # snapshot prod into ~/Library/dinary/<ts>/
cp ~/Library/dinary/<ts>/data/dinary.duckdb data/
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
inv setup    # one-time: install deps, clone, create systemd services, upload creds
inv deploy   # pull latest code and restart
inv status   # check service status
inv logs     # tail server logs
```

See [Oracle Cloud deployment guide](https://andgineer.github.io/dinary/deploy-oracle/) for details.

## Reports

* [Allure test report](https://andgineer.github.io/dinary/builds/tests/)
* [Codecov](https://app.codecov.io/gh/andgineer/dinary/tree/main/src%2Fdinary)
* [Coveralls](https://coveralls.io/github/andgineer/dinary)

> Created with cookiecutter using [template](https://github.com/andgineer/cookiecutter-python-package)
