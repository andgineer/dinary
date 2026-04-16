# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                   |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py            |        1 |        0 |    100% |           |
| src/dinary/api/categories.py           |       20 |        0 |    100% |           |
| src/dinary/api/expenses.py             |       46 |        0 |    100% |           |
| src/dinary/api/qr.py                   |       17 |        0 |    100% |           |
| src/dinary/config.py                   |       19 |        1 |     95% |        15 |
| src/dinary/main.py                     |       57 |        6 |     89% |27-29, 80, 96, 106 |
| src/dinary/services/category\_store.py |       27 |        0 |    100% |           |
| src/dinary/services/db\_migrations.py  |       55 |        2 |     96% |    51, 54 |
| src/dinary/services/duckdb\_repo.py    |      136 |        8 |     94% |145-147, 183, 193-203 |
| src/dinary/services/exchange\_rate.py  |       16 |        0 |    100% |           |
| src/dinary/services/import\_sheet.py   |      121 |      121 |      0% |    14-237 |
| src/dinary/services/qr\_parser.py      |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py    |      108 |       20 |     81% |56, 60, 196-198, 208-239 |
| src/dinary/services/sheets.py          |      165 |       15 |     91% |38-44, 48-49, 81, 101-104, 109-111, 261 |
| src/dinary/services/sql\_loader.py     |       34 |        0 |    100% |           |
| src/dinary/services/sync.py            |      174 |       40 |     77% |46-60, 91-92, 126-127, 135-136, 174-181, 209-215, 224, 244-245, 275-277, 285-290, 315-316, 322-327, 338, 345-346, 358-359 |
| **TOTAL**                              | **1012** |  **214** | **79%** |           |


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