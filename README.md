# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary-server/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                       |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                |        1 |        0 |    100% |           |
| src/dinary/api/categories.py               |       21 |        0 |    100% |           |
| src/dinary/api/expenses.py                 |       67 |        4 |     94% |178-179, 201-202 |
| src/dinary/api/qr.py                       |       17 |        6 |     65% |     26-32 |
| src/dinary/config.py                       |       20 |        1 |     95% |        15 |
| src/dinary/imports/report\_2d\_3d.py       |      183 |       46 |     75% |161-164, 225-234, 270, 384-385, 410, 431-473, 477-509, 513 |
| src/dinary/main.py                         |       57 |        7 |     88% |27-29, 74, 80, 96, 106 |
| src/dinary/services/category\_store.py     |       27 |        0 |    100% |           |
| src/dinary/services/db\_migrations.py      |       54 |        2 |     96% |    49, 52 |
| src/dinary/services/duckdb\_repo.py        |      289 |       35 |     88% |145-146, 153-154, 172-173, 197-198, 370-379, 396-397, 574-578, 583-589, 672, 678, 798, 838-839, 866-873, 954 |
| src/dinary/services/exchange\_rate.py      |       16 |        9 |     44% |     19-28 |
| src/dinary/services/import\_income.py      |      134 |      134 |      0% |     9-353 |
| src/dinary/services/import\_sheet.py       |      370 |      105 |     72% |242, 247-248, 262, 264, 266, 294-311, 320, 345, 349, 378-384, 387, 394, 396, 398-405, 427, 432-433, 457, 501-505, 523-526, 651-655, 658-660, 748, 785-790, 803, 815, 828, 873-887, 900, 903, 908, 918, 958-962, 964-968, 1042-1049, 1053-1066, 1096-1103 |
| src/dinary/services/nbs.py                 |      100 |       44 |     56% |30, 35, 46-55, 69-78, 103, 107-108, 112-114, 118, 121-122, 126-133, 151-156 |
| src/dinary/services/qr\_parser.py          |       16 |        1 |     94% |        31 |
| src/dinary/services/seed\_config.py        |      438 |       98 |     78% |155, 157, 159, 161, 163, 182-192, 202, 239-240, 538, 543-545, 547-553, 555, 561, 563, 565, 568, 571-573, 575-582, 586, 613, 615, 617, 619, 624, 630, 649, 728-741, 788-789, 815, 916-917, 922-923, 926-927, 951-958, 972-977, 999-1005, 1079-1080, 1089-1090, 1093-1094, 1096-1097, 1099-1103, 1119-1124, 1180-1188, 1201, 1222-1224, 1247-1259 |
| src/dinary/services/sheets.py              |      165 |       15 |     91% |38-44, 48-49, 81, 101-104, 109-111, 261 |
| src/dinary/services/sql\_loader.py         |       33 |        0 |    100% |           |
| src/dinary/services/sync.py                |      200 |       33 |     84% |172-176, 324, 359-365, 448-451, 472-477, 481, 546, 560-561, 579-586, 592 |
| src/dinary/services/verify\_equivalence.py |       91 |       91 |      0% |    18-205 |
| src/dinary/services/verify\_income.py      |       36 |       36 |      0% |      7-81 |
| **TOTAL**                                  | **2335** |  **667** | **71%** |           |


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