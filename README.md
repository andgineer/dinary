# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                      |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------ | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py               |        1 |        0 |    100% |           |
| src/dinary/api/categories.py              |       21 |        0 |    100% |           |
| src/dinary/api/expenses.py                |       67 |        4 |     94% |182-183, 205-206 |
| src/dinary/api/qr.py                      |       17 |        6 |     65% |     26-32 |
| src/dinary/config.py                      |       41 |        2 |     95% |    19, 29 |
| src/dinary/imports/expense\_import.py     |      370 |      105 |     72% |242, 247-248, 262, 264, 266, 294-311, 320, 345, 349, 378-384, 387, 394, 396, 398-405, 427, 432-433, 457, 500-504, 522-525, 650-654, 657-659, 747, 781-786, 799, 811, 824, 869-883, 896, 899, 904, 914, 955-959, 961-965, 1011-1018, 1022-1035, 1065-1072 |
| src/dinary/imports/income\_import.py      |      134 |      134 |      0% |    10-282 |
| src/dinary/imports/report\_2d\_3d.py      |      183 |       46 |     75% |161-164, 225-234, 270, 384-385, 410, 431-473, 477-509, 513 |
| src/dinary/imports/verify\_equivalence.py |       91 |       91 |      0% |    20-207 |
| src/dinary/imports/verify\_income.py      |       36 |       36 |      0% |      7-81 |
| src/dinary/main.py                        |       89 |        8 |     91% |29-31, 79, 115, 121, 137, 147 |
| src/dinary/services/db\_migrations.py     |       54 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py       |      297 |       28 |     91% |146-147, 154-155, 173-174, 198-199, 380, 386-387, 405-406, 599-603, 608-614, 697, 703, 823, 897-898, 925-932, 1013 |
| src/dinary/services/exchange\_rate.py     |       16 |        9 |     44% |     19-28 |
| src/dinary/services/nbs.py                |      100 |       44 |     56% |30, 35, 46-55, 69-78, 103, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py         |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py       |      457 |       97 |     79% |163, 165, 167, 169, 171, 190, 200, 237-238, 536, 541-543, 545-551, 553, 559, 561, 563, 566, 569-571, 573-580, 584, 611, 613, 615, 617, 622, 628, 647, 726-739, 770-771, 797, 898-899, 904-905, 908-909, 933-940, 954-959, 981-987, 1081, 1129-1130, 1138-1139, 1142-1143, 1145-1146, 1148-1152, 1174-1179, 1235-1243, 1256, 1277-1279, 1302-1314 |
| src/dinary/services/sheet\_logging.py     |      194 |       26 |     87% |143, 215, 255-261, 344-347, 541, 562, 583-592, 602 |
| src/dinary/services/sheets.py             |      205 |       42 |     80% |64, 72-78, 82, 143-144, 150, 204, 243, 384, 513, 525-528, 533, 575-580, 609-641 |
| src/dinary/services/sql\_loader.py        |       33 |        0 |    100% |           |
| **TOTAL**                                 | **2422** |  **681** | **72%** |           |


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