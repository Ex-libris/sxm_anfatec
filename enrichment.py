"""Restartable background creation of enriched copies. Raw paths are read-only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from .spectroscopy_metadata import prepare_export, publish_copy, typed_filename, event_not_before


def signature(path):
    s = path.stat()
    return s.st_size, s.st_mtime_ns


def basename(value):
    return value.replace('\\', '/').split('/')[-1]


class CopyEnricher:
    def __init__(self, watch, metadata, emit=lambda *a, **k: None):
        self.watch = Path(watch).resolve()
        self.metadata = Path(metadata).resolve()
        self.destination = self.watch / 'enriched'
        self.manifest = self.metadata / 'enrichment_status.json'
        self.emit = emit
        self.index = {}
        self.finished = {}
        self.jobs = {}
        self.last_written = None

    def process_once(self):
        """Discover complete capture files; retry pending jobs and recover after restart."""
        from .recorder import atomic_json, stamp
        if self.destination.resolve().parent != self.watch:
            raise ValueError('Enriched folder must be a real subfolder of the raw data directory')
        groups = {}
        present = set()
        for session in sorted(self.metadata.glob('session_*')):
            if not session.is_dir():
                continue
            try:
                session_info = json.loads((session / 'session.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                session_info = {}
            unit = session_info.get('modulation_unit')
            if unit not in ('uV', 'mV', 'V'):
                unit = None
            for path in session.glob('capture_*.json'):
                present.add(path)
                sig = signature(path)
                cached = self.index.get(path)
                if cached is None or cached[0] != sig:
                    try:
                        capture = json.loads(path.read_text(encoding='utf-8'))
                        trigger = capture['trigger']
                    except (OSError, ValueError, KeyError):
                        # An incomplete/corrupt capture is never used for enrichment.
                        continue
                    self.index[path] = (sig, trigger)
                else:
                    trigger = cached[1]
                save = trigger.get('item') == 'SpectSave'
                stable = trigger.get('kind') == 'file_stable'
                if not save and not stable:
                    continue
                rawname = basename(trigger.get('value' if save else 'path', ''))
                if not rawname.lower().endswith('.dat') or rawname in ('.', '..'):
                    continue
                key = session.name + '/' + rawname.casefold()
                group = groups.setdefault(key, {'name': rawname, 'saves': [], 'stable': [], 'unit': unit})
                group['saves' if save else 'stable'].append((path, trigger))
        for missing in set(self.index) - present:
            del self.index[missing]
        for key, group in groups.items():
            source = self.watch / group['name']
            if source.resolve().parent != self.watch:
                self._record(key, 'blocked', reason='Raw path escapes data directory')
                continue
            if len(group['saves']) == 1:
                save_event = group['saves'][0][1]
                group['stable'] = [pair for pair in group['stable'] if event_not_before(pair[1], save_event)]
                hashes = {pair[1].get('sha256') for pair in group['stable']}
                if len(hashes) == 1 and len(group['stable']) > 1:
                    group['stable'] = [max(group['stable'], key=lambda pair: pair[1].get('utc', str(pair[1]['monotonic_ns'])))]
            if len(group['saves']) != 1 or len(group['stable']) != 1:
                reason = 'Waiting for both save metadata and stable-file metadata'
                state = 'pending'
                if len(group['saves']) > 1 or len(group['stable']) > 1:
                    reason, state = 'Multiple captures for the same filename; association ambiguous', 'blocked'
                self._record(key, state, source=str(source), reason=reason)
                continue
            savepath, trigger = group['saves'][0]
            stablepath, _ = group['stable'][0]
            try:
                fingerprint = (signature(source), signature(savepath), signature(stablepath), group['unit'])
                previous = self.finished.get(key)
                if (previous and previous[0] == fingerprint and previous[1].exists()
                        and signature(previous[1]) == previous[2]
                        and self.jobs.get(key, {}).get('state') == 'complete'):
                    continue
                capture = json.loads(savepath.read_text(encoding='utf-8'))
                evidence = json.loads(stablepath.read_text(encoding='utf-8'))
                result, fields, rawhash = prepare_export(source, capture, evidence, savepath.name, group['unit'])
                target = self.destination / typed_filename(source.name, fields['Spectroscopy.Type'])
                if target.exists() and target.read_bytes() != result:
                    # Never overwrite a prior enriched result or any unrelated file.
                    tag = hashlib.sha256(result).hexdigest()[:16]
                    target = target.with_name(target.stem + '__' + tag + target.suffix)
                if target.exists():
                    if target.read_bytes() != result:
                        raise ValueError('Versioned output already exists with different content')
                    if hashlib.sha256(source.read_bytes()).hexdigest() != rawhash:
                        raise ValueError('Source changed during verification')
                else:
                    publish_copy(source, target, result, rawhash)
                self.finished[key] = (fingerprint, target, signature(target))
                self._record(key, 'complete', source=str(source), output=str(target),
                             spectroscopy_type=fields['Spectroscopy.Type'],
                             source_sha256=rawhash, output_sha256=hashlib.sha256(result).hexdigest(),
                             capture=str(savepath), stable_capture=str(stablepath))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._record(key, 'pending', source=str(source), reason=str(exc))
        # Record progress durably; a fresh worker can reconstruct everything from captures.
        serial = json.dumps(self.jobs, sort_keys=True)
        if serial != self.last_written:
            atomic_json(self.manifest, dict(stamp(), jobs=self.jobs))
            self.last_written = serial
        counts = {name: sum(j['state'] == name for j in self.jobs.values())
                  for name in ('complete', 'pending', 'blocked')}
        return counts

    def _record(self, key, state, **fields):
        value = dict(state=state, **fields)
        if self.jobs.get(key) != value:
            self.jobs[key] = value
            self.emit('enrichment_' + state, job=key, **fields)
