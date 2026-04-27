"""Tests for ``inv import-report-2d-3d`` (local / remote transport).

The CLI surface mirrors ``inv report-*`` but the import diagnostic
lives under :mod:`tasks.imports`, so the contract — JSON-only over
SSH, snapshot wrapper for the live DB, byte-passthrough for
``--json`` — is pinned independently here.
"""

import json
from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.imports


@allure.epic("Deploy")
@allure.feature("import-report-2d-3d: local / remote transport")
class TestImportReport2d3dTransport:
    """CLI surface of ``inv import-report-2d-3d`` mirrors ``inv report-*``:

    * default runs locally via ``c.run``; no SSH,
    * ``--remote`` always asks the server for ``--json`` and renders
      on the local terminal (the only way to keep Cyrillic /
      box-drawing glyphs intact across the SSH pipe),
    * ``--json --remote`` forwards the server's bytes verbatim so
      stdout can be piped into ``jq`` without a round-trip through
      ``rows_from_json``.
    """

    @pytest.fixture
    def _spy_transports(self, monkeypatch):
        class Spy:
            ssh_bytes_cmd: str | None = None
            ssh_bytes_payload: bytes = b""
            local_cmd: str | None = None

        spy = Spy()

        def fake_bytes(cmd: str) -> bytes:
            spy.ssh_bytes_cmd = cmd
            return spy.ssh_bytes_payload

        monkeypatch.setattr(tasks.imports, "ssh_capture_bytes", fake_bytes)
        return spy

    @staticmethod
    def _run(c, **kwargs):
        return tasks.import_report_2d_3d.body(c, **kwargs)

    def _sample_payload(self) -> bytes:
        return json.dumps(
            {
                "detail": False,
                "columns": [
                    "category",
                    "event",
                    "tags",
                    "rows",
                    "sheet_category",
                    "sheet_group",
                    "resolution_kind",
                    "years",
                    "amount",
                    "comment",
                ],
                "rows": [
                    {
                        "category": "путешествия",
                        "event": "",
                        "tags": "",
                        "rows": 3,
                        "sheet_category": "путешествия",
                        "sheet_group": "",
                        "resolution_kind": "mapping",
                        "years": "2024-2026",
                        "amount": "42000.00",
                        "comment": "Бали",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode()

    def test_default_runs_locally(self, _spy_transports):
        c = MagicMock()
        self._run(c)
        c.run.assert_called_once()
        cmd = c.run.call_args[0][0]
        assert cmd.startswith("uv run python -m dinary.imports.report_2d_3d")
        assert _spy_transports.ssh_bytes_cmd is None

    def test_remote_rich_uses_json_transport_and_preserves_cyrillic(
        self,
        _spy_transports,
        capsys,
    ):
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, remote=True)

        cmd = _spy_transports.ssh_bytes_cmd or ""
        assert "--json" in cmd
        assert "--csv" not in cmd
        c.run.assert_not_called()

        out = capsys.readouterr().out
        assert "\ufffd" not in out
        assert "путешествия" in out

    def test_remote_uses_snapshot_wrapper_not_live_db(self, _spy_transports):
        """Even though SQLite WAL would technically let a reader open
        the live ``data/dinary.db`` concurrently with the writer, the
        reader can race with in-flight checkpoints and Litestream
        replication and surface ephemeral inconsistencies.
        ``import-report-2d-3d --remote`` must go through the same
        ``sqlite3 .backup`` snapshot wrapper as ``inv report-*``.
        """
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, remote=True)

        cmd = _spy_transports.ssh_bytes_cmd or ""
        # Snapshot wrapper invariants: must snapshot the live DB,
        # run the report against the snapshot, and set up the trap
        # before the backup.
        assert "SNAP=/tmp/dinary-report-snapshot-$$.db" in cmd
        assert 'sqlite3 "/home/ubuntu/dinary/data/dinary.db"' in cmd
        assert '.backup \\"$SNAP\\"' in cmd
        assert 'DINARY_DATA_PATH="$SNAP"' in cmd
        assert 'trap "rm -f \\"$SNAP\\"" EXIT' in cmd
        assert cmd.index("trap") < cmd.index("sqlite3")

    def test_remote_csv_renders_locally(self, _spy_transports, capsys):
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, csv=True, remote=True)

        # Remote always runs in JSON mode; ``--csv`` is applied locally.
        assert "--json" in (_spy_transports.ssh_bytes_cmd or "")
        assert "--csv" not in (_spy_transports.ssh_bytes_cmd or "")

        out = capsys.readouterr().out
        assert "путешествия" in out
        # CSV output is line-based with commas, not rich's box-drawing.
        assert "━" not in out

    def test_remote_json_forwards_server_bytes_verbatim(
        self,
        _spy_transports,
        capsysbinary,
    ):
        payload = (
            b'{"detail": false, "columns": ["category"], '
            b'"rows": [{"category": "\xd0\xbf\xd1\x83"}]}\n'
        )
        _spy_transports.ssh_bytes_payload = payload
        c = MagicMock()
        self._run(c, json=True, remote=True)
        out = capsysbinary.readouterr().out
        assert out == payload

    def test_csv_and_json_are_mutually_exclusive(self, _spy_transports):
        c = MagicMock()
        with pytest.raises(SystemExit) as excinfo:
            self._run(c, csv=True, json=True)
        assert excinfo.value.code == 1
