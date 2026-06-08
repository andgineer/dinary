"""Invoke tasks that install and manage the dinary-ai background service."""

import csv
import io
import os
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

from invoke import task

from dinary_analytics.paths import MCP_PORT

_LABEL = "dev.dinary.ai"
_TASK_NAME = "dinary-ai"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _uv_path() -> str:
    path = shutil.which("uv")
    if path is None:
        raise RuntimeError("`uv` not found on PATH — install it before setting up dinary-ai")
    return path


def _service_arguments() -> list[str]:
    return [
        _uv_path(),
        "run",
        "python",
        "-m",
        "dinary_analytics.ai_service",
        "--port",
        str(MCP_PORT),
    ]


# --- macOS: launchd ------------------------------------------------------------


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _write_plist() -> Path:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": _LABEL,
                "ProgramArguments": _service_arguments(),
                "WorkingDirectory": str(_repo_root()),
                "KeepAlive": True,
                "RunAtLoad": True,
            },
        ),
    )
    return path


def _launchctl_target() -> str:
    return f"gui/{os.getuid()}/{_LABEL}"


def _macos_install(c) -> None:
    path = _write_plist()
    c.run(f"launchctl load {path}")


def _macos_uninstall(c) -> None:
    path = _plist_path()
    c.run(f"launchctl unload {path}", warn=True)
    path.unlink(missing_ok=True)


def _macos_kickstart(c) -> None:
    target = _launchctl_target()
    if c.run(f"launchctl kickstart -k {target}", warn=True, hide=True).ok:
        return
    c.run(f"launchctl load {_plist_path()}", warn=True)
    c.run(f"launchctl kickstart -k {target}")


def _macos_setup(c) -> None:
    if not _plist_path().exists():
        _macos_install(c)
    _macos_kickstart(c)


# --- Windows: Task Scheduler ----------------------------------------------------


def _task_scheduler_xml() -> str:
    args = _service_arguments()
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Settings>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>3</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{args[0]}</Command>\n"
        f"      <Arguments>{' '.join(args[1:])}</Arguments>\n"
        f"      <WorkingDirectory>{_repo_root()}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def _windows_install(c) -> None:
    with tempfile.NamedTemporaryFile(
        suffix=".xml",
        delete=False,
        mode="w",
        encoding="utf-16",
    ) as tmp:
        tmp.write(_task_scheduler_xml())
        tmp_path = Path(tmp.name)
    try:
        c.run(f'schtasks /create /tn {_TASK_NAME} /xml "{tmp_path}" /f')
    finally:
        tmp_path.unlink(missing_ok=True)
    c.run(f"schtasks /run /tn {_TASK_NAME}")


def _windows_uninstall(c) -> None:
    c.run(f"schtasks /end /tn {_TASK_NAME}", warn=True)
    c.run(f"schtasks /delete /tn {_TASK_NAME} /f")


def _task_exists_windows(c) -> bool:
    return c.run(f"schtasks /query /tn {_TASK_NAME}", warn=True, hide=True).ok


def _task_status_windows(c) -> str:
    result = c.run(f"schtasks /query /tn {_TASK_NAME} /fo csv", hide=True)
    header, data = list(csv.reader(io.StringIO(result.stdout.strip())))[:2]
    return data[header.index("Status")]


def _windows_setup(c) -> None:
    if not _task_exists_windows(c):
        _windows_install(c)
    if _task_status_windows(c) != "Running":
        c.run(f"schtasks /run /tn {_TASK_NAME}")


# --- tasks -----------------------------------------------------------------------


@task(name="setup-dinary-ai")
def setup_dinary_ai(c) -> None:
    """Install the dinary-ai background service if needed, and make sure it's running."""
    if sys.platform == "darwin":
        _macos_setup(c)
    elif sys.platform == "win32":
        _windows_setup(c)
    else:
        raise RuntimeError(f"dinary-ai service setup is not supported on {sys.platform}")


@task(name="install-dinary-ai")
def install_dinary_ai(c) -> None:
    """Write the dinary-ai service definition and start it."""
    if sys.platform == "darwin":
        _macos_install(c)
    elif sys.platform == "win32":
        _windows_install(c)
    else:
        raise RuntimeError(f"dinary-ai service install is not supported on {sys.platform}")


@task(name="uninstall-dinary-ai")
def uninstall_dinary_ai(c) -> None:
    """Stop the dinary-ai service and remove its service definition."""
    if sys.platform == "darwin":
        _macos_uninstall(c)
    elif sys.platform == "win32":
        _windows_uninstall(c)
    else:
        raise RuntimeError(f"dinary-ai service uninstall is not supported on {sys.platform}")
