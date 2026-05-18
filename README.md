# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                      |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                               |        1 |        0 |    100% |           |
| src/dinary/adapters/exchange\_rates.py                    |       38 |        1 |     97% |        39 |
| src/dinary/adapters/llm\_bootstrap.py                     |       61 |       13 |     79% |57-60, 90-98 |
| src/dinary/adapters/llm\_client.py                        |      203 |       22 |     89% |200-202, 223-226, 277-278, 407-408, 428-431, 449, 453, 475, 477-480 |
| src/dinary/adapters/nbp.py                                |       37 |        0 |    100% |           |
| src/dinary/adapters/nbs.py                                |       47 |        0 |    100% |           |
| src/dinary/adapters/rate\_helpers.py                      |       27 |        0 |    100% |           |
| src/dinary/adapters/serbian\_receipt\_parser.py           |      106 |       18 |     83% |66, 72, 81-87, 101, 108, 142-143, 147-148, 151, 172-174, 201-207 |
| src/dinary/adapters/sheets\_client.py                     |       44 |       28 |     36% |26-35, 39, 54-63, 72-84 |
| src/dinary/api/catalog.py                                 |      102 |       10 |     90% |71-73, 102-110, 197-208, 267 |
| src/dinary/api/controllers/catalog.py                     |      133 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer.py             |       33 |        0 |    100% |           |
| src/dinary/api/controllers/catalog\_writer\_categories.py |       90 |       12 |     87% |48, 53, 67, 78, 184, 235, 239-241, 248-250 |
| src/dinary/api/controllers/catalog\_writer\_errors.py     |       31 |        2 |     94% |     83-84 |
| src/dinary/api/controllers/catalog\_writer\_events.py     |      209 |       34 |     84% |39, 247-249, 264, 274-279, 313, 315, 317, 319, 329, 345, 382-384, 418, 426, 439-441, 465, 501, 505-507, 514-516, 538, 551-553 |
| src/dinary/api/controllers/catalog\_writer\_groups.py     |       76 |       31 |     59% |37-41, 108-110, 130-170, 179, 203 |
| src/dinary/api/controllers/expense\_corrections.py        |       59 |        0 |    100% |           |
| src/dinary/api/controllers/expenses.py                    |      142 |        6 |     96% |142, 231-232, 371-373 |
| src/dinary/api/controllers/llm.py                         |       90 |       18 |     80% |85-86, 88-89, 94-95, 97-98, 131-147 |
| src/dinary/api/controllers/qr\_parser.py                  |       16 |        1 |     94% |        31 |
| src/dinary/api/controllers/rules.py                       |       34 |        2 |     94% |     51-52 |
| src/dinary/api/currencies.py                              |       34 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                    |        9 |        0 |    100% |           |
| src/dinary/api/expenses.py                                |       20 |        0 |    100% |           |
| src/dinary/api/llm.py                                     |       23 |        1 |     96% |        54 |
| src/dinary/api/qr.py                                      |       17 |        6 |     65% |     26-31 |
| src/dinary/api/receipts.py                                |       36 |        5 |     86% |     61-66 |
| src/dinary/api/rules.py                                   |       11 |        0 |    100% |           |
| src/dinary/background/classification/item\_normalizer.py  |       13 |        0 |    100% |           |
| src/dinary/background/classification/store\_resolver.py   |       18 |        0 |    100% |           |
| src/dinary/background/classification/task.py              |      258 |       42 |     84% |109-110, 135-140, 144-146, 150-154, 158-162, 166-170, 213-215, 241, 273, 282-290, 384, 422-424, 504-506, 524-526, 565-567 |
| src/dinary/background/rate\_prefetch/task.py              |       56 |        2 |     96% |   86, 117 |
| src/dinary/background/sheet\_logging/logging\_jobs.py     |       63 |        9 |     86% |88-89, 105-111 |
| src/dinary/background/sheet\_logging/sheet\_logging.py    |      227 |       41 |     82% |101-105, 119-126, 134, 200-201, 211, 216-218, 261-263, 287-288, 292-302, 379, 418-422, 433, 437, 467-468, 488-498 |
| src/dinary/background/sheet\_logging/sheets\_write.py     |       65 |       35 |     46% |114-119, 124-127, 131-136, 158-192 |
| src/dinary/background/sheet\_logging/task.py              |       54 |        7 |     87% |     45-58 |
| src/dinary/config.py                                      |      157 |       19 |     88% |38, 48, 111-113, 144, 150, 206-210, 212-216, 219-224, 227-231, 294-296 |
| src/dinary/db/catalog.py                                  |       44 |        0 |    100% |           |
| src/dinary/db/classification\_rules.py                    |       33 |        2 |     94% |     53-54 |
| src/dinary/db/currencies.py                               |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/db/db\_migrations.py                           |       54 |        2 |     96% |    86, 89 |
| src/dinary/db/expenses.py                                 |      132 |       14 |     89% |126, 146, 149, 168, 201-208, 227-233, 319, 423, 465 |
| src/dinary/db/receipts.py                                 |       76 |       10 |     87% |94-95, 120-122, 170-172, 233, 249 |
| src/dinary/db/sql\_loader.py                              |       31 |        0 |    100% |           |
| src/dinary/db/storage.py                                  |      120 |       11 |     91% |144-145, 256-259, 267-273, 319-321 |
| src/dinary/main.py                                        |       80 |        8 |     90% |40-42, 110, 116, 122, 132, 142 |
| src/dinary/sheets/sheet\_mapping.py                       |      250 |       54 |     78% |239, 311-313, 340-341, 348-354, 404, 407-419, 433-434, 444-462, 539-546, 574-626, 654, 665-670 |
| src/dinary/sheets/sheets.py                               |      104 |        6 |     94% |59-60, 83, 119, 137, 220 |
| **TOTAL**                                                 | **3598** |  **477** | **87%** |           |


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