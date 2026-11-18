"""Offline folder-following, recovery and supervisor health tests."""
from pathlib import Path
import datetime as dt
import json
import tempfile
import threading
import time
import unittest
import sys
from unittest.mock import patch

from sxm_anfatec.folder_router import FolderRouter, fingerprint, spectrum_path
from sxm_anfatec.recorder import Recorder
from sxm_anfatec.spectroscopy_metadata import original_bytes, event_not_before
from sxm_anfatec.supervisor import needs_restart, child_command

RAW = b';Delays 1/Aqu/3/4/dead: 0.3/0.01/0.1/0.02/0.06\r\ntime\tdz\tBias\r\n0.07\t0\t-1000\r\n0.14\t0\t1000\r\n'


def wait_for(fn, timeout=10):
    end = time.monotonic() + timeout
    while not fn():
        if time.monotonic() >= end:
            raise AssertionError('Timeout')
        time.sleep(.03)


class AutoStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.session = self.state / 'session_first'
        self.session.mkdir(parents=True)
        (self.session / 'session.json').write_text('{}')

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, folder, identifier='one', proof=True):
        folder.mkdir(exist_ok=True)
        source = folder / 'test.dat'
        source.write_bytes(RAW)
        payload = {'trigger': {'kind': 'dde', 'item': 'SpectSave', 'value': str(source),
                              'connection_id': 'epoch_' + identifier, 'event_id': identifier,
                              'utc': '2020-01-01T00:00:00+00:00', 'monotonic_ns': 10**18},
                   'gui': {'parameters': {}}, 'trigger_to_read_ms': .1,
                   'read_start': {'utc': 'start', 'monotonic_ns': 2},
                   'read_end': {'utc': 'end', 'monotonic_ns': 3}}
        if proof:
            payload['source_file_at_capture'] = fingerprint(source)
        (self.session / ('capture_' + identifier + '.json')).write_text(json.dumps(payload))
        return source

    def test_new_daily_folders_are_discovered_without_configuration(self):
        a = self.capture(self.root / 'day1')
        router = FolderRouter(self.state, self.session, settle_seconds=0)
        router.process_once()
        self.assertEqual(router.process_once()['complete'], 1)
        self.assertEqual(original_bytes((a.parent / 'enriched/test_bias_sweep.dat').read_bytes()), RAW)
        b = self.capture(self.root / 'day2', 'two')
        router.process_once()
        self.assertEqual(router.process_once()['complete'], 2)
        self.assertEqual(original_bytes((b.parent / 'enriched/test_bias_sweep.dat').read_bytes()), RAW)
        self.assertEqual(a.read_bytes(), RAW)
        self.assertEqual(b.read_bytes(), RAW)
        self.assertFalse(router.enrichers)  # No repeated scans of completed old folders.

    def test_reboot_recovery_uses_raw_fingerprint_and_utc(self):
        source = self.capture(self.root / 'day1')
        new = self.state / 'session_after_reboot'
        new.mkdir()
        router = FolderRouter(self.state, new, settle_seconds=0)
        router.process_once()
        self.assertEqual(router.process_once()['complete'], 1)
        target = source.parent / 'enriched/test_bias_sweep.dat'
        saved_mtime = target.stat().st_mtime_ns
        recovered = FolderRouter(self.state, new, settle_seconds=0)
        recovered.process_once()
        recovered.process_once()
        self.assertEqual(saved_mtime, target.stat().st_mtime_ns)

    def test_restart_never_attaches_stale_capture_to_changed_raw(self):
        source = self.capture(self.root / 'day1')
        source.write_bytes(RAW + b'0.21\t0\t2000\r\n')
        router = FolderRouter(self.state, self.session, settle_seconds=0)
        self.assertEqual(router.process_once()['pending'], 1)
        self.assertFalse((source.parent / 'enriched').exists())

    def test_missing_fingerprint_is_explicitly_pending(self):
        source = self.capture(self.root / 'day1', proof=False)
        router = FolderRouter(self.state, self.session, settle_seconds=0)
        self.assertEqual(router.process_once()['pending'], 1)
        self.assertIn('No saved raw-file fingerprint', next(iter(router.jobs.values()))['reason'])
        self.assertFalse((source.parent / 'enriched').exists())

    def test_only_absolute_spectrum_save_paths_are_followed(self):
        self.assertIsNone(spectrum_path({'item': 'Scan', 'value': str(self.root / 'test.dat')}))
        self.assertIsNone(spectrum_path({'item': 'SpectSave', 'value': 'relative.dat'}))
        self.assertIsNone(spectrum_path({'item': 'SpectSave', 'value': str(self.root / 'test.bmp')}))

    def test_reboot_clock_comparison(self):
        self.assertTrue(event_not_before({'utc': '2026-11-11T09:00:00+00:00', 'monotonic_ns': 1},
                                         {'utc': '2026-11-10T17:00:00+00:00', 'monotonic_ns': 10**18}))

    def test_supervisor_restart_conditions(self):
        class Process:
            pid = 42
            result = None
            def poll(self):
                return self.result
        p = Process()
        self.assertIsNone(needs_restart(p, {}, 0, 20))
        self.assertIsNotNone(needs_restart(p, {}, 0, 61))
        fresh = {'pid': 42, 'running': True, 'utc': dt.datetime.now(dt.timezone.utc).isoformat()}
        self.assertIsNone(needs_restart(p, fresh, 0, 61))
        old = dict(fresh, utc=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=70)).isoformat())
        self.assertIsNotNone(needs_restart(p, old, 0, 61))
        p.result = 1
        self.assertEqual(needs_restart(p, fresh, 0, 1), 'recorder exited')
        self.assertIn('--follow-sxm', child_command(self.state))
        self.assertIn('--no-topo', child_command(self.state))   # never holds the unshared driver

    def test_task_name_is_account_specific(self):
        from sxm_anfatec.startup import task_name
        self.assertEqual(task_name('LAB\\Alice'), task_name('lab\\alice'))
        self.assertNotEqual(task_name('LAB\\Alice'), task_name('LAB\\Bob'))

    def test_supervisor_restarts_an_actual_failed_child_without_hardware(self):
        from sxm_anfatec.supervisor import serve
        script = self.root / 'fake_child.py'
        script.write_text('''import os, sys, time, json, datetime
from pathlib import Path
root = Path(sys.argv[1])
calls = root / 'calls.txt'
first = not calls.exists()
with calls.open('a') as f:
    f.write(str(os.getpid()) + '\\n')
if first:
    sys.exit(1)
out = root / 'recordings'
(out / 'STOP').unlink(missing_ok=True)
while not (out / 'STOP').exists():
    (out / 'status.json').write_text(json.dumps({'pid': os.getpid(), 'running': True,
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    time.sleep(.1)
''')
        with patch('sxm_anfatec.supervisor.child_command', return_value=[sys.executable, '-B', str(script), str(self.state)]):
            worker = threading.Thread(target=serve, args=(self.state,))
            worker.start()
            try:
                wait_for(lambda: (self.state / 'calls.txt').exists())
                wait_for(lambda: len((self.state / 'calls.txt').read_text().splitlines()) >= 2)
                pids = (self.state / 'calls.txt').read_text().splitlines()
                self.assertNotEqual(pids[0], pids[1])
            finally:
                (self.state / 'PAUSED').touch()
                worker.join(20)
            self.assertFalse(worker.is_alive())

    def test_live_recorder_follows_new_folder_with_fake_hardware(self):
        class Bridge:
            def snapshot(self):
                return {'parameters': {}}
        r = Recorder(self.state, topo=False, bridge_factory=Bridge, follow_sxm=True)
        r.dde_worker = lambda: r.stop.wait()
        thread = threading.Thread(target=r.run)
        thread.start()
        try:
            wait_for(lambda: (self.state / 'status.json').exists())
            for foldername in ('day1', 'day2'):
                folder = self.root / foldername
                folder.mkdir()
                source = folder / 'test.dat'
                source.write_bytes(RAW)
                r.emit('dde', item='SpectSave', value=str(source))
                target = folder / 'enriched/test_bias_sweep.dat'
                wait_for(target.exists)
                self.assertEqual(original_bytes(target.read_bytes()), RAW)
                self.assertEqual(source.read_bytes(), RAW)
        finally:
            r.stop.set()
            thread.join(12)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
