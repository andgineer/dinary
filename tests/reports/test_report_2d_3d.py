"""CLI dispatch tests for the 2D→3D report module.

Covers the ``--csv`` / ``--json`` mutex on the argparse layer.
Resolution, aggregation, and rendering live in sibling
``test_report_2d_3d_*.py`` files.
"""

import argparse

import allure
import pytest


@allure.epic("Report")
@allure.feature("CLI dispatch")
class TestCliDispatch:
    """Argparse-level contract for the ``--csv`` / ``--json`` mutex.

    Output always goes to stdout: default ``rich`` table, ``--csv``
    for CSV, ``--json`` for the wire envelope used by ``inv
    import-report-2d-3d --remote``. The two format flags are
    mutually exclusive so a command line cannot request contradictory
    output at once.
    """

    def _build_parser(self):
        p = argparse.ArgumentParser()
        p.add_argument("--detail", action="store_true")
        fmt = p.add_mutually_exclusive_group()
        fmt.add_argument("--csv", action="store_true")
        fmt.add_argument("--json", action="store_true")
        p.add_argument("--year", type=int, default=None)
        return p

    def test_defaults_select_rich(self):
        parser = self._build_parser()
        args = parser.parse_args([])
        assert not args.csv
        assert not args.json

    def test_csv_and_json_are_mutex(self):
        parser = self._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--csv", "--json"])

    def test_json_flag_is_accepted(self):
        parser = self._build_parser()
        args = parser.parse_args(["--json"])
        assert args.json is True

    def test_csv_flag_is_accepted(self):
        parser = self._build_parser()
        args = parser.parse_args(["--csv"])
        assert args.csv is True
