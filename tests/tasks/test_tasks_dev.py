"""Tests for helpers in :mod:`tasks.dev`."""

import json
import subprocess
import sys
from pathlib import Path

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


class _FakeContext:
    """Minimal stand-in for an Invoke ``Context`` in build_static tests."""

    def __init__(self, *, build_writer=None) -> None:
        self.commands: list[str] = []
        self._build_writer = build_writer

    def run(self, cmd, **_kwargs):
        self.commands.append(cmd)
        if self._build_writer is not None and ("vite" in cmd or "run build" in cmd):
            self._build_writer()


_build_static_fn = _dev.build_static.body  # unwrap @task to call directly


@allure.epic("Deploy")
@allure.feature("build-static: Vue PWA build pipeline")
class TestBuildStatic:
    @pytest.fixture
    def fake_repo(self, tmp_path, monkeypatch):
        """Run build_static against a writable scratch repo."""
        (tmp_path / "webapp").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_dev, "_build_version", lambda: "abc1234")
        return tmp_path

    def test_runs_npm_ci_then_build_and_writes_version(self, fake_repo):
        static_dir = fake_repo / "_static"

        def write_index():
            static_dir.mkdir(exist_ok=True)
            (static_dir / "index.html").write_text("<html></html>")

        ctx = _FakeContext(build_writer=write_index)
        _build_static_fn(ctx)

        assert ctx.commands == [
            "npm --prefix webapp ci --no-audit --no-fund",
            "npm --prefix webapp run build",
        ]
        assert (fake_repo / "data" / ".deployed_version").read_text() == "abc1234"
        version_json = json.loads((static_dir / "version.json").read_text())
        assert version_json == {"version": "abc1234"}

    def test_omits_npm_ci_when_node_modules_present(self, fake_repo):
        (fake_repo / "webapp" / "node_modules").mkdir()
        static_dir = fake_repo / "_static"

        def write_index():
            static_dir.mkdir(exist_ok=True)
            (static_dir / "index.html").write_text("<html></html>")

        ctx = _FakeContext(build_writer=write_index)
        _build_static_fn(ctx)

        assert ctx.commands == ["npm --prefix webapp run build"]

    def test_raises_when_webapp_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _FakeContext()
        with pytest.raises(RuntimeError, match="webapp/ is missing"):
            _build_static_fn(ctx)
        assert ctx.commands == []

    def test_raises_when_vite_produces_no_index(self, fake_repo):
        (fake_repo / "webapp" / "node_modules").mkdir()
        ctx = _FakeContext()
        with pytest.raises(RuntimeError, match="Vite build did not produce"):
            _build_static_fn(ctx)


@allure.epic("Deploy")
@allure.feature("inv dev: ensures _static/ exists before serving")
class TestEnsureStaticBuilt:
    def test_short_circuits_when_index_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "_static").mkdir()
        (tmp_path / "_static" / "index.html").write_text("<html></html>")
        called: list[Path] = []
        monkeypatch.setattr(_dev, "build_static", lambda c: called.append(Path.cwd()))

        _dev._ensure_static_built(_FakeContext())

        assert called == []

    def test_invokes_build_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        called: list[bool] = []
        monkeypatch.setattr(_dev, "build_static", lambda c: called.append(True))

        _dev._ensure_static_built(_FakeContext())

        assert called == [True]

    def test_invokes_build_when_dir_present_but_index_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "_static").mkdir()
        called: list[bool] = []
        monkeypatch.setattr(_dev, "build_static", lambda c: called.append(True))

        _dev._ensure_static_built(_FakeContext())

        assert called == [True]
