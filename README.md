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
| src/dinary/services/db\_migrations.py      |       55 |        2 |     96% |    51, 54 |
| src/dinary/services/duckdb\_repo.py        |      155 |       17 |     89% |148-150, 245-268 |
| src/dinary/services/exchange\_rate.py      |       16 |        9 |     44% |     19-28 |
| src/dinary/services/import\_sheet.py       |      261 |       93 |     64% |277, 282-283, 289, 291, 293, 320, 329-345, 355, 380, 428-434, 437, 440, 444, 446, 448-455, 469, 472, 486-517, 525, 538, 552, 598, 616, 619, 624, 641-655, 664-665, 675-681, 688, 703-713, 745-752 |
| src/dinary/services/nbs.py                 |      100 |       46 |     54% |30, 35, 46-55, 69-78, 102-104, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py          |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py        |      341 |       62 |     82% |83, 85, 87, 89, 91, 111, 267-268, 536, 539-545, 548, 551, 557, 559, 561, 564, 567-569, 572-581, 586-593, 601, 603, 605, 607, 617, 628-630, 643, 653, 716, 739-743, 779-789, 880-882, 942 |
| src/dinary/services/sheets.py              |      165 |       15 |     91% |38-44, 48-49, 81, 101-104, 109-111, 261 |
| src/dinary/services/sql\_loader.py         |       34 |        0 |    100% |           |
| src/dinary/services/sync.py                |      176 |       78 |     56% |46-60, 73-92, 122-127, 131, 135-136, 167, 178-185, 213-219, 228, 248-249, 279-281, 289-294, 298-300, 314-321, 326-331, 340-365 |
| src/dinary/services/verify\_equivalence.py |      106 |      106 |      0% |     9-207 |
| **TOTAL**                                  | **1622** |  **436** | **73%** |           |


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