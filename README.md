# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                       |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                |        1 |        0 |    100% |           |
| src/dinary/api/categories.py               |       21 |        0 |    100% |           |
| src/dinary/api/expenses.py                 |       67 |        4 |     94% |178-179, 201-202 |
| src/dinary/api/qr.py                       |       17 |        6 |     65% |     26-32 |
| src/dinary/config.py                       |       20 |        1 |     95% |        15 |
| src/dinary/main.py                         |       57 |        7 |     88% |27-29, 74, 80, 96, 106 |
| src/dinary/services/category\_store.py     |       27 |        0 |    100% |           |
| src/dinary/services/db\_migrations.py      |       54 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py        |      235 |       32 |     86% |45-46, 168-170, 181-190, 207-208, 385-389, 394-400, 483, 489, 609, 649-650, 677-684, 765 |
| src/dinary/services/exchange\_rate.py      |       16 |        9 |     44% |     19-28 |
| src/dinary/services/import\_income.py      |      134 |      134 |      0% |     9-354 |
| src/dinary/services/import\_sheet.py       |      285 |      110 |     61% |242, 247-248, 262, 264, 266, 283-297, 306, 331, 335, 364-370, 373, 376, 380, 382, 384-391, 412-421, 425-428, 443, 483-491, 502-504, 509-512, 516-525, 553-558, 571, 583, 596, 627-631, 645-649, 655-656, 696, 699, 704, 714, 745-752, 756-770, 794-801 |
| src/dinary/services/nbs.py                 |      100 |       46 |     54% |30, 35, 46-55, 69-78, 102-104, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py          |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py        |      437 |      101 |     77% |155, 157, 159, 161, 163, 182-192, 202, 233-234, 532, 537-539, 541-547, 549, 555, 557, 559, 562, 565-567, 569-576, 579-585, 607, 609, 611, 613, 618, 624, 643, 722-735, 782-783, 809, 910-911, 916-917, 920-921, 945-952, 966-971, 993-999, 1073-1074, 1083-1084, 1087-1088, 1090-1091, 1093-1097, 1113-1118, 1174-1182, 1195, 1216-1218, 1241-1253 |
| src/dinary/services/sheets.py              |      165 |       15 |     91% |38-44, 48-49, 81, 101-104, 109-111, 261 |
| src/dinary/services/sql\_loader.py         |       33 |        0 |    100% |           |
| src/dinary/services/sync.py                |      200 |       35 |     82% |172-176, 324, 359-365, 436-437, 448-451, 472-477, 481, 546, 560-561, 579-586, 592 |
| src/dinary/services/verify\_equivalence.py |       92 |       92 |      0% |    18-201 |
| src/dinary/services/verify\_income.py      |       36 |       36 |      0% |      7-81 |
| **TOTAL**                                  | **2013** |  **631** | **69%** |           |


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