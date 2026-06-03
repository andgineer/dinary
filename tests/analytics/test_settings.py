from dinary_analytics.settings import (
    get_config,
    get_config_json,
    set_config,
    set_config_json,
)


def test_set_and_get_string(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("api_key", "secret123", db_path=db)
    assert get_config("api_key", db_path=db) == "secret123"


def test_get_missing_key_returns_none(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("present", "yes", db_path=db)
    assert get_config("absent", db_path=db) is None


def test_overwrite_existing_key(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("key", "first", db_path=db)
    set_config("key", "second", db_path=db)
    assert get_config("key", db_path=db) == "second"


def test_multiple_independent_keys(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("a", "alpha", db_path=db)
    set_config("b", "beta", db_path=db)
    assert get_config("a", db_path=db) == "alpha"
    assert get_config("b", db_path=db) == "beta"


def test_set_config_json_round_trips(tmp_path):
    db = tmp_path / "analytics.db"
    payload = {"widgets": ["chart", "chat"], "count": 2}
    set_config_json("dashboard", payload, db_path=db)
    result = get_config_json("dashboard", db_path=db)
    assert result == payload


def test_get_config_json_missing_returns_none(tmp_path):
    db = tmp_path / "analytics.db"
    assert get_config_json("nonexistent", db_path=db) is None


def test_unicode_value(tmp_path):
    db = tmp_path / "analytics.db"
    set_config("tag", "командировка", db_path=db)
    assert get_config("tag", db_path=db) == "командировка"
