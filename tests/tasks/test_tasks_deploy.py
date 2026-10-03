"""Tests for deploy helpers in :mod:`tasks.deploy`."""

import subprocess
import sys
from unittest.mock import MagicMock

import allure
import pytest

import tasks  # noqa: F401  # populates sys.modules['tasks.deploy']

tasks_deploy = sys.modules["tasks.deploy"]


@allure.epic("Infrastructure")
@allure.feature("Deploy")
class TestDeployRefRequired:
    def test_exits_when_ref_is_missing(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tasks.deploy.body(MagicMock(), ref="", no_start=False)
        assert exc.value.code == 1
        assert "--ref is required" in capsys.readouterr().err


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@allure.epic("Infrastructure")
@allure.feature("Deploy")
class TestDeployCheckout:
    @pytest.fixture
    def server_behind_origin(self, tmp_path):
        origin = tmp_path / "origin"
        origin.mkdir()
        _git(origin, "init", "-q", "-b", "main")
        _git(origin, "commit", "-q", "--allow-empty", "-m", "first")
        _git(origin, "tag", "v1")
        server = tmp_path / "server"
        _git(tmp_path, "clone", "-q", str(origin), str(server))
        _git(origin, "commit", "-q", "--allow-empty", "-m", "second")
        return origin, server

    def test_a_branch_ref_deploys_what_origin_has_not_the_stale_local_branch(
        self,
        server_behind_origin,
    ):
        origin, server = server_behind_origin

        subprocess.run(["bash", "-c", tasks_deploy.checkout_script("main")], cwd=server, check=True)

        assert _git(server, "rev-parse", "HEAD") == _git(origin, "rev-parse", "main")

    def test_a_tag_ref_deploys_the_tagged_commit(self, server_behind_origin):
        origin, server = server_behind_origin

        subprocess.run(["bash", "-c", tasks_deploy.checkout_script("v1")], cwd=server, check=True)

        assert _git(server, "rev-parse", "HEAD") == _git(origin, "rev-parse", "v1^{commit}")
