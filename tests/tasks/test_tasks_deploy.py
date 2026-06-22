"""Tests for deploy helpers in :mod:`tasks.deploy`."""

from unittest.mock import MagicMock

import allure
import pytest

import tasks  # noqa: F401  # populates sys.modules['tasks.deploy']


@allure.epic("Infrastructure")
@allure.feature("Deploy")
class TestDeployRefRequired:
    def test_exits_when_ref_is_missing(self, capsys):
        with pytest.raises(SystemExit) as exc:
            tasks.deploy.body(MagicMock(), ref="", no_start=False)
        assert exc.value.code == 1
        assert "--ref is required" in capsys.readouterr().err
