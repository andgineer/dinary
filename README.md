# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/andgineer/dinary/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                   |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/dinary/\_\_about\_\_.py                            |        1 |        0 |    100% |           |
| src/dinary/api/admin\_catalog.py                       |      186 |       33 |     82% |197-198, 210-224, 263-264, 288-289, 307-308, 348-365, 377-378, 398-399, 420-421, 436-437, 477 |
| src/dinary/api/admin\_llm.py                           |      122 |       30 |     75% |87-89, 111-112, 114-115, 120-121, 123-124, 135-137, 172-174, 185-206 |
| src/dinary/api/catalog.py                              |       77 |        3 |     96% |   319-321 |
| src/dinary/api/currencies.py                           |       41 |        0 |    100% |           |
| src/dinary/api/expense\_corrections.py                 |       76 |        5 |     93% |111-113, 168-169 |
| src/dinary/api/expenses.py                             |       97 |        4 |     96% |156, 300-302 |
| src/dinary/api/qr.py                                   |       17 |        6 |     65% |     26-32 |
| src/dinary/api/receipt\_review.py                      |       28 |        0 |    100% |           |
| src/dinary/api/receipts.py                             |       40 |        9 |     78% |     57-65 |
| src/dinary/background/rate\_prefetch\_task.py          |       56 |        2 |     96% |   86, 117 |
| src/dinary/background/receipt\_classification\_task.py |      227 |       47 |     79% |89-95, 119-124, 128-130, 134-138, 142-146, 150-154, 197-199, 225, 257, 266-274, 345, 385-387, 424-426, 444-446, 476-478 |
| src/dinary/background/sheet\_logging\_task.py          |       53 |        7 |     87% |     44-57 |
| src/dinary/config.py                                   |      152 |       20 |     87% |37, 47, 110-112, 143, 149, 205-209, 211-215, 218-223, 226-230, 289, 293-295 |
| src/dinary/imports/expense\_import.py                  |      418 |      132 |     68% |260, 265-266, 280, 282, 284, 311-329, 359, 363, 379-385, 389-397, 408, 410, 427, 429, 434, 455, 460-461, 485, 516-520, 533-537, 539-542, 694-695, 743-745, 748-750, 832-843, 912-955, 968, 978, 1001-1037, 1095, 1109, 1112, 1117, 1127, 1185-1191, 1196-1208, 1247-1251, 1253-1257, 1315-1324 |
| src/dinary/imports/income\_import.py                   |      139 |      139 |      0% |    12-318 |
| src/dinary/imports/report\_2d\_3d.py                   |      199 |       42 |     79% |170, 226-235, 271, 493-494, 532-563, 567-606, 610 |
| src/dinary/imports/seed.py                             |      306 |       78 |     75% |312-318, 323, 326, 330-338, 356, 358, 365, 384, 391, 425, 427, 429, 431, 435, 455, 534-547, 609-615, 643, 699-700, 705-706, 709-710, 717, 767-771, 779-780, 782-783, 785-786, 788-792, 816-821, 866-870, 878-881, 903-905, 956-965, 1023-1024 |
| src/dinary/imports/verify\_equivalence.py              |       92 |       92 |      0% |    20-211 |
| src/dinary/imports/verify\_income.py                   |       37 |       37 |      0% |     9-107 |
| src/dinary/main.py                                     |       81 |        8 |     90% |41-43, 112, 118, 124, 134, 144 |
| src/dinary/reports/expenses.py                         |      122 |        5 |     96% |299, 301, 322-323, 420 |
| src/dinary/reports/income.py                           |       86 |        3 |     97% |209-210, 285 |
| src/dinary/reports/verify\_budget.py                   |       83 |        1 |     99% |       104 |
| src/dinary/reports/verify\_income.py                   |       88 |        1 |     99% |        38 |
| src/dinary/services/catalog\_writer.py                 |      427 |       79 |     81% |177-178, 340-344, 498-500, 520-562, 571, 662, 673, 707, 825-827, 842, 852-857, 891, 893, 895, 897, 907, 923, 963-965, 1001, 1010, 1024-1026, 1051, 1083, 1088, 1105, 1201, 1233, 1237-1239, 1246-1248, 1269, 1273-1275, 1282-1284, 1306, 1319-1321 |
| src/dinary/services/classification\_rules.py           |       21 |        0 |    100% |           |
| src/dinary/services/currency\_repo.py                  |       34 |        5 |     85% |21-22, 59-64 |
| src/dinary/services/db\_migrations.py                  |       54 |        2 |     96% |    86, 89 |
| src/dinary/services/exchange\_rates.py                 |       38 |        1 |     97% |        64 |
| src/dinary/services/item\_normalizer.py                |       13 |        0 |    100% |           |
| src/dinary/services/ledger\_repo.py                    |      297 |       31 |     90% |63-64, 294-303, 311-317, 661, 685, 736, 739, 758, 795-802, 821-827, 931, 1043-1044, 1060-1066, 1131 |
| src/dinary/services/llm\_bootstrap.py                  |       37 |       13 |     65% |34-37, 48-56 |
| src/dinary/services/llm\_client.py                     |      192 |       22 |     89% |165-167, 188-191, 242-243, 362-363, 383-386, 404, 408, 430, 432-435 |
| src/dinary/services/nbp.py                             |       37 |        0 |    100% |           |
| src/dinary/services/nbs.py                             |       47 |        0 |    100% |           |
| src/dinary/services/qr\_parser.py                      |       16 |        1 |     94% |        31 |
| src/dinary/services/rate\_helpers.py                   |       27 |        0 |    100% |           |
| src/dinary/services/receipt\_parser.py                 |      106 |       18 |     83% |66, 72, 81-87, 101, 108, 142-143, 147-148, 151, 172-174, 201-207 |
| src/dinary/services/receipt\_repo.py                   |       74 |       10 |     86% |94-95, 120-122, 170-172, 233, 249 |
| src/dinary/services/seed\_config.py                    |      138 |        7 |     95% |220-221, 511-513, 551-555 |
| src/dinary/services/sheet\_logging.py                  |      223 |       41 |     82% |97-101, 115-122, 130, 196-197, 207, 212-214, 257-259, 283-284, 288-298, 375, 414-418, 429, 433, 463-464, 484-494 |
| src/dinary/services/sheet\_mapping.py                  |      250 |       54 |     78% |259, 331-333, 360-361, 368-374, 424, 427-439, 453-454, 464-482, 559-566, 594-646, 674, 685-690 |
| src/dinary/services/sheets.py                          |      233 |       63 |     73% |87-96, 100, 137-146, 164-176, 210-211, 238, 292, 331, 389, 472, 622, 666-671, 707-747 |
| src/dinary/services/sql\_loader.py                     |       31 |        0 |    100% |           |
| src/dinary/services/sqlite\_types.py                   |       36 |        0 |    100% |           |
| src/dinary/services/store\_resolver.py                 |       18 |        0 |    100% |           |
| src/dinary/tools/backup\_retention.py                  |       55 |       26 |     53% |26-34, 71-91, 95 |
| src/dinary/tools/backup\_snapshots.py                  |       79 |        7 |     91% |157, 213-218 |
| src/dinary/tools/report\_helpers.py                    |       30 |       10 |     67% |     44-53 |
| src/dinary/tools/sql.py                                |       71 |        3 |     96% |86, 176, 183 |
| **TOTAL**                                              | **5408** | **1097** | **80%** |           |


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