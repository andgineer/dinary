"""Tests for ``inv setup-replica`` orchestration and its scripts.

Four layers, all routed through :mod:`tasks.backups_replica`:

* :class:`TestLitestreamSetupPermissions` pins the ``sudo`` scope of
  the ``/etc/litestream.yml`` chown/chmod step (regression for a
  split-scope bug that left ``chmod`` running as ``ubuntu``).
* :class:`TestSetupReplicaScripts` pins the apt and litestream-dir
  shell builders.
* :class:`TestSetupReplicaTask` pins the step composition the task
  itself emits — the VM2 bootstrap sequence, the trust-establishment
  step, and the VM1 Litestream replicator configuration.
* :class:`TestReplicaResetTrustTask` pins the explicit trust-refresh
  task used after a VM re-provision.
"""

from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.backups_replica
import tasks.constants
import tasks.ssh_utils


_FAKE_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIABCDEFGHIJKLMNOPQRSTUVWXYZabcd dinary-vm1-litestream"
)


@allure.epic("Deploy")
@allure.feature("litestream-setup: /etc/litestream.yml permissions")
class TestLitestreamSetupPermissions:
    """Regression for a sudo-scope bug in ``inv setup-replica``:
    a naive ``sudo chown root:root ... && chmod 644 ...`` escalates
    only the ``chown``, leaving ``chmod`` to run as ``ubuntu`` and
    fail with ``Operation not permitted`` on ``/etc/litestream.yml``.
    The fix is to wrap both commands in ``bash -c`` so the outer
    ``sudo`` covers the whole pipeline atomically.

    These tests pin the contract at the outgoing-SSH boundary so a
    future refactor cannot silently reintroduce the split-scope
    shape.
    """

    @pytest.fixture
    def _spy(self, monkeypatch, tmp_path):
        calls: list[tuple[str, str]] = []

        def fake_ssh(_c, cmd: str) -> None:
            calls.append(("ssh", cmd))

        def fake_ssh_sudo(_c, cmd: str) -> None:
            calls.append(("sudo", cmd))

        def fake_ssh_replica(_c, cmd: str) -> None:
            calls.append(("ssh_replica", cmd))

        def fake_ssh_capture(_c, _cmd: str) -> str:
            calls.append(("capture", _cmd))
            return _FAKE_PUBKEY + "\n"

        def fake_write_remote_file(_c, _path: str, _content: str) -> None:
            calls.append(("write", _path))

        def fake_create_service(*_args, **_kwargs) -> None:
            calls.append(("service", "litestream"))

        def fake_write_remote_replica_file(_c, _path: str, _content: str) -> None:
            pass

        monkeypatch.setattr(tasks.backups_replica, "ssh_run", fake_ssh)
        monkeypatch.setattr(tasks.backups_replica, "ssh_sudo", fake_ssh_sudo)
        monkeypatch.setattr(tasks.backups_replica, "ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(tasks.backups_replica, "ssh_capture", fake_ssh_capture)
        monkeypatch.setattr(tasks.backups_replica, "write_remote_file", fake_write_remote_file)
        monkeypatch.setattr(
            tasks.backups_replica, "write_remote_replica_file", fake_write_remote_replica_file
        )
        monkeypatch.setattr(tasks.backups_replica, "create_service", fake_create_service)
        monkeypatch.setattr(
            tasks.backups_replica,
            "ensure_yandex_rclone_configured",
            lambda _c: calls.append(("yandex", "configured")),
        )
        monkeypatch.setattr(tasks.backups_replica, "replica_host", lambda: "ubuntu@dinary-replica")
        return calls

    def test_chown_and_chmod_run_inside_a_single_sudo_bash_c(self, _spy):
        """The compound command must be ``bash -c '... && ...'`` so
        the outer ``sudo`` (prepended by :func:`_ssh_sudo`) elevates
        the entire bash invocation, not just the first word of the
        pipeline. A bare ``&&`` chain would leave ``chmod`` running
        as the SSH user.
        """
        tasks.setup_replica.body(MagicMock(), no_swap=True)
        perm_call = next(
            (cmd for kind, cmd in _spy if kind == "sudo" and "chmod 644" in cmd),
            None,
        )
        assert perm_call is not None, "setup_replica must emit a chmod call for litestream config"
        assert perm_call.startswith("bash -c '"), (
            "permissions fix must be wrapped in bash -c so outer sudo "
            f"covers both chown and chmod; got: {perm_call!r}"
        )
        assert "chown root:root /etc/litestream.yml" in perm_call
        assert "chmod 644 /etc/litestream.yml" in perm_call
        assert perm_call.rstrip().endswith("'"), "bash -c payload must be fully quoted"

    def test_permissions_target_the_canonical_config_path(self, _spy):
        """Pin that the permissions fix addresses
        ``REMOTE_LITESTREAM_CONFIG_PATH`` specifically — a regression
        that renamed the constant but forgot to update the permissions
        step would leave the uploaded file at whatever ``sudo tee``
        left on disk (0664 with UMASK 002), readable by group
        ``ubuntu`` on a multi-user VM.
        """
        tasks.setup_replica.body(MagicMock(), no_swap=True)
        perm_call = next(
            (cmd for kind, cmd in _spy if kind == "sudo" and "chmod" in cmd),
            None,
        )
        assert perm_call is not None
        assert tasks.constants.REMOTE_LITESTREAM_CONFIG_PATH in perm_call


@allure.epic("Deploy")
@allure.feature("setup-replica: replica apt + litestream dir builders")
class TestSetupReplicaScripts:
    """``inv setup-replica`` wires several pure-shell builders
    together; two of them (swap, ssh-hardening) are pinned in their
    own classes, the remaining two (apt, litestream dir) are pinned
    here. A regression in either silently corrupts the replica's
    ability to receive Litestream WAL segments: the apt step blocks
    forever on a debconf prompt, or the directory lands with wrong
    perms and ``sftp`` cannot write the ``generations/`` tree.
    """

    def test_apt_runs_noninteractive_so_debconf_cannot_hang(self):
        """On a fresh Ubuntu cloud image ``apt-get install`` can block
        on a postfix/grub debconf dialog. ``DEBIAN_FRONTEND=
        noninteractive`` is what keeps the bootstrap hands-off — a
        refactor that dropped it would reintroduce a class of
        "inv setup-replica hangs forever" incidents.
        """
        script = tasks.backups_replica._build_setup_replica_packages_script()
        assert "export DEBIAN_FRONTEND=noninteractive" in script

    def test_apt_installs_unattended_upgrades(self):
        """Unattended security patches are the only automated channel
        replica VMs have for CVE coverage — nobody runs ``inv deploy``
        on the replica. Pin the package name so a rename in the apt
        step doesn't quietly remove the patch cadence.
        """
        script = tasks.backups_replica._build_setup_replica_packages_script()
        assert "apt-get install -y -qq unattended-upgrades" in script

    def test_apt_refreshes_package_index_before_install(self):
        """``apt-get update`` must run before ``apt-get install`` —
        without it, a cloud image with a stale package index fails
        ``install`` with ``Unable to locate package`` on any
        newly-mirrored dependency.
        """
        script = tasks.backups_replica._build_setup_replica_packages_script()
        update_idx = script.index("apt-get update -qq")
        install_idx = script.index("apt-get install -y -qq unattended-upgrades")
        assert update_idx < install_idx

    def test_apt_script_elevates_whole_block(self):
        """``apt`` steps each need root — the outer
        ``sudo bash <<HEREDOC`` is the elevation boundary; a
        semicolon-chain ``sudo apt-get update; apt-get install`` would
        only elevate the first command and the install would fail
        with EACCES.
        """
        script = tasks.backups_replica._build_setup_replica_packages_script()
        assert script.startswith("sudo bash <<'DINARY_REPLICA_PKG_EOF'\n")
        assert script.rstrip().endswith("DINARY_REPLICA_PKG_EOF")

    def test_litestream_dir_path_is_the_canonical_constant(self):
        """The path baked into the bootstrap script MUST match
        :data:`REPLICA_LITESTREAM_DIR` — the ``inv
        litestream-setup`` replica URL on VM1 (``sftp://.../var/lib/litestream``) points
        at the same string. A silent drift here would let the
        bootstrap succeed and the first WAL push fail with "No such
        file or directory" on the remote end.
        """
        script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        assert tasks.constants.REPLICA_LITESTREAM_DIR == "/var/lib/litestream"
        assert f"mkdir -p {tasks.constants.REPLICA_LITESTREAM_DIR}" in script

    def test_litestream_dir_mode_is_0750_not_world_readable(self):
        """The replica stream contains full pre-compaction row data
        (amounts, descriptions) — we do NOT want it world-readable
        on a shared VM. ``0750`` lets the ``ubuntu`` group members
        read for diagnostics while keeping "other" out.
        """
        script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        assert f"chmod 750 {tasks.constants.REPLICA_LITESTREAM_DIR}" in script
        assert "chmod 755" not in script
        assert "chmod 777" not in script

    def test_litestream_dir_owned_by_ubuntu(self):
        """Litestream on VM1 connects as ``ubuntu`` over SFTP; the
        receive directory on VM2 must be ``ubuntu``-owned or the very
        first WAL segment write fails with EPERM. Pin ``ubuntu:ubuntu``
        so a refactor to ``root:root`` is caught at review time.
        """
        script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        assert f"chown ubuntu:ubuntu {tasks.constants.REPLICA_LITESTREAM_DIR}" in script

    def test_litestream_dir_script_elevates_whole_block(self):
        """``mkdir -p /var/lib/litestream`` and ``chown ubuntu:ubuntu``
        both require root. The outer ``sudo bash <<HEREDOC`` is the
        single elevation boundary; a bare ``mkdir`` would fail with
        EACCES on ``/var/lib/``.
        """
        script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        assert script.startswith("sudo bash <<'DINARY_REPLICA_DIR_EOF'\n")
        assert script.rstrip().endswith("DINARY_REPLICA_DIR_EOF")

    def test_litestream_dir_script_verifies_final_state(self):
        """A trailing ``ls -ld`` on the provisioned directory surfaces
        the mode/owner in ``inv setup-replica`` output. If a silent
        umask on the remote rewrote the perms, the operator sees the
        drift immediately instead of discovering it later when the
        first SFTP write fails.
        """
        script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        assert f"ls -ld {tasks.constants.REPLICA_LITESTREAM_DIR}" in script


@allure.epic("Deploy")
@allure.feature("setup-replica: bootstrap orchestration")
class TestSetupReplicaTask:
    """The ``setup-replica`` task is a linear composition of:

    1. Five VM2 bootstrap steps (packages, ssh-hardening, fail2ban,
       litestream directory, swap).
    2. One trust step on VM2 (append VM1 pubkey to
       ``~ubuntu/.ssh/authorized_keys``).
    3. VM1 side: ensure the replica key exists, add VM2's host key
       to ``known_hosts``, install Litestream, write the config,
       fix permissions, enable the service, verify it runs.

    VM2's sshd is intentionally NOT restricted to Tailscale — the
    operator needs to reach it by public IP. Trust is installed
    once; a VM re-provision is handled by the explicit
    ``inv replica-reset-trust`` task.

    Order matters: packages first so ``unattended-upgrades`` is the
    first unit installed; trust strictly before ``create_service``
    so the Litestream unit's first start finds SSH auth ready.
    """

    @pytest.fixture
    def _spy(self, monkeypatch, tmp_path):
        class Spy:
            replica_calls: list[str]
            ssh_calls: list[str]
            sudo_calls: list[str]
            capture_calls: list[str]

            def __init__(self) -> None:
                self.replica_calls = []
                self.ssh_calls = []
                self.sudo_calls = []
                self.capture_calls = []

        spy = Spy()
        spy.yandex_calls = []  # type: ignore[attr-defined]

        def fake_ssh_replica(_c, cmd: str) -> None:
            spy.replica_calls.append(cmd)

        def fake_ssh_run(_c, cmd: str) -> None:
            spy.ssh_calls.append(cmd)

        def fake_ssh_sudo(_c, cmd: str) -> None:
            spy.sudo_calls.append(cmd)

        def fake_ssh_capture(_c, cmd: str) -> str:
            spy.capture_calls.append(cmd)
            return _FAKE_PUBKEY + "\n"

        def fake_write_remote_file(_c, _path: str, _content: str) -> None:
            pass

        def fake_create_service(*_args, **_kwargs) -> None:
            pass

        def fake_ensure_yandex(_c) -> None:
            spy.yandex_calls.append("ensure")  # type: ignore[attr-defined]

        deploy_dir = tmp_path / ".deploy"
        deploy_dir.mkdir()
        (deploy_dir / ".env").write_text(
            "# TestSetupReplicaTask fixture — not a real deploy env\n"
            "DINARY_DEPLOY_HOST=ubuntu@test-primary\n"
            "DINARY_REPLICA_HOST=ubuntu@test-replica\n"
            "DINARY_TUNNEL=tailscale\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        def fake_write_remote_replica_file(_c, _path: str, _content: str) -> None:
            pass

        monkeypatch.setattr(tasks.backups_replica, "ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(tasks.backups_replica, "ssh_run", fake_ssh_run)
        monkeypatch.setattr(tasks.backups_replica, "ssh_sudo", fake_ssh_sudo)
        monkeypatch.setattr(tasks.backups_replica, "ssh_capture", fake_ssh_capture)
        monkeypatch.setattr(tasks.backups_replica, "write_remote_file", fake_write_remote_file)
        monkeypatch.setattr(
            tasks.backups_replica, "write_remote_replica_file", fake_write_remote_replica_file
        )
        monkeypatch.setattr(tasks.backups_replica, "create_service", fake_create_service)
        monkeypatch.setattr(
            tasks.backups_replica,
            "ensure_yandex_rclone_configured",
            fake_ensure_yandex,
        )
        monkeypatch.setattr(
            tasks.backups_replica,
            "replica_host",
            lambda: "ubuntu@dinary-replica",
        )
        return spy

    def test_runs_all_twelve_replica_steps(self, _spy):
        """Dropping any VM2 step leaves the replica in a
        half-configured state:

        * no ``unattended-upgrades`` → no CVE cadence
        * no ssh-hardening → root-login regression vs. primary
        * no fail2ban → brute-force exposure
        * no ``/var/lib/litestream`` → Litestream push fails
        * no swap → OOM during dpkg spikes on 956 MiB RAM
        * no backup_packages → dinary-backup.service fails (no rclone/sqlite3/zstd)
        * no litestream on VM2 → litestream restore in backup fails
        * no chmod backup script → backup cron fails with EACCES
        * no chmod retention script → GFS pruning fails with EACCES
        * no daemon-reload → systemd ignores new service/timer
        * no enable timer → daily backup never runs
        * no authorized_key → Litestream SFTP handshake fails
        """
        tasks.setup_replica.body(MagicMock())
        assert len(_spy.replica_calls) == 12

    def test_bootstrap_order_is_stable(self, _spy):
        """Order is load-bearing:

        1. ``apt`` first so ``unattended-upgrades`` is active before
           anything else sits on the box.
        2. ssh hardening (X11 off, PermitRootLogin no, key wipe).
        3. fail2ban — needs apt, must be active before the box
           starts accepting external traffic.
        4. litestream dir (pure FS, no network).
        5. swap (OOM guard during backup apt install on 956 MiB RAM VM).
        6. backup_packages — rclone/sqlite3/zstd for daily Yadisk pipeline.
        7. litestream binary on VM2 for ``litestream restore`` in backup.
        8. chmod backup script.
        9. chmod retention script.
        10. daemon-reload — must precede enable so systemd sees new units.
        11. enable timer.
        12. authorized_key install — VM1 pubkey lands LAST so the
            Litestream replicator can SFTP in immediately after this
            function returns and the VM1-side steps run.
        """
        tasks.setup_replica.body(MagicMock())
        pkg_script = tasks.backups_replica._build_setup_replica_packages_script()
        harden_script = tasks.ssh_utils.build_harden_sshd_script()
        f2b_script = tasks.ssh_utils.build_install_fail2ban_script()
        dir_script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        swap_script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        backup_pkg_script = tasks.backups_replica._build_setup_replica_backup_packages_script()
        litestream_script = tasks.ssh_utils.litestream_install_script()
        trust_script = tasks.ssh_utils.build_install_authorized_key_script(_FAKE_PUBKEY)
        assert _spy.replica_calls == [
            pkg_script,
            harden_script,
            f2b_script,
            dir_script,
            swap_script,
            backup_pkg_script,
            litestream_script,
            f"sudo chmod 0755 {tasks.constants.BACKUP_SCRIPT_PATH}",
            f"sudo chmod 0755 {tasks.constants.BACKUP_RETENTION_SCRIPT_PATH}",
            "sudo systemctl daemon-reload",
            "sudo systemctl enable --now dinary-backup.timer",
            trust_script,
        ]

    def test_does_not_rebind_replica_sshd_to_tailscale(self, _spy):
        """``setup-replica`` must NOT restrict replica sshd to Tailscale.
        The operator reaches VM2 by its public IP (the same one in
        ``DINARY_REPLICA_HOST``); a tailscale-only drop-in landed
        here would silently lock them out on the next deploy.
        """
        tasks.setup_replica.body(MagicMock())
        ts_script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert ts_script not in _spy.replica_calls
        assert ts_script not in _spy.ssh_calls

    def test_swap_size_is_forwarded(self, _spy):
        """A replica on a fatter shape should be able to opt up; the
        ``--swap-size-gb`` flag must reach ``_build_setup_swap_script``
        unchanged, not get silently coerced back to 1.
        """
        tasks.setup_replica.body(MagicMock(), swap_size_gb=4)
        swap_script = next(
            (c for c in _spy.replica_calls if "fallocate" in c),
            None,
        )
        assert swap_script is not None
        assert "fallocate -l 4G /swapfile" in swap_script

    def test_swap_size_defaults_to_one_gigabyte(self, _spy):
        """The Always Free VM2 shape (E2.1.Micro, 956 MiB RAM) needs
        a 1 GB swap minimum to survive ``apt-get upgrade`` under
        concurrent Litestream SFTP sessions. Pinning the default
        guards against a refactor that drops the kwarg default.
        """
        tasks.setup_replica.body(MagicMock())
        swap_script = next(
            (c for c in _spy.replica_calls if "fallocate" in c),
            None,
        )
        assert swap_script is not None
        assert "fallocate -l 1G /swapfile" in swap_script

    def test_trust_step_installs_vm1_pubkey_on_vm2(self, _spy):
        """The pubkey fetched from VM1 must land verbatim inside the
        shell payload sent to VM2 — a truncation or re-quoting bug
        would produce ``Permission denied (publickey)`` on the first
        Litestream SFTP connect with no obvious cause.
        """
        tasks.setup_replica.body(MagicMock())
        trust_call = next(
            (c for c in _spy.replica_calls if _FAKE_PUBKEY in c),
            None,
        )
        assert trust_call is not None, "pubkey must land verbatim in an ssh_replica payload"
        assert "authorized_keys" in trust_call

    def test_trust_step_fetches_vm1_pubkey_via_ssh_capture(self, _spy):
        """Pubkey fetch must use ``ssh_capture`` — ``ssh_run`` drops
        stdout, so a refactor that routed the keygen through
        ``ssh_run`` would leave the authorized_keys step operating
        on an empty string and silently succeed with no trust
        installed.
        """
        tasks.setup_replica.body(MagicMock())
        assert any("ssh-keygen -t ed25519" in c for c in _spy.capture_calls), (
            "VM1 pubkey fetch must use ssh_capture"
        )

    def test_calls_ensure_yandex_rclone_configured(self, _spy):
        """``setup-replica`` must invoke the Yandex.Disk rclone
        bootstrap so a fresh VM2 actually prompts the operator for
        credentials. A regression that dropped this call silently
        leaves the off-site backup path un-configured — the replica
        boots, Litestream replicates, and the nightly
        ``dinary-backup.service`` fails against a missing ``yandex:``
        remote. Pinning the call here keeps the interactive credential
        prompt on the bootstrap path.
        """
        tasks.setup_replica.body(MagicMock())
        assert _spy.yandex_calls == ["ensure"], (
            "setup_replica must call ensure_yandex_rclone_configured exactly once"
        )

    def test_yandex_bootstrap_runs_before_litestream_service(self, _spy):
        """The Yandex.Disk prompt is interactive; if we ran it *after*
        ``create_service`` started Litestream, a typo-driven retry
        would leave the replicator pushing while credentials are still
        being typed — noisy and pointless. Pin ordering so the
        interactive step is finished before VM1's Litestream goes live.
        """
        call_log: list[str] = []

        def observe_yandex(_c) -> None:
            call_log.append("yandex")

        def observe_create(*_a, **_kw) -> None:
            call_log.append("create_service")

        import tasks.backups_replica as br

        br_module = br
        orig_create = br_module.create_service
        orig_yandex = br_module.ensure_yandex_rclone_configured
        orig_write_replica = br_module.write_remote_replica_file
        br_module.create_service = observe_create
        br_module.ensure_yandex_rclone_configured = observe_yandex
        br_module.write_remote_replica_file = lambda *_a, **_kw: None
        try:
            tasks.setup_replica.body(MagicMock())
        finally:
            br_module.create_service = orig_create
            br_module.ensure_yandex_rclone_configured = orig_yandex
            br_module.write_remote_replica_file = orig_write_replica
        assert call_log == ["yandex", "create_service"]

    def test_trust_step_adds_vm2_host_key_to_vm1_known_hosts(self, _spy):
        """VM1 Litestream connects to VM2 by the exact host string in
        ``litestream.yml``. ``known_hosts`` on VM1 must have an entry
        for that string BEFORE the Litestream service starts, or
        every SFTP handshake fails with ``StrictHostKeyChecking yes``
        (the default on modern OpenSSH).
        """
        tasks.setup_replica.body(MagicMock())
        host_script = tasks.ssh_utils.build_add_known_host_script("dinary-replica")
        assert host_script in _spy.ssh_calls


@allure.epic("Deploy")
@allure.feature("setup-replica: setup-reset-trust task")
class TestReplicaResetTrustTask:
    """``inv setup-reset-trust`` is the operator's explicit
    statement that a VM (VM1 or VM2) was legitimately re-provisioned
    and the new SSH host key / pubkey pairing should be accepted.
    Pinning the two destructive operations (known_hosts reset,
    pubkey re-install) keeps the task aligned with the ``inv
    setup-replica`` trust step so a future refactor of one side
    cannot silently diverge from the other.
    """

    @pytest.fixture
    def _spy(self, monkeypatch, tmp_path):
        class Spy:
            replica_calls: list[str]
            ssh_calls: list[str]
            capture_calls: list[str]

            def __init__(self) -> None:
                self.replica_calls = []
                self.ssh_calls = []
                self.capture_calls = []

        spy = Spy()

        def fake_ssh_replica(_c, cmd: str) -> None:
            spy.replica_calls.append(cmd)

        def fake_ssh_run(_c, cmd: str) -> None:
            spy.ssh_calls.append(cmd)

        def fake_ssh_capture(_c, cmd: str) -> str:
            spy.capture_calls.append(cmd)
            return _FAKE_PUBKEY + "\n"

        monkeypatch.setattr(tasks.backups_replica, "ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(tasks.backups_replica, "ssh_run", fake_ssh_run)
        monkeypatch.setattr(tasks.backups_replica, "ssh_capture", fake_ssh_capture)
        monkeypatch.setattr(
            tasks.backups_replica,
            "replica_host",
            lambda: "ubuntu@dinary-replica",
        )
        return spy

    def test_wipes_and_re_adds_vm2_host_key(self, _spy):
        """Reset-trust must force-remove VM2's existing known_hosts
        entry before ``ssh-keyscan``. Without the ``ssh-keygen -R``,
        OpenSSH would keep matching the old entry and refuse the new
        host key with ``REMOTE HOST IDENTIFICATION HAS CHANGED``.
        """
        tasks.replica_reset_trust.body(MagicMock())
        reset_script = tasks.ssh_utils.build_reset_known_host_script("dinary-replica")
        assert reset_script in _spy.ssh_calls

    def test_reinstalls_pubkey_on_vm2(self, _spy):
        """Reset-trust also covers the VM1-reinstalled case: the VM1
        pubkey may be new, so it must be re-pushed to VM2. The
        installer is the SAME idempotent builder as setup-replica
        uses — a parallel implementation here would drift under
        refactors and silently diverge.
        """
        tasks.replica_reset_trust.body(MagicMock())
        install_script = tasks.ssh_utils.build_install_authorized_key_script(_FAKE_PUBKEY)
        assert install_script in _spy.replica_calls

    def test_does_not_touch_the_database(self, _spy):
        """Reset-trust MUST NOT wipe the replica DB or restart
        litestream — that is ``inv setup-resync``'s job. Mixing
        the two would make a trust refresh accidentally destroy
        WAL state.
        """
        tasks.replica_reset_trust.body(MagicMock())
        all_calls = _spy.replica_calls + _spy.ssh_calls
        joined = "\n".join(all_calls)
        assert "rm -f /var/lib/litestream" not in joined
        assert "systemctl stop litestream" not in joined
        assert "systemctl start litestream" not in joined


@allure.epic("Deploy")
@allure.feature("setup-replica: setup-resync task")
class TestSetupResyncTask:
    """``inv setup-resync`` resets the replica WAL position after a
    primary DB restore or txid mismatch.  Litestream runs on VM1
    (not VM2), so the correct sequence is: stop replicator on VM1,
    wipe the LTX tree on VM2, start replicator on VM1.
    """

    @pytest.fixture
    def _spy(self, monkeypatch):
        class Spy:
            replica_calls: list[str]
            ssh_calls: list[str]

            def __init__(self) -> None:
                self.replica_calls = []
                self.ssh_calls = []

        spy = Spy()

        monkeypatch.setattr(
            tasks.backups_replica, "ssh_replica", lambda _c, cmd: spy.replica_calls.append(cmd)
        )
        monkeypatch.setattr(
            tasks.backups_replica, "ssh_run", lambda _c, cmd: spy.ssh_calls.append(cmd)
        )
        monkeypatch.setattr(tasks.backups_replica, "replica_host", lambda: "ubuntu@dinary-replica")
        return spy

    def test_stops_litestream_on_vm1_not_vm2(self, _spy):
        """Litestream only runs on VM1 — sending stop to VM2 is a no-op
        that silently leaves the replicator pushing while the LTX tree
        is being wiped, causing WAL corruption.
        """
        tasks.replica_resync.body(MagicMock())
        assert any("systemctl stop litestream" in c for c in _spy.ssh_calls), (
            "stop must target VM1 (ssh_run), not VM2"
        )
        assert not any("systemctl stop litestream" in c for c in _spy.replica_calls), (
            "stop must NOT target VM2 (ssh_replica)"
        )

    def test_wipes_ltx_tree_on_vm2(self, _spy):
        """The LTX tree on VM2 must be removed so litestream on VM1
        pushes a fresh snapshot. Leaving stale LTX behind means
        litestream re-syncs to the old txid and the restored primary
        diverges from the replica immediately.
        """
        tasks.replica_resync.body(MagicMock())
        wipe_call = next(
            (c for c in _spy.replica_calls if "rm -rf" in c and "litestream" in c),
            None,
        )
        assert wipe_call is not None, "must wipe LTX tree on VM2 via ssh_replica"
        assert tasks.constants.REPLICA_LITESTREAM_DIR in wipe_call
        assert tasks.constants.REPLICA_DB_NAME in wipe_call

    def test_starts_litestream_on_vm1_after_wipe(self, _spy):
        """Litestream must restart on VM1 AFTER the LTX tree is wiped —
        starting before the wipe means the replicator sees existing LTX
        and skips the fresh-push it needs to do.
        """
        tasks.replica_resync.body(MagicMock())
        stop_idx = next(
            (i for i, c in enumerate(_spy.ssh_calls) if "systemctl stop litestream" in c),
            None,
        )
        start_idx = next(
            (i for i, c in enumerate(_spy.ssh_calls) if "systemctl start litestream" in c),
            None,
        )
        assert stop_idx is not None
        assert start_idx is not None
        assert stop_idx < start_idx, "start must come after stop on VM1"
