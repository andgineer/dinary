# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                       |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                |        1 |        0 |    100% |           |
| src/dinary/api/categories.py               |       20 |        0 |    100% |           |
| src/dinary/api/expenses.py                 |       55 |        0 |    100% |           |
| src/dinary/api/qr.py                       |       17 |        0 |    100% |           |
| src/dinary/config.py                       |       20 |        1 |     95% |        15 |
| src/dinary/main.py                         |       57 |        6 |     89% |27-29, 80, 96, 106 |
| src/dinary/services/category\_store.py     |       27 |        0 |    100% |           |
| src/dinary/services/db\_migrations.py      |       54 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py        |      157 |       19 |     88% |150-152, 185, 248-271, 289 |
| src/dinary/services/exchange\_rate.py      |       16 |        9 |     44% |     19-28 |
| src/dinary/services/import\_income.py      |      109 |      109 |      0% |     9-201 |
| src/dinary/services/import\_sheet.py       |      261 |      202 |     23% |277-284, 289-295, 299, 304-306, 321, 330-344, 353-357, 368-390, 399-405, 416-456, 465-471, 485-516, 520-561, 586-763 |
| src/dinary/services/nbs.py                 |      100 |       46 |     54% |30, 35, 46-55, 69-78, 102-104, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py          |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py        |      343 |       86 |     75% |85, 87, 89, 91, 93, 113, 134, 262-275, 293-321, 552, 555-561, 564, 567, 573, 575, 577, 580, 583-585, 588-597, 602-609, 617, 619, 621, 623, 633, 642-647, 656-670, 732, 755-759, 795-805, 896-898, 907, 958 |
| src/dinary/services/sheets.py              |      165 |       15 |     91% |38-44, 48-49, 81, 101-104, 109-111, 261 |
| src/dinary/services/sql\_loader.py         |       33 |        0 |    100% |           |
| src/dinary/services/sync.py                |      176 |       78 |     56% |46-60, 73-92, 122-127, 131, 135-136, 167, 178-185, 213-219, 228, 248-249, 279-281, 289-294, 298-300, 314-321, 326-331, 340-365 |
| src/dinary/services/verify\_equivalence.py |      106 |      106 |      0% |     9-207 |
| src/dinary/services/verify\_income.py      |       36 |       36 |      0% |      7-81 |
| **TOTAL**                                  | **1769** |  **716** | **60%** |           |


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