"""Enriched copies of single-spectrum SXM .dat files; no instrument connection."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

BEGIN = b';Meta.Begin: sxm-enrichment/1'
END = b';Meta.End: sxm-enrichment/1'


def event_not_before(later, earlier):
    """Wall time survives reboot; monotonic clocks do not. Legacy fallback for same-session records."""
    if later.get('utc') and earlier.get('utc'):
        try:
            return dt.datetime.fromisoformat(later['utc']) >= dt.datetime.fromisoformat(earlier['utc'])
        except ValueError:
            pass
    return later['monotonic_ns'] >= earlier['monotonic_ns']


def original_bytes(data):
    """Remove our one complete preamble block, preserving all other bytes."""
    out, inside, found, table = [], False, False, False
    for line in data.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == BEGIN:
            if inside or found or table:
                raise ValueError('Duplicate or misplaced enrichment block')
            inside, found = True, True
        elif stripped == END:
            if not inside:
                raise ValueError('Unmatched metadata end')
            inside = False
        elif not inside:
            out.append(line)
            if stripped.lower().startswith(b'time\t'):
                table = True
    if inside:
        raise ValueError('Incomplete enrichment block')
    return b''.join(out)


def parse(data):
    lines = data.splitlines(keepends=True)
    index = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith(b'time\t')), None)
    if index is None:
        raise ValueError('No time/dz/Bias column header found')
    columns = lines[index].decode('ascii').strip().split()
    if len(set(columns)) != len(columns) or any(is_number(c) for c in columns):
        raise ValueError('Matrix metadata requires per-spectrum association; not supported here')
    rows = []
    for line in lines[index + 1:]:
        if not line.strip():
            continue
        try:
            row = list(map(float, line.split()))
        except ValueError as exc:
            raise ValueError('Non-numeric row: matrix/unit rows are not supported') from exc
        if len(row) != len(columns) or not all(math.isfinite(x) for x in row):
            raise ValueError('Malformed or non-finite spectrum data')
        rows.append(row)
    if not rows:
        raise ValueError('Empty spectrum')
    headers = {}
    for line in lines[:index]:
        text = line.decode('cp1252').strip().lstrip(';')
        if ':' in text:
            key, value = text.split(':', 1)
            headers[key.strip()] = value.strip()
    return lines, index, headers, {c: [r[i] for r in rows] for i, c in enumerate(columns)}


def is_number(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def spectroscopy_type(data):
    """Classify recorded command axes, independently of save-time GUI settings."""
    _, _, _, columns = parse(original_bytes(data))
    z = 'dz' in columns and min(columns['dz']) != max(columns['dz'])
    bias = 'Bias' in columns and min(columns['Bias']) != max(columns['Bias'])
    if z and bias:
        return 'mixed_sweep'
    if z:
        return 'z_sweep'
    if bias:
        return 'bias_sweep'
    return 'constant_axes'


def typed_filename(original_name, kind):
    if kind not in ('bias_sweep', 'z_sweep', 'mixed_sweep', 'constant_axes'):
        raise ValueError('Unknown spectroscopy classification')
    name = Path(original_name)
    return name.stem + '_' + kind + name.suffix


def summaries(data):
    _, _, headers, cols = parse(data)
    times = cols['time']
    out = {'Acquisition.RecordedPoints': len(times), 'Acquisition.Channels': ', '.join(cols),
           'Acquisition.DataClockFirst_s': times[0], 'Acquisition.DataClockLast_s': times[-1],
           'Acquisition.FirstToLastPoint_s': times[-1] - times[0],
           'Acquisition.WallClockElapsed_s': 'unavailable: no verified start event',
           'Acquisition.Position_xy': headers.get('x/y-Pos', 'unavailable')}
    intervals = [b-a for a, b in zip(times, times[1:])]
    monotonic = all(x > 0 for x in intervals)
    out['Acquisition.TimeStrictlyIncreasing'] = monotonic
    if intervals and monotonic:
        out['Acquisition.PointIntervalMedian_s'] = statistics.median(intervals)
        out['Acquisition.PointIntervalMin_s'] = min(intervals)
        out['Acquisition.PointIntervalMax_s'] = max(intervals)
    try:
        delay = [float(x) for x in headers['Delays 1/Aqu/3/4/dead'].split('/')]
        if len(delay) != 5:
            raise ValueError('delay count')
        for key, value in zip(('Delay1', 'AcquisitionPerPoint', 'Delay3', 'Delay4', 'DeadTime'), delay):
            out['Timing.' + key + '_s'] = value
        out['Timing.NominalPointAcquisitionSum_s'] = len(times) * delay[1]
        out['Timing.NominalPointAcquisitionSumNote'] = 'rows times acquisition-per-point; excludes overhead, unrecorded repeats and averaging effects'
    except (KeyError, ValueError):
        out['Timing.Delays'] = 'unavailable or unrecognised header'
    for label, key in [('CountAverageAndSNR', 'CntAverage/SNR'), ('SlewRatesBiasAndTopo', 'Slew Rate Bias/Topo')]:
        out['Timing.' + label] = headers.get(key, 'unavailable')
    moving = []
    for name, unit in [('dz', 'nm'), ('Bias', 'mV')]:
        if name not in cols:
            continue
        values = cols[name]
        diffs = [b-a for a, b in zip(values, values[1:])]
        steps = [abs(d) for d in diffs if d != 0]
        signs = [1 if d > 0 else -1 for d in diffs if d != 0]
        legs = 1 + sum(a != b for a, b in zip(signs, signs[1:])) if signs else 0
        prefix = 'Sweep.' + name + '.'
        for key, value in [('First', values[0]), ('Last', values[-1]), ('Minimum', min(values)),
                           ('Maximum', max(values)), ('Span', max(values)-min(values)),
                           ('NetChange', values[-1]-values[0]), ('RecordedTravel', sum(steps)),
                           ('StepMedianNonzero', statistics.median(steps) if steps else 0)]:
            out[prefix + key + '_' + unit] = value
        out[prefix + 'DirectionLegs'] = legs
        if monotonic and times[-1] > times[0]:
            out[prefix + 'MeanRecordedTravelRate_' + unit + '_per_s'] = sum(steps) / (times[-1]-times[0])
        if steps:
            moving.append(name)
    out['Sweep.MovingAxesFromData'] = ', '.join(moving) or 'none'
    out['Spectroscopy.Type'] = spectroscopy_type(data)
    out['Spectroscopy.TypeSource'] = 'variation of saved Bias/dz columns; independent of save-time GUI mode'
    out['Sweep.TravelScope'] = 'between saved rows only; excludes lead-in, return and unsaved motion'
    if 'Topo' in cols and 'dz' in cols:
        reference = [z-dz for z, dz in zip(cols['Topo'], cols['dz'])]
        out['Height.TopoMinusDzMean_nm'] = statistics.mean(reference)
        out['Height.TopoMinusDzRange_nm'] = max(reference)-min(reference)
        out['Height.TopoMinusDzSource'] = 'saved data; inspect residual range before assuming a constant reference'
    return out


def instrument_fields(capture, modulation_unit=None):
    params = capture.get('gui', {}).get('parameters', {})
    out = {'Capture.Event': capture['trigger'].get('item', capture['trigger']['kind']),
           'Capture.EventUTC': capture['trigger']['utc'],
           'Capture.ReadStartUTC': capture['read_start']['utc'],
           'Capture.ReadEndUTC': capture['read_end']['utc'],
           'Capture.Latency_ms': capture['trigger_to_read_ms'],
           'Capture.ReadDuration_ms': (capture['read_end']['monotonic_ns']-capture['read_start']['monotonic_ns'])/1e6,
           'GUI.Provenance': 'displayed values at save; not independently verified acquisition settings'}
    scan = params.get('Scan', {})
    for key, target in (('Angle', 'Angle_deg'), ('x-Center', 'CenterX_nm'),
                        ('y-Center', 'CenterY_nm'), ('Range', 'Range_nm'),
                        ('Pixel', 'Pixel'), ('Speed', 'SpeedDisplayed')):
        out['GUI.Scan.' + target] = scan.get(key)
    out['GUI.Scan.GeometrySource'] = 'live Scan parameters at GUI read; not verified as geometry of the image selected in Select'
    out['Position.TransformApplied'] = False
    out['Position.FrameNote'] = 'raw .dat x/y-Pos, live Scan geometry and Select display coordinates are separate sources; rotation sign/origin conventions unverified'
    selection = params.get('Select', {})
    out['GUI.Select.Status'] = ('unavailable: ' + selection['_error'] if '_error' in selection
                              else 'captured' if selection.get('coordinates') else 'unavailable: not captured')
    for axis in ('X', 'Y', 'Z'):
        coordinate = selection.get('coordinates', {}).get(axis, {})
        out[f'GUI.Select.{axis}Displayed'] = coordinate.get('value')
        out[f'GUI.Select.{axis}UnitDisplayed'] = coordinate.get('unit_displayed') or 'not shown / unavailable'
    for source, target in (('parent_path', 'ImageTreePath'), ('selected_text', 'SelectedLabel'),
                           ('coordinate_labels', 'CoordinateLabels'), ('source', 'Source'),
                           ('association', 'Association'), ('coordinate_frame', 'CoordinateFrame'),
                           ('z_meaning', 'ZMeaning'), ('read_start_utc', 'ReadStartUTC'),
                           ('read_end_utc', 'ReadEndUTC')):
        out['GUI.Select.' + target] = selection.get(source)
    errors = {section: value['_error'] for section, value in params.items()
              if isinstance(value, dict) and '_error' in value}
    if capture.get('gui', {}).get('_error'):
        errors['snapshot'] = capture['gui']['_error']
    out['Capture.GUIReadErrors'] = errors or 'none reported'
    dnc = params.get('Dynamic Non-Contact R / Phi', {})
    edits = dnc.get('unmapped_numeric_controls', [])
    if len(edits) == 4:
        edits = sorted(edits, key=lambda x: x['rect'][1])
        for key, edit in zip(('SweepStart_Hz', 'SweepStop_Hz', 'Used_f0_Hz', 'Drive_GUI'), edits):
            out['GUI.DNC.' + key] = edit['value']
        out['GUI.DNC.Used_f0_Source'] = 'third numeric field; existing ncAFM positional mapping, not independent hardware verification'
    else:
        out['GUI.DNC.Used_f0_Hz'] = 'unavailable: unexpected DNC control count'
    for key in ('TimeConstant', 'RollOff', 'Range', 'Input Gain InA', 'f_peak', 'q', 'tau_us'):
        out['GUI.DNC.' + key.replace(' ', '_')] = dnc.get(key)
    lockin = params.get('Multi Channel LockIn', {})
    for key in ('TimeConstant', 'RollOff', 'Input Gain InB', 'Output Channel', 'Show'):
        out['GUI.Lockin.' + key.replace(' ', '_')] = lockin.get(key)
    for n in (1, 2, 3):
        lia = lockin.get(f'Lia{n}', {})
        prefix = f'GUI.Lockin.Lia{n}.'
        out[prefix+'FrequencyDisplayed_Hz'] = lia.get('Value1')
        out[prefix+'Phase_deg'] = lia.get('Phase')
        out[prefix+'Link'] = lia.get('Link')
    out['GUI.Lockin.Lia1.ModulationAmplitudeDisplayed'] = lockin.get('Lia1', {}).get('Amplitude')
    out['GUI.Lockin.Lia1.ModulationAmplitudeUnit'] = modulation_unit or 'unavailable: label not read by bridge'
    out['GUI.Lockin.Lia1.ModulationAmplitudeUnitSource'] = 'operator supplied for this export' if modulation_unit else 'unknown'
    out['GUI.Lockin.Lia1.ModulationAmplitudeConvention'] = 'rms oscillator setting; junction amplitude requires routing/range/attenuation calibration'
    for section in ('Amplitude feedback', 'PLL', 'z feedback', 'zControl', 'Feedback mode', 'Spectroscopy', 'Spectroscopy Options'):
        for key, value in params.get(section, {}).items():
            out['GUI.'+section.replace(' ', '_')+'.'+key.replace(' ', '_')] = value
    samples = capture.get('topo_before_trigger', [])
    sample = capture.get('topo_at_read_end')
    if sample:
        out['Height.ControllerAtSave_nm'] = sample['value']
        out['Height.ControllerAtSaveUTC'] = sample['utc']
        out['Height.ControllerScale_nm_per_count'] = sample['scale']
    # Preserve evidence without silently turning a save-time sample into z0.
    out['Height.ReferenceFromStartEvent_nm'] = 'unavailable: start event not verified'
    if samples:
        out['Height.PreSaveHistoryRange_nm'] = max(s['value'] for s in samples)-min(s['value'] for s in samples)
    return out


def format_block(fields, newline=b'\r\n'):
    lines = [BEGIN]
    for key, value in fields.items():
        if value is None:
            value = 'unavailable'
        elif isinstance(value, float):
            value = format(value, '.12g')
        elif isinstance(value, (dict, list, bool)):
            value = json.dumps(value, ensure_ascii=True)
        value = str(value).replace('\r', r'\r').replace('\n', r'\n')
        lines.append(f';Meta.{key}: {value}'.encode('ascii', errors='backslashreplace'))
    return newline.join(lines + [END]) + newline


def enriched_bytes(data, fields):
    original = original_bytes(data)
    lines, index, _, _ = parse(original)
    newline = b'\r\n' if lines[index].endswith(b'\r\n') else b'\n'
    return b''.join(lines[:index]) + format_block(fields, newline) + b''.join(lines[index:])


def prepare_export(source, capture, evidence, capture_name, modulation_unit=None):
    """Build in memory from a matched capture pair; never open source for writing."""
    source = Path(source)
    before = source.stat()
    data = source.read_bytes()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source is changing; export postponed')
    original = original_bytes(data)
    digest = hashlib.sha256(original).hexdigest()
    def basename(value):
        return value.replace('\\', '/').split('/')[-1].casefold()
    if (capture['trigger'].get('item') != 'SpectSave'
            or basename(capture['trigger'].get('value', '')) != source.name.casefold()
            or basename(evidence['trigger'].get('path', '')) != source.name.casefold()
            or evidence['trigger'].get('sha256') != digest):
        raise ValueError('Filename or source hash does not match saved metadata')
    if not event_not_before(evidence['trigger'], capture['trigger']):
        raise ValueError('Stable-file capture predates save notification')
    fields = {'OriginalFilename': source.name, 'OriginalSHA256': digest, 'CaptureFile': capture_name,
              'Association': 'SpectSave filename plus stable-file SHA256; GUI values are save-time observations'}
    fields.update(summaries(original))
    fields.update(instrument_fields(capture, modulation_unit))
    fields['Capture.Contradictions'] = 'GUI sweep fields may differ from the saved sweep; use Sweep.* derived from data'
    result = enriched_bytes(original, fields)
    if original_bytes(result) != original:
        raise ValueError('Original-byte verification failed')
    return result, fields, hashlib.sha256(data).hexdigest()


def publish_copy(source, destination, result, source_hash):
    """Flush a temporary copy, recheck raw bytes, then publish without replacement."""
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('In-place enrichment is prohibited')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + destination.name + '.', suffix='.tmp', dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(result)
            fh.flush()
            os.fsync(fh.fileno())
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise ValueError('Source changed while preparing copy; export postponed')
        if os.name == 'nt':
            os.rename(temporary, destination)  # Windows refuses an existing target.
        else:
            os.link(temporary, destination)  # Atomic publication without replacement.
    finally:
        temporary.unlink(missing_ok=True)


def export(source, session, destination, modulation_unit=None):
    source, session, destination = Path(source), Path(session), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('Use a separate output copy; in-place enrichment is not enabled')
    digest = hashlib.sha256(original_bytes(source.read_bytes())).hexdigest()
    captures = [(p, json.loads(p.read_text(encoding='utf-8'))) for p in session.glob('capture_*.json')]
    stable = [(p, c) for p, c in captures if c['trigger'].get('sha256') == digest
              and c['trigger'].get('path', '').replace('\\', '/').split('/')[-1] == source.name]
    saves = [(p, c) for p, c in captures if c['trigger'].get('item') == 'SpectSave'
             and c['trigger'].get('value', '').replace('\\', '/').split('/')[-1] == source.name]
    if len(stable) != 1 or len(saves) != 1:
        raise ValueError('Requires one filename-matched SpectSave capture and one matching stable-file hash; ambiguous association refused')
    cp, capture = saves[0]
    result, fields, source_hash = prepare_export(source, capture, stable[0][1], cp.name, modulation_unit)
    publish_copy(source, destination, result, source_hash)
    return fields


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('files', type=Path, nargs='+')
    ap.add_argument('--session', type=Path, required=True)
    ap.add_argument('--output-directory', type=Path, required=True)
    ap.add_argument('--modulation-unit', choices=('uV', 'mV', 'V'), help='Only if the SXM amplitude label unit is known for these captures')
    args = ap.parse_args()
    for source in args.files:
        target = args.output_directory / typed_filename(source.name, spectroscopy_type(source.read_bytes()))
        export(source, args.session, target, args.modulation_unit)
        print(target)


if __name__ == '__main__':
    main()
