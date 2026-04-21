# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                      |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------ | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py               |        1 |        0 |    100% |           |
| src/dinary/api/admin\_catalog.py          |      186 |       33 |     82% |199-200, 212-226, 270-271, 295-296, 314-315, 360-377, 389-390, 415-416, 437-438, 453-454, 499 |
| src/dinary/api/catalog.py                 |       77 |        3 |     96% |   318-320 |
| src/dinary/api/expenses.py                |       96 |        4 |     96% |153, 295-297 |
| src/dinary/api/qr.py                      |       17 |        6 |     65% |     26-32 |
| src/dinary/config.py                      |      148 |       19 |     87% |37, 47, 110-112, 143, 149, 203-207, 209-213, 216-221, 224-228, 291-293 |
| src/dinary/imports/expense\_import.py     |      397 |      131 |     67% |260, 265-266, 280, 282, 284, 311-335, 382, 386, 427-433, 436, 443, 445, 447-454, 476, 481-482, 506, 549-553, 571-574, 576-588, 697-698, 746-748, 751-753, 840-851, 928-966, 979, 989, 1011-1047, 1105, 1119, 1122, 1127, 1137, 1197-1201, 1203-1207, 1257-1264, 1268-1281, 1309-1318 |
| src/dinary/imports/income\_import.py      |      140 |      140 |      0% |    12-327 |
| src/dinary/imports/report\_2d\_3d.py      |      199 |       42 |     79% |170, 229-238, 274, 496-497, 535-566, 570-609, 613 |
| src/dinary/imports/seed.py                |      297 |       83 |     72% |328, 333-335, 337-343, 345, 351, 353, 355, 358, 361-363, 365-372, 376, 404, 406, 408, 410, 415, 421, 440, 519-532, 642-643, 648-649, 652-653, 664, 687-693, 766-770, 778-779, 781-782, 784-785, 787-791, 815-820, 865-869, 877-880, 899-901, 952-961, 1019-1020 |
| src/dinary/imports/verify\_equivalence.py |       92 |       92 |      0% |    20-210 |
| src/dinary/imports/verify\_income.py      |       37 |       37 |      0% |     9-107 |
| src/dinary/main.py                        |      115 |       10 |     91% |29-31, 127, 134, 136, 178, 184, 200, 210 |
| src/dinary/reports/expenses.py            |      122 |        5 |     96% |285, 287, 308-309, 406 |
| src/dinary/reports/income.py              |       86 |        3 |     97% |209-210, 285 |
| src/dinary/reports/verify\_budget.py      |       83 |        1 |     99% |       106 |
| src/dinary/reports/verify\_income.py      |       88 |        1 |     99% |        38 |
| src/dinary/services/catalog\_writer.py    |      422 |       79 |     81% |177-178, 340-344, 498-500, 520-562, 571, 686, 698, 706, 835-837, 870, 879-884, 893, 895, 900, 905, 915, 931, 971-973, 1009, 1018, 1032-1034, 1059, 1091, 1096, 1113, 1209, 1241, 1245-1247, 1254-1256, 1277, 1281-1283, 1290-1292, 1314, 1327-1329 |
| src/dinary/services/db\_migrations.py     |       49 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py       |      242 |       19 |     92% |60-61, 565, 661, 667, 710, 755-769, 801-808, 934-935, 955-957, 1022 |
| src/dinary/services/exchange\_rate.py     |       16 |       16 |      0% |      3-28 |
| src/dinary/services/nbs.py                |      102 |       46 |     55% |30, 35, 46-55, 69-78, 102-104, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py         |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py       |      142 |       11 |     92% |221-222, 310-322, 550-552, 591-595 |
| src/dinary/services/sheet\_logging.py     |      216 |       49 |     77% |96-100, 114-121, 129, 200, 204-206, 262-268, 283-285, 295-308, 337-338, 382, 447-448, 469-487, 496, 500 |
| src/dinary/services/sheet\_mapping.py     |      252 |       49 |     81% |274, 346-348, 383-389, 439, 468-469, 479-497, 574-581, 609-661, 689, 700-705 |
| src/dinary/services/sheets.py             |      231 |       58 |     75% |87-96, 100, 144-146, 165-176, 237-238, 244, 298, 337, 395, 478, 628, 672-677, 713-753 |
| src/dinary/services/sql\_loader.py        |       33 |        0 |    100% |           |
| **TOTAL**                                 | **3902** |  **940** | **76%** |           |


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