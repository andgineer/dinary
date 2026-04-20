[![Build Status](https://github.com/andgineer/dinary-server/workflows/CI/badge.svg)](https://github.com/andgineer/dinary-server/actions)
[![Coverage](https://raw.githubusercontent.com/andgineer/dinary-server/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)
# Dinary (server)

Server for [Dinary - your dinar diary](https://github.com/andgineer/dinary).

Track expenses, scan receipts, analyze spending with AI

# Documentation

[Dinary server](https://andgineer.github.io/dinary-server/)

# Local development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync

# Create .env from the example (one-time, .env is gitignored)
cp .env.example .env

uv run dinary
```

The server starts on `http://localhost:8000` with auto-reload.

Credentials are read from `~/.config/gspread/service_account.json` (standard gspread location).
Don't have a service account key yet?
See [Google Sheets Setup](https://andgineer.github.io/dinary-server/google-sheets-setup/).

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

Configure `.env` (see `.env.example`), then:

```bash
inv setup    # one-time: install deps, clone, create systemd services, upload creds
inv deploy   # pull latest code and restart
inv status   # check service status
inv logs     # tail server logs
```

See [Oracle Cloud deployment guide](https://andgineer.github.io/dinary-server/deploy-oracle/) for details.

## Reports

* [Allure test report](https://andgineer.github.io/dinary-server/builds/tests/)
* [Codecov](https://app.codecov.io/gh/andgineer/dinary-server/tree/main/src%2Fdinary)
* [Coveralls](https://coveralls.io/github/andgineer/dinary-server)

> Created with cookiecutter using [template](https://github.com/andgineer/cookiecutter-python-package)
