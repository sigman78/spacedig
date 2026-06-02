"""Windows Task Scheduler integration.

Registers a scheduled task that periodically runs ``spacedig scan`` for the
chosen roots and appends the regression report to a log file, so disk-space
growth is captured automatically over time.

Implemented with the built-in ``schtasks.exe``; no third-party dependency.
On non-Windows platforms the functions raise ``NotImplementedError`` (the
core scan/diff features still work everywhere).
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

TASK_NAME = "spacedig\\scan"


def _is_windows() -> bool:
    return os.name == "nt"


def _require_windows() -> None:
    if not _is_windows():
        raise NotImplementedError("scheduling is only supported on Windows")


def default_log_path() -> str:
    from .store import home_dir

    return os.path.join(home_dir(), "regressions.log")


def build_command(roots: List[str], log_path: str, threshold: Optional[int]) -> str:
    """Build the command line the scheduled task will run.

    Uses the current Python interpreter with ``-m spacedig`` so it works
    regardless of how spacedig was installed.
    """
    py = os.path.normpath(sys.executable)
    parts = [_q(py), "-m", "spacedig", "scan"]
    parts.extend(_q(r) for r in roots)
    parts.append("--quiet")
    parts.extend(["--log", _q(log_path)])
    if threshold is not None:
        parts.extend(["--threshold", str(threshold)])
    return " ".join(parts)


def _q(s: str) -> str:
    """Quote a token for the schtasks /TR command string."""
    if not s:
        return '""'
    if any(ch in s for ch in ' \t"'):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def install(
    roots: List[str],
    interval: str = "DAILY",
    at: str = "09:00",
    log_path: Optional[str] = None,
    threshold: Optional[int] = None,
    task_name: str = TASK_NAME,
) -> str:
    """Create (or replace) the scheduled task. Returns the command registered."""
    _require_windows()
    log_path = log_path or default_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    command = build_command(roots, log_path, threshold)

    args = [
        "schtasks",
        "/Create",
        "/TN",
        task_name,
        "/TR",
        command,
        "/SC",
        interval.upper(),
        "/F",
    ]
    # /ST is valid for DAILY/WEEKLY/MONTHLY/ONCE; HOURLY/MINUTE use it as a
    # start time too, which is fine.
    if at:
        args.extend(["/ST", at])
    subprocess.run(args, check=True, capture_output=True, text=True)
    return command


def remove(task_name: str = TASK_NAME) -> None:
    _require_windows()
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        check=True,
        capture_output=True,
        text=True,
    )


def status(task_name: str = TASK_NAME) -> Optional[str]:
    """Return the schtasks status text, or None if the task does not exist."""
    _require_windows()
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
