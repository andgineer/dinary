# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                   |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                            |        1 |        0 |    100% |           |
| src/dinary/api/admin\_catalog.py                       |      156 |       27 |     83% |187-188, 199-209, 244-245, 266-267, 282-283, 319-332, 343-344, 361-362, 375-376, 388-389, 424 |
| src/dinary/api/admin\_llm.py                           |       98 |       18 |     82% |103-104, 106-107, 112-113, 115-116, 161-178 |
| src/dinary/api/catalog.py                              |       90 |        3 |     97% |   322-324 |
| src/dinary/api/currencies.py                           |       34 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                 |       89 |        2 |     98% |   204-205 |
| src/dinary/api/expenses.py                             |      102 |        4 |     96% |150, 294-296 |
| src/dinary/api/qr.py                                   |       17 |        6 |     65% |     26-32 |
| src/dinary/api/receipt\_review.py                      |       27 |        0 |    100% |           |
| src/dinary/api/receipts.py                             |       36 |        5 |     86% |     61-66 |
| src/dinary/background/rate\_prefetch\_task.py          |       56 |        2 |     96% |   86, 117 |
| src/dinary/background/receipt\_classification\_task.py |      244 |       42 |     83% |119-120, 145-150, 154-156, 160-164, 168-172, 176-180, 223-225, 251, 283, 292-300, 371, 411-413, 460-462, 480-482, 512-514 |
| src/dinary/background/sheet\_logging\_task.py          |       53 |        7 |     87% |     44-57 |
| src/dinary/config.py                                   |      152 |       19 |     88% |37, 47, 110-112, 143, 149, 205-209, 211-215, 218-223, 226-230, 293-295 |
| src/dinary/imports/expense\_import.py                  |      421 |      132 |     69% |235, 240-241, 255, 257, 259, 286-304, 334, 338, 354-360, 364-372, 383, 385, 402, 404, 409, 430, 435-436, 460, 491-495, 508-512, 514-517, 669-670, 718-720, 723-725, 807-818, 887-930, 943, 953, 976-1012, 1070, 1084, 1087, 1092, 1102, 1160-1166, 1171-1183, 1222-1226, 1228-1232, 1290-1299 |
| src/dinary/imports/income\_import.py                   |      139 |      139 |      0% |    12-318 |
| src/dinary/imports/report\_2d\_3d.py                   |      199 |       42 |     79% |170, 226-235, 271, 493-494, 532-563, 567-606, 610 |
| src/dinary/imports/seed.py                             |      206 |       49 |     76% |142-155, 217-223, 251, 307-308, 313-314, 317-318, 325, 375-379, 387-388, 390-391, 393-394, 396-400, 424-429, 474-478, 486-489, 511-513, 564-573, 631-632 |
| src/dinary/imports/seed\_derivation.py                 |      105 |       29 |     72% |252-258, 263, 266, 270-278, 296, 298, 305, 324, 331, 365, 367, 369, 371, 375, 395 |
| src/dinary/imports/verify\_equivalence.py              |       93 |       93 |      0% |    20-208 |
| src/dinary/imports/verify\_income.py                   |       37 |       37 |      0% |     9-107 |
| src/dinary/main.py                                     |       81 |        8 |     90% |41-43, 112, 118, 124, 134, 144 |
| src/dinary/reports/expenses.py                         |      122 |        5 |     96% |299, 301, 322-323, 420 |
| src/dinary/reports/income.py                           |       86 |        3 |     97% |209-210, 285 |
| src/dinary/reports/verify\_budget.py                   |       83 |        1 |     99% |       104 |
| src/dinary/reports/verify\_income.py                   |       88 |        1 |     99% |        38 |
| src/dinary/services/catalog.py                         |       44 |        0 |    100% |           |
| src/dinary/services/catalog\_writer.py                 |       33 |        0 |    100% |           |
| src/dinary/services/catalog\_writer\_categories.py     |       90 |       12 |     87% |48, 53, 67, 78, 184, 235, 239-241, 248-250 |
| src/dinary/services/catalog\_writer\_errors.py         |       31 |        2 |     94% |     83-84 |
| src/dinary/services/catalog\_writer\_events.py         |      209 |       34 |     84% |39, 247-249, 264, 274-279, 313, 315, 317, 319, 329, 345, 382-384, 418, 426, 439-441, 465, 501, 505-507, 514-516, 538, 551-553 |
| src/dinary/services/catalog\_writer\_groups.py         |       76 |       31 |     59% |37-41, 108-110, 130-170, 179, 203 |
| src/dinary/services/classification\_rules.py           |       21 |        0 |    100% |           |
| src/dinary/services/currencies.py                      |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/services/db\_migrations.py                  |       54 |        2 |     96% |    86, 89 |
| src/dinary/services/exchange\_rates.py                 |       38 |        1 |     97% |        39 |
| src/dinary/services/expenses.py                        |      132 |       14 |     89% |126, 146, 149, 168, 201-208, 227-233, 319, 423, 465 |
| src/dinary/services/item\_normalizer.py                |       13 |        0 |    100% |           |
| src/dinary/services/llm\_bootstrap.py                  |       61 |       13 |     79% |57-60, 90-98 |
| src/dinary/services/llm\_client.py                     |      192 |       22 |     89% |165-167, 188-191, 242-243, 362-363, 383-386, 404, 408, 430, 432-435 |
| src/dinary/services/logging\_jobs.py                   |       63 |        9 |     86% |88-89, 105-111 |
| src/dinary/services/nbp.py                             |       37 |        0 |    100% |           |
| src/dinary/services/nbs.py                             |       47 |        0 |    100% |           |
| src/dinary/services/qr\_parser.py                      |       16 |        1 |     94% |        31 |
| src/dinary/services/rate\_helpers.py                   |       27 |        0 |    100% |           |
| src/dinary/services/receipt\_parser.py                 |      106 |       18 |     83% |66, 72, 81-87, 101, 108, 142-143, 147-148, 151, 172-174, 201-207 |
| src/dinary/services/receipts.py                        |       76 |       10 |     87% |94-95, 120-122, 170-172, 233, 249 |
| src/dinary/services/seed\_config.py                    |      139 |        7 |     95% |178-179, 469-471, 509-513 |
| src/dinary/services/sheet\_logging.py                  |      228 |       41 |     82% |104-108, 122-129, 137, 203-204, 214, 219-221, 264-266, 290-291, 295-305, 382, 421-425, 436, 440, 470-471, 491-501 |
| src/dinary/services/sheet\_mapping.py                  |      250 |       54 |     78% |239, 311-313, 340-341, 348-354, 404, 407-419, 433-434, 444-462, 539-546, 574-626, 654, 665-670 |
| src/dinary/services/sheets.py                          |      104 |        6 |     94% |59-60, 83, 119, 137, 220 |
| src/dinary/services/sheets\_client.py                  |       44 |       28 |     36% |26-35, 39, 54-63, 72-84 |
| src/dinary/services/sheets\_write.py                   |       65 |       35 |     46% |114-119, 124-127, 131-136, 158-192 |
| src/dinary/services/sql\_loader.py                     |       31 |        0 |    100% |           |
| src/dinary/services/sqlite\_types.py                   |       36 |        0 |    100% |           |
| src/dinary/services/storage.py                         |       87 |       11 |     87% |47-48, 159-162, 170-176, 222-224 |
| src/dinary/services/store\_resolver.py                 |       18 |        0 |    100% |           |
| src/dinary/tools/backup\_retention.py                  |       55 |       26 |     53% |26-34, 71-91, 95 |
| src/dinary/tools/backup\_snapshots.py                  |       79 |        7 |     91% |157, 213-218 |
| src/dinary/tools/report\_helpers.py                    |       30 |       10 |     67% |     44-53 |
| src/dinary/tools/sql.py                                |       71 |        3 |     96% |86, 176, 183 |
| **TOTAL**                                              | **5452** | **1076** | **80%** |           |


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