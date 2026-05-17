# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                   |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                            |        1 |        0 |    100% |           |
| src/dinary/api/admin\_catalog.py                       |      152 |       27 |     82% |198-199, 210-220, 255-256, 277-278, 293-294, 330-343, 354-355, 372-373, 391-392, 404-405, 443 |
| src/dinary/api/admin\_llm.py                           |       98 |       18 |     82% |103-104, 106-107, 112-113, 115-116, 161-178 |
| src/dinary/api/catalog.py                              |       90 |        3 |     97% |   360-362 |
| src/dinary/api/currencies.py                           |       34 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                 |       89 |        2 |     98% |   204-205 |
| src/dinary/api/expenses.py                             |      102 |        4 |     96% |168, 312-314 |
| src/dinary/api/qr.py                                   |       17 |        6 |     65% |     26-32 |
| src/dinary/api/receipt\_review.py                      |       27 |        0 |    100% |           |
| src/dinary/api/receipts.py                             |       36 |        5 |     86% |     61-66 |
| src/dinary/background/rate\_prefetch\_task.py          |       56 |        2 |     96% |   86, 117 |
| src/dinary/background/receipt\_classification\_task.py |      244 |       42 |     83% |119-120, 145-150, 154-156, 160-164, 168-172, 176-180, 223-225, 251, 283, 292-300, 371, 411-413, 460-462, 480-482, 512-514 |
| src/dinary/background/sheet\_logging\_task.py          |       53 |        7 |     87% |     44-57 |
| src/dinary/config.py                                   |      152 |       19 |     88% |37, 47, 110-112, 143, 149, 205-209, 211-215, 218-223, 226-230, 293-295 |
| src/dinary/imports/expense\_import.py                  |      420 |      132 |     69% |262, 267-268, 282, 284, 286, 313-331, 361, 365, 381-387, 391-399, 410, 412, 429, 431, 436, 457, 462-463, 487, 518-522, 535-539, 541-544, 696-697, 745-747, 750-752, 834-845, 914-957, 970, 980, 1003-1039, 1097, 1111, 1114, 1119, 1129, 1187-1193, 1198-1210, 1249-1253, 1255-1259, 1317-1326 |
| src/dinary/imports/income\_import.py                   |      139 |      139 |      0% |    12-318 |
| src/dinary/imports/report\_2d\_3d.py                   |      199 |       42 |     79% |170, 226-235, 271, 493-494, 532-563, 567-606, 610 |
| src/dinary/imports/seed.py                             |      204 |       49 |     76% |140-153, 215-221, 249, 305-306, 311-312, 315-316, 323, 373-377, 385-386, 388-389, 391-392, 394-398, 422-427, 472-476, 484-487, 509-511, 562-571, 629-630 |
| src/dinary/imports/seed\_derivation.py                 |      105 |       29 |     72% |252-258, 263, 266, 270-278, 296, 298, 305, 324, 331, 365, 367, 369, 371, 375, 395 |
| src/dinary/imports/verify\_equivalence.py              |       92 |       92 |      0% |    20-211 |
| src/dinary/imports/verify\_income.py                   |       37 |       37 |      0% |     9-107 |
| src/dinary/main.py                                     |       81 |        8 |     90% |41-43, 112, 118, 124, 134, 144 |
| src/dinary/reports/expenses.py                         |      122 |        5 |     96% |299, 301, 322-323, 420 |
| src/dinary/reports/income.py                           |       86 |        3 |     97% |209-210, 285 |
| src/dinary/reports/verify\_budget.py                   |       83 |        1 |     99% |       104 |
| src/dinary/reports/verify\_income.py                   |       88 |        1 |     99% |        38 |
| src/dinary/services/catalog.py                         |       44 |        0 |    100% |           |
| src/dinary/services/catalog\_writer.py                 |      266 |       46 |     83% |175-176, 338-342, 496-498, 518-560, 569, 660, 671, 705, 748, 753, 770, 866, 898, 902-904, 911-913 |
| src/dinary/services/catalog\_writer\_events.py         |      167 |       33 |     80% |117-119, 134, 144-149, 183, 185, 187, 189, 199, 215, 255-257, 293, 302, 316-318, 343, 382, 386-388, 395-397, 419, 432-434 |
| src/dinary/services/classification\_rules.py           |       21 |        0 |    100% |           |
| src/dinary/services/currencies.py                      |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/services/db\_migrations.py                  |       54 |        2 |     96% |    86, 89 |
| src/dinary/services/exchange\_rates.py                 |       38 |        1 |     97% |        64 |
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
| src/dinary/services/seed\_config.py                    |      139 |        7 |     95% |221-222, 512-514, 552-556 |
| src/dinary/services/sheet\_logging.py                  |      226 |       41 |     82% |107-111, 125-132, 140, 206-207, 217, 222-224, 267-269, 293-294, 298-308, 385, 424-428, 439, 443, 473-474, 494-504 |
| src/dinary/services/sheet\_mapping.py                  |      250 |       54 |     78% |259, 331-333, 360-361, 368-374, 424, 427-439, 453-454, 464-482, 559-566, 594-646, 674, 685-690 |
| src/dinary/services/sheets.py                          |      214 |       71 |     67% |87-96, 100, 137-146, 164-176, 210-211, 238, 292, 331, 447, 581-586, 596-599, 610-615, 651-691 |
| src/dinary/services/sql\_loader.py                     |       31 |        0 |    100% |           |
| src/dinary/services/sqlite\_types.py                   |       36 |        0 |    100% |           |
| src/dinary/services/storage.py                         |       87 |       11 |     87% |47-48, 159-162, 170-176, 222-224 |
| src/dinary/services/store\_resolver.py                 |       18 |        0 |    100% |           |
| src/dinary/tools/backup\_retention.py                  |       55 |       26 |     53% |26-34, 71-91, 95 |
| src/dinary/tools/backup\_snapshots.py                  |       79 |        7 |     91% |157, 213-218 |
| src/dinary/tools/report\_helpers.py                    |       30 |       10 |     67% |     44-53 |
| src/dinary/tools/sql.py                                |       71 |        3 |     96% |86, 176, 183 |
| **TOTAL**                                              | **5437** | **1077** | **80%** |           |


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