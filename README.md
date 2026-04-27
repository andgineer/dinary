# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                          |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                   |        1 |        0 |    100% |           |
| src/dinary/api/admin\_catalog.py              |      186 |       33 |     82% |199-200, 212-226, 270-271, 295-296, 314-315, 360-377, 389-390, 415-416, 437-438, 453-454, 499 |
| src/dinary/api/catalog.py                     |       77 |        3 |     96% |   318-320 |
| src/dinary/api/expenses.py                    |       97 |        4 |     96% |154, 296-298 |
| src/dinary/api/qr.py                          |       17 |        6 |     65% |     26-32 |
| src/dinary/background/rate\_prefetch\_task.py |       56 |        2 |     96% |   86, 117 |
| src/dinary/background/sheet\_logging\_task.py |       53 |        7 |     87% |     44-57 |
| src/dinary/config.py                          |      148 |       20 |     86% |37, 47, 110-112, 143, 149, 205-209, 211-215, 218-223, 226-230, 289, 293-295 |
| src/dinary/imports/expense\_import.py         |      396 |      131 |     67% |260, 265-266, 280, 282, 284, 311-329, 359, 363, 396-402, 405, 412, 414, 416-423, 445, 450-451, 475, 518-522, 540-543, 545-557, 666-667, 715-717, 720-722, 809-820, 889-932, 945, 955, 978-1014, 1072, 1086, 1089, 1094, 1104, 1165-1169, 1171-1175, 1230-1237, 1241-1254, 1282-1291 |
| src/dinary/imports/income\_import.py          |      139 |      139 |      0% |    12-318 |
| src/dinary/imports/report\_2d\_3d.py          |      199 |       42 |     79% |170, 229-238, 274, 496-497, 535-566, 570-609, 613 |
| src/dinary/imports/seed.py                    |      297 |       83 |     72% |326, 331-333, 335-341, 343, 349, 351, 353, 356, 359-361, 363-370, 374, 402, 404, 406, 408, 413, 419, 438, 517-530, 637-638, 643-644, 647-648, 659, 682-688, 761-765, 773-774, 776-777, 779-780, 782-786, 810-815, 860-864, 872-875, 897-899, 950-959, 1017-1018 |
| src/dinary/imports/verify\_equivalence.py     |       92 |       92 |      0% |    20-211 |
| src/dinary/imports/verify\_income.py          |       37 |       37 |      0% |     9-107 |
| src/dinary/main.py                            |       72 |        7 |     90% |31-33, 90, 96, 112, 122 |
| src/dinary/reports/expenses.py                |      122 |        5 |     96% |299, 301, 322-323, 420 |
| src/dinary/reports/income.py                  |       86 |        3 |     97% |209-210, 285 |
| src/dinary/reports/verify\_budget.py          |       83 |        1 |     99% |       104 |
| src/dinary/reports/verify\_income.py          |       88 |        1 |     99% |        38 |
| src/dinary/services/catalog\_writer.py        |      422 |       79 |     81% |177-178, 340-344, 498-500, 520-562, 571, 686, 698, 706, 835-837, 870, 879-884, 893, 895, 900, 905, 915, 931, 971-973, 1009, 1018, 1032-1034, 1059, 1091, 1096, 1113, 1209, 1241, 1245-1247, 1254-1256, 1277, 1281-1283, 1290-1292, 1314, 1327-1329 |
| src/dinary/services/db\_migrations.py         |       54 |        2 |     96% |    86, 89 |
| src/dinary/services/exchange\_rates.py        |       44 |        9 |     80% |49-52, 112-117 |
| src/dinary/services/ledger\_repo.py           |      275 |       30 |     89% |62-63, 287-296, 304-310, 637, 672, 778, 784, 818, 845-854, 876-883, 1019-1020, 1036-1042, 1107 |
| src/dinary/services/nbs.py                    |       47 |        0 |    100% |           |
| src/dinary/services/qr\_parser.py             |       16 |        1 |     94% |        31 |
| src/dinary/services/rate\_helpers.py          |       30 |        6 |     80% |26, 35-37, 68-74 |
| src/dinary/services/seed\_config.py           |      137 |        7 |     95% |220-221, 515-517, 556-560 |
| src/dinary/services/sheet\_logging.py         |      217 |       49 |     77% |96-100, 114-121, 129, 195, 199-201, 257-263, 276-278, 282-296, 325-326, 370, 435-436, 457-475, 484, 488 |
| src/dinary/services/sheet\_mapping.py         |      252 |       54 |     79% |274, 346-348, 375-376, 383-389, 439, 442-454, 468-469, 479-497, 574-581, 609-661, 689, 700-705 |
| src/dinary/services/sheets.py                 |      231 |       63 |     73% |87-96, 100, 137-146, 164-176, 237-238, 244, 298, 337, 395, 478, 628, 672-677, 713-753 |
| src/dinary/services/sql\_loader.py            |       31 |        0 |    100% |           |
| src/dinary/services/sqlite\_types.py          |       36 |        0 |    100% |           |
| src/dinary/tools/backup\_retention.py         |       55 |       26 |     53% |26-34, 71-91, 95 |
| src/dinary/tools/backup\_snapshots.py         |       79 |        7 |     91% |157, 213-218 |
| src/dinary/tools/report\_helpers.py           |       30 |       10 |     67% |     44-53 |
| src/dinary/tools/sql.py                       |       71 |        3 |     96% |86, 176, 183 |
| **TOTAL**                                     | **4273** |  **962** | **77%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/andgineer/dinary/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/andgineer/dinary/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fandgineer%2Fdinary%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.