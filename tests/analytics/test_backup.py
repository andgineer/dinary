import shutil

import allure
import pytest

import dinary_analytics.backup as backup_module
import dinary_analytics.paths as paths_module
import dinary_analytics.settings as settings_module
from dinary_analytics.backup import backup_to_file, restore_from_file
from dinary_analytics.settings import get_config, set_config

pytestmark = pytest.mark.skipif(
    shutil.which("zstd") is None,
    reason="zstd not installed",
)


@pytest.fixture
def analytics_db(tmp_path, monkeypatch):
    db = tmp_path / "analytics.db"
    monkeypatch.setattr(paths_module, "ANALYTICS_DB_PATH", db)
    monkeypatch.setattr(settings_module, "ANALYTICS_DB_PATH", db)
    monkeypatch.setattr(backup_module, "ANALYTICS_DB_PATH", db)
    set_config("test_key", "hello", db_path=db)
    set_config("another", "world", db_path=db)
    return db


@allure.epic("Analytics")
@allure.feature("Backup")
def test_backup_creates_file(tmp_path, analytics_db):
    archive = tmp_path / "backup.db.zst"
    backup_to_file(archive)
    assert archive.exists()
    assert archive.stat().st_size > 0


@allure.epic("Analytics")
@allure.feature("Backup")
def test_backup_then_restore_recovers_data(tmp_path, analytics_db, monkeypatch):
    archive = tmp_path / "backup.db.zst"
    backup_to_file(archive)

    restored_db = tmp_path / "restored.db"
    monkeypatch.setattr(backup_module, "ANALYTICS_DB_PATH", restored_db)
    monkeypatch.setattr(settings_module, "ANALYTICS_DB_PATH", restored_db)

    restore_from_file(archive)

    assert get_config("test_key", db_path=restored_db) == "hello"
    assert get_config("another", db_path=restored_db) == "world"


@allure.epic("Analytics")
@allure.feature("Backup")
def test_restore_preserves_old_data_mdb(tmp_path, analytics_db):
    archive = tmp_path / "backup.db.zst"
    backup_to_file(archive)

    restore_from_file(archive)

    before_files = list(analytics_db.glob("data.mdb.before-restore-*"))
    assert len(before_files) == 1


@allure.epic("Analytics")
@allure.feature("Backup")
def test_backup_missing_db_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "ANALYTICS_DB_PATH", tmp_path / "nonexistent.db")
    with pytest.raises(SystemExit):
        backup_to_file(tmp_path / "out.db.zst")


@allure.epic("Analytics")
@allure.feature("Backup")
def test_restore_missing_file_exits(tmp_path, analytics_db):
    with pytest.raises(SystemExit):
        restore_from_file(tmp_path / "ghost.db.zst")
