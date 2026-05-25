# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                           |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                                    |        1 |        0 |    100% |           |
| src/dinary/adapters/exchange\_rates.py                         |       38 |        1 |     97% |        39 |
| src/dinary/adapters/llm\_storage.py                            |      108 |       18 |     83% |72-74, 115-118, 162, 179, 182-185, 200, 208, 220, 224-225 |
| src/dinary/adapters/llmbroker.py                               |      106 |       10 |     91% |94-95, 191-192, 221-222, 234-237 |
| src/dinary/adapters/nbp.py                                     |       37 |        0 |    100% |           |
| src/dinary/adapters/nbs.py                                     |       47 |        0 |    100% |           |
| src/dinary/adapters/rate\_helpers.py                           |       27 |        0 |    100% |           |
| src/dinary/adapters/serbian\_receipt\_parser.py                |      107 |       18 |     83% |72, 78, 87-93, 107, 114, 148-149, 153-154, 157, 182-184, 211-217 |
| src/dinary/adapters/sheets\_client.py                          |       44 |       28 |     36% |26-35, 39, 54-63, 72-84 |
| src/dinary/api/catalog.py                                      |      100 |       10 |     90% |68-70, 99-107, 194-205, 264 |
| src/dinary/api/controllers/catalog.py                          |      133 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer.py                  |       33 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer\_categories.py      |       90 |       12 |     87% |48, 53, 67, 78, 184, 235, 239-241, 248-250 |
| src/dinary/api/controllers/catalog\_writer\_errors.py          |       31 |        2 |     94% |     83-84 |
| src/dinary/api/controllers/catalog\_writer\_events.py          |      209 |       34 |     84% |39, 247-249, 264, 274-279, 313, 315, 317, 319, 329, 345, 382-384, 418, 426, 439-441, 465, 501, 505-507, 514-516, 538, 551-553 |
| src/dinary/api/controllers/catalog\_writer\_groups.py          |       76 |       31 |     59% |37-41, 108-110, 130-170, 179, 203 |
| src/dinary/api/controllers/expense\_corrections.py             |       61 |        0 |    100% |           |
| src/dinary/api/controllers/expenses.py                         |      176 |       16 |     91% |155, 245-246, 281-298, 345, 446-448 |
| src/dinary/api/controllers/income.py                           |       61 |        3 |     95% |109, 122-123 |
| src/dinary/api/controllers/llm.py                              |       77 |        8 |     90% |91-92, 94-95, 100-101, 103-104 |
| src/dinary/api/controllers/qr\_parser.py                       |       23 |        0 |    100% |           |
| src/dinary/api/controllers/rules.py                            |       56 |        2 |     96% |     40-41 |
| src/dinary/api/currencies.py                                   |       34 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                         |        8 |        0 |    100% |           |
| src/dinary/api/expenses.py                                     |       23 |        0 |    100% |           |
| src/dinary/api/income.py                                       |       26 |        2 |     92% |    41, 54 |
| src/dinary/api/llm.py                                          |       20 |        0 |    100% |           |
| src/dinary/api/qr.py                                           |       17 |        6 |     65% |     26-31 |
| src/dinary/api/receipts.py                                     |       50 |        5 |     90% |     84-89 |
| src/dinary/api/rules.py                                        |       21 |        0 |    100% |           |
| src/dinary/background/classification/item\_normalizer.py       |       13 |        0 |    100% |           |
| src/dinary/background/classification/persist.py                |       88 |        4 |     95% |155, 209-211 |
| src/dinary/background/classification/receipt\_classifier.py    |       51 |        0 |    100% |           |
| src/dinary/background/classification/store\_resolver.py        |       29 |        1 |     97% |        68 |
| src/dinary/background/classification/task.py                   |      175 |       25 |     86% |81-82, 144-160, 164-165, 169-170, 184, 213-218, 235, 241-247, 290, 318 |
| src/dinary/background/rate\_prefetch/task.py                   |       51 |        2 |     96% |   81, 109 |
| src/dinary/background/sheet\_logging/income\_sheet\_logging.py |      176 |       44 |     75% |58-63, 89-90, 97-98, 106-108, 119, 152, 165-168, 176-179, 204-205, 217, 234, 240-242, 270-277, 289, 293, 317-325 |
| src/dinary/background/sheet\_logging/logging\_jobs.py          |       63 |        9 |     86% |88-89, 105-111 |
| src/dinary/background/sheet\_logging/sheet\_logging.py         |      221 |       39 |     82% |101-105, 119-126, 134, 200-201, 211, 216-218, 261-263, 286-287, 291-301, 376, 415-416, 427, 431, 458-459, 479-489 |
| src/dinary/background/sheet\_logging/sheets\_write.py          |       65 |       35 |     46% |114-119, 124-127, 131-136, 158-192 |
| src/dinary/background/sheet\_logging/task.py                   |       59 |        8 |     86% | 46-59, 98 |
| src/dinary/config.py                                           |      155 |       19 |     88% |38, 48, 111-113, 144, 150, 206-210, 212-216, 219-224, 227-231, 294-296 |
| src/dinary/db/catalog.py                                       |       44 |        0 |    100% |           |
| src/dinary/db/classification\_rules.py                         |       39 |        2 |     95% |     64-65 |
| src/dinary/db/currencies.py                                    |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/db/db\_migrations.py                                |       54 |        2 |     96% |    86, 89 |
| src/dinary/db/expenses.py                                      |      130 |       14 |     89% |126, 146, 149, 168, 201-208, 227-233, 316, 420, 462 |
| src/dinary/db/income.py                                        |       34 |        0 |    100% |           |
| src/dinary/db/receipts.py                                      |       86 |        5 |     94% |110-112, 210, 226 |
| src/dinary/db/sql\_loader.py                                   |       31 |        0 |    100% |           |
| src/dinary/db/storage.py                                       |      123 |        8 |     93% |143-144, 254-257, 265-271 |
| src/dinary/main.py                                             |       86 |        8 |     91% |43-45, 117, 123, 129, 139, 149 |
| src/dinary/sheets/sheet\_mapping.py                            |      244 |       50 |     80% |239, 311-313, 340-341, 348-354, 401, 404-416, 430-431, 441-459, 536-543, 571-617, 645, 656-661 |
| src/dinary/sheets/sheets.py                                    |      104 |        6 |     94% |59-60, 83, 119, 137, 220 |
| **TOTAL**                                                      | **3995** |  **492** | **88%** |           |


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