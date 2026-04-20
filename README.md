# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                      |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------ | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py               |        1 |        0 |    100% |           |
| src/dinary/api/categories.py              |       21 |        0 |    100% |           |
| src/dinary/api/expenses.py                |       54 |        2 |     96% |    51, 99 |
| src/dinary/api/qr.py                      |       17 |        6 |     65% |     26-32 |
| src/dinary/config.py                      |       42 |        2 |     95% |    19, 29 |
| src/dinary/imports/expense\_import.py     |      354 |      101 |     71% |244, 249-250, 264, 266, 268, 294-316, 362, 366, 407-413, 416, 423, 425, 427-434, 456, 461-462, 486, 529-533, 551-554, 672-674, 677-679, 761, 795-800, 813, 825, 838, 887-901, 914, 917, 922, 932, 992-996, 998-1002, 1052-1059, 1063-1076, 1098-1107 |
| src/dinary/imports/income\_import.py      |      140 |      140 |      0% |    11-305 |
| src/dinary/imports/report\_2d\_3d.py      |      183 |       46 |     75% |161-164, 223-232, 268, 382-383, 408, 429-471, 475-507, 511 |
| src/dinary/imports/verify\_equivalence.py |       91 |       91 |      0% |    20-209 |
| src/dinary/imports/verify\_income.py      |       37 |       37 |      0% |      8-84 |
| src/dinary/main.py                        |       97 |        7 |     93% |29-31, 128, 134, 150, 160 |
| src/dinary/services/db\_migrations.py     |       49 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py       |      223 |       12 |     95% |528, 534, 577, 624, 668-675, 796-797, 817-819, 884 |
| src/dinary/services/exchange\_rate.py     |       16 |       16 |      0% |      3-28 |
| src/dinary/services/nbs.py                |      102 |       46 |     55% |30, 35, 46-55, 69-78, 102-104, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py         |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py       |      496 |      109 |     78% |189, 191, 193, 195, 197, 216, 226, 262-263, 558, 563-565, 567-573, 575, 581, 583, 585, 588, 591-593, 595-602, 606, 633, 635, 637, 639, 644, 650, 669, 748-761, 792-793, 881-893, 994, 1095-1096, 1101-1102, 1105-1106, 1117, 1140-1146, 1262, 1276, 1284, 1308, 1329-1338, 1360-1361, 1369-1370, 1373-1374, 1376-1377, 1379-1383, 1406-1411, 1466-1468, 1480, 1504-1506, 1545-1554, 1578-1580 |
| src/dinary/services/sheet\_logging.py     |      190 |       41 |     78% |95-99, 113-120, 128, 156, 160-162, 214-220, 234-235, 264-265, 309, 374-375, 391-409, 418, 422 |
| src/dinary/services/sheets.py             |      199 |       40 |     80% |70-76, 80, 141-142, 148, 202, 241, 299, 382, 532, 576-581, 617-657 |
| src/dinary/services/sql\_loader.py        |       33 |        0 |    100% |           |
| **TOTAL**                                 | **2361** |  **699** | **70%** |           |


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