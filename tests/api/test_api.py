"""Cross-cutting API tests that don't touch the expenses route (that surface is
split across ``test_api_post_expense.py``, ``test_api_validation.py``,
``test_api_conflict.py``, ``test_api_concurrency.py``)."""

import allure


@allure.epic("Infrastructure")
@allure.feature("App startup")
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
