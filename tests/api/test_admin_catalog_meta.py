"""Cross-cutting admin-catalog plumbing tests.

Pins the ``catalog_version`` end-to-end surface that doesn't fit the
per-verb (add/patch/delete) files: every mutation must bump the
counter and the value must surface both in ``GET /api/catalog`` JSON
and in its weak ``ETag`` header so the PWA can ``If-None-Match`` the
snapshot.

Sibling files cover add (:file:`test_admin_catalog_add.py`),
patch (:file:`test_admin_catalog_patch.py`), and delete
(:file:`test_admin_catalog_delete.py`).
"""

import allure

from _admin_catalog_helpers import db  # noqa: F401  (autouse)


@allure.epic("Catalog")
@allure.feature("Admin API")
class TestCatalogVersionE2E:
    def test_add_bumps_catalog_version_visible_in_get_catalog(self, client):
        v0 = client.get("/api/catalog").json()["catalog_version"]
        client.post("/api/catalog/tags", json={"name": "brand-new-tag"})
        snap_resp = client.get("/api/catalog")
        snap = snap_resp.json()
        assert snap["catalog_version"] == v0 + 1
        assert any(t["name"] == "brand-new-tag" for t in snap["tags"])
        assert snap_resp.headers["ETag"] == f'W/"catalog-v{v0 + 1}"'
