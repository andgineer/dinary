"""Tests for receipt-pipeline healthcheck functions in tasks/healthcheck.py."""

import allure

from tasks.healthcheck import _healthcheck_receipt_fetch, _healthcheck_receipt_llm


@allure.epic("Tasks")
@allure.feature("Healthcheck — Receipt Pipeline")
class TestHealthcheckReceiptLLM:
    def test_ok_when_all_clear(self):
        results = {"llm_switch": "", "llm_exhausted": "", "llm_switch_count": "0"}
        assert _healthcheck_receipt_llm(results) is False

    def test_fails_on_switch(self):
        results = {
            "llm_switch": "2026-05-08T10:00:00Z | from: Groq | reason: 429 | to: OpenRouter",
            "llm_exhausted": "",
            "llm_switch_count": "1",
        }
        assert _healthcheck_receipt_llm(results) is True

    def test_fails_on_exhausted(self):
        results = {
            "llm_switch": "",
            "llm_exhausted": "2026-05-08T10:05:00Z | invoice: ABC-123",
            "llm_switch_count": "3",
        }
        assert _healthcheck_receipt_llm(results) is True

    def test_both_failures_reported(self, capsys):
        """When both switch and exhausted are set, both FAIL lines are printed."""
        results = {
            "llm_switch": "2026-05-08T10:00:00Z | from: Groq | reason: 429 | to: OpenRouter",
            "llm_exhausted": "2026-05-08T10:05:00Z | invoice: ABC-123",
            "llm_switch_count": "1",
        }
        failed = _healthcheck_receipt_llm(results)
        assert failed is True
        err = capsys.readouterr().err
        assert "LLM provider switched" in err
        assert "All LLM providers exhausted" in err

    def test_prints_switch_count_info(self, capsys):
        results = {"llm_switch": "", "llm_exhausted": "", "llm_switch_count": "5"}
        _healthcheck_receipt_llm(results)
        out = capsys.readouterr().out
        assert "5" in out


@allure.epic("Tasks")
@allure.feature("Healthcheck — Receipt Pipeline")
class TestHealthcheckReceiptFetch:
    def test_ok_when_all_clear(self):
        results = {"receipt_fallback": "", "receipt_fallback_count": "0"}
        assert _healthcheck_receipt_fetch(results) is False

    def test_fails_on_fallback(self):
        results = {
            "receipt_fallback": "2026-05-08T09:55:00Z | invoice: XYZ | reason: HTTP 503",
            "receipt_fallback_count": "1",
        }
        assert _healthcheck_receipt_fetch(results) is True

    def test_prints_fallback_count_info(self, capsys):
        results = {"receipt_fallback": "", "receipt_fallback_count": "3"}
        _healthcheck_receipt_fetch(results)
        out = capsys.readouterr().out
        assert "3" in out
