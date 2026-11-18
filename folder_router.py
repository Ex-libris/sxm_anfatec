"""Follow SpectSave paths and route durable captures into each measurement folder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

from .enrichment import CopyEnricher
from .recorder import atomic_json, stamp


def spectrum_path(trigger):
    if trigger.get('item') != 'SpectSave':
        return None
    value = str(trigger.get('value', '')).strip().strip('"')
    p = Path(value)
    if not p.is_absolute() or p.suffix.lower() != '.dat':
        return None
    return p.resolve()


def fingerprint(source):
    """Stable read only: never opens the source with write access."""
    source = Path(source)
    before = source.stat()
    h = hashlib.sha256()
    with source.open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Raw file changed during fingerprinting')
    return {'sha256': h.hexdigest(), 'size': after.st_size, 'mtime_ns': after.st_mtime_ns}


class FolderRouter:
    def __init__(self, state, current_session, emit=lambda *a, **k: None, settle_seconds=2):
        self.state, self.current = Path(state), Path(current_session)
        self.emit, self.settle_seconds = emit, settle_seconds
        self.pending = {}
        self.discovered = set()
        self.first = True
        self.jobs = {}
        self.enrichers = {}
        self.folder_results = {}
        self.last_manifest = None

    def process_once(self):
        sessions = list(self.state.glob('session_*')) if self.first else [self.current]
        self.first = False
        for session in sessions:
            for cp in session.glob('capture_*.json'):
                if cp in self.discovered:
                    continue
                try:
                    capture = json.loads(cp.read_text(encoding='utf-8'))
                    source = spectrum_path(capture['trigger'])
                except (OSError, ValueError, KeyError):
                    continue
                self.discovered.add(cp)
                if source is not None:
                    self.pending[str(cp)] = (cp, capture, source)
        for key, (cp, capture, source) in list(self.pending.items()):
            try:
                if not source.parent.is_dir():
                    raise ValueError('Measurement drive/folder is unavailable; retrying')
                proof = capture.get('source_file_at_capture')
                if not proof or not proof.get('sha256'):
                    raise ValueError('No saved raw-file fingerprint; cannot recover a verified association')
                current = fingerprint(source)
                if current['sha256'] != proof['sha256']:
                    raise ValueError('Raw file differs from the acquisition capture; not attaching stale metadata')
                previous = self.jobs.get(key, {})
                if previous.get('_signature') != current:
                    self.jobs[key] = {'state': 'settling', 'source': str(source), '_signature': current,
                                      '_observed_at': time.monotonic()}
                    continue
                if time.monotonic() - previous['_observed_at'] < self.settle_seconds:
                    continue
                metadata = source.parent / 'metadata'
                if metadata.resolve().parent != source.parent:
                    raise ValueError('Metadata folder resolves outside the measurement folder')
                epoch = str(capture['trigger'].get('connection_id') or cp.parent.name)
                if not re.fullmatch(r'[A-Za-z0-9_-]+', epoch):
                    raise ValueError('Invalid connection identifier')
                target_session = metadata / ('session_' + epoch)
                target_session.mkdir(parents=True, exist_ok=True)
                try:
                    session_info = json.loads((cp.parent / 'session.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    session_info = {}
                info = target_session / 'session.json'
                if not info.exists():
                    atomic_json(info, dict(session_info, watch=str(source.parent), output=str(metadata),
                                           central_session=str(cp.parent), connection_id=epoch))
                target_capture = target_session / cp.name
                if not target_capture.exists():
                    atomic_json(target_capture, capture)
                elif json.loads(target_capture.read_text(encoding='utf-8')) != capture:
                    raise ValueError('Existing capture differs; refusing replacement')
                evidence_path = target_session / ('capture_stable_' + cp.stem + '.json')
                if not evidence_path.exists():
                    trigger = dict(stamp(), kind='file_stable', path=str(source), sha256=current['sha256'],
                                   size=current['size'], completion_confirmed=False,
                                   recovered_after_restart=cp.parent != self.current)
                    atomic_json(evidence_path, {'schema_version': 1, 'trigger': trigger,
                                               'source': 'save-time fingerprint plus subsequent stable read'})
                self.enrichers.setdefault(str(source.parent), CopyEnricher(source.parent, metadata, self.emit))
                self.folder_results.pop(str(source.parent), None)
                self.folder_results[str(source.parent)] = {'complete': 0, 'pending': 0, 'blocked': 0}
                self.jobs[key] = {'state': 'metadata_saved', 'source': str(source),
                                  'capture': str(target_capture), 'metadata': str(metadata)}
                del self.pending[key]
                self.emit('folder_followed', folder=str(source.parent), capture=str(target_capture))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Retain settle observation on temporary failure; never use new GUI readings.
                previous = self.jobs.get(key, {})
                self.jobs[key] = dict(previous, state='pending', source=str(source), reason=str(exc))
        for folder, worker in list(self.enrichers.items()):
            try:
                result = worker.process_once()
                self.folder_results[folder] = result
                if result['pending'] == 0 and result['blocked'] == 0:
                    del self.enrichers[folder]  # Completed old folders need no continuous polling.
            except Exception as exc:
                self.folder_results[folder] = {'complete': 0, 'pending': 1, 'blocked': 0}
                self.emit('folder_enrichment_error', folder=folder, error=str(exc))
        counts = {'complete': 0, 'pending': len(self.pending), 'blocked': 0}
        for result in self.folder_results.values():
            for k, value in result.items():
                counts[k] += value
        public_jobs = {k: {f: v for f, v in job.items() if not f.startswith('_')} for k, job in self.jobs.items()}
        state = {'jobs': public_jobs, 'folders': list(self.folder_results), 'counts': counts}
        serial = json.dumps(state, sort_keys=True)
        if serial != self.last_manifest:
            atomic_json(self.state / 'routing_status.json', dict(stamp(), **state))
            self.last_manifest = serial
        return dict(counts, folders=list(self.folder_results))
