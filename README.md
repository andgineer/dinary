# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                           |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                                    |        1 |        0 |    100% |           |
| src/dinary/adapters/exchange\_rates.py                         |       38 |        1 |     97% |        39 |
| src/dinary/adapters/llm\_storage.py                            |      136 |       18 |     87% |73-75, 113-116, 173, 202-205, 218, 226, 230, 253, 257-258 |
| src/dinary/adapters/llmbroker.py                               |      118 |       10 |     92% |112-113, 226-227, 256-257, 269-272 |
| src/dinary/adapters/nbp.py                                     |       37 |        0 |    100% |           |
| src/dinary/adapters/nbs.py                                     |       47 |        0 |    100% |           |
| src/dinary/adapters/rate\_helpers.py                           |       27 |        0 |    100% |           |
| src/dinary/adapters/serbian\_receipt\_parser.py                |      107 |       18 |     83% |72, 78, 87-93, 107, 114, 148-149, 153-154, 157, 182-184, 211-217 |
| src/dinary/adapters/sheets\_client.py                          |       44 |       28 |     36% |26-35, 39, 54-63, 72-84 |
| src/dinary/api/catalog.py                                      |      100 |       10 |     90% |68-70, 99-107, 194-205, 264 |
| src/dinary/api/controllers/catalog.py                          |      126 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer.py                  |       33 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer\_categories.py      |       90 |       12 |     87% |48, 53, 67, 78, 184, 235, 239-241, 248-250 |
| src/dinary/api/controllers/catalog\_writer\_errors.py          |       31 |        2 |     94% |     83-84 |
| src/dinary/api/controllers/catalog\_writer\_events.py          |      187 |       33 |     82% |37, 199-201, 216, 226-231, 265, 267, 269, 271, 281, 297, 333-335, 359, 366, 376-378, 405, 409-411, 418-420, 442, 455-457 |
| src/dinary/api/controllers/catalog\_writer\_groups.py          |       76 |       31 |     59% |37-41, 108-110, 130-170, 179, 203 |
| src/dinary/api/controllers/expense\_corrections.py             |       61 |        0 |    100% |           |
| src/dinary/api/controllers/expenses.py                         |      176 |       16 |     91% |155, 245-246, 281-298, 345, 446-448 |
| src/dinary/api/controllers/income.py                           |       62 |        2 |     97% |   141-142 |
| src/dinary/api/controllers/llm.py                              |       70 |        6 |     91% |86-87, 89-90, 95-96 |
| src/dinary/api/controllers/qr\_parser.py                       |       23 |        0 |    100% |           |
| src/dinary/api/controllers/rules.py                            |       54 |        2 |     96% |     40-41 |
| src/dinary/api/currencies.py                                   |       34 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                         |        8 |        0 |    100% |           |
| src/dinary/api/expenses.py                                     |       23 |        0 |    100% |           |
| src/dinary/api/income.py                                       |       26 |        2 |     92% |    41, 53 |
| src/dinary/api/llm.py                                          |       20 |        0 |    100% |           |
| src/dinary/api/qr.py                                           |       17 |        6 |     65% |     26-31 |
| src/dinary/api/receipts.py                                     |       50 |        5 |     90% |     84-89 |
| src/dinary/api/rules.py                                        |       18 |        0 |    100% |           |
| src/dinary/background/classification/item\_normalizer.py       |       13 |        0 |    100% |           |
| src/dinary/background/classification/persist.py                |       90 |        4 |     96% |154, 207-209 |
| src/dinary/background/classification/receipt\_classifier.py    |       55 |        0 |    100% |           |
| src/dinary/background/classification/store\_resolver.py        |       34 |        1 |     97% |        72 |
| src/dinary/background/classification/task.py                   |      242 |       14 |     94% |106-107, 113-120, 175, 277, 331, 480 |
| src/dinary/background/rate\_prefetch/task.py                   |       51 |        2 |     96% |   81, 109 |
| src/dinary/background/sheet\_logging/income\_sheet\_logging.py |      176 |       46 |     74% |58-63, 89-90, 97-98, 106-108, 119, 152, 165-168, 176-179, 204-205, 217, 234, 240-242, 270-277, 289, 293, 309-310, 317-325 |
| src/dinary/background/sheet\_logging/logging\_jobs.py          |       63 |        9 |     86% |88-89, 105-111 |
| src/dinary/background/sheet\_logging/sheet\_logging.py         |      221 |       39 |     82% |101-105, 119-126, 134, 200-201, 211, 216-218, 261-263, 286-287, 291-301, 376, 415-416, 427, 431, 458-459, 479-489 |
| src/dinary/background/sheet\_logging/sheets\_write.py          |       65 |       35 |     46% |114-119, 124-127, 131-136, 158-192 |
| src/dinary/background/sheet\_logging/task.py                   |       59 |       17 |     71% |46-59, 89-100 |
| src/dinary/config.py                                           |      155 |       19 |     88% |38, 48, 111-113, 144, 150, 206-210, 212-216, 219-224, 227-231, 294-296 |
| src/dinary/db/catalog.py                                       |       44 |        0 |    100% |           |
| src/dinary/db/classification\_rules.py                         |       39 |        2 |     95% |     65-66 |
| src/dinary/db/currencies.py                                    |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/db/db\_migrations.py                                |       54 |        2 |     96% |    86, 89 |
| src/dinary/db/expenses.py                                      |      130 |       14 |     89% |126, 146, 149, 168, 201-208, 227-233, 316, 420, 462 |
| src/dinary/db/income.py                                        |       55 |        5 |     91% |103, 117-124 |
| src/dinary/db/receipts.py                                      |       92 |        3 |     97% |   119-121 |
| src/dinary/db/sql\_loader.py                                   |       31 |        0 |    100% |           |
| src/dinary/db/storage.py                                       |      123 |        8 |     93% |143-144, 254-257, 265-271 |
| src/dinary/main.py                                             |       87 |        8 |     91% |43-45, 118, 124, 130, 140, 150 |
| src/dinary/sheets/sheet\_mapping.py                            |      240 |       52 |     78% |239, 311-313, 340-341, 348-354, 401, 404-416, 430-431, 441-459, 536-543, 571-617, 645, 656-661, 666-667 |
| src/dinary/sheets/sheets.py                                    |      104 |        6 |     94% |59-60, 83, 119, 137, 220 |
| **TOTAL**                                                      | **4097** |  **493** | **88%** |           |


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