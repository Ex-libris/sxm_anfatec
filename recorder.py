"""Capture spectroscopy metadata and publish enriched copies; raw files are read-only."""
from __future__ import annotations

import argparse
from collections import deque
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid


def stamp():
    return {'utc': dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds'),
            'monotonic_ns': time.monotonic_ns()}


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as fh:
            json.dump(clean(value), fh, indent=2, ensure_ascii=False, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Recorder:
    """Can also share a caller-owned SXMIOCTL via driver= (never closes it)."""

    def __init__(self, output, watch=None, hz=10, history_seconds=30, topo=True,
                 driver=None, bridge_factory=None, auto_enrich=True, modulation_unit=None, follow_sxm=False):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.session = self.output / ('session_' + dt.datetime.now().strftime('%Y%m%d_%H%M%S')
                                      + '_' + uuid.uuid4().hex[:8])
        self.session.mkdir()
        self.watch = Path(watch).resolve() if watch else None
        self.follow_sxm = follow_sxm
        self.auto_enrich = auto_enrich and (self.watch is not None or follow_sxm)
        self.connection_id = uuid.uuid4().hex
        self.modulation_unit = modulation_unit
        self.hz, self.topo, self.driver = hz, topo, driver
        self.bridge_factory = bridge_factory
        self.stop = threading.Event()
        self.events = queue.Queue(maxsize=10000)
        self.requests = queue.Queue(maxsize=64)
        self.history = deque(maxlen=max(1, int(hz * history_seconds)))
        self.lock = threading.Lock()
        self.health = {'dde': 'waiting', 'topo': 'waiting' if topo else 'disabled',
                       'snapshots': 0, 'events_dropped': 0, 'snapshots_dropped': 0}
        self.previous_states = {}
        self.threads = []

    def enrichment_worker(self):
        if self.follow_sxm:
            from .folder_router import FolderRouter
            worker = FolderRouter(self.output, self.session, self.emit)
        else:
            from .enrichment import CopyEnricher
            worker = CopyEnricher(self.watch, self.output, self.emit)
        while not self.stop.is_set():
            try:
                self.state('enrichment', worker.process_once())
                self.state('enrichment_error', None)
            except Exception as exc:
                self.state('enrichment_error', str(exc))
            self.stop.wait(2)

    def emit(self, kind, **fields):
        record = dict(stamp(), kind=kind, event_id=uuid.uuid4().hex, connection_id=self.connection_id, **fields)
        try:
            self.events.put_nowait(record)
        except queue.Full:
            with self.lock:
                self.health['events_dropped'] += 1
        trigger = kind in ('baseline', 'file_stable')
        if kind == 'dde':
            item, value = fields.get('item'), fields.get('value')
            trigger = item in ('SpectSave', 'SaveFileName')
            if item in ('MicState', 'Scan'):
                trigger = self.previous_states.get(item) != value
                self.previous_states[item] = value
        if trigger:
            with self.lock:
                before = list(self.history)
            try:
                self.requests.put_nowait((record, before))
            except queue.Full:
                with self.lock:
                    self.health['snapshots_dropped'] += 1
        return record

    def state(self, name, value):
        with self.lock:
            self.health[name] = value

    def dde_worker(self):
        from .event_listener import listen
        def receive(kind, **fields):
            if kind == 'dde_connected':
                self.state('dde', 'connected')
                self.state('subscriptions', fields['subscriptions'])
            self.emit(kind, **fields)
        while not self.stop.is_set():
            try:
                self.connection_id = uuid.uuid4().hex
                with self.lock:
                    self.history.clear()
                    self.previous_states.clear()
                listen(receive, self.stop)
            except Exception as exc:
                self.state('dde', str(exc))
                self.emit('dde_unavailable', error=repr(exc))
            self.stop.wait(5)

    def topo_worker(self):
        owned = self.driver is None
        driver = self.driver
        try:
            while not self.stop.is_set():
                try:
                    if self.follow_sxm and self.health.get('dde') != 'connected':
                        if owned and driver is not None:
                            driver.close()
                            driver = None
                        self.state('topo', 'waiting for SXM')
                        self.stop.wait(.5)
                        continue
                    from .driver import SXMIOCTL, CHANNELS
                    if driver is None:
                        driver = SXMIOCTL()
                    index, _, unit, scale = CHANNELS['Topo']
                    started = stamp()
                    raw = driver.read_raw(index)
                    sample = dict(started, read_end=stamp(), raw=raw, value=raw * scale,
                                  unit=unit, scale=scale, channel='Topo', channel_index=index,
                                  interpretation='controller_channel_not_absolute_tip_sample_distance')
                    with self.lock:
                        self.history.append(sample)
                        self.health['topo'] = 'reading'
                    self.emit('topo', sample=sample)
                    self.stop.wait(1 / self.hz)
                except Exception as exc:
                    self.state('topo', str(exc))
                    self.emit('topo_unavailable', error=repr(exc))
                    if owned and driver is not None:
                        driver.close()
                        driver = None
                    self.stop.wait(5)
        finally:
            if owned and driver is not None:
                driver.close()

    def snapshot_worker(self):
        while not self.stop.is_set() or not self.requests.empty():
            try:
                trigger, before = self.requests.get(timeout=0.2)
            except queue.Empty:
                continue
            started = stamp()
            try:
                if self.bridge_factory:
                    bridge = self.bridge_factory()
                    snapshot = bridge.snapshot()
                else:
                    from .bridge import AnfatecSXMBridge
                    bridge = AnfatecSXMBridge(strict=False)
                    snapshot = bridge.snapshot(include_selection=True)
            except Exception as exc:
                snapshot = {'_error': repr(exc)}
            ended = stamp()
            errors = {k: v['_error'] for k, v in snapshot.get('parameters', {}).items()
                      if isinstance(v, dict) and '_error' in v}
            if '_error' in snapshot:
                errors['snapshot'] = snapshot['_error']
            self.state('gui_errors', errors)
            with self.lock:
                after = list(self.history)
            payload = {'schema_version': 1, 'purpose': 'diagnostic', 'trigger': trigger,
                       'read_start': started, 'read_end': ended,
                       'trigger_to_read_ms': (started['monotonic_ns'] - trigger['monotonic_ns']) / 1e6,
                       'gui': snapshot, 'topo_before_trigger': before,
                       'topo_at_read_end': after[-1] if after else None,
                       'reference_height': None,
                       'association': 'unverified; event payload is evidence, not an acquisition identifier'}
            if self.follow_sxm and trigger.get('item') == 'SpectSave':
                try:
                    from .folder_router import spectrum_path, fingerprint
                    source = spectrum_path(trigger)
                    if source is not None:
                        payload['source_file_at_capture'] = dict(fingerprint(source), **stamp())
                except Exception as exc:
                    payload['source_file_at_capture'] = {'error': str(exc)}
            try:
                target = self.session / ('capture_' + trigger['event_id'] + '.json')
                atomic_json(target, payload)
                with self.lock:
                    self.health['snapshots'] += 1
                self.emit('snapshot_saved', path=str(target), trigger_event_id=trigger['event_id'])
            except Exception as exc:
                self.emit('snapshot_error', error=repr(exc), trigger_event_id=trigger['event_id'])
                self.state('snapshot_error', repr(exc))
            finally:
                self.requests.task_done()

    def file_worker(self):
        # Existing files are baselined. Never label an old file as a new acquisition.
        seen, pending = {}, {}
        first = True
        while not self.stop.is_set():
            try:
                current = {}
                for path in self.watch.glob('*.dat'):
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                    current[path] = signature
                    if signature != seen.get(path) and not first:
                        pending[path] = (signature, time.monotonic())
                        self.emit('file_changed', path=str(path), size=stat.st_size)
                seen = current
                first = False
                for path, (signature, when) in list(pending.items()):
                    if path not in current:
                        del pending[path]
                        continue
                    if time.monotonic() - when < 2:
                        continue
                    digest = hashlib.sha256()
                    with path.open('rb') as fh:
                        for block in iter(lambda: fh.read(1024 * 1024), b''):
                            if self.stop.is_set():
                                return
                            digest.update(block)
                    stat = path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != signature:
                        continue
                    self.emit('file_stable', path=str(path), size=signature[0],
                              sha256=digest.hexdigest(), stable_seconds=2,
                              completion_confirmed=False)
                    del pending[path]
            except Exception as exc:
                self.emit('file_watch_error', error=repr(exc))
            self.stop.wait(0.5)

    def run(self):
        # Held exclusively by Windows for process lifetime, including pythonw runs.
        import msvcrt
        guard = (self.output / 'recorder.lock').open('a+b')
        guard.seek(0)
        if not guard.read(1):
            guard.write(b'0')
            guard.flush()
        guard.seek(0)
        try:
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            guard.close()
            raise RuntimeError('A recorder already uses this output folder')
        from .journal import EventJournal
        journal = EventJournal(self.session / 'events.jsonl')
        def log_event(event):
            try:
                journal.append(event)
                self.state('journal_events_written', journal.written)
            except Exception as exc:
                self.state('journal_error', str(exc))
                with self.lock:
                    self.health['journal_events_lost'] = self.health.get('journal_events_lost', 0) + 1
        stopfile = self.output / 'STOP'
        stopfile.unlink(missing_ok=True)
        # Do not start hardware readers if a full stop raced with this launch.
        if self.follow_sxm and (self.output.parent / 'PAUSED').exists():
            journal.close()
            guard.close()
            return
        atomic_json(self.session / 'session.json', dict(stamp(), pid=os.getpid(),
                    watch=str(self.watch) if self.watch else None, topo_hz=self.hz,
                    diagnostic_only=not self.auto_enrich, output=str(self.output),
                    enriched_output='follow SpectSave folder' if self.follow_sxm else
                                    str(self.watch / 'enriched') if self.auto_enrich else None,
                    follow_sxm=self.follow_sxm,
                    modulation_unit=self.modulation_unit))
        workers = [self.dde_worker, self.snapshot_worker]
        if self.topo:
            workers.append(self.topo_worker)
        if self.watch:
            workers.append(self.file_worker)
        if self.auto_enrich:
            workers.append(self.enrichment_worker)
        for worker in workers:
            def guarded(fn=worker):
                try:
                    fn()
                except Exception as exc:
                    self.state(fn.__name__, 'failed: ' + repr(exc))
                    self.emit('worker_failed', worker=fn.__name__, error=repr(exc))
                    self.stop.set()  # Supervisor restarts a failed worker process.
            thread = threading.Thread(target=guarded, name=worker.__name__, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.emit('baseline')
        last_status = 0
        try:
            while not self.stop.is_set():
                if stopfile.exists():
                    self.stop.set()
                try:
                    event = self.events.get(timeout=0.2)
                    log_event(event)
                except queue.Empty:
                    pass
                if time.monotonic() - last_status > 1:
                    try:
                        journal.sync()
                    except Exception as exc:
                        self.state('journal_error', str(exc))
                    with self.lock:
                        health = dict(self.health)
                    atomic_json(self.output / 'status.json', dict(stamp(), pid=os.getpid(),
                                running=True, session=str(self.session), **health))
                    last_status = time.monotonic()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            deadline = time.monotonic() + 10
            while any(t.is_alive() for t in self.threads) and time.monotonic() < deadline:
                try:
                    log_event(self.events.get(timeout=0.1))
                except queue.Empty:
                    pass
            while not self.events.empty():
                log_event(self.events.get_nowait())
            with self.lock:
                final_health = dict(self.health)
            atomic_json(self.output / 'status.json', dict(stamp(), running=False,
                        session=str(self.session), workers_still_running=[t.name for t in self.threads if t.is_alive()],
                        **final_health))
            try:
                journal.close()
            finally:
                guard.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'stop', 'status'], nargs='?', default='run')
    parser.add_argument('--output', type=Path, help='Defaults to DATA_FOLDER/metadata when --watch is given')
    parser.add_argument('--watch', type=Path, help='Existing SXM .dat folder; non-recursive')
    parser.add_argument('--follow-sxm', action='store_true', help='Discover each measurement folder from SpectSave')
    parser.add_argument('--topo-hz', type=float, default=10)
    parser.add_argument('--history-seconds', type=float, default=30)
    parser.add_argument('--no-topo', action='store_true', help='GUI/events only; another application may own IOCTL')
    parser.add_argument('--no-enrich', action='store_true', help='Capture metadata only')
    parser.add_argument('--modulation-unit', choices=('uV', 'mV', 'V'), help='Known amplitude-label unit for this session')
    args = parser.parse_args()
    if args.follow_sxm and args.watch:
        parser.error('--follow-sxm and --watch are alternatives')
    args.output = args.output or (args.watch / 'metadata' if args.watch else Path.home() / 'SXM_metadata')
    if args.command == 'stop':
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'STOP').touch()
        return
    if args.command == 'status':
        status = json.loads((args.output / 'status.json').read_text(encoding='utf-8'))
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(status['utc'])).total_seconds()
        status['heartbeat_age_seconds'] = age
        status['heartbeat_stale'] = age > 10
        print(json.dumps(status, indent=2))
        return
    if not math.isfinite(args.topo_hz) or not 0 < args.topo_hz <= 100:
        parser.error('--topo-hz must be between 0 (exclusive) and 100')
    if not math.isfinite(args.history_seconds) or not 0 < args.history_seconds <= 300:
        parser.error('--history-seconds must be between 0 (exclusive) and 300')
    if args.watch and not args.watch.is_dir():
        parser.error('--watch must be an existing directory')
    Recorder(args.output, args.watch, args.topo_hz, args.history_seconds,
             topo=not args.no_topo, auto_enrich=not args.no_enrich, modulation_unit=args.modulation_unit,
             follow_sxm=args.follow_sxm).run()


if __name__ == '__main__':
    main()
