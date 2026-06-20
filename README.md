# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                           |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                                    |        1 |        0 |    100% |           |
| src/dinary/adapters/exchange\_rates.py                         |       43 |        1 |     98% |        41 |
| src/dinary/adapters/nbp.py                                     |       37 |        0 |    100% |           |
| src/dinary/adapters/nbs.py                                     |       47 |        0 |    100% |           |
| src/dinary/adapters/rate\_helpers.py                           |       27 |        0 |    100% |           |
| src/dinary/adapters/serbian\_receipt\_parser.py                |      127 |       18 |     86% |110, 116, 125-131, 145, 152, 186-187, 191-192, 195, 220-222, 249-255 |
| src/dinary/adapters/sheets\_client.py                          |       44 |       28 |     36% |26-35, 39, 54-63, 72-84 |
| src/dinary/api/analytics.py                                    |       52 |        1 |     98% |        33 |
| src/dinary/api/catalog.py                                      |       92 |       10 |     89% |71-73, 108-116, 161-172, 237 |
| src/dinary/api/category\_templates.py                          |       33 |        0 |    100% |           |
| src/dinary/api/controllers/catalog.py                          |      137 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer.py                  |       33 |        1 |     97% |        17 |
| src/dinary/api/controllers/catalog\_writer\_errors.py          |       31 |        2 |     94% |     83-84 |
| src/dinary/api/controllers/catalog\_writer\_events.py          |      187 |       33 |     82% |37, 199-201, 216, 226-231, 265, 267, 269, 271, 281, 297, 333-335, 359, 366, 376-378, 405, 409-411, 418-420, 442, 455-457 |
| src/dinary/api/controllers/catalog\_writer\_groups.py          |       76 |       31 |     59% |37-41, 108-110, 130-170, 179, 203 |
| src/dinary/api/controllers/category\_templates.py              |       80 |        1 |     99% |       118 |
| src/dinary/api/controllers/expense\_corrections.py             |       70 |        1 |     99% |        94 |
| src/dinary/api/controllers/expenses.py                         |      172 |       17 |     90% |231-232, 267-277, 324, 415-417, 430-432 |
| src/dinary/api/controllers/income.py                           |       59 |        2 |     97% |   135-136 |
| src/dinary/api/controllers/llm.py                              |       42 |        0 |    100% |           |
| src/dinary/api/controllers/qr\_parser.py                       |       23 |        0 |    100% |           |
| src/dinary/api/controllers/receipt\_queue.py                   |       68 |        3 |     96% |99, 122, 196 |
| src/dinary/api/controllers/rules.py                            |       54 |        2 |     96% |     41-42 |
| src/dinary/api/currencies.py                                   |       33 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                         |        8 |        0 |    100% |           |
| src/dinary/api/expenses.py                                     |       23 |        0 |    100% |           |
| src/dinary/api/http\_errors.py                                 |        9 |        0 |    100% |           |
| src/dinary/api/income.py                                       |       26 |        2 |     92% |    41, 53 |
| src/dinary/api/llm.py                                          |       21 |        0 |    100% |           |
| src/dinary/api/qr.py                                           |       17 |        6 |     65% |     26-31 |
| src/dinary/api/receipts.py                                     |       57 |        5 |     91% |   107-112 |
| src/dinary/api/rules.py                                        |       18 |        0 |    100% |           |
| src/dinary/background/classification/item\_normalizer.py       |       13 |        0 |    100% |           |
| src/dinary/background/classification/persist.py                |       81 |        1 |     99% |       152 |
| src/dinary/background/classification/receipt\_classifier.py    |       57 |        0 |    100% |           |
| src/dinary/background/classification/store\_resolver.py        |       31 |        1 |     97% |        68 |
| src/dinary/background/classification/task.py                   |      259 |       19 |     93% |80, 133-134, 140-147, 215-229, 332, 381, 530 |
| src/dinary/background/rate\_prefetch/task.py                   |       51 |        2 |     96% |   81, 109 |
| src/dinary/background/sheet\_logging/income\_sheet\_logging.py |      176 |       44 |     75% |58-63, 89-90, 97-98, 106-108, 119, 152, 165-168, 176-179, 204-205, 217, 234, 240-242, 270-277, 289, 293, 317-325 |
| src/dinary/background/sheet\_logging/logging\_jobs.py          |       63 |        9 |     86% |88-89, 105-111 |
| src/dinary/background/sheet\_logging/sheet\_logging.py         |      221 |       39 |     82% |101-105, 119-126, 134, 200-201, 211, 216-218, 261-263, 286-287, 291-301, 376, 415-416, 427, 431, 458-459, 479-489 |
| src/dinary/background/sheet\_logging/sheets\_write.py          |       65 |       35 |     46% |114-119, 124-127, 131-136, 158-192 |
| src/dinary/background/sheet\_logging/task.py                   |       59 |        8 |     86% | 46-59, 98 |
| src/dinary/category\_templates/loader.py                       |       65 |        0 |    100% |           |
| src/dinary/config.py                                           |       68 |        6 |     91% |34, 44, 89, 107-109 |
| src/dinary/db/catalog.py                                       |      119 |        1 |     99% |        97 |
| src/dinary/db/category\_apply.py                               |       40 |        0 |    100% |           |
| src/dinary/db/category\_seed.py                                |       59 |        6 |     90% |150-157, 162 |
| src/dinary/db/classification\_rules.py                         |       37 |        2 |     95% |     66-67 |
| src/dinary/db/currencies.py                                    |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/db/db\_migrations.py                                |       54 |        2 |     96% |    86, 89 |
| src/dinary/db/expenses.py                                      |      130 |       12 |     91% |126, 168, 201-208, 227-233, 316, 420, 462 |
| src/dinary/db/income.py                                        |       55 |        5 |     91% |103, 117-124 |
| src/dinary/db/migrations/0006\_category\_templates.py          |       35 |        5 |     86% |118-119, 122-124 |
| src/dinary/db/migrations/0007\_drop\_legacy\_llmbroker.py      |        3 |        0 |    100% |           |
| src/dinary/db/receipts.py                                      |       92 |        3 |     97% |   119-121 |
| src/dinary/db/sql\_loader.py                                   |       31 |        0 |    100% |           |
| src/dinary/db/storage.py                                       |      129 |        7 |     95% |72, 266-269, 277-283 |
| src/dinary/main.py                                             |       93 |        8 |     91% |45-47, 129, 135, 141, 151, 161 |
| src/dinary/sheets/sheet\_mapping.py                            |      224 |       47 |     79% |239, 314-316, 343-344, 351-357, 404, 407-419, 433-434, 444-462, 480-514, 538, 549-554, 559-560 |
| src/dinary/sheets/sheets.py                                    |      104 |        6 |     94% |59-60, 83, 119, 137, 220 |
| src/dinary\_analytics/ai\_service.py                           |       77 |       10 |     87% |60, 66, 139-145, 149 |
| src/dinary\_analytics/backup.py                                |       66 |       21 |     68% |24-25, 43-44, 62-63, 77-95, 99 |
| src/dinary\_analytics/charts.py                                |       57 |        0 |    100% |           |
| src/dinary\_analytics/connection.py                            |       11 |        0 |    100% |           |
| src/dinary\_analytics/llm.py                                   |       63 |       10 |     84% |39-44, 66, 69, 131-132 |
| src/dinary\_analytics/notebooks/dashboard.py                   |      425 |      166 |     61% |32, 76, 85, 98, 115, 131-134, 148-190, 212-214, 216, 220-221, 270-340, 352, 443, 455-525, 538-549, 554-584, 589-619, 631-644, 649-651, 683-691, 702, 707-711, 716-720, 746, 762, 895, 911-913, 980-1016, 1021-1044, 1065-1068, 1072-1076, 1080-1084, 1088-1090, 1104-1105, 1110, 1149 |
| src/dinary\_analytics/paths.py                                 |       17 |        0 |    100% |           |
| src/dinary\_analytics/refresh.py                               |      100 |        2 |     98% |   50, 161 |
| src/dinary\_analytics/settings.py                              |       49 |        1 |     98% |        56 |
| src/dinary\_analytics/views.py                                 |       23 |        1 |     96% |        65 |
| **TOTAL**                                                      | **5083** |  **648** | **87%** |           |


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