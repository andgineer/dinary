"""Cross-cutting admin-catalog plumbing: every mutation must bump
``catalog_version`` and surface it in both the JSON body and the weak ETag.
Sibling files cover add, patch, and delete."""

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
