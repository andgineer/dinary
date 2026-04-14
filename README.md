[![Build Status](https://github.com/andgineer/dinary-server/workflows/CI/badge.svg)](https://github.com/andgineer/dinary-server/actions)
[![Coverage](https://raw.githubusercontent.com/andgineer/dinary-server/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)
# Dinary (server)

Server for [Dinary - your dinar diary](https://github.com/andgineer/dinary).

Track expenses, scan receipts, analyze spending with AI

# Documentation

[Dinary server](https://andgineer.github.io/dinary-server/)

# Developers

Do not forget to run `. ./activate.sh`.

For work it need [uv](https://github.com/astral-sh/uv) installed.

Use [pre-commit](https://pre-commit.com/#install) hooks for code quality:

    pre-commit install

## Allure test report

* [Allure report](https://andgineer.github.io/dinary-server/builds/tests/)

# Scripts

Install [invoke](https://docs.pyinvoke.org/en/stable/) preferably with [uv tool](https://docs.astral.sh/uv/):

    uv tool install invoke

For a list of available scripts run:

    invoke --list

For more information about a script run:

    invoke <script> --help


## Coverage report
* [Codecov](https://app.codecov.io/gh/andgineer/dinary-server/tree/main/src%2Fdinary)
* [Coveralls](https://coveralls.io/github/andgineer/dinary-server)

> Created with cookiecutter using [template](https://github.com/andgineer/cookiecutter-python-package)
