[![Build Status](https://github.com/andgineer/dinary/workflows/CI/badge.svg)](https://github.com/andgineer/dinary/actions)
[![Coverage](https://raw.githubusercontent.com/andgineer/dinary/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)
# dinary

Your dinar diary — track expenses, scan receipts, analyze spending with AI 

# Documentation

[Dinary](https://andgineer.github.io/dinary/)



# Developers

Do not forget to run `. ./activate.sh`.

For work it need [uv](https://github.com/astral-sh/uv) installed.

Use [pre-commit](https://pre-commit.com/#install) hooks for code quality:

    pre-commit install

## Allure test report

* [Allure report](https://andgineer.github.io/dinary/builds/tests/)

# Scripts

Install [invoke](https://docs.pyinvoke.org/en/stable/) preferably with [uv tool](https://docs.astral.sh/uv/):

    uv tool install invoke

For a list of available scripts run:

    invoke --list

For more information about a script run:

    invoke <script> --help


## Coverage report
* [Codecov](https://app.codecov.io/gh/andgineer/dinary/tree/main/src%2Fdinary)
* [Coveralls](https://coveralls.io/github/andgineer/dinary)

> Created with cookiecutter using [template](https://github.com/andgineer/cookiecutter-python-package)
