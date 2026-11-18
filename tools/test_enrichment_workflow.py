"""Offline checks for automatic copies, recovery and original preservation."""
import hashlib
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sxm_anfatec.enrichment import CopyEnricher
from sxm_anfatec.journal import EventJournal
from sxm_anfatec.recorder import Recorder
from sxm_anfatec.spectroscopy_metadata import original_bytes, publish_copy

RAW = b';Delays 1/Aqu/3/4/dead: 0.3/0.01/0.1/0.02/0.06\r\ntime\tdz\tBias\r\n0.07\t0\t-1000\r\n0.14\t0\t1000\r\n'


def wait_for(fn, timeout=8):
    deadline = time.monotonic() + timeout
    while not fn():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out')
        time.sleep(.03)


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rawdir = Path(self.tmp.name)
        self.source = self.rawdir / 'test.dat'
        self.source.write_bytes(RAW)
        self.metadata = self.rawdir / 'metadata'
        self.session = self.metadata / 'session_test'
        self.session.mkdir(parents=True)
        self.worker = CopyEnricher(self.rawdir, self.metadata)

    def tearDown(self):
        self.tmp.cleanup()

    def captures(self, unit=None):
        (self.session / 'session.json').write_text(json.dumps({'modulation_unit': unit}))
        capture = {'trigger': {'item': 'SpectSave', 'kind': 'dde', 'value': str(self.source),
                               'monotonic_ns': 1, 'utc': '2026-11-10T12:00:00+00:00'},
                   'read_start': {'monotonic_ns': 2, 'utc': 'start'},
                   'read_end': {'monotonic_ns': 3, 'utc': 'end'}, 'trigger_to_read_ms': .1,
                   'gui': {'parameters': {}}}
        evidence = {'trigger': {'kind': 'file_stable', 'path': str(self.source),
                                'monotonic_ns': 4, 'sha256': hashlib.sha256(RAW).hexdigest()}}
        (self.session / 'capture_save.json').write_text(json.dumps(capture))
        (self.session / 'capture_stable.json').write_text(json.dumps(evidence))
        return capture, evidence

    def test_raw_unchanged_copy_created_and_recovered_without_duplication(self):
        self.captures()
        original_mtime = self.source.stat().st_mtime_ns
        result = self.worker.process_once()
        target = self.rawdir / 'enriched/test_bias_sweep.dat'
        self.assertEqual(result['complete'], 1)
        self.assertEqual(self.source.read_bytes(), RAW)
        self.assertEqual(self.source.stat().st_mtime_ns, original_mtime)
        self.assertEqual(original_bytes(target.read_bytes()), RAW)
        saved_mtime = target.stat().st_mtime_ns
        CopyEnricher(self.rawdir, self.metadata).process_once()
        self.assertEqual(target.stat().st_mtime_ns, saved_mtime)
        self.assertEqual(len(list(target.parent.glob('*.dat'))), 1)

    def test_pending_capture_completes_later(self):
        self.captures()
        stable = self.session / 'capture_stable.json'
        content = stable.read_bytes()
        stable.unlink()
        self.assertEqual(self.worker.process_once()['pending'], 1)
        self.assertFalse((self.rawdir / 'enriched').exists())
        stable.write_bytes(content)
        self.assertEqual(self.worker.process_once()['complete'], 1)

    def test_different_existing_output_is_never_replaced(self):
        self.captures()
        target = self.rawdir / 'enriched/test_bias_sweep.dat'
        target.parent.mkdir()
        target.write_bytes(b'previous result')
        self.assertEqual(self.worker.process_once()['complete'], 1)
        self.assertEqual(target.read_bytes(), b'previous result')
        versioned = list(target.parent.glob('test_bias_sweep__*.dat'))
        self.assertEqual(len(versioned), 1)
        self.assertEqual(original_bytes(versioned[0].read_bytes()), RAW)

    def test_missing_copy_is_rebuilt(self):
        self.captures()
        self.worker.process_once()
        target = self.rawdir / 'enriched/test_bias_sweep.dat'
        target.unlink()
        self.assertEqual(self.worker.process_once()['complete'], 1)
        self.assertTrue(target.exists())

    def test_changed_raw_or_ambiguous_save_is_not_enriched(self):
        capture, _ = self.captures()
        self.source.write_bytes(RAW + b'0.21\t0\t2000\r\n')
        self.assertEqual(self.worker.process_once()['pending'], 1)
        self.assertFalse((self.rawdir / 'enriched').exists())
        self.source.write_bytes(RAW)
        (self.session / 'capture_second_save.json').write_text(json.dumps(capture))
        self.assertEqual(self.worker.process_once()['blocked'], 1)
        self.assertFalse((self.rawdir / 'enriched').exists())

    def test_partial_output_not_published_when_source_changes(self):
        target = self.rawdir / 'enriched/test_bias_sweep.dat'
        self.source.write_bytes(b'new raw')
        with self.assertRaises(ValueError):
            publish_copy(self.source, target, b'enriched', hashlib.sha256(RAW).hexdigest())
        self.assertEqual(self.source.read_bytes(), b'new raw')
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob('*.tmp')))

    def test_amplitude_unit_comes_from_original_session(self):
        self.captures(unit='mV')
        self.worker.process_once()
        self.assertIn(b'ModulationAmplitudeUnit: mV', (self.rawdir / 'enriched/test_bias_sweep.dat').read_bytes())

    def test_journal_survives_disabled_python_logging(self):
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        journal = EventJournal(self.rawdir / 'events.jsonl', max_bytes=60, backups=3)
        try:
            for i in range(8):
                journal.append({'kind': 'test', 'number': i})
            self.assertEqual(journal.written, 8)
        finally:
            journal.close()
            logging.disable(disabled)
        for path in self.rawdir.glob('events.jsonl*'):
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertTrue(rows)
            self.assertEqual(rows[-1]['kind'], 'test')

    def test_recording_to_automatic_copy_end_to_end(self):
        class FakeBridge:
            def snapshot(self):
                return {'parameters': {}}
        recorder = Recorder(self.metadata, self.rawdir, topo=False, bridge_factory=FakeBridge)
        recorder.dde_worker = lambda: recorder.stop.wait()
        thread = threading.Thread(target=recorder.run)
        thread.start()
        try:
            wait_for(lambda: (self.metadata / 'status.json').exists())
            time.sleep(.7)  # File watch baseline established before this new measurement.
            newraw = self.rawdir / 'new.dat'
            newraw.write_bytes(RAW)
            recorder.emit('dde', item='SpectSave', value=str(newraw))
            target = self.rawdir / 'enriched/new_bias_sweep.dat'
            wait_for(target.exists)
            self.assertEqual(newraw.read_bytes(), RAW)
            self.assertEqual(original_bytes(target.read_bytes()), RAW)
            self.assertGreater((recorder.session / 'events.jsonl').stat().st_size, 0)
        finally:
            recorder.stop.set()
            thread.join(12)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
