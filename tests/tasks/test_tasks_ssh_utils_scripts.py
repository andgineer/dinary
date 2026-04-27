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
