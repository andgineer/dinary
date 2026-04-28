"""Cross-cutting admin-catalog plumbing tests.

Pin two surfaces that don't fit the per-verb (add/patch/delete)
files:

* ``catalog_version`` end-to-end — every mutation must bump the
  counter and the value must surface both in
  ``GET /api/catalog`` JSON and in its weak ``ETag`` header so the
  PWA can ``If-None-Match`` the snapshot.
* ``POST /api/admin/reload-map`` — the operator-triggered swap of
  the ``sheet_mapping`` table. Returns 503 when
  ``DINARY_SHEET_LOGGING_SPREADSHEET`` is unset and translates a
  ``MapTabError`` into a 400 with the parser's diagnostic preserved.

Sibling files cover add (:file:`test_admin_catalog_add.py`),
patch (:file:`test_admin_catalog_patch.py`), and delete
(:file:`test_admin_catalog_delete.py`).
"""

from unittest.mock import patch

import allure

from dinary.config import settings
from dinary.services import sheet_mapping

from _admin_catalog_helpers import db  # noqa: F401  (autouse)


@allure.epic("API")
@allure.feature("Admin catalog — catalog_version end-to-end")
class TestCatalogVersionE2E:
    def test_add_bumps_catalog_version_visible_in_get_catalog(self, client):
        v0 = client.get("/api/catalog").json()["catalog_version"]
        client.post("/api/admin/catalog/tags", json={"name": "brand-new-tag"})
        snap_resp = client.get("/api/catalog")
        snap = snap_resp.json()
        assert snap["catalog_version"] == v0 + 1
        assert any(t["name"] == "brand-new-tag" for t in snap["tags"])
        assert snap_resp.headers["ETag"] == f'W/"catalog-v{v0 + 1}"'


@allure.epic("API")
@allure.feature("Admin catalog — reload-map")
class TestReloadMap:
    def test_reload_503_when_spreadsheet_unset(self, client, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        resp = client.post("/api/admin/reload-map")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_reload_validation_error_surfaces_as_400(self, client, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "FAKE_SSID")

        def fail(*_a, **_kw):
            raise sheet_mapping.MapTabError("bad row 3: unknown category 'xxx'")

        with patch.object(sheet_mapping, "reload_now", side_effect=fail):
            resp = client.post("/api/admin/reload-map")
        assert resp.status_code == 400
        assert "unknown category" in resp.json()["detail"]
