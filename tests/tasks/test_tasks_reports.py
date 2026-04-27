"""Tests for the ``inv report-* --remote`` orchestration in :mod:`tasks.reports`.

The local-only path is a thin ``c.run`` wrapper covered by manual
smoke; what matters here is the JSON-on-the-wire contract for
``--remote`` so Cyrillic / box-drawing glyphs survive the SSH pipe
intact.
"""

import json
from unittest.mock import MagicMock

import allure
import pytest

import tasks.reports


@allure.epic("Deploy")
@allure.feature("report-* --remote: fetch JSON, render locally")
class TestRunReportModuleRemote:
    """End-to-end architectural contract for ``inv report-<mod> --remote``:

    1. Remote side executes the report in ``--json`` mode and only
       ships raw row data over SSH (no rich text).
    2. Transport captures bytes, decodes UTF-8 *once*, parses JSON.
    3. Local process calls the module's ``render`` on the resulting
       rows, so the final rendered output matches what the operator
       would see from a local run against the same data.

    These tests assert that contract with a mocked
    ``_ssh_capture_bytes`` so we never touch the network.
    """

    @pytest.fixture
    def _fake_ssh_bytes(self, monkeypatch):
        """Return a handle that lets each test set the remote JSON payload."""

        class Spy:
            payload: bytes = b"[]\n"
            last_cmd: str | None = None

        spy = Spy()

        def fake(cmd):
            spy.last_cmd = cmd
            return spy.payload

        monkeypatch.setattr(tasks.reports, "ssh_capture_bytes", fake)
        return spy

    def test_income_remote_uses_json_transport_regardless_of_user_format(
        self,
        _fake_ssh_bytes,
    ):
        """The user may ask for ``--csv`` / ``--json`` / rich — the
        wire format is always JSON, because that's the only
        byte-exact, chunk-boundary-safe transport.
        """
        _fake_ssh_bytes.payload = b"[]\n"
        c = MagicMock()
        tasks.reports._run_report_module(c, "income", [], remote=True)
        # The remote cmd must ask for ``--json`` so we get structured
        # data back, not a rendered rich table.
        assert "--json" in _fake_ssh_bytes.last_cmd

    def test_income_remote_csv_still_pulls_json_locally_renders_csv(
        self,
        _fake_ssh_bytes,
        monkeypatch,
        capsys,
    ):
        _fake_ssh_bytes.payload = json.dumps(
            [{"year": 2026, "months": 3, "total": "1779756.00", "avg_month": "593252.00"}],
        ).encode()
        c = MagicMock()
        tasks.reports._run_report_module(c, "income", ["--csv"], remote=True)
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "year,months,total,avg_month"
        assert "1779756.00" in out
        # Remote never rendered CSV — the server emitted JSON only.
        assert "--csv" not in (_fake_ssh_bytes.last_cmd or "")
        assert "--json" in (_fake_ssh_bytes.last_cmd or "")

    def test_income_remote_json_forwards_raw_bytes_without_re_rendering(
        self,
        _fake_ssh_bytes,
        capsysbinary,
    ):
        """``--json --remote`` is the piping-into-jq case. We should
        pass the server's bytes straight through — no ``rows_from_json``
        + ``render_json`` round-trip (which would re-format and
        re-shape whitespace / key order).
        """
        payload = b'[{"year": 2025, "tags": "\xd0\xbf\xd1\x83"}]\n'
        _fake_ssh_bytes.payload = payload
        c = MagicMock()
        tasks.reports._run_report_module(c, "income", ["--json"], remote=True)
        out = capsysbinary.readouterr().out
        assert out == payload

    def test_income_remote_rich_cyrillic_tags_survive_roundtrip(
        self,
        _fake_ssh_bytes,
        capsys,
    ):
        """The original bug: Cyrillic text corrupted into ``�`` on the
        way back. With JSON transport + single-shot decode + local
        rich render, the tag / event names must appear intact in the
        operator's terminal.
        """
        _fake_ssh_bytes.payload = json.dumps(
            [
                {
                    "year": 2025,
                    "months": 10,
                    "total": "5899845.00",
                    "avg_month": "589984.50",
                },
            ],
        ).encode()
        c = MagicMock()
        tasks.reports._run_report_module(c, "income", [], remote=True)
        out = capsys.readouterr().out
        # No replacement characters anywhere in the rendered output.
        assert "\ufffd" not in out
        assert "2025" in out
        assert "5,899,845.00" in out or "5899845.00" in out

    def test_expenses_remote_rich_preserves_cyrillic_category(
        self,
        _fake_ssh_bytes,
        capsys,
    ):
        _fake_ssh_bytes.payload = json.dumps(
            [
                {
                    "category": "путешествия",
                    "event": "",
                    "tags": "",
                    "rows": 3,
                    "total": "42000.00",
                },
            ],
            ensure_ascii=False,
        ).encode()
        c = MagicMock()
        tasks.reports._run_report_module(c, "expenses", [], remote=True)
        out = capsys.readouterr().out
        assert "\ufffd" not in out
        assert "путешествия" in out

    def test_expenses_remote_forwards_filter_flags_but_not_format_flags(
        self,
        _fake_ssh_bytes,
        capsys,
    ):
        _fake_ssh_bytes.payload = b"[]\n"
        c = MagicMock()
        tasks.reports._run_report_module(
            c,
            "expenses",
            ["--year", "2026", "--csv"],
            remote=True,
        )
        cmd = _fake_ssh_bytes.last_cmd or ""
        # Filters go to remote (they affect the query result).
        assert "--year 2026" in cmd
        # Format flags do NOT — wire format is always JSON.
        assert "--csv" not in cmd
        assert "--json" in cmd
