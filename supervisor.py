"""Interactive-session supervisor; owns only its own recorder child process."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .recorder import atomic_json, stamp


def state_directory():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'SXMMetadataRecorder'


def interpreter():
    p = Path(sys.executable).with_name('pythonw.exe')
    return str(p if p.exists() else Path(sys.executable))


def child_command(state):
    # --no-topo: the driver opens without sharing, so a background reader holding it would lock
    # sxm_ncafm_control / sxm_sts_control out of the instrument. Topo history only with a manual recorder.
    return [interpreter(), '-B', '-m', 'sxm_anfatec.recorder', '--follow-sxm', '--no-topo',
            '--output', str(Path(state) / 'recordings')]


def needs_restart(process, status, launched_monotonic, now_monotonic=None):
    if process.poll() is not None:
        return 'recorder exited'
    now = time.monotonic() if now_monotonic is None else now_monotonic
    if now - launched_monotonic < 60:
        return None
    if status.get('pid') != process.pid or not status.get('running'):
        return 'recorder heartbeat unavailable'
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(status['utc'])).total_seconds()
    except (ValueError, KeyError, TypeError):
        return 'invalid recorder heartbeat'
    return 'recorder heartbeat stalled' if age > 60 else None


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def stop_child(child, recordings):
    if child is None or child.poll() is not None:
        return
    (Path(recordings) / 'STOP').touch()
    try:
        child.wait(timeout=12)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def recorder_outputs(state):
    outputs = [Path(state) / 'recordings']
    legacy = read_json(Path.home() / 'SXM_metadata_recorder.json')
    if legacy.get('output'):
        old = Path(legacy['output']).resolve()
        if old.is_dir() and old not in outputs:
            outputs.append(old)
    return outputs


def lock_busy(path):
    """Probe the same Windows lock as the worker; stale status files are not proof."""
    import msvcrt
    try:
        guard = Path(path).open('r+b')
    except FileNotFoundError:
        return False
    with guard:
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (13, 11, 36):
                return True
            raise
        msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
    return False


def busy_workers(state):
    paths = [Path(state) / 'supervisor.lock']
    paths.extend(folder / 'recorder.lock' for folder in recorder_outputs(state))
    return [str(path) for path in paths if lock_busy(path)]


def stop_background(state=None, timeout=30):
    state = Path(state or state_directory())
    state.mkdir(parents=True, exist_ok=True)
    (state / 'PAUSED').touch()
    deadline = time.monotonic() + timeout
    while True:
        # Repeat for a child that was starting when the stop request arrived.
        for folder in recorder_outputs(state):
            if folder.is_dir():
                (folder / 'STOP').touch()
        busy = busy_workers(state)
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Shutdown was not confirmed within the allowed time. Still active: ' + ', '.join(busy))
        time.sleep(.2)


def serve(state=None):
    import msvcrt
    state = Path(state or state_directory())
    state.mkdir(parents=True, exist_ok=True)
    guard = (state / 'supervisor.lock').open('a+b')
    if guard.tell() == 0:
        guard.write(b'0')
        guard.flush()
    guard.seek(0)
    try:
        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        guard.close()
        return  # A scheduled/repeated start is harmless.
    child, log = None, None
    recordings = state / 'recordings'
    recordings.mkdir(exist_ok=True)
    restarts, reason, started = 0, 'starting', 0.0
    try:
        while not (state / 'PAUSED').exists():
            status = read_json(recordings / 'status.json')
            restart = needs_restart(child, status, started) if child else 'starting'
            if restart:
                reason = restart
                stop_child(child, recordings)
                if (state / 'PAUSED').exists():
                    break
                if log:
                    log.close()
                logfile = state / 'recorder_process.log'
                if logfile.exists() and logfile.stat().st_size > 2_000_000:
                    os.replace(logfile, logfile.with_suffix('.previous.log'))
                log = logfile.open('ab')
                child = subprocess.Popen(child_command(state), cwd=Path(__file__).resolve().parent.parent,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                started = time.monotonic()
                restarts += 1
            atomic_json(state / 'supervisor_status.json', dict(stamp(), running=True, pid=os.getpid(),
                        recorder_pid=child.pid, launches=restarts, last_reason=reason,
                        recorder=status, automatic_folders=True))
            time.sleep(2)
    finally:
        try:
            stop_child(child, recordings)
            atomic_json(state / 'supervisor_status.json', dict(stamp(), running=False, paused=(state / 'PAUSED').exists()))
        finally:
            if log:
                log.close()
            guard.close()


def start_background():
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True)
    (state / 'PAUSED').unlink(missing_ok=True)
    # Gracefully retire the earlier fixed-folder recorder when its saved config exists.
    legacy = read_json(Path.home() / 'SXM_metadata_recorder.json')
    if legacy.get('output'):
        old_output = Path(legacy['output'])
        old_status = read_json(old_output / 'status.json')
        if old_output.is_dir() and old_status.get('running'):
            (old_output / 'STOP').touch()
    with (state / 'supervisor_process.log').open('ab') as log:
        subprocess.Popen([interpreter(), '-B', '-m', 'sxm_anfatec.supervisor'],
                         cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    serve(args.state)


if __name__ == '__main__':
    main()
