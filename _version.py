"""Version of this copy of sxm_anfatec: '<commit date>+<commit>', e.g. '2026.10.09+abc1234'.

Read from the VERSION file when there is one (copies exported for the offline
microscope PC have it), otherwise from git (a checkout), otherwise 'unknown'.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_cached: str | None = None


def version() -> str:
    global _cached
    if _cached is None:
        _cached = _read()
    return _cached


def _read() -> str:
    stamp = _HERE / 'VERSION'
    if stamp.is_file():
        return stamp.read_text(encoding='utf-8').strip() or 'unknown'
    try:
        out = subprocess.run(['git', '-C', str(_HERE), 'log', '-1', '--format=%cd+%h', '--date=format:%Y.%m.%d'],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    v = out.stdout.strip()
    if out.returncode or not v:
        return 'unknown'
    dirty = subprocess.run(['git', '-C', str(_HERE), 'status', '--porcelain', '--untracked-files=no'],
                           capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
    return v + ('.modified' if dirty else '')


def require(minimum: str) -> None:
    """Raise RuntimeError if this copy is older than ``minimum`` ('YYYY.MM.DD').

    An unknown version passes (with a printed warning): never block a measurement on that.
    """
    have = version()
    if have == 'unknown':
        print(f'sxm_anfatec: version unknown, cannot check it is at least {minimum}')
        return
    if have.split('+')[0] < minimum:
        raise RuntimeError(f'sxm_anfatec {have} at {_HERE} is too old: this program needs {minimum} or newer. '
                           'Copy the current sxm_anfatec folder next to it.')
