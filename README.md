# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                           |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                                    |        1 |        0 |    100% |           |
| src/dinary/adapters/exchange\_rates.py                         |       43 |        1 |     98% |        41 |
| src/dinary/adapters/llm\_chat.py                               |       76 |        6 |     92% |89-90, 140-142, 164 |
| src/dinary/adapters/llm\_storage.py                            |      137 |       18 |     87% |74-76, 114-117, 174, 203-206, 219, 227, 231, 254, 258-259 |
| src/dinary/adapters/llmbroker.py                               |      112 |        8 |     93% |110-111, 244-245, 257-260 |
| src/dinary/adapters/nbp.py                                     |       37 |        0 |    100% |           |
| src/dinary/adapters/nbs.py                                     |       47 |        0 |    100% |           |
| src/dinary/adapters/rate\_helpers.py                           |       27 |        0 |    100% |           |
| src/dinary/adapters/serbian\_receipt\_parser.py                |      127 |       18 |     86% |110, 116, 125-131, 145, 152, 186-187, 191-192, 195, 220-222, 249-255 |
| src/dinary/adapters/sheets\_client.py                          |       44 |       28 |     36% |26-35, 39, 54-63, 72-84 |
| src/dinary/api/analytics.py                                    |       52 |        1 |     98% |        33 |
| src/dinary/api/catalog.py                                      |      100 |       10 |     90% |68-70, 99-107, 194-205, 264 |
| src/dinary/api/controllers/catalog.py                          |      126 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer.py                  |       33 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer\_categories.py      |       90 |       12 |     87% |48, 53, 67, 78, 184, 235, 239-241, 248-250 |
| src/dinary/api/controllers/catalog\_writer\_errors.py          |       31 |        2 |     94% |     83-84 |
| src/dinary/api/controllers/catalog\_writer\_events.py          |      187 |       33 |     82% |37, 199-201, 216, 226-231, 265, 267, 269, 271, 281, 297, 333-335, 359, 366, 376-378, 405, 409-411, 418-420, 442, 455-457 |
| src/dinary/api/controllers/catalog\_writer\_groups.py          |       76 |       31 |     59% |37-41, 108-110, 130-170, 179, 203 |
| src/dinary/api/controllers/expense\_corrections.py             |       61 |        0 |    100% |           |
| src/dinary/api/controllers/expenses.py                         |      167 |       14 |     92% |231-232, 267-277, 324, 425-427 |
| src/dinary/api/controllers/income.py                           |       59 |        2 |     97% |   135-136 |
| src/dinary/api/controllers/llm.py                              |       69 |        6 |     91% |86-87, 89-90, 95-96 |
| src/dinary/api/controllers/qr\_parser.py                       |       23 |        0 |    100% |           |
| src/dinary/api/controllers/receipt\_queue.py                   |       68 |        3 |     96% |99, 122, 196 |
| src/dinary/api/controllers/rules.py                            |       53 |        2 |     96% |     40-41 |
| src/dinary/api/currencies.py                                   |       33 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                         |        8 |        0 |    100% |           |
| src/dinary/api/expenses.py                                     |       23 |        0 |    100% |           |
| src/dinary/api/http\_errors.py                                 |        9 |        0 |    100% |           |
| src/dinary/api/income.py                                       |       26 |        2 |     92% |    41, 53 |
| src/dinary/api/llm.py                                          |       20 |        0 |    100% |           |
| src/dinary/api/qr.py                                           |       17 |        6 |     65% |     26-31 |
| src/dinary/api/receipts.py                                     |       57 |        5 |     91% |   107-112 |
| src/dinary/api/rules.py                                        |       18 |        0 |    100% |           |
| src/dinary/background/classification/item\_normalizer.py       |       13 |        0 |    100% |           |
| src/dinary/background/classification/persist.py                |       82 |        1 |     99% |       153 |
| src/dinary/background/classification/receipt\_classifier.py    |       55 |        0 |    100% |           |
| src/dinary/background/classification/store\_resolver.py        |       31 |        1 |     97% |        68 |
| src/dinary/background/classification/task.py                   |      257 |       19 |     93% |79, 132-133, 139-146, 214-228, 331, 380, 527 |
| src/dinary/background/rate\_prefetch/task.py                   |       51 |        2 |     96% |   81, 109 |
| src/dinary/background/sheet\_logging/income\_sheet\_logging.py |      176 |       46 |     74% |58-63, 89-90, 97-98, 106-108, 119, 152, 165-168, 176-179, 204-205, 217, 234, 240-242, 270-277, 289, 293, 309-310, 317-325 |
| src/dinary/background/sheet\_logging/logging\_jobs.py          |       63 |        9 |     86% |88-89, 105-111 |
| src/dinary/background/sheet\_logging/sheet\_logging.py         |      221 |       39 |     82% |101-105, 119-126, 134, 200-201, 211, 216-218, 261-263, 286-287, 291-301, 376, 415-416, 427, 431, 458-459, 479-489 |
| src/dinary/background/sheet\_logging/sheets\_write.py          |       65 |       35 |     46% |114-119, 124-127, 131-136, 158-192 |
| src/dinary/background/sheet\_logging/task.py                   |       59 |       17 |     71% |46-59, 89-100 |
| src/dinary/config.py                                           |      155 |       19 |     88% |38, 48, 111-113, 144, 150, 206-210, 212-216, 219-224, 227-231, 294-296 |
| src/dinary/db/catalog.py                                       |       44 |        0 |    100% |           |
| src/dinary/db/classification\_rules.py                         |       37 |        2 |     95% |     66-67 |
| src/dinary/db/currencies.py                                    |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/db/db\_migrations.py                                |       54 |        2 |     96% |    86, 89 |
| src/dinary/db/expenses.py                                      |      130 |       12 |     91% |126, 168, 201-208, 227-233, 316, 420, 462 |
| src/dinary/db/income.py                                        |       55 |        5 |     91% |103, 117-124 |
| src/dinary/db/receipts.py                                      |       94 |        3 |     97% |   119-121 |
| src/dinary/db/sql\_loader.py                                   |       31 |        0 |    100% |           |
| src/dinary/db/storage.py                                       |      125 |        6 |     95% |256-259, 267-273 |
| src/dinary/main.py                                             |       88 |        8 |     91% |44-46, 120, 126, 132, 142, 152 |
| src/dinary/sheets/sheet\_mapping.py                            |      247 |       52 |     79% |239, 311-313, 340-341, 348-354, 401, 404-416, 430-431, 441-459, 536-543, 571-617, 645, 656-661, 666-667 |
| src/dinary/sheets/sheets.py                                    |      104 |        6 |     94% |59-60, 83, 119, 137, 220 |
| src/dinary\_analytics/ai\_service.py                           |       77 |       10 |     87% |60, 66, 139-145, 149 |
| src/dinary\_analytics/backup.py                                |       66 |       21 |     68% |24-25, 43-44, 62-63, 77-95, 99 |
| src/dinary\_analytics/charts.py                                |       57 |        0 |    100% |           |
| src/dinary\_analytics/connection.py                            |       11 |        0 |    100% |           |
| src/dinary\_analytics/llm.py                                   |       62 |        4 |     94% |85, 88, 143-144 |
| src/dinary\_analytics/notebooks/dashboard.py                   |      425 |      166 |     61% |32, 76, 85, 98, 115, 131-134, 148-190, 212-214, 216, 220-221, 270-340, 352, 443, 455-525, 538-549, 554-584, 589-619, 631-644, 649-651, 683-691, 702, 707-711, 716-720, 746, 762, 895, 911-913, 980-1016, 1021-1044, 1065-1068, 1072-1076, 1080-1084, 1088-1090, 1104-1105, 1110, 1149 |
| src/dinary\_analytics/paths.py                                 |       17 |        0 |    100% |           |
| src/dinary\_analytics/refresh.py                               |      100 |        2 |     98% |   50, 161 |
| src/dinary\_analytics/settings.py                              |       49 |        1 |     98% |        56 |
| src/dinary\_analytics/views.py                                 |       23 |        1 |     96% |        65 |
| **TOTAL**                                                      | **5215** |  **702** | **87%** |           |


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