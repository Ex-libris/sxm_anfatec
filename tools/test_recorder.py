"""Offline recorder checks: no SXM connection and no controller access."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from sxm_anfatec.recorder import Recorder, atomic_json


class FakeBridge:
    def snapshot(self):
        return {'parameters': {'DNC': {'TimeConstant': '3 ms'},
                               'PLL': {'_error': 'panel not created'}}}


def wait_until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out')
        time.sleep(.02)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make(self, **kw):
        return Recorder(self.folder / 'meta', topo=False, bridge_factory=FakeBridge, **kw)

    def test_events_deduplicated_but_saves_always_captured(self):
        r = self.make()
        r.emit('dde', item='ScanLine', value='1')
        self.assertTrue(r.requests.empty())
        for _ in range(2):
            r.emit('dde', item='MicState', value='unknown-state')
        self.assertEqual(r.requests.qsize(), 1)
        for _ in range(2):
            r.emit('dde', item='SpectSave', value='same-payload')
        self.assertEqual(r.requests.qsize(), 3)
        self.assertEqual(r.events.qsize(), 5)

    def test_snapshot_keeps_errors_and_does_not_invent_reference(self):
        r = self.make(hz=2, history_seconds=1)
        r.history.extend([{'raw': 1}, {'raw': 2}, {'raw': 3}])
        event = r.emit('dde', item='SpectSave', value='test.dat')
        r.history.append({'raw': 4})
        r.stop.set()
        r.snapshot_worker()
        data = json.loads((r.session / ('capture_' + event['event_id'] + '.json')).read_text())
        self.assertEqual(data['topo_before_trigger'], [{'raw': 2}, {'raw': 3}])
        self.assertEqual(data['topo_at_read_end'], {'raw': 4})
        self.assertIsNone(data['reference_height'])
        self.assertIn('_error', data['gui']['parameters']['PLL'])
        self.assertGreaterEqual(data['trigger_to_read_ms'], 0)
        self.assertGreaterEqual(data['read_end']['monotonic_ns'], data['read_start']['monotonic_ns'])

    def test_failed_bridge_still_saves_evidence(self):
        r = self.make()
        def fail():
            raise RuntimeError('closed')
        r.bridge_factory = fail
        r.emit('baseline')
        r.stop.set()
        r.snapshot_worker()
        data = json.loads(next(r.session.glob('capture_*.json')).read_text())
        self.assertIn('closed', data['gui']['_error'])

    def test_overload_is_visible(self):
        r = self.make()
        for i in range(70):
            r.emit('dde', item='SpectSave', value=str(i))
        self.assertEqual(r.requests.qsize(), 64)
        self.assertEqual(r.health['snapshots_dropped'], 6)

    def test_atomic_json_nonfinite_and_replace(self):
        path = self.folder / 'data.json'
        atomic_json(path, {'value': float('inf')})
        self.assertEqual(json.loads(path.read_text())['value'], 'inf')
        atomic_json(path, {'value': 2})
        self.assertEqual(json.loads(path.read_text())['value'], 2)
        self.assertFalse(list(self.folder.glob('*.tmp')))

    def test_file_baseline_and_new_file_fingerprint(self):
        watch = self.folder / 'data'
        watch.mkdir()
        (watch / 'old.dat').write_text('old')
        r = self.make(watch=watch)
        worker = threading.Thread(target=r.file_worker)
        worker.start()
        try:
            time.sleep(.7)
            self.assertTrue(r.events.empty())
            (watch / 'new.dat').write_bytes(b'abc')
            wait_until(lambda: not r.requests.empty())
            event, _ = r.requests.get_nowait()
            self.assertEqual(event['kind'], 'file_stable')
            self.assertEqual(event['sha256'], 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            self.assertFalse(event['completion_confirmed'])
        finally:
            r.stop.set()
            worker.join(3)

    def test_background_lifecycle_offline(self):
        r = self.make()
        r.dde_worker = lambda: r.stop.wait()
        worker = threading.Thread(target=r.run)
        worker.start()
        try:
            wait_until(lambda: (r.output / 'status.json').exists())
            wait_until(lambda: bool(list(r.session.glob('capture_*.json'))))
            (r.output / 'STOP').touch()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            status = json.loads((r.output / 'status.json').read_text())
            self.assertFalse(status['running'])
            self.assertEqual(status['workers_still_running'], [])
        finally:
            r.stop.set()
            worker.join(5)


if __name__ == '__main__':
    unittest.main()
