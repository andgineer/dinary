"""Tests for ``inv setup-replica`` orchestration and its scripts.

Three layers, all routed through :mod:`tasks.backups_replica`:

* :class:`TestLitestreamSetupPermissions` pins the ``sudo`` scope of
  the ``/etc/litestream.yml`` chown/chmod step (regression for a
  split-scope bug that left ``chmod`` running as ``ubuntu``).
* :class:`TestSetupReplicaScripts` pins the apt and litestream-dir
  shell builders.
* :class:`TestSetupReplicaTask` pins the four-step composition the
  task itself emits.
"""

from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.backups_replica
import tasks.constants
import tasks.ssh_utils


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

        def fake_write_remote_file(_c, _path: str, _content: str) -> None:
            calls.append(("write", _path))

        def fake_create_service(*_args, **_kwargs) -> None:
            calls.append(("service", "litestream"))

        config = tmp_path / "litestream.yml"
        config.write_text("snapshot: {interval: 1h, retention: 168h}\n")

        monkeypatch.setattr(tasks.backups_replica, "ssh_run", fake_ssh)
        monkeypatch.setattr(tasks.backups_replica, "ssh_sudo", fake_ssh_sudo)
        monkeypatch.setattr(tasks.backups_replica, "ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(tasks.backups_replica, "write_remote_file", fake_write_remote_file)
        monkeypatch.setattr(tasks.backups_replica, "create_service", fake_create_service)
        monkeypatch.setattr(tasks.backups_replica, "LOCAL_LITESTREAM_CONFIG_PATH", str(config))
        monkeypatch.setattr(tasks.backups_replica, "replica_host", lambda: "ubuntu@dinary-replica")
        return calls

    def test_chown_and_chmod_run_inside_a_single_sudo_bash_c(self, _spy):
        """The compound command must be ``bash -c '... && ...'`` so
        the outer ``sudo`` (prepended by :func:`_ssh_sudo`) elevates
        the entire bash invocation, not just the first word of the
        pipeline. A bare ``&&`` chain would leave ``chmod`` running
        as the SSH user.
        """
        tasks.setup_replica.body(MagicMock(), no_swap=True, no_tailscale=True)
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
        tasks.setup_replica.body(MagicMock(), no_swap=True, no_tailscale=True)
        perm_call = next(
            (cmd for kind, cmd in _spy if kind == "sudo" and "chmod" in cmd),
            None,
        )
        assert perm_call is not None
        assert tasks.constants.REMOTE_LITESTREAM_CONFIG_PATH in perm_call


@allure.epic("Deploy")
@allure.feature("setup-replica: replica apt + litestream dir builders")
class TestSetupReplicaScripts:
    """``inv setup-replica`` wires four pure-shell builders together;
    two of them (swap, ssh-tailscale-only) are pinned in their own
    classes above, the remaining two (apt, litestream dir) are pinned
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
        :data:`REPLICA_LITESTREAM_DIR` — the ``inv setup-replica``
        replica URL on VM1 (``sftp://.../var/lib/litestream``) points
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
    """The ``setup-replica`` task is a linear composition of the four
    builders pinned above: apt, litestream dir, swap, ssh-tailscale-
    only. The composition itself is the contract — the order matters
    (packages before swap so ``unattended-upgrades`` is the first
    unit installed, ssh-tailscale-only strictly last because it is
    the only step that can lock the operator out if a predecessor
    has failed silently). These tests pin the composition without
    executing any shell.
    """

    @pytest.fixture
    def _spy(self, monkeypatch, tmp_path):
        """Capture every ``_ssh_replica`` payload in order so we can
        assert the exact sequence the task emits. ``DINARY_REPLICA_HOST``
        is stubbed so ``_replica_host`` does not read
        ``.deploy/.env``.

        ``setup-replica`` also requires ``.deploy/litestream.yml`` and
        ``ssh_run`` reads ``.deploy/.env`` via :func:`tasks.env.host`;
        CI sandboxes omit both, so we create minimal stubs and chdir
        into ``tmp_path``.
        """

        class Spy:
            calls: list[str]

            def __init__(self) -> None:
                self.calls = []

        spy = Spy()

        def fake_ssh_replica(_c, cmd: str) -> None:
            spy.calls.append(cmd)

        deploy_dir = tmp_path / ".deploy"
        deploy_dir.mkdir()
        (deploy_dir / "litestream.yml").write_text(
            "# test stub — not a real Litestream config\n",
            encoding="utf-8",
        )
        (deploy_dir / ".env").write_text(
            "# TestSetupReplicaTask fixture — not a real deploy env\n"
            "DINARY_DEPLOY_HOST=ubuntu@test-primary\n"
            "DINARY_REPLICA_HOST=ubuntu@test-replica\n"
            "DINARY_TUNNEL=tailscale\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(tasks.backups_replica, "ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(
            tasks.backups_replica,
            "replica_host",
            lambda: "ubuntu@dinary-replica",
        )
        return spy

    def test_runs_all_four_bootstrap_steps(self, _spy):
        """The task must dispatch all four steps — dropping any one
        would leave the replica in a half-configured state (e.g. no
        ``/var/lib/litestream`` → Litestream push fails; no
        ssh-tailscale-only → public 22 stays exposed).
        """
        tasks.setup_replica.body(MagicMock())
        assert len(_spy.calls) == 4

    def test_packages_first_swap_third_ssh_lock_last(self, _spy):
        """Order is load-bearing:

        1. ``apt`` first so ``unattended-upgrades`` is active before
           anything else sits on the box.
        2. litestream dir second (pure FS, no network, no lockout risk).
        3. swap third (needed for ``unattended-upgrades`` dpkg spikes
           on a 956 MiB RAM VM to avoid OOM).
        4. ssh-tailscale-only LAST — any earlier failure must be
           diagnosable over the still-open public 22 path; once this
           step lands, only tailnet/serial-console works.
        """
        tasks.setup_replica.body(MagicMock())
        pkg_script = tasks.backups_replica._build_setup_replica_packages_script()
        dir_script = tasks.backups_replica._build_setup_replica_litestream_dir_script()
        swap_script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        ssh_script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert _spy.calls == [pkg_script, dir_script, swap_script, ssh_script]

    def test_swap_size_is_forwarded(self, _spy):
        """A replica on a fatter shape should be able to opt up; the
        ``--swap-size-gb`` flag must reach ``_build_setup_swap_script``
        unchanged, not get silently coerced back to 1.
        """
        tasks.setup_replica.body(MagicMock(), swap_size_gb=4)
        swap_script = next(
            (c for c in _spy.calls if "fallocate" in c),
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
            (c for c in _spy.calls if "fallocate" in c),
            None,
        )
        assert swap_script is not None
        assert "fallocate -l 1G /swapfile" in swap_script

    def test_reuses_the_same_ssh_tailscale_only_script_as_the_app_server(
        self,
        _spy,
    ):
        """VM2 and VM1 must apply *byte-identical* ssh-tailscale-only
        payloads; a divergent copy on the replica path would let a
        hardening change land on one host and silently skip the
        other. The task must call the shared builder, not inline a
        parallel implementation.
        """
        tasks.setup_replica.body(MagicMock())
        assert _spy.calls[-1] == tasks.ssh_utils.build_ssh_tailscale_only_script()
