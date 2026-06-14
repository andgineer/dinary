"""Tests for /api/category-templates and /api/categories."""

import json
import shutil

import allure
import pytest

from dinary.db import category_seed, storage
from dinary.db.category_apply import apply_template


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", dst)
    con = storage.get_connection()
    try:
        category_seed.seed_category_templates(con)
    finally:
        con.close()


def _apply(template_code: str, lang: str = "ru") -> None:
    con = storage.get_connection()
    try:
        apply_template(con, template_code, lang)
    finally:
        con.close()


def _group_id(code: str) -> int:
    con = storage.get_connection()
    try:
        return int(
            con.execute(
                "SELECT id FROM category_groups WHERE code = ?",
                [code],
            ).fetchone()[0],
        )
    finally:
        con.close()


@allure.epic("Category templates")
@allure.feature("API")
class TestListTemplates:
    def test_returns_factory_sets_ordered(self, client):
        resp = client.get("/api/category-templates")
        assert resp.status_code == 200
        data = resp.json()
        assert [t["code"] for t in data] == ["simple", "active", "family", "freelancer"]
        for item in data:
            assert item["origin"] == "factory"
            assert "ru" in item["names"]
            assert "ru" in item["taglines"]

    def test_includes_custom_template(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_templates (code, origin, sort_order, definition_json) "
                "VALUES ('mine', 'custom', 99, ?)",
                [
                    json.dumps(
                        {
                            "names": {"ru": "Мой набор", "en": "My setup"},
                            "taglines": {"ru": "тег", "en": "tag"},
                            "groups": {},
                            "renames": {},
                            "visible": {},
                            "hidden": {},
                        },
                    ),
                ],
            )
        finally:
            con.close()

        resp = client.get("/api/category-templates")
        data = resp.json()
        custom = next(t for t in data if t["code"] == "mine")
        assert custom["origin"] == "custom"
        assert custom["names"]["ru"] == "Мой набор"
        assert custom["taglines"]["en"] == "tag"
        assert custom["groups"] == []

    def test_groups_preview_is_ordered_with_visible_categories_only(self, client):
        resp = client.get("/api/category-templates")
        data = resp.json()
        simple = next(t for t in data if t["code"] == "simple")

        group_codes = [g["code"] for g in simple["groups"]]
        assert group_codes == ["food", "housing", "life", "growth", "leisure"]

        food = simple["groups"][0]
        assert food["names"] == {"en": "Food", "ru": "Еда", "sr": "Hrana"}
        ru_names = [c["names"]["ru"] for c in food["categories"]]
        assert ru_names == ["продукты", "кафе", "доставка еды", "алкоголь"]
        en_names = [c["names"]["en"] for c in food["categories"]]
        assert en_names == ["Groceries", "Cafe", "Food delivery", "Alcohol"]

    def test_hidden_codes_are_absent_from_preview(self, client):
        resp = client.get("/api/category-templates")
        data = resp.json()
        simple = next(t for t in data if t["code"] == "simple")

        all_codes_ru = {c["names"]["ru"] for g in simple["groups"] for c in g["categories"]}
        # 'fruit' (ru: фрукты) is in simple's hidden bucket for the 'food' group.
        assert "фрукты" not in all_codes_ru

    def test_renamed_code_surfaces_rename_not_vocabulary_name(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_templates (code, origin, sort_order, definition_json) "
                "VALUES ('mine', 'custom', 99, ?)",
                [
                    json.dumps(
                        {
                            "names": {"ru": "Мой набор", "en": "My setup"},
                            "taglines": {"ru": "тег", "en": "tag"},
                            "groups": {"food": {"ru": "Еда", "en": "Food"}},
                            "renames": {"groceries": {"ru": "Моя еда", "en": "My Food"}},
                            "visible": {"food": ["groceries"]},
                            "hidden": {},
                        },
                    ),
                ],
            )
        finally:
            con.close()

        resp = client.get("/api/category-templates")
        data = resp.json()
        mine = next(t for t in data if t["code"] == "mine")
        food = mine["groups"][0]
        assert food["code"] == "food"
        assert food["categories"][0]["names"] == {"ru": "Моя еда", "en": "My Food"}


@allure.epic("Category templates")
@allure.feature("API")
class TestActiveTemplate:
    def test_null_on_fresh_db(self, client):
        resp = client.get("/api/category-templates/active")
        assert resp.status_code == 200
        assert resp.json() == {"active_template": None}

    def test_becomes_code_after_apply(self, client):
        client.post("/api/category-templates/apply", json={"code": "simple", "lang": "ru"})

        resp = client.get("/api/category-templates/active")
        assert resp.json() == {"active_template": "simple"}


@allure.epic("Category templates")
@allure.feature("API")
class TestApplyTemplate:
    def test_apply_switches_visibility_and_bumps_catalog_version(self, client):
        before = client.get("/api/categories").json()
        assert before["categories"] == []
        version_before = before["catalog_version"]
        etag_before = client.get("/api/categories").headers["ETag"]

        resp = client.post("/api/category-templates/apply", json={"code": "simple", "lang": "ru"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_template"] == "simple"
        assert body["catalog_version"] == version_before + 1

        after = client.get("/api/categories")
        assert after.json()["categories"]
        assert after.headers["ETag"] != etag_before

    def test_unknown_code_returns_404(self, client):
        resp = client.post(
            "/api/category-templates/apply",
            json={"code": "does-not-exist", "lang": "ru"},
        )
        assert resp.status_code == 404


@allure.epic("Category templates")
@allure.feature("API")
class TestGetCategories:
    def test_returns_only_visible_grouped(self, client):
        _apply("simple")

        resp = client.get("/api/categories")
        assert resp.status_code == 200
        data = resp.json()
        codes = {c["code"] for c in data["categories"]}
        # 'groceries' is in 'simple's visible bucket.
        assert "groceries" in codes
        # 'fruit' is in 'simple's hidden bucket (is_active=0, unused).
        assert "fruit" not in codes

    def test_304_on_matching_etag(self, client):
        _apply("simple")

        first = client.get("/api/categories")
        etag = first.headers["ETag"]
        second = client.get("/api/categories", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""


@allure.epic("Category templates")
@allure.feature("API")
class TestSearchAndActivate:
    def test_search_finds_hidden_category(self, client):
        _apply("simple")
        con = storage.get_connection()
        try:
            name = con.execute("SELECT name FROM categories WHERE code = 'fruit'").fetchone()[0]
        finally:
            con.close()

        resp = client.get("/api/categories/search", params={"q": name})
        assert resp.status_code == 200
        results = resp.json()
        fruit = next(r for r in results if r["code"] == "fruit")
        assert fruit["is_active"] is False
        assert fruit["is_hidden"] is False

    def test_activate_makes_category_appear_in_get_categories(self, client):
        _apply("simple")

        resp = client.post("/api/categories/fruit/activate")
        assert resp.status_code == 200
        assert "catalog_version" in resp.json()

        codes = {c["code"] for c in client.get("/api/categories").json()["categories"]}
        assert "fruit" in codes

    def test_activate_unknown_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories/does_not_exist/activate")
        assert resp.status_code == 404


@allure.epic("Category templates")
@allure.feature("API")
class TestHideUnhide:
    def test_hide_removes_used_category_unhide_restores(self, client):
        _apply("simple")
        con = storage.get_connection()
        try:
            cat_id = con.execute(
                "SELECT id FROM categories WHERE code = 'groceries'",
            ).fetchone()[0]
            con.execute(
                "INSERT INTO expenses"
                " (datetime, amount, amount_original, currency_original, category_id)"
                " VALUES ('2026-05-01T10:00:00', 100.0, 100.0, 'EUR', ?)",
                [cat_id],
            )
        finally:
            con.close()

        resp = client.post("/api/categories/groceries/hide")
        assert resp.status_code == 200

        codes = {c["code"] for c in client.get("/api/categories").json()["categories"]}
        assert "groceries" not in codes, "hide is sticky even for used categories"

        resp = client.post("/api/categories/groceries/unhide")
        assert resp.status_code == 200

        codes = {c["code"] for c in client.get("/api/categories").json()["categories"]}
        assert "groceries" in codes

    def test_hide_unknown_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories/does_not_exist/hide")
        assert resp.status_code == 404

    def test_unhide_unknown_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories/does_not_exist/unhide")
        assert resp.status_code == 404


@allure.epic("Category templates")
@allure.feature("API")
class TestMoveAndRename:
    def test_move_changes_group(self, client):
        _apply("simple")

        resp = client.post("/api/categories/groceries/move", json={"group_code": "housing"})
        assert resp.status_code == 200

        cats = client.get("/api/categories").json()["categories"]
        groceries = next(c for c in cats if c["code"] == "groceries")
        assert groceries["group_id"] == _group_id("housing")

    def test_move_unknown_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories/does_not_exist/move", json={"group_code": "housing"})
        assert resp.status_code == 404

    def test_move_unknown_group_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories/groceries/move", json={"group_code": "does_not_exist"})
        assert resp.status_code == 404

    def test_rename_changes_label_keeps_code(self, client):
        _apply("simple")

        resp = client.post("/api/categories/groceries/rename", json={"name": "Продукты питания"})
        assert resp.status_code == 200

        cats = client.get("/api/categories").json()["categories"]
        groceries = next(c for c in cats if c["code"] == "groceries")
        assert groceries["name"] == "Продукты питания"

    def test_rename_unknown_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories/does_not_exist/rename", json={"name": "x"})
        assert resp.status_code == 404


@allure.epic("Category templates")
@allure.feature("API")
class TestCreateCategory:
    def test_create_returns_code_and_appears_in_group(self, client):
        _apply("simple")

        resp = client.post("/api/categories", json={"name": "My Thing", "group_code": "food"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == "u_my_thing"
        assert "catalog_version" in body

        cats = client.get("/api/categories").json()["categories"]
        created = next(c for c in cats if c["code"] == "u_my_thing")
        assert created["group_id"] == _group_id("food")

    def test_unknown_group_code_returns_404(self, client):
        _apply("simple")

        resp = client.post("/api/categories", json={"name": "My Thing", "group_code": "nope"})
        assert resp.status_code == 404
