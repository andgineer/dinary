"""Tests for the GFS retention policy used by the daily backup timer.

Pins the public ``pick_keepers`` contract (7 daily / 4 weekly /
12 monthly / all yearly) and the canonical filename pattern shared
with the restore path. Sibling :file:`test_tasks_backups_restore.py`
covers the inventory + destructive replacement helpers.
"""

import datetime

import allure

import tasks.constants
from dinary.tools.backup_retention import _make_pattern, pick_keepers
from dinary.tools.backup_snapshots import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
)


@allure.epic("Deploy")
@allure.feature("backup-cloud-setup: retention (GFS policy)")
class TestBackupRetentionScript:
    """GFS retention policy: 7 daily / 4 weekly / 12 monthly / all yearly.

    Tests import ``pick_keepers`` directly from
    ``dinary.tools.backup_retention``.
    """

    _D = tasks.constants.BACKUP_RETENTION_DAILY
    _W = tasks.constants.BACKUP_RETENTION_WEEKLY
    _M = tasks.constants.BACKUP_RETENTION_MONTHLY

    def _pk(self, snaps):
        return pick_keepers(snaps, daily=self._D, weekly=self._W, monthly=self._M)

    def test_pattern_matches_canonical_filename_shape(self):
        """Pin filename format so any change (suffix, time precision)
        that breaks restore also breaks this test.
        """
        pattern = _make_pattern(BACKUP_FILENAME_PREFIX, BACKUP_FILENAME_SUFFIX)
        m = pattern.match("dinary-2026-04-22T0317Z.db.zst")
        assert m is not None
        assert m.group(1) == "2026-04-22"
        assert pattern.match("dinary-2026-04-22.db.zst") is None
        assert pattern.match("not-a-backup.txt") is None

    @staticmethod
    def _synth(days_back_from, *, end):
        snaps = []
        for i in range(days_back_from):
            d = end - datetime.timedelta(days=i)
            name = f"dinary-{d.isoformat()}T0317Z.db.zst"
            snaps.append((d, name))
        snaps.sort()
        return snaps

    def test_keeps_exactly_daily_count_on_short_history(self):
        """Under DAILY_KEEP days of history, everything is a daily keeper."""
        end = datetime.date(2026, 4, 22)
        snaps = self._synth(self._D, end=end)
        assert len(self._pk(snaps)) == self._D

    def test_keeps_yearly_winners_indefinitely(self):
        """Closed-year snapshots survive beyond the monthly window."""
        end = datetime.date(2029, 12, 31)
        snaps = self._synth(365 * 10 + 3, end=end)
        keepers = self._pk(snaps)
        yearly_winners = {
            datetime.date.fromisoformat(n.split("dinary-")[1].split("T")[0])
            for n in keepers
            if "-12-31T" in n
        }
        for year in range(2020, 2030):
            assert datetime.date(year, 12, 31) in yearly_winners

    def test_prunes_old_dailies_but_keeps_monthly_winners(self):
        """After MONTHLY_KEEP months, dailies are pruned but monthly
        winners survive.
        """
        end = datetime.date(2026, 4, 15)
        snaps = self._synth(400, end=end)
        keepers = self._pk(snaps)
        kept_dates = {
            datetime.date.fromisoformat(n.split("dinary-")[1].split("T")[0]) for n in keepers
        }
        assert datetime.date(2026, 3, 31) in kept_dates
        day_in_scope = end - datetime.timedelta(days=3)
        day_out_of_daily = end - datetime.timedelta(days=90)
        assert day_in_scope in kept_dates
        month_of_ood = (day_out_of_daily.year, day_out_of_daily.month)
        monthly_winner = max(d for d in kept_dates if (d.year, d.month) == month_of_ood)
        if monthly_winner != day_out_of_daily:
            assert day_out_of_daily not in kept_dates

    def test_pick_keepers_is_idempotent_on_buckets(self):
        """Running retention twice on the same set produces the same result."""
        end = datetime.date(2026, 4, 22)
        snaps = self._synth(400, end=end)
        assert self._pk(snaps) == self._pk(snaps)
