"""Tests for the pure-shell builders in :mod:`tasks.ssh_utils`.

The three remote-bootstrap scripts the deploy task layer composes:

* ``litestream_install_script`` — arch-aware ``.deb`` download + install.
* ``build_setup_swap_script`` — persistent swapfile provisioner.
* ``build_ssh_tailscale_only_script`` — rebind sshd to Tailscale + loopback.

Sibling :file:`test_tasks_ssh_utils.py` covers the smaller helpers
(``systemd_quote``, ``remote_snapshot_cmd``, ``ssh_capture_bytes``).
"""

import allure
import pytest

import tasks.constants
import tasks.env
import tasks.ssh_utils


@allure.epic("Deploy")
@allure.feature("Litestream install script: arch-to-asset mapping")
class TestLitestreamInstallScript:
    """``inv setup-replica`` downloads a Litestream ``.deb`` whose
    filename suffix depends on the remote VM's CPU. Oracle Free Tier
    ships both x86_64 Micro and Ampere (arm64) shapes, so a typo or
    silent drift between the pinned version and the published asset
    names would only surface at the next VM bootstrap — weeks or
    months after the change lands. These tests pin:

    * the pinned ``LITESTREAM_VERSION`` that the release URL interpolates,
    * the canonical ``uname -m`` → asset-suffix mapping (Litestream's
      release assets use ``x86_64`` / ``arm64``, which are NOT the
      dpkg ``amd64`` / ``arm64`` spellings),
    * a clean, actionable failure on unsupported architectures.
    """

    def test_default_version_matches_pinned_constant(self):
        script = tasks.ssh_utils.litestream_install_script()
        assert f"litestream-{tasks.constants.LITESTREAM_VERSION}-linux-x86_64.deb" in script
        assert f"litestream-{tasks.constants.LITESTREAM_VERSION}-linux-arm64.deb" in script

    def test_x86_64_and_amd64_both_map_to_x86_64_asset(self):
        """``uname -m`` historically varies: Linux kernels on Intel
        report ``x86_64``, but some embedded userlands and Debian
        dpkg spelling use ``amd64``. Both must route to the same
        Litestream asset.
        """
        script = tasks.ssh_utils.litestream_install_script()
        assert (
            f"x86_64|amd64) ASSET=litestream-{tasks.constants.LITESTREAM_VERSION}-linux-x86_64.deb"
            in script
        )

    def test_aarch64_and_arm64_both_map_to_arm64_asset(self):
        """Same double-spelling problem on Ampere / Graviton:
        Linux kernels report ``aarch64``, Debian userland prefers
        ``arm64``. Both must pick the arm64 asset.
        """
        script = tasks.ssh_utils.litestream_install_script()
        assert (
            f"aarch64|arm64) ASSET=litestream-{tasks.constants.LITESTREAM_VERSION}-linux-arm64.deb"
            in script
        )

    def test_unsupported_arch_exits_with_actionable_error(self):
        """An unsupported ``uname -m`` (e.g. ``riscv64``) must error
        out loudly with the offending arch and the pinned version,
        not silently ``curl 404`` a non-existent asset.
        """
        script = tasks.ssh_utils.litestream_install_script()
        assert (
            f"Unsupported arch $ARCH for litestream {tasks.constants.LITESTREAM_VERSION}" in script
        )
        assert "*) echo" in script
        assert "exit 1" in script

    def test_download_url_uses_github_release_path_for_pinned_version(self):
        """The asset URL is ``<.../releases/download/v<ver>/$ASSET>``
        (upstream's canonical layout) — a typo in the ``v`` prefix or
        the path layout here is invisible until bootstrap day.
        """
        script = tasks.ssh_utils.litestream_install_script()
        assert (
            "https://github.com/benbjohnson/litestream/releases/download/"
            f"v{tasks.constants.LITESTREAM_VERSION}/$ASSET" in script
        )

    def test_script_is_idempotent_when_litestream_already_installed(self):
        """Re-running ``inv setup-replica`` must be cheap: no new
        download when the binary is already on PATH. The outer
        ``if command -v litestream`` gate is the only thing
        preserving that property — pin it.
        """
        script = tasks.ssh_utils.litestream_install_script()
        assert "if ! command -v litestream >/dev/null" in script

    def test_version_parameter_allows_future_upgrade(self):
        """Pure-helper ergonomics: passing a different version
        interpolates cleanly into every line that mentions it, so a
        future upgrade is a one-line constant bump rather than a
        string-surgery PR.
        """
        script = tasks.ssh_utils.litestream_install_script(version="0.6.0")
        assert "litestream-0.6.0-linux-x86_64.deb" in script
        assert "litestream-0.6.0-linux-arm64.deb" in script
        assert "/releases/download/v0.6.0/$ASSET" in script
        # Sanity: the pinned-default version is NOT leaking into a
        # caller-overridden script.
        assert f"litestream-{tasks.constants.LITESTREAM_VERSION}" not in script


@allure.epic("Deploy")
@allure.feature("setup-swap: persistent swapfile provisioner")
class TestSetupSwapScript:
    """``inv setup-server --swap-size-gb N`` is the only mechanism that provisions swap
    on the Oracle Free Tier VMs, which ship with zero swap and
    ~956 MiB of RAM. A silent regression here (wrong size, forgotten
    fstab entry, broken idempotency) would surface weeks later as an
    OOM-killed ``dinary.service`` during a heavy import. These tests
    pin the script's observable contract so the next reviewer does
    not have to re-derive it.
    """

    def test_default_allocates_one_gigabyte(self):
        """Default swap size is 1 GB — matches the Always Free VM
        profile (enough headroom for ``uv sync`` / bulk import
        spikes without eating meaningful disk on a 45 GB root fs).
        """
        script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        assert "fallocate -l 1G /swapfile" in script

    def test_size_parameter_interpolates_into_fallocate(self):
        """Operators on a fatter shape can opt up; the size must
        land verbatim in the ``fallocate`` line, not just a format
        placeholder.
        """
        script = tasks.ssh_utils.build_setup_swap_script(size_gb=4)
        assert "fallocate -l 4G /swapfile" in script
        assert "fallocate -l 1G" not in script

    def test_rejects_nonpositive_size(self):
        """``fallocate -l 0G`` silently succeeds with a zero-byte
        file that ``mkswap`` then rejects — the error message from
        ``mkswap`` is cryptic. Fail fast with a clear local error
        before we even build the script.
        """
        with pytest.raises(ValueError, match="size_gb must be a positive integer"):
            tasks.ssh_utils.build_setup_swap_script(size_gb=0)
        with pytest.raises(ValueError, match="size_gb must be a positive integer"):
            tasks.ssh_utils.build_setup_swap_script(size_gb=-1)

    def test_idempotent_on_reapply(self):
        """The swapon-check short-circuits allocation when
        ``/swapfile`` is already active. Without this, a second
        ``inv setup-server`` run would ``fallocate`` a fresh file on top
        of the live one and ``mkswap`` would corrupt the signature
        of the currently-swapped backing store.
        """
        script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        assert "swapon --show=NAME --noheadings" in script
        assert "grep -qx /swapfile" in script
        assert "/swapfile already active, skipping allocation" in script

    def test_fstab_line_is_deduplicated(self):
        """The fstab append uses ``grep -qxF || echo >>`` so
        re-running never accumulates duplicate entries — otherwise
        every ``inv setup-server`` would grow ``/etc/fstab`` by a line and
        the system would eventually refuse to mount.
        """
        script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        assert "/swapfile none swap sw 0 0" in script
        assert 'grep -qxF "$FSTAB_LINE" /etc/fstab || echo "$FSTAB_LINE" >> /etc/fstab' in script

    def test_elevation_wraps_entire_block_not_just_first_command(self):
        """Every step (``fallocate`` / ``chmod`` / ``mkswap`` /
        ``swapon`` / fstab edit) needs root. ``sudo bash <<HEREDOC``
        elevates the whole block in one call; a plain semicolon
        chain prefixed with ``sudo`` would only elevate the first
        command and the rest would fail with a permission error.
        """
        script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        assert script.startswith("sudo bash <<'DINARY_SWAP_EOF'\n")
        assert script.rstrip().endswith("DINARY_SWAP_EOF")

    def test_quoted_heredoc_prevents_local_variable_expansion(self):
        """Without ``<<'EOF'`` (quoted delimiter), the local shell
        would expand ``$FSTAB_LINE`` to an empty string *before*
        the script ever reached the remote, so the fstab would
        get ``grep -qxF "" /etc/fstab`` — a silent match that
        never appends the real entry.
        """
        script = tasks.ssh_utils.build_setup_swap_script(size_gb=1)
        assert "<<'DINARY_SWAP_EOF'" in script
        assert "$FSTAB_LINE" in script


@allure.epic("Deploy")
@allure.feature("ssh-tailscale-only: rebind sshd to tailnet ingress")
class TestSshTailscaleOnlyScript:
    """``inv setup-server --tailscale`` closes the public TCP/22 attack
    surface by rebinding sshd to the Tailscale IPv4 + loopback. A
    regression here is a lockout risk (operator cannot reach the VM
    except via Oracle Cloud's Serial Console), so these tests pin the
    script's observable contract: the pre-flight checks, the atomic
    sshd -t gate with rollback on failure, and the idempotent drop-in
    file layout.
    """

    def test_refuses_when_tailscale_is_not_installed(self):
        """Binding sshd to a non-existent tailscaled IP would silently
        kill inbound SSH entirely. Gate the flip on ``command -v
        tailscale`` before touching any config file.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert "command -v tailscale" in script
        assert "tailscale is not installed" in script

    def test_refuses_when_tailscaled_has_no_ipv4(self):
        """``tailscale`` binary being present is not enough — the
        daemon may still be logged out or starting. Require a
        non-empty ``tailscale ip -4`` output before the flip.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert 'TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"' in script
        assert 'if [ -z "$TS_IP" ]; then' in script
        assert "tailscaled is not up" in script

    def test_keeps_loopback_listen_address(self):
        """Loopback must stay bound so operators who reach the box via
        the Oracle Cloud Serial Console can still ``ssh 127.0.0.1``
        locally to trigger ``systemctl reload`` after rolling back a
        bad config.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert "ListenAddress 127.0.0.1:22" in script

    def test_binds_to_live_tailscale_ip_not_a_hardcoded_value(self):
        """The drop-in file must interpolate the *current* tailscale
        IPv4, not a stale value baked into the script. This guards
        against a subtle regression where a refactor replaces
        ``${TS_IP}`` with an IPv4 literal and the task stops
        self-healing after a Tailscale IP rotation.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert "ListenAddress ${TS_IP}:22" in script

    def test_inner_heredoc_is_unquoted_so_tsip_expands(self):
        """The inner ``cat >"$DROPIN" <<EOC`` delimiter is unquoted on
        purpose: bash must expand ``${TS_IP}`` when writing the file,
        otherwise the literal string ``${TS_IP}`` lands in
        ``sshd_config.d/`` and ``sshd -t`` rejects it. Complementary
        to the outer heredoc being quoted (checked below).
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert 'cat >"$DROPIN" <<EOC\n' in script
        assert "<<'EOC'" not in script

    def test_sshd_t_validates_before_reload(self):
        """``sshd -t`` must run *before* ``systemctl reload ssh``.
        Reloading on an invalid config would leave the service
        refusing new connections, and — combined with the public IP
        being closed — trap the operator outside the box.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        t_idx = script.index("sshd -t")
        reload_idx = script.index("systemctl reload ssh")
        assert t_idx < reload_idx

    def test_rejected_config_is_rolled_back(self):
        """If ``sshd -t`` fails, the drop-in must be removed — a
        persistent broken config would survive reboot and kill sshd
        on next service start. Without rollback the only recovery
        path is the Oracle Cloud Serial Console.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert 'rm -f "$DROPIN"' in script
        assert "sshd -t rejected the new config" in script

    def test_drop_in_path_and_idempotent_overwrite(self):
        """The canonical Ubuntu drop-in directory is honored, and the
        file is rewritten (``cat >``) on every run rather than
        appended — so a Tailscale IP rotation is absorbed by a simple
        replay.
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert "DROPIN=/etc/ssh/sshd_config.d/10-tailscale-only.conf" in script
        assert 'cat >"$DROPIN" <<EOC' in script
        assert 'cat >>"$DROPIN"' not in script

    def test_elevation_wraps_the_whole_block(self):
        """Writing into ``/etc/ssh/sshd_config.d/``, running
        ``sshd -t``, and ``systemctl reload ssh`` all require root;
        the outer ``sudo bash <<HEREDOC`` is the single elevation
        boundary that keeps these atomic (no partial apply if the
        operator's sudo timestamp expires mid-script).
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert script.startswith("sudo bash <<'DINARY_SSH_TS_EOF'\n")
        assert script.rstrip().endswith("DINARY_SSH_TS_EOF")

    def test_outer_heredoc_is_quoted_so_remote_vars_dont_expand_locally(self):
        """``<<'DINARY_SSH_TS_EOF'`` (quoted delimiter) means the local
        shell leaves ``$TS_IP`` / ``$DROPIN`` literal while it ships
        the script to the remote. Without the quotes both would
        expand to the empty string *before* ``_ssh`` even base64-
        encodes the payload, which would end up silently writing a
        file with ``ListenAddress :22`` (sshd rejects with a clear
        error — but we would still have lost the pre-flight checks
        along the way).
        """
        script = tasks.ssh_utils.build_ssh_tailscale_only_script()
        assert "<<'DINARY_SSH_TS_EOF'" in script
        assert "$TS_IP" in script
        assert "$DROPIN" in script


@allure.epic("Deploy")
@allure.feature("SSH hardening script (X11 off, PermitRootLogin no, root/opc key wipe)")
class TestHardenSshdScript:
    """Pins the cross-VM sshd hardening block. Regressions here would
    silently re-expose the dormant root/opc cloud-init seed key or
    leave X11Forwarding on — both caught on the old VM1 audit and
    must not re-appear on freshly provisioned VMs.
    """

    def test_disables_x11_forwarding_via_dropin(self):
        script = tasks.ssh_utils.build_harden_sshd_script()
        assert "X11Forwarding no" in script
        assert "/etc/ssh/sshd_config.d/no-x11.conf" in script

    def test_forces_permit_root_login_no(self):
        """``sed`` replaces whatever value cloud-init left (commented,
        ``prohibit-password``, ``without-password``) with an explicit
        ``no``. The pattern must handle both the commented and
        uncommented forms.
        """
        script = tasks.ssh_utils.build_harden_sshd_script()
        assert "PermitRootLogin no" in script
        assert "s/^#\\?PermitRootLogin" in script

    def test_wipes_root_and_opc_authorized_keys(self):
        """Oracle cloud-init defaults seed the same key under
        ``/root`` and ``/home/opc`` as the ``ubuntu`` user. A
        compromised laptop key would bypass the sudo audit trail
        unless we wipe both files on every setup run.
        """
        script = tasks.ssh_utils.build_harden_sshd_script()
        assert ": >/root/.ssh/authorized_keys" in script
        assert ": >/home/opc/.ssh/authorized_keys" in script

    def test_locks_opc_user_when_present(self):
        script = tasks.ssh_utils.build_harden_sshd_script()
        assert "usermod -L -s /usr/sbin/nologin opc" in script
        # Guarded so the script is safe on hosts that never had opc.
        assert "id opc" in script

    def test_validates_sshd_before_reload_and_rolls_back_on_failure(self):
        """If ``sshd -t`` rejects the new config we must remove the
        X11 drop-in before exiting, otherwise the next ``systemctl
        reload ssh`` would pick up a broken config.
        """
        script = tasks.ssh_utils.build_harden_sshd_script()
        assert "sshd -t" in script
        assert 'rm -f "$DROPIN"' in script
        assert "systemctl reload ssh" in script

    def test_uses_quoted_heredoc_so_vars_dont_expand_locally(self):
        script = tasks.ssh_utils.build_harden_sshd_script()
        assert "<<'DINARY_SSH_HARDEN_EOF'" in script


@allure.epic("Deploy")
@allure.feature("fail2ban install script (jail.local + sshd jail)")
class TestInstallFail2banScript:
    """Pins the shape of the fail2ban install + jail.local payload.
    Losing any of these knobs would either disable the sshd jail
    (``enabled = true``), unban too fast (``bantime``/``findtime``),
    or — most critically — drop the Tailscale ``ignoreip`` exclusion
    and start banning operators coming in over the tailnet.
    """

    def test_installs_fail2ban_via_apt_noninteractive(self):
        script = tasks.ssh_utils.build_install_fail2ban_script()
        assert "DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban" in script

    def test_writes_jail_local_with_sshd_enabled(self):
        script = tasks.ssh_utils.build_install_fail2ban_script()
        assert "/etc/fail2ban/jail.local" in script
        assert "[sshd]" in script
        assert "enabled = true" in script
        assert "backend = systemd" in script

    def test_ignoreip_whitelists_tailscale_cgnat(self):
        """Tailscale uses CGNAT (100.64.0.0/10). Without this line an
        operator who mistypes a password over the tailnet would get
        banned on the only tunnel into the box — defeating the whole
        break-glass posture.
        """
        script = tasks.ssh_utils.build_install_fail2ban_script()
        assert "100.64.0.0/10" in script
        assert "ignoreip" in script

    def test_ban_escalation_policy_matches_old_vm(self):
        """Geometric escalation with a 30-day cap is what the
        previous VM1 ran and what ``docs/operations.md`` references.
        If any of these drift, the op note goes stale.
        """
        script = tasks.ssh_utils.build_install_fail2ban_script()
        assert "bantime = 1d" in script
        assert "bantime.increment = true" in script
        assert "bantime.factor = 2" in script
        assert "bantime.maxtime = 30d" in script
        assert "findtime = 10m" in script
        assert "maxretry = 3" in script

    def test_enables_and_starts_service(self):
        script = tasks.ssh_utils.build_install_fail2ban_script()
        assert "systemctl enable --now fail2ban" in script

    def test_uses_quoted_heredoc_so_vars_dont_expand_locally(self):
        script = tasks.ssh_utils.build_install_fail2ban_script()
        assert "<<'DINARY_F2B_EOF'" in script


@allure.epic("Deploy")
@allure.feature("data/ permissions tightening (chmod 700 + db* 600)")
class TestDataDirPermissionsScript:
    """Re-applied on every ``inv deploy`` and ``inv setup-server``.
    SQLite recreates ``-wal`` / ``-shm`` with the umask default on
    restart, so a one-shot chmod is insufficient — the helper must
    be idempotent and cover the glob.
    """

    def test_tightens_data_directory_to_700(self):
        script = tasks.ssh_utils.build_data_dir_permissions_script()
        assert "chmod 700 ~/dinary/data" in script

    def test_tightens_all_dinary_db_files_to_600(self):
        script = tasks.ssh_utils.build_data_dir_permissions_script()
        assert "dinary.db*" in script
        assert "chmod 600" in script
        assert "find ~/dinary/data" in script

    def test_find_scoped_to_top_level_not_recursive(self):
        """``-maxdepth 1`` keeps us from stat-ing backups/ subtrees
        on every restart. The live DB is always at the top level.
        """
        script = tasks.ssh_utils.build_data_dir_permissions_script()
        assert "-maxdepth 1" in script


@allure.epic("Deploy")
@allure.feature("VM1→VM2 SSH trust (keygen, authorized_keys, known_hosts)")
class TestReplicaTrustScripts:
    """Litestream on VM1 replicates to VM2 by SFTP as ``ubuntu``. The
    three builders below wire that trust chain: generate the VM1
    keypair, publish its pubkey into VM2's ``authorized_keys``, and
    pin VM2's host key in VM1's ``known_hosts``. The contract pinned
    here is that each step is safe to re-run — ``inv setup-replica``
    is idempotent in the common case — but the known_hosts script
    specifically is NOT auto-refreshing: a host key change is a
    security signal that must travel through ``inv replica-reset-trust``.
    """

    def test_ensure_key_script_generates_ed25519_when_missing(self):
        """The keypair must be ``ed25519``, not RSA — Oracle's Free
        Tier VMs ship old OpenSSH builds where RSA host keys default
        to SHA-1 signatures that newer clients refuse. Pin the
        algorithm so a future "make it configurable" refactor cannot
        land RSA here.
        """
        script = tasks.ssh_utils.build_ensure_vm1_replica_key_script()
        assert "ssh-keygen -t ed25519" in script
        assert "-N ''" in script, "keygen must be non-interactive (no passphrase)"

    def test_ensure_key_script_is_idempotent(self):
        """Re-running ``inv setup-replica`` must not overwrite an
        existing keypair — doing so would invalidate every previously
        installed ``authorized_keys`` entry on VM2 in one stroke.
        """
        script = tasks.ssh_utils.build_ensure_vm1_replica_key_script()
        assert "[ ! -f ~/.ssh/id_ed25519 ]" in script

    def test_ensure_key_script_prints_pubkey_on_stdout(self):
        """The caller feeds stdout straight into
        ``build_install_authorized_key_script`` — if the script ever
        printed the private key or extra diagnostics, the shell
        substitution would install garbage into
        ``authorized_keys``. Pin the exact last command.
        """
        script = tasks.ssh_utils.build_ensure_vm1_replica_key_script()
        assert script.rstrip().endswith("cat ~/.ssh/id_ed25519.pub")

    def test_install_authorized_key_is_idempotent(self):
        """Repeated ``inv setup-replica`` runs must not duplicate the
        key — ``grep -qxF`` does a full-line, fixed-string match so
        trailing-newline / whitespace drift is treated as a different
        key (forcing an operator check rather than a silent shadow).
        """
        pubkey = "ssh-ed25519 AAAAFAKE dinary-vm1-litestream"
        script = tasks.ssh_utils.build_install_authorized_key_script(pubkey)
        assert "grep -qxF" in script

    def test_install_authorized_key_validates_payload(self):
        """``ssh-keygen -l -f -`` catches a malformed pubkey before it
        lands in ``authorized_keys``. Without the validation, a
        truncation in transport would install a dead line that shadows
        nothing but fills the file with junk.
        """
        pubkey = "ssh-ed25519 AAAAFAKE dinary-vm1-litestream"
        script = tasks.ssh_utils.build_install_authorized_key_script(pubkey)
        assert "ssh-keygen -l -f -" in script

    def test_install_authorized_key_sets_strict_permissions(self):
        """A mode wider than 0600 on ``authorized_keys`` makes
        strict-mode sshd reject the file entirely — failing all
        subsequent logins on VM2. Pin 0600 and 0700 on ``.ssh/``.
        """
        pubkey = "ssh-ed25519 AAAAFAKE dinary-vm1-litestream"
        script = tasks.ssh_utils.build_install_authorized_key_script(pubkey)
        assert "chmod 700 ~/.ssh" in script
        assert "chmod 600 ~/.ssh/authorized_keys" in script

    def test_install_authorized_key_escapes_shell_metachars(self):
        """If a pubkey (or its comment) ever contains shell
        metacharacters, ``shlex.quote`` keeps the install script
        safe — without it, a crafted comment could execute arbitrary
        code on VM2 as ``ubuntu``.
        """
        hostile = "ssh-ed25519 AAAAFAKE $(touch /tmp/owned)"
        script = tasks.ssh_utils.build_install_authorized_key_script(hostile)
        assert "$(touch /tmp/owned)" not in script.replace(
            "'ssh-ed25519 AAAAFAKE $(touch /tmp/owned)'", ""
        ), "shlex.quote must fully escape shell-substitution payloads"

    def test_add_known_host_is_idempotent(self):
        """``ssh-keygen -F`` short-circuits the scan when an entry
        already exists — a re-run of ``inv setup-replica`` must not
        append a duplicate line, and (critically) must NOT overwrite
        an entry whose host key disagrees with a fresh scan. That's
        the ``replica-reset-trust`` boundary.
        """
        script = tasks.ssh_utils.build_add_known_host_script("replica.example")
        assert "ssh-keygen -F replica.example" in script
        assert "ssh-keyscan" in script

    def test_add_known_host_uses_ed25519_only(self):
        """Only ``ed25519`` host keys are accepted — matches the
        ``ssh-keygen -t ed25519`` we generated on VM1. Pin the scan
        type so a drift to ``-t rsa,ecdsa,ed25519`` doesn't silently
        accept weaker algorithms.
        """
        script = tasks.ssh_utils.build_add_known_host_script("replica.example")
        assert "ssh-keyscan -T 10 -t ed25519" in script

    def test_reset_known_host_wipes_before_scan(self):
        """Reset-trust must first ``ssh-keygen -R`` to remove the old
        entry. Without that, the old host key remains the first
        match and OpenSSH refuses the new one with "REMOTE HOST
        IDENTIFICATION HAS CHANGED" no matter how many fresh scans
        we append.
        """
        script = tasks.ssh_utils.build_reset_known_host_script("replica.example")
        keygen_idx = script.index("ssh-keygen -R replica.example")
        keyscan_idx = script.index("ssh-keyscan")
        assert keygen_idx < keyscan_idx

    def test_reset_known_host_tolerates_missing_old_entry(self):
        """``ssh-keygen -R`` exits non-zero if there's nothing to
        remove — without ``|| true`` the whole script fails the
        first time after a known_hosts wipe.
        """
        script = tasks.ssh_utils.build_reset_known_host_script("replica.example")
        assert "ssh-keygen -R replica.example -f ~/.ssh/known_hosts >/dev/null 2>&1 || true" in (
            script
        )

    def test_builders_escape_hostname_metachars(self):
        """The hostname flows into multiple shell invocations; a
        metachar-laden host derived from ``DINARY_REPLICA_HOST`` must
        not execute code on VM1 when ``inv setup-replica`` runs.
        """
        hostile = "replica.example;rm -rf /"
        add = tasks.ssh_utils.build_add_known_host_script(hostile)
        reset = tasks.ssh_utils.build_reset_known_host_script(hostile)
        assert "rm -rf /" not in add.replace("'replica.example;rm -rf /'", "")
        assert "rm -rf /" not in reset.replace("'replica.example;rm -rf /'", "")


@allure.epic("Deploy")
@allure.feature("dinary.service bind host per tunnel type")
class TestDinaryServiceBindHost:
    """``bind_host()`` determines the ``--host`` uvicorn receives.

    For ``tailscale`` and ``cloudflare`` the app must listen on
    ``127.0.0.1`` so the tunnel's local proxy can reach it.  For
    ``none`` it binds to ``0.0.0.0`` (direct public exposure, no
    tunnel in front).

    Binding to a Tailscale IP instead of loopback breaks ``tailscale
    serve`` (which proxies ``https://hostname.ts.net`` →
    ``http://127.0.0.1:8000``) and forces clients onto plain HTTP,
    where ``crypto.randomUUID()`` is not available (requires a secure
    context).  This class pins the contract so a future refactor that
    accidentally reintroduces ``$(tailscale ip -4)`` fails immediately.
    """

    def test_tailscale_binds_to_loopback(self):
        assert tasks.env.bind_host("tailscale") == "127.0.0.1"

    def test_cloudflare_binds_to_loopback(self):
        assert tasks.env.bind_host("cloudflare") == "127.0.0.1"

    def test_none_binds_to_all_interfaces(self):
        assert tasks.env.bind_host("none") == "0.0.0.0"

    def test_tailscale_service_unit_uses_loopback_not_tailscale_ip(self):
        """The rendered unit must NOT contain ``tailscale ip`` in ExecStart.

        If it does, ``tailscale serve`` cannot proxy to the app and
        HTTPS breaks silently — the service starts fine but the tunnel
        proxy gets connection-refused.
        """
        unit = tasks.constants.DINARY_SERVICE.format(host=tasks.env.bind_host("tailscale"))
        exec_start = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        assert "--host 127.0.0.1" in exec_start
        assert "tailscale ip" not in exec_start

    def test_tailscale_service_unit_waits_for_tailscaled_before_start(self):
        """Even though uvicorn binds to loopback, ``tailscale serve``
        still needs tailscaled running to accept incoming HTTPS.
        The ExecStartPre guard ensures tailscaled is up before the app
        starts — without it the HTTPS proxy accepts connections but the
        backend isn't ready.
        """
        unit = tasks.constants.DINARY_SERVICE.format(host=tasks.env.bind_host("tailscale"))
        assert "ExecStartPre=" in unit
        assert "tailscale ip -4" in unit

    def test_none_service_unit_binds_to_all_interfaces(self):
        unit = tasks.constants.DINARY_SERVICE.format(host=tasks.env.bind_host("none"))
        exec_start = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        assert "--host 0.0.0.0" in exec_start
