"""Tests for the unified SQLite migration in ``db_migrations.migrate_db``.

After the storage-engine port there is only one migration target:
``data/dinary.db``. These tests verify that applying the bundled
migrations to a fresh file produces the expected schema and seed rows.
"""

import asyncio
import shutil
import sqlite3

import allure
import llmbroker
import pytest
from llmbroker import sqlite as llmbroker_sqlite
from llmbroker.backends import spec as llmbroker_spec

from dinary.config import settings
from dinary.db import db_migrations, storage
from dinary.db.catalog import get_catalog_version


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point ``ledger_repo`` at an empty tmp file and apply all migrations."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dinary.db")
    db_migrations.migrate_db(storage.DB_PATH)
    return storage.DB_PATH


def _connect(path) -> sqlite3.Connection:
    # Foreign-key enforcement matches runtime; the yoyo bookkeeping
    # table is tolerated in listings below.
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'",
    ).fetchall()
    return {r[0] for r in rows}


def _column_names(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
    return {r[1] for r in rows}


def _apply_through(db, keep_prefix: str) -> None:
    """Apply migrations up to and including the one named by ``keep_prefix``."""
    migrations = db_migrations._read_migrations()
    target = next(m for m in migrations if m.id.startswith(keep_prefix))
    wanted = migrations.filter(lambda m: m.id <= target.id)
    with db_migrations._backend_for(db) as backend, backend.lock():
        backend.apply_migrations(backend.to_apply(wanted))


def _rollback_to(db, keep_prefix: str) -> None:
    """Roll back every migration after the one named by ``keep_prefix``, newest
    first — what ``inv restore-yoyo --to=<prefix>`` runs on the server."""
    migrations = db_migrations._read_migrations()
    target = next(m for m in migrations if m.id.startswith(keep_prefix))
    to_roll_back = migrations.filter(lambda m: m.id > target.id)
    with db_migrations._backend_for(db) as backend, backend.lock():
        backend.rollback_migrations(backend.to_rollback(to_roll_back))


async def _provision_broker_schema(db) -> None:
    """Touch the journal so llmbroker creates its own schema — no pool, no network."""
    broker = llmbroker.AsyncBroker(f"sqlite://{db}", sync=None, sync_interval=None)
    try:
        await broker.calls(limit=1)
    finally:
        await broker.aclose()


@allure.epic("Infrastructure")
@allure.feature("Migrations")
class TestInitialSchema:
    def test_creates_expected_catalog_tables(self, fresh_db):
        con = _connect(fresh_db)
        try:
            tables = _table_names(con)
        finally:
            con.close()

        expected = {
            "category_groups",
            "categories",
            "events",
            "tags",
            "exchange_rates",
            "import_mapping",
            "import_mapping_tags",
            "sheet_mapping",
            "sheet_mapping_tags",
            "app_metadata",
        }
        assert expected.issubset(tables)
        assert "import_sources" not in tables, (
            "import_sources migrated out of the ledger — the registry now "
            "lives in .deploy/import_sources.json (see dinary.config)."
        )

    def test_creates_expected_ledger_tables(self, fresh_db):
        con = _connect(fresh_db)
        try:
            tables = _table_names(con)
        finally:
            con.close()

        assert {"expenses", "expense_tags", "sheet_logging_jobs", "income"}.issubset(tables)

    def test_no_old_config_or_budget_tables(self, fresh_db):
        """The old split-DB refactor dropped these legacy artefacts."""
        con = _connect(fresh_db)
        try:
            tables = _table_names(con)
        finally:
            con.close()

        assert "expense_id_registry" not in tables

    def test_catalog_tables_have_is_active_column(self, fresh_db):
        con = _connect(fresh_db)
        try:
            for table in ("category_groups", "categories", "events", "tags"):
                assert "is_active" in _column_names(con, table), table
        finally:
            con.close()

    def test_app_metadata_is_key_value(self, fresh_db):
        con = _connect(fresh_db)
        try:
            cols = _column_names(con, "app_metadata")
            row = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'catalog_version'",
            ).fetchone()
        finally:
            con.close()
        assert cols == {"key", "value"}
        assert row is not None
        assert row[0] == "1"

    def test_expenses_has_client_expense_id_unique(self, fresh_db):
        con = _connect(fresh_db)
        try:
            assert "client_expense_id" in _column_names(con, "expenses")
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'c', 1)",
            )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ["cid-1", "2026-04-15 12:00:00", 100, 100, "RSD", 1],
            )
            # Re-inserting the same client_expense_id must violate UNIQUE.
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO expenses (client_expense_id, datetime, amount,"
                    " amount_original, currency_original, category_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ["cid-1", "2026-04-15 12:00:00", 100, 100, "RSD", 1],
                )
            # NULL client_expense_id is allowed many times over (bootstrap rows).
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (NULL, ?, ?, ?, ?, ?)",
                ["2026-04-15 12:00:00", 100, 100, "RSD", 1],
            )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (NULL, ?, ?, ?, ?, ?)",
                ["2026-04-16 12:00:00", 50, 50, "RSD", 1],
            )
        finally:
            con.close()

    def test_sheet_logging_jobs_is_keyed_by_expense_id(self, fresh_db):
        con = _connect(fresh_db)
        try:
            cols = _column_names(con, "sheet_logging_jobs")
        finally:
            con.close()
        assert "expense_id" in cols
        assert "status" in cols
        assert "claim_token" in cols

    def test_idempotent_reapply(self, fresh_db):
        """Running migrate_db twice is a no-op (yoyo records applied migrations)."""
        db_migrations.migrate_db(fresh_db)
        db_migrations.migrate_db(fresh_db)

        con = _connect(fresh_db)
        try:
            row = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'catalog_version'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row[0] == "1"


@allure.epic("Infrastructure")
@allure.feature("Migrations")
class TestCategoryTemplateSchema:
    def test_new_columns_and_tables_exist(self, fresh_db):
        con = _connect(fresh_db)
        try:
            category_cols = _column_names(con, "categories")
            group_cols = _column_names(con, "category_groups")
            tables = _table_names(con)
        finally:
            con.close()

        assert {"code", "is_hidden", "is_retired"}.issubset(category_cols)
        assert "code" in group_cols
        assert {"category_templates", "category_translations"}.issubset(tables)

    def test_foreign_keys_intact(self, fresh_db):
        con = _connect(fresh_db)
        try:
            problems = con.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            con.close()
        assert problems == []

    def test_duplicate_category_and_group_names_allowed(self, fresh_db):
        con = _connect(fresh_db)
        try:
            con.execute(
                "INSERT INTO category_groups (name, sort_order, code) VALUES ('Group', 1, 'a')",
            )
            con.execute(
                "INSERT INTO category_groups (name, sort_order, code) VALUES ('Group', 2, 'b')",
            )
            con.execute(
                "INSERT INTO categories (name, code) VALUES ('Category', 'c1')",
            )
            con.execute(
                "INSERT INTO categories (name, code) VALUES ('Category', 'c2')",
            )
        finally:
            con.close()

    def test_category_code_is_unique(self, fresh_db):
        con = _connect(fresh_db)
        try:
            con.execute("INSERT INTO categories (name, code) VALUES ('A', 'dup')")
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("INSERT INTO categories (name, code) VALUES ('B', 'dup')")
        finally:
            con.close()


@allure.epic("Infrastructure")
@allure.feature("Migrations")
class TestInitDbIntegration:
    def test_init_db_creates_file_and_connects(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dinary.db")

        assert not storage.DB_PATH.exists()
        storage.init_db()
        assert storage.DB_PATH.exists()

        con = storage.get_connection()
        try:
            version = get_catalog_version(con)
        finally:
            con.close()
        assert version == 1


@allure.epic("Infrastructure")
@allure.feature("Migrations")
class TestAccountingCurrencyAnchor:
    """See ``specs/reference/currencies.md`` "Accounting currency source of truth"."""

    def _point_repo_at_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dinary.db")

    def test_fresh_db_persists_anchor_uppercased(self, tmp_path, monkeypatch):
        """Both the stored value and ``settings.accounting_currency`` must be
        uppercased so downstream callers can trust the normalised form."""
        self._point_repo_at_tmp(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, "accounting_currency", "eur")

        storage.init_db()

        con = _connect(storage.DB_PATH)
        try:
            row = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'accounting_currency'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row[0] == "EUR"
        assert settings.accounting_currency == "EUR"

    def test_matching_anchor_is_noop(self, tmp_path, monkeypatch):
        """Re-running ``init_db`` with the SAME accounting currency
        must be a clean no-op (no duplicate row, no error). This is
        the hot path every server restart / test fixture hits.
        """
        self._point_repo_at_tmp(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, "accounting_currency", "EUR")

        storage.init_db()
        storage.init_db()

        con = _connect(storage.DB_PATH)
        try:
            rows = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'accounting_currency'",
            ).fetchall()
        finally:
            con.close()
        assert rows == [("EUR",)]

    def test_mismatched_anchor_refuses_to_start(self, tmp_path, monkeypatch):
        """Must raise instead of silently writing rows in the wrong unit; the
        message must name both currencies so the operator can tell the drift direction."""
        self._point_repo_at_tmp(tmp_path, monkeypatch)

        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        storage.init_db()

        monkeypatch.setattr(settings, "accounting_currency", "RSD")
        with pytest.raises(RuntimeError, match="accounting_currency") as excinfo:
            storage.init_db()
        assert "'EUR'" in str(excinfo.value)
        assert "'RSD'" in str(excinfo.value)

    def test_case_insensitive_match(self, tmp_path, monkeypatch):
        """``EUR`` vs ``eur`` must NOT be treated as a mismatch —
        only the ISO-4217 identity matters, not the operator's
        capitalisation habits in ``.deploy/.env``.
        """
        self._point_repo_at_tmp(tmp_path, monkeypatch)

        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        storage.init_db()

        monkeypatch.setattr(settings, "accounting_currency", "eur")
        storage.init_db()
        assert settings.accounting_currency == "EUR"

    def test_fresh_db_without_env_rejects(self, tmp_path, monkeypatch):
        """Fresh DB + empty ``DINARY_ACCOUNTING_CURRENCY`` has no seed
        source — we refuse to guess. The operator must pick a currency
        on the very first deploy; after that they can drop the env var.
        """
        self._point_repo_at_tmp(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, "accounting_currency", "  ")

        with pytest.raises(RuntimeError, match="Fresh"):
            storage.init_db()

    def test_populated_db_without_env_reads_anchor(self, tmp_path, monkeypatch):
        """Must read the anchored value from the DB and broadcast it via
        ``settings.accounting_currency``, not fail."""
        self._point_repo_at_tmp(tmp_path, monkeypatch)

        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        storage.init_db()

        monkeypatch.setattr(settings, "accounting_currency", "")
        storage.init_db()

        assert settings.accounting_currency == "EUR"
        con = _connect(storage.DB_PATH)
        try:
            row = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'accounting_currency'",
            ).fetchone()
        finally:
            con.close()
        assert row[0] == "EUR"


@allure.epic("Infrastructure")
@allure.feature("Migrations")
class TestLlmbrokerUpgrade:
    """0002 and 0003 each drop the llmbroker tables an older release left behind
    and reset ``PRAGMA user_version``; 0003 also swaps the rating key on
    ``classification_rules`` from the model name to the broker call id.

    llmbroker stamps a store schema version and migrates nothing: a file carrying
    an older one raises on the first broker call. ``DROP TABLE`` cannot clear a
    header value, so a migration that drops the tables must reset it too.
    """

    # Every table a pre-1.3.0 dinary could leave behind: the llmbroker 0.0.11 set
    # plus the two dinary owned before the package took over provider management.
    _LEGACY_TABLES = (
        "llmbroker_registry",
        "llmbroker_calls",
        "llmbroker_secrets",
        "llmbroker_state",
        "llmbroker_providers",
        "llmbroker_call_log",
    )

    # 0003 drops what 1.9.0 cannot reuse: the journal, whose columns changed; the
    # registry, whose 1.3.0 rows carry no `from_preset` marker and would be read as
    # installation-owned entries no sync may ever remove; and the disable map, whose
    # rows are keyed by provider names the curated list does not share.
    _1_3_0_DROPPED = ("llmbroker_registry", "llmbroker_calls", "llmbroker_disabled")
    _1_3_0_KEPT = ("llmbroker_secrets",)
    _1_3_0_TABLES = _1_3_0_DROPPED + _1_3_0_KEPT

    def _seed_broker_database(self, tmp_path, monkeypatch, tables, user_version):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dinary.db")
        con = sqlite3.connect(str(storage.DB_PATH), isolation_level=None)
        try:
            for table in tables:
                con.execute(f"CREATE TABLE {table} (name TEXT)")
            con.execute(f"PRAGMA user_version = {user_version}")
        finally:
            con.close()
        return storage.DB_PATH

    def _seed_0_0_11_database(self, tmp_path, monkeypatch):
        return self._seed_broker_database(tmp_path, monkeypatch, self._LEGACY_TABLES, 1)

    def _seed_1_3_0_database(self, tmp_path, monkeypatch):
        return self._seed_broker_database(tmp_path, monkeypatch, self._1_3_0_TABLES, 5)

    def _seed_real_1_3_0_upgrade(self, tmp_path, monkeypatch):
        """The upgrade as the server meets it: 0001 and 0002 already applied, then
        the four tables llmbroker 1.3.0 created for itself, carrying data. 0002
        itself drops the secrets table, so a bare file would not reproduce this."""
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dinary.db")
        db = storage.DB_PATH
        _apply_through(db, "0002")
        con = sqlite3.connect(str(db), isolation_level=None)
        try:
            con.execute(
                "CREATE TABLE llmbroker_registry (name TEXT PRIMARY KEY, base_url TEXT,"
                " model TEXT, api_key_ref TEXT, metadata TEXT)"
            )
            con.execute("CREATE TABLE llmbroker_calls (id TEXT PRIMARY KEY, key_hash TEXT)")
            con.execute("CREATE TABLE llmbroker_secrets (ref TEXT PRIMARY KEY, value TEXT)")
            con.execute("CREATE TABLE llmbroker_disabled (name TEXT PRIMARY KEY, disabled INT)")
            con.execute(
                "INSERT INTO llmbroker_secrets VALUES ('GROQ_API_KEY', 'rotated-in-db-only')"
            )
            con.execute(
                "INSERT INTO llmbroker_registry VALUES ('groq-llama-3.3-70b',"
                " 'https://api.groq.com/openai/v1', 'llama-3.3-70b-versatile',"
                " 'GROQ_API_KEY', '{}')"
            )
            # A name off the list dinary ran before the bump — the curated list has none.
            con.execute("INSERT INTO llmbroker_disabled VALUES ('groq-llama-3.3-70b', 1)")
            con.execute("PRAGMA user_version = 5")
        finally:
            con.close()
        return db

    def test_resets_user_version_and_drops_legacy_tables(self, tmp_path, monkeypatch):
        db = self._seed_0_0_11_database(tmp_path, monkeypatch)

        db_migrations.migrate_db(db)

        con = _connect(db)
        try:
            user_version = con.execute("PRAGMA user_version").fetchone()[0]
            tables = _table_names(con)
        finally:
            con.close()
        assert user_version == 0
        assert tables.isdisjoint(self._LEGACY_TABLES)

    def test_drops_the_1_3_0_tables_and_resets_user_version(self, tmp_path, monkeypatch):
        """Seeded from the realistic path, not a bare file: there, migration 0002
        would drop these tables and clear the header before 0003 ran, and every
        assertion below would hold no matter what 0003 did."""
        db = self._seed_real_1_3_0_upgrade(tmp_path, monkeypatch)

        db_migrations.migrate_db(db)

        con = _connect(db)
        try:
            user_version = con.execute("PRAGMA user_version").fetchone()[0]
            tables = _table_names(con)
        finally:
            con.close()
        assert user_version == 0
        assert tables.isdisjoint(self._1_3_0_DROPPED)
        assert set(self._1_3_0_KEPT) <= tables

    def test_pre_upgrade_providers_do_not_outlive_the_registry_drop(self, tmp_path, monkeypatch):
        """Why the registry is dropped rather than kept: a 1.3.0 row carries no
        `from_preset` marker, which 1.9.0 reads as an entry the installation stated
        itself — one no model-list merge may ever remove."""
        db = self._seed_real_1_3_0_upgrade(tmp_path, monkeypatch)

        db_migrations.migrate_db(db)

        async def _entries() -> list[str]:
            registry = llmbroker_sqlite.Registry(db)
            try:
                return [cfg.name for cfg in await registry.load()]
            finally:
                await registry.aclose()

        assert asyncio.run(_entries()) == []

    def test_upgrade_keeps_stored_keys(self, tmp_path, monkeypatch):
        """A key rotated straight in the database has no other copy — ``.deploy/.env``
        only ever seeded it — and the table definition is unchanged in 1.9.0."""
        db = self._seed_real_1_3_0_upgrade(tmp_path, monkeypatch)

        db_migrations.migrate_db(db)

        con = _connect(db)
        try:
            secret = con.execute(
                "SELECT value FROM llmbroker_secrets WHERE ref = 'GROQ_API_KEY'",
            ).fetchone()
        finally:
            con.close()
        assert secret[0] == "rotated-in-db-only"

    def test_upgraded_broker_still_resolves_a_stored_key(self, tmp_path, monkeypatch):
        """Surviving the migration is not enough — the running 1.9.0 broker has to
        read the row back through its own schema."""
        db = self._seed_real_1_3_0_upgrade(tmp_path, monkeypatch)
        db_migrations.migrate_db(db)

        async def _read() -> str:
            secrets = llmbroker_sqlite.Secrets(db)
            try:
                return await secrets.resolve("GROQ_API_KEY")
            finally:
                await secrets.aclose()

        assert asyncio.run(_read()) == "rotated-in-db-only"

    def test_adds_llm_call_id_to_classification_rules(self, fresh_db):
        con = _connect(fresh_db)
        try:
            columns = _column_names(con, "classification_rules")
        finally:
            con.close()
        assert "llm_call_id" in columns
        assert "llm_name" not in columns

    def test_upgraded_database_gets_llm_call_id(self, tmp_path, monkeypatch):
        """The column must also land on a 1.3.0-era database, not only a fresh one."""
        db = self._seed_1_3_0_database(tmp_path, monkeypatch)

        db_migrations.migrate_db(db)

        con = _connect(db)
        try:
            columns = _column_names(con, "classification_rules")
        finally:
            con.close()
        assert "llm_call_id" in columns
        assert "llm_name" not in columns

    def test_fresh_broker_owns_every_remaining_llmbroker_table(self, tmp_path, monkeypatch):
        """No llmbroker_-prefixed table may outlive the cleanup unless the running
        llmbroker recreated it — a stale one holds dead rows (and plaintext keys)
        that nothing migrates or reads again."""
        db = self._seed_1_3_0_database(tmp_path, monkeypatch)
        db_migrations.migrate_db(db)

        asyncio.run(_provision_broker_schema(db))

        con = _connect(db)
        try:
            tables = _table_names(con)
        finally:
            con.close()
        # The version marker is not one of the store's data tables, so it is not in
        # the spec; the running broker creates it alongside them.
        current = {spec.name for spec in llmbroker_spec.TABLES.values()} | {
            "llmbroker_schema_version",
        }
        assert {t for t in tables if t.startswith("llmbroker_")} <= current

    def _upgraded_and_provisioned(self, tmp_path, monkeypatch):
        """A database that has been through 0003 and then had 1.9.0 create its own
        schema on it — what a downgrade actually starts from."""
        db = self._seed_real_1_3_0_upgrade(tmp_path, monkeypatch)
        db_migrations.migrate_db(db)
        asyncio.run(_provision_broker_schema(db))
        return db

    def test_rollback_returns_the_db_to_the_1_3_0_shape(self, tmp_path, monkeypatch):
        """``inv restore-yoyo --to=0002`` is the only way back off llmbroker 1.9.0.
        It has to take away the 1.9.0 journal and both schema stamps, and leave the
        stored keys the upgrade preserved."""
        db = self._upgraded_and_provisioned(tmp_path, monkeypatch)

        _rollback_to(db, "0002")

        con = _connect(db)
        try:
            columns = _column_names(con, "classification_rules")
            tables = _table_names(con)
            user_version = con.execute("PRAGMA user_version").fetchone()[0]
            secret = con.execute(
                "SELECT value FROM llmbroker_secrets WHERE ref = 'GROQ_API_KEY'",
            ).fetchone()
        finally:
            con.close()
        assert "llm_name" in columns
        assert "llm_call_id" not in columns
        assert tables.isdisjoint({*self._1_3_0_DROPPED, "llmbroker_schema_version"})
        assert set(self._1_3_0_KEPT) <= tables
        assert user_version == 0
        assert secret[0] == "rotated-in-db-only"

    def test_rollback_leaves_no_schema_stamp_for_the_older_broker_to_refuse(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Assert the state the older broker reads *before* provisioning. Asserting
        what a 1.9.0 broker writes afterwards would pass with the stamp left in
        place, since it reads its own version back and accepts it."""
        db = self._upgraded_and_provisioned(tmp_path, monkeypatch)

        _rollback_to(db, "0002")

        con = _connect(db)
        try:
            marker = con.execute(
                "SELECT count(*) FROM sqlite_master"
                " WHERE type = 'table' AND name = 'llmbroker_schema_version'",
            ).fetchone()[0]
            user_version = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
        assert marker == 0
        assert user_version == 0

    def test_rollback_leaves_a_database_a_broker_can_provision_again(self, tmp_path, monkeypatch):
        """And the version check really does pass on that state, rather than only
        looking like it would."""
        db = self._upgraded_and_provisioned(tmp_path, monkeypatch)

        _rollback_to(db, "0002")
        # Under a new path: llmbroker caches "schema is ready" per file path for
        # the life of the process, and a real downgrade restarts the service.
        restarted = tmp_path / "after-rollback.db"
        shutil.copy(db, restarted)
        asyncio.run(_provision_broker_schema(restarted))

        con = _connect(restarted)
        try:
            version = con.execute(
                "SELECT version FROM llmbroker_schema_version WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert version[0] == llmbroker_spec.SCHEMA_VERSION

    @pytest.mark.parametrize("seed", ["_seed_0_0_11_database", "_seed_1_3_0_database"])
    def test_broker_starts_on_upgraded_db(self, tmp_path, monkeypatch, seed):
        """After the migration a real llmbroker broker provisions its schema on
        the upgraded DB without raising the stale-version error."""
        db = getattr(self, seed)(tmp_path, monkeypatch)
        db_migrations.migrate_db(db)

        # Would raise SchemaVersionError if the migration had not reset user_version.
        asyncio.run(_provision_broker_schema(db))

        con = _connect(db)
        try:
            version = con.execute(
                "SELECT version FROM llmbroker_schema_version WHERE id = 1",
            ).fetchone()
        finally:
            con.close()
        assert version[0] == llmbroker_spec.SCHEMA_VERSION
