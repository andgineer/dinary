"""Tests for helpers in ``tasks.py`` (deploy/operator orchestration).

We only cover pure helpers here; tasks themselves run shell commands
against a real server and are exercised via the deploy flow.
"""

import importlib.util
import sys
from pathlib import Path

import allure

_TASKS_PATH = Path(__file__).resolve().parent.parent / "tasks.py"
_spec = importlib.util.spec_from_file_location("_dinary_tasks", _TASKS_PATH)
_tasks = importlib.util.module_from_spec(_spec)
sys.modules["_dinary_tasks"] = _tasks
_spec.loader.exec_module(_tasks)
_systemd_quote = _tasks._systemd_quote


@allure.epic("Deploy")
@allure.feature("systemd EnvironmentFile quoting")
class TestSystemdQuote:
    def test_bare_alphanumeric_unquoted(self):
        assert _systemd_quote("abc123") == "abc123"

    def test_url_safe_chars_unquoted(self):
        assert _systemd_quote("ubuntu@1.2.3.4") == "ubuntu@1.2.3.4"
        assert _systemd_quote("/home/ubuntu/.creds.json") == "/home/ubuntu/.creds.json"

    def test_empty_value_emitted_bare(self):
        # Trailing `KEY=` is the documented "unset" form for systemd.
        assert _systemd_quote("") == ""
        assert _systemd_quote(None) == ""

    def test_value_with_space_is_quoted(self):
        assert _systemd_quote("hello world") == '"hello world"'

    def test_value_with_double_quote_is_escaped(self):
        # JSON values like {"year": 2025} round-trip via backslash escaping.
        assert _systemd_quote('{"year": 2025}') == '"{\\"year\\": 2025}"'

    def test_value_with_dollar_is_escaped(self):
        # Without the $ escape systemd would try to expand $X as a variable.
        assert _systemd_quote("price=$5") == '"price=\\$5"'

    def test_value_with_backslash_is_escaped(self):
        assert _systemd_quote("a\\b") == '"a\\\\b"'

    def test_url_with_query_string_is_quoted(self):
        # ? is not in the safe set so we get a quoted form.
        result = _systemd_quote("https://docs.google.com/spreadsheets/d/abc?usp=sharing")
        assert result.startswith('"') and result.endswith('"')
        assert "https://docs.google.com/spreadsheets/d/abc?usp=sharing" in result
