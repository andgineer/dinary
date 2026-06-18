import allure

from dinary_analytics.settings import (
    delete_view,
    get_config,
    get_config_json,
    get_view,
    list_view_ids,
    save_view,
    set_config,
    set_config_json,
)


@allure.epic("Analytics")
@allure.feature("Settings")
def test_set_and_get_string(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("api_key", "secret123", db_path=db)
    assert get_config("api_key", db_path=db) == "secret123"


@allure.epic("Analytics")
@allure.feature("Settings")
def test_get_missing_key_returns_none(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("present", "yes", db_path=db)
    assert get_config("absent", db_path=db) is None


@allure.epic("Analytics")
@allure.feature("Settings")
def test_overwrite_existing_key(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("key", "first", db_path=db)
    set_config("key", "second", db_path=db)
    assert get_config("key", db_path=db) == "second"


@allure.epic("Analytics")
@allure.feature("Settings")
def test_multiple_independent_keys(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("a", "alpha", db_path=db)
    set_config("b", "beta", db_path=db)
    assert get_config("a", db_path=db) == "alpha"
    assert get_config("b", db_path=db) == "beta"


@allure.epic("Analytics")
@allure.feature("Settings")
def test_set_config_json_round_trips(tmp_path):
    db = tmp_path / "analytics.db"
    payload = {"widgets": ["chart", "chat"], "count": 2}
    set_config_json("dashboard", payload, db_path=db)
    result = get_config_json("dashboard", db_path=db)
    assert result == payload


@allure.epic("Analytics")
@allure.feature("Settings")
def test_get_config_json_missing_returns_none(tmp_path):
    db = tmp_path / "analytics.db"
    assert get_config_json("nonexistent", db_path=db) is None


@allure.epic("Analytics")
@allure.feature("Settings")
def test_unicode_value(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("tag", "business-trip", db_path=db)
    assert get_config("tag", db_path=db) == "business-trip"


@allure.epic("Analytics")
@allure.feature("Settings")
def test_save_and_get_view_round_trip(tmp_path):
    db = tmp_path / "analytics.db"
    config = {"name": "My View", "baskets": [], "default_basket": "Other"}
    view_id = save_view(config, db_path=db)
    result = get_view(view_id, db_path=db)
    assert result is not None
    assert result["name"] == "My View"
    assert result["id"] == view_id


@allure.epic("Analytics")
@allure.feature("Settings")
def test_save_view_assigns_uuid_when_no_id(tmp_path):
    db = tmp_path / "analytics.db"
    config = {"name": "Auto ID", "baskets": []}
    view_id = save_view(config, db_path=db)
    assert view_id
    assert len(view_id) == 36  # UUID format


@allure.epic("Analytics")
@allure.feature("Settings")
def test_save_view_preserves_explicit_id(tmp_path):
    db = tmp_path / "analytics.db"
    config = {"id": "custom-id", "name": "Named", "baskets": []}
    view_id = save_view(config, db_path=db)
    assert view_id == "custom-id"
    result = get_view("custom-id", db_path=db)
    assert result is not None
    assert result["id"] == "custom-id"


@allure.epic("Analytics")
@allure.feature("Settings")
def test_list_view_ids_returns_saved_ids(tmp_path):
    db = tmp_path / "analytics.db"
    id1 = save_view({"name": "A", "baskets": []}, db_path=db)
    id2 = save_view({"name": "B", "baskets": []}, db_path=db)
    ids = list_view_ids(db_path=db)
    assert id1 in ids
    assert id2 in ids


@allure.epic("Analytics")
@allure.feature("Settings")
def test_list_view_ids_empty_when_none_saved(tmp_path):
    db = tmp_path / "analytics.db"
    assert list_view_ids(db_path=db) == []


@allure.epic("Analytics")
@allure.feature("Settings")
def test_list_view_ids_excludes_non_view_keys(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("dashboard.tag_id", "5", db_path=db)
    save_view({"name": "V", "baskets": []}, db_path=db)
    ids = list_view_ids(db_path=db)
    assert all("dashboard" not in vid for vid in ids)


@allure.epic("Analytics")
@allure.feature("Settings")
def test_delete_view_removes_it(tmp_path):
    db = tmp_path / "analytics.db"
    view_id = save_view({"name": "ToDelete", "baskets": []}, db_path=db)
    delete_view(view_id, db_path=db)
    assert get_view(view_id, db_path=db) is None
    assert view_id not in list_view_ids(db_path=db)


@allure.epic("Analytics")
@allure.feature("Settings")
def test_delete_view_noop_if_missing(tmp_path):
    db = tmp_path / "analytics.db"
    delete_view("nonexistent-id", db_path=db)  # must not raise


@allure.epic("Analytics")
@allure.feature("Settings")
def test_get_view_missing_returns_none(tmp_path):
    db = tmp_path / "analytics.db"
    assert get_view("no-such-id", db_path=db) is None
