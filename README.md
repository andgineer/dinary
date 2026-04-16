# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                   |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py            |        1 |        0 |    100% |           |
| src/dinary/api/categories.py           |       18 |        0 |    100% |           |
| src/dinary/api/expenses.py             |       46 |        0 |    100% |           |
| src/dinary/api/qr.py                   |       17 |        0 |    100% |           |
| src/dinary/config.py                   |       19 |        1 |     95% |        15 |
| src/dinary/main.py                     |       57 |        6 |     89% |27-29, 80, 96, 106 |
| src/dinary/services/category\_store.py |       27 |        0 |    100% |           |
| src/dinary/services/duckdb\_repo.py    |      119 |        3 |     97% |   154-156 |
| src/dinary/services/exchange\_rate.py  |       16 |        0 |    100% |           |
| src/dinary/services/qr\_parser.py      |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py    |      102 |        5 |     95% |35, 39, 187-189 |
| src/dinary/services/sheets.py          |      164 |       14 |     91% |38-44, 48, 80, 100-103, 108-110, 260 |
| src/dinary/services/sync.py            |      176 |       41 |     77% |46-52, 75-76, 104-105, 113-114, 153-157, 185-188, 194, 200, 219-220, 249-251, 259-264, 289-290, 296-301, 312, 319-320, 332-333 |
| **TOTAL**                              |  **778** |   **71** | **91%** |           |


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