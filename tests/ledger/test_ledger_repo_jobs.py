"""``sheet_logging_jobs`` queue: claim/clear/release/poison/list filters
plus stale-claim recovery semantics."""

from datetime import datetime, timedelta

import allure

from dinary.services import ledger_repo

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    _tmp_data_dir,
    fresh_db,
    populated_catalog,
)


@allure.epic("Ledger repo")
@allure.feature("sheet_logging_jobs queue")
class TestLoggingQueue:
    def _insert_one_expense(self) -> int:
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="job1",
                expense_datetime=datetime(2026, 1, 1, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=True,
            )
            pk_row = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'job1'",
            ).fetchone()
        finally:
            con.close()
        return int(pk_row[0])

    def test_claim_then_clear(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            token = ledger_repo.claim_logging_job(con, pk)
            assert token is not None
            assert ledger_repo.clear_logging_job(con, pk, token) is True
            assert ledger_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    def test_clear_with_wrong_token_returns_false(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            ledger_repo.claim_logging_job(con, pk)
            assert ledger_repo.clear_logging_job(con, pk, "wrongtoken") is False
        finally:
            con.close()

    def test_release_returns_to_pending(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            token = ledger_repo.claim_logging_job(con, pk)
            assert ledger_repo.release_logging_claim(con, pk, token) is True
            row = con.execute(
                "SELECT status, claim_token FROM sheet_logging_jobs WHERE expense_id = ?",
                [pk],
            ).fetchone()
            assert row[0] == "pending"
            assert row[1] is None
        finally:
            con.close()

    def test_double_claim_blocked(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            t1 = ledger_repo.claim_logging_job(con, pk)
            t2 = ledger_repo.claim_logging_job(con, pk)
            assert t1 is not None
            assert t2 is None
        finally:
            con.close()

    def test_stale_claim_recoverable(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            now = datetime(2026, 1, 1, 12)
            t1 = ledger_repo.claim_logging_job(con, pk, now=now)
            assert t1 is not None
            future = now + timedelta(hours=1)
            t2 = ledger_repo.claim_logging_job(
                con,
                pk,
                now=future,
                stale_before=future - timedelta(minutes=5),
            )
            assert t2 is not None
            assert t2 != t1
        finally:
            con.close()

    def test_list_filters_fresh_in_progress_but_resurfaces_stale(
        self,
        populated_catalog,
    ):
        """``list_logging_jobs`` must:
        - include ``pending`` rows
        - exclude ``in_progress`` rows whose claim is newer than
          ``stale_before`` (a recently-claimed row belongs to the
          drain that claimed it; skipping avoids burning a
          BEGIN/COMMIT on every iteration for the same row)
        - include ``in_progress`` rows whose claim is older than
          ``stale_before`` (that drain died; the row must surface
          again so the next drain can recover it)
        """
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            now = datetime(2026, 1, 1, 12)
            token = ledger_repo.claim_logging_job(con, pk, now=now)
            assert token is not None

            # Fresh claim (1 minute old) with a 5-minute cutoff → filtered out.
            fresh_now = now + timedelta(minutes=1)
            fresh_cutoff = fresh_now - timedelta(minutes=5)
            assert (
                ledger_repo.list_logging_jobs(
                    con,
                    now=fresh_now,
                    stale_before=fresh_cutoff,
                )
                == []
            )

            # Stale claim (10 minutes old) with a 5-minute cutoff → resurfaces.
            stale_now = now + timedelta(minutes=10)
            stale_cutoff = stale_now - timedelta(minutes=5)
            assert ledger_repo.list_logging_jobs(
                con,
                now=stale_now,
                stale_before=stale_cutoff,
            ) == [pk]
        finally:
            con.close()

    def test_poison_marks_row(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            ledger_repo.poison_logging_job(con, pk, "boom")
            row = con.execute(
                "SELECT status, last_error FROM sheet_logging_jobs WHERE expense_id = ?",
                [pk],
            ).fetchone()
            # Poisoned rows are excluded from list_logging_jobs().
            assert ledger_repo.list_logging_jobs(con) == []
        finally:
            con.close()
        assert row == ("poisoned", "boom")

    def test_force_clear_wipes_row(self, populated_catalog):
        pk = self._insert_one_expense()
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.force_clear_logging_job(con, pk) is True
            assert ledger_repo.count_logging_jobs(con) == 0
            # Already gone — idempotent false on re-delete.
            assert ledger_repo.force_clear_logging_job(con, pk) is False
        finally:
            con.close()
