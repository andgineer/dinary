"""Verify ensure_pool warning behaviour with constructor seed.

Key invariant: a provider whose secret CAN be resolved (env var present) must
produce zero warnings. _seed_secrets runs before _populate_pool, so resolution
sees the secret on first init.
"""

import logging
from pathlib import Path

import llmbroker
import llmbroker.sqlite
import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


_KEY_REF = "TEST_LLMBROKER_API_KEY"
_KEY_VAL = "sk-test-secret-value"


def _make_toml(tmp_path: Path) -> Path:
    p = tmp_path / "llms.toml"
    p.write_text(
        f"""
[[llms]]
name = "test-llm"
base_url = "https://api.example.com/v1"
model = "test-model"
api_key_ref = "{_KEY_REF}"
"""
    )
    return p


def _missing_secret_warnings(caplog) -> list:
    return [r for r in caplog.records if _KEY_REF in r.message]


def _broker(db, toml):
    return llmbroker.AsyncBroker(
        registry=llmbroker.sqlite.Registry(db),
        secrets=llmbroker.sqlite.Secrets(db),
        seed=llmbroker.Registry(toml),
        seed_policy=llmbroker.SeedPolicy.ADD,
    )


@pytest.fixture()
def broker_db(tmp_path):
    return tmp_path / "llmbroker_test.db"


@pytest.fixture()
def llms_toml(tmp_path):
    return _make_toml(tmp_path)


async def test_empty_db_with_env_var_zero_warnings(
    broker_db, llms_toml, monkeypatch, caplog, real_ensure_pool
):
    """Empty DB + env var set → 0 warnings.

    _seed_secrets succeeds before _populate_pool, so resolution is silent.
    """
    monkeypatch.setenv(_KEY_REF, _KEY_VAL)
    broker = _broker(broker_db, llms_toml)
    with caplog.at_level(logging.WARNING, logger="llmbroker.broker"):
        await broker.ensure_pool()
    await broker.aclose()

    assert len(_missing_secret_warnings(caplog)) == 0


async def test_empty_db_env_absent_exactly_one_warning(
    broker_db, llms_toml, monkeypatch, caplog, real_ensure_pool
):
    """Empty DB + env var absent → exactly 1 warning."""
    monkeypatch.delenv(_KEY_REF, raising=False)
    broker = _broker(broker_db, llms_toml)
    with caplog.at_level(logging.WARNING, logger="llmbroker.broker"):
        await broker.ensure_pool()
    await broker.aclose()

    assert len(_missing_secret_warnings(caplog)) == 1


async def test_restart_env_var_present_no_sqlite_secret_zero_warnings(
    broker_db, llms_toml, monkeypatch, caplog, real_ensure_pool
):
    """Provider in DB, no sqlite secret, env var present → 0 warnings.

    On restart _seed_secrets seeds from env before pool build, so resolution is silent.
    """
    # Phase 1: populate the registry without env var present.
    setup_broker = _broker(broker_db, llms_toml)
    await setup_broker.ensure_pool()
    await setup_broker.aclose()

    # Phase 2: restart with env var present.
    monkeypatch.setenv(_KEY_REF, _KEY_VAL)
    broker = _broker(broker_db, llms_toml)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="llmbroker.broker"):
        await broker.ensure_pool()
    await broker.aclose()

    assert len(_missing_secret_warnings(caplog)) == 0


async def test_restart_secret_absent_everywhere_exactly_one_warning(
    broker_db, llms_toml, monkeypatch, caplog, real_ensure_pool
):
    """Restart with secret absent in sqlite and env → exactly 1 warning (regression guard).

    Before the pool-init refactor this emitted 2 warnings; seeding now happens before
    pool build, so the single unresolvable key produces exactly one warning.
    """
    monkeypatch.delenv(_KEY_REF, raising=False)

    first_broker = _broker(broker_db, llms_toml)
    await first_broker.ensure_pool()
    await first_broker.aclose()

    caplog.clear()
    broker = _broker(broker_db, llms_toml)
    with caplog.at_level(logging.WARNING, logger="llmbroker.broker"):
        await broker.ensure_pool()
    await broker.aclose()

    assert len(_missing_secret_warnings(caplog)) == 1


async def test_restart_with_seeded_secret_zero_warnings(
    broker_db, llms_toml, monkeypatch, caplog, real_ensure_pool
):
    """Normal restart: provider + secret already in DB → 0 warnings."""
    monkeypatch.setenv(_KEY_REF, _KEY_VAL)

    first_broker = _broker(broker_db, llms_toml)
    await first_broker.ensure_pool()
    await first_broker.aclose()

    caplog.clear()
    broker = _broker(broker_db, llms_toml)
    with caplog.at_level(logging.WARNING, logger="llmbroker.broker"):
        await broker.ensure_pool()
    await broker.aclose()

    assert len(_missing_secret_warnings(caplog)) == 0
