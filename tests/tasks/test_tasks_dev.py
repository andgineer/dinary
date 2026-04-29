"""Tests for helpers in :mod:`tasks.dev`."""

import subprocess
import sys

import allure
import pytest

import tasks  # noqa: F401 — loads tasks/__init__.py which imports tasks.dev

_dev = sys.modules["tasks.dev"]


@allure.epic("Deploy")
@allure.feature("build-static: version resolution from git")
class TestBuildVersion:
    def test_returns_tag_with_v_prefix_stripped(self, monkeypatch):
        def fake_check_output(args, **_kwargs):
            if "describe" in args:
                return "v1.2.3\n"
            pytest.fail(f"unexpected subprocess call: {args}")

        monkeypatch.setattr(_dev.subprocess, "check_output", fake_check_output)
        assert _dev._build_version() == "1.2.3"

    def test_tag_without_v_prefix_passed_through(self, monkeypatch):
        def fake_check_output(args, **_kwargs):
            if "describe" in args:
                return "1.2.3\n"
            pytest.fail(f"unexpected subprocess call: {args}")

        monkeypatch.setattr(_dev.subprocess, "check_output", fake_check_output)
        assert _dev._build_version() == "1.2.3"

    def test_falls_back_to_commit_hash_when_no_tag(self, monkeypatch):
        def fake_check_output(args, **_kwargs):
            if "describe" in args:
                raise subprocess.CalledProcessError(128, "git")
            if "rev-parse" in args:
                return "abc1234\n"
            pytest.fail(f"unexpected subprocess call: {args}")

        monkeypatch.setattr(_dev.subprocess, "check_output", fake_check_output)
        assert _dev._build_version() == "abc1234"
