"""Cross-cutting API tests that don't fit any one POST-expense subset.

The full ``POST /api/expenses`` surface lives in dedicated sibling files
which share fixtures via ``_api_helpers``:

- ``test_api_post_expense.py`` — happy-path create/replay/FX/sheet-logging
- ``test_api_validation.py`` — 422 + reseed/inactive carve-outs
- ``test_api_conflict.py`` — 409 modified-amount/date/category replays
- ``test_api_concurrency.py`` — race recovery, serialization, 500 propagation

This file keeps tests that don't touch the expenses route at all so the
``_api_helpers`` autouse fixture (per-test DB + minimal catalog seed)
stays scoped to the tests that actually rely on it.
"""

import allure


@allure.epic("API")
@allure.feature("Health")
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
