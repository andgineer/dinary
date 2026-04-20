# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                      |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------ | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py               |        1 |        0 |    100% |           |
| src/dinary/api/admin\_catalog.py          |      160 |       27 |     83% |220-221, 234-249, 274-275, 344-361, 380-381, 404-405, 456 |
| src/dinary/api/catalog.py                 |       44 |        3 |     93% |   172-174 |
| src/dinary/api/expenses.py                |       84 |        4 |     95% |117, 240-242 |
| src/dinary/api/qr.py                      |       17 |        6 |     65% |     26-32 |
| src/dinary/config.py                      |       45 |        2 |     96% |    19, 29 |
| src/dinary/imports/expense\_import.py     |      354 |      101 |     71% |244, 249-250, 264, 266, 268, 294-316, 362, 366, 407-413, 416, 423, 425, 427-434, 456, 461-462, 486, 529-533, 551-554, 672-674, 677-679, 761, 795-800, 813, 825, 838, 887-901, 914, 917, 922, 932, 992-996, 998-1002, 1052-1059, 1063-1076, 1098-1107 |
| src/dinary/imports/income\_import.py      |      140 |      140 |      0% |    11-305 |
| src/dinary/imports/report\_2d\_3d.py      |      183 |       46 |     75% |161-164, 223-232, 268, 382-383, 408, 429-471, 475-507, 511 |
| src/dinary/imports/verify\_equivalence.py |       91 |       91 |      0% |    20-209 |
| src/dinary/imports/verify\_income.py      |       37 |       37 |      0% |      8-84 |
| src/dinary/main.py                        |      115 |       10 |     91% |29-31, 127, 134, 136, 177, 183, 199, 209 |
| src/dinary/services/catalog\_writer.py    |      284 |       66 |     77% |252-256, 269-273, 327-329, 349-391, 400, 508, 520, 532, 671, 680-716, 728, 764-766, 787, 790-795, 804, 836, 841 |
| src/dinary/services/db\_migrations.py     |       49 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py       |      234 |       15 |     94% |334-338, 448, 568, 574, 617, 664, 708-715, 836-837, 857-859, 924 |
| src/dinary/services/exchange\_rate.py     |       16 |       16 |      0% |      3-28 |
| src/dinary/services/nbs.py                |      102 |       46 |     55% |30, 35, 46-55, 69-78, 102-104, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py         |       16 |        1 |     94% |        31 |
| src/dinary/services/runtime\_map.py       |      184 |       60 |     67% |156-158, 190-191, 282, 287-289, 336-342, 402, 425-430, 453-472, 505-574 |
| src/dinary/services/seed\_config.py       |      470 |      104 |     78% |190, 192, 194, 196, 198, 217, 227, 263-264, 559, 564-566, 568-574, 576, 582, 584, 586, 589, 592-594, 596-603, 607, 634, 636, 638, 640, 645, 651, 670, 749-762, 793-794, 882-894, 994, 1095-1096, 1101-1102, 1105-1106, 1117, 1140-1146, 1224-1228, 1250-1251, 1259-1260, 1263-1264, 1266-1267, 1269-1273, 1296-1301, 1363-1365, 1377, 1401-1403, 1453-1455, 1501-1510, 1545-1546 |
| src/dinary/services/sheet\_logging.py     |      192 |       41 |     79% |95-99, 113-120, 128, 156, 160-162, 219-225, 239-240, 269-270, 314, 379-380, 401-419, 428, 432 |
| src/dinary/services/sheets.py             |      231 |       58 |     75% |87-96, 100, 144-146, 165-176, 237-238, 244, 298, 337, 395, 478, 628, 672-677, 713-753 |
| src/dinary/services/sql\_loader.py        |       33 |        0 |    100% |           |
| **TOTAL**                                 | **3082** |  **876** | **72%** |           |


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