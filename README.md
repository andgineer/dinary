# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                   |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py            |        1 |        0 |    100% |           |
| src/dinary/api/categories.py           |       15 |        0 |    100% |           |
| src/dinary/api/expenses.py             |       23 |        0 |    100% |           |
| src/dinary/api/qr.py                   |       17 |        0 |    100% |           |
| src/dinary/config.py                   |       19 |        1 |     95% |        15 |
| src/dinary/main.py                     |       30 |        2 |     93% |    58, 68 |
| src/dinary/services/category\_store.py |       27 |        0 |    100% |           |
| src/dinary/services/exchange\_rate.py  |       16 |        0 |    100% |           |
| src/dinary/services/qr\_parser.py      |       16 |        1 |     94% |        31 |
| src/dinary/services/sheets.py          |      156 |       16 |     90% |38-44, 48, 63-64, 80, 100-103, 108-110, 246 |
| **TOTAL**                              |  **320** |   **20** | **94%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/andgineer/dinary-server/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/andgineer/dinary-server/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fandgineer%2Fdinary-server%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.