"""Write PARAMETERS.md: every AnfatecSXMWriter parameter name, from PARAMS.

    python -m sxm_anfatec.tools.make_param_doc                    # writes sxm_anfatec/PARAMETERS.md
    python -m sxm_anfatec.tools.make_param_doc out.md
    python -m sxm_anfatec.tools.make_param_doc out.md snapshots/  # offline values from SXM_snapshot_*.json there

Names, SXM paths, meanings and DDE equivalents come from PARAMS, so they are always
filled in. The control type, the accepted values (dropdown items, radio captions) and
the current value exist only in the running GUI: they are filled in when
Femto_28_4.exe is open. Otherwise type and accepted values show '?' and the value is
taken from the newest SXM_snapshot_*.json in the folder given as second argument (values only).
Run it on the instrument PC to get the full table, and again after changing PARAMS.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

from ..bridge import SECTIONS, AnfatecSXMBridge, SXMBridgeError, _walk
from ..writer import EDIT_COMMIT, GROUPS, PARAMS, VERIFIED, AnfatecSXMWriter, SXMWriteError

_KIND_WORDS = {'edit': 'number', 'combo': 'dropdown', 'check': 'tick box', 'choice': 'radio', 'button': 'button'}


def _dde(description: str) -> str:
    """'DDE ScanPara Edit23' / 'DDE Edit24' / 'DDE DNCPara 1' in a PARAMS description -> DDE call."""
    m = re.search(r'DDE ((?:ScanPara )?Edit\d+|DNCPara \d+)', description)
    if not m:
        return ''
    code = m.group(1).removeprefix('ScanPara ')
    return f"`ScanPara('{code}', v)`" if code.startswith('Edit') else f"`DNCPara({code.split()[1]}, v)`"


def _cell(s) -> str:
    return str(s).replace('|', '\\|').replace('\n', ' ')


SNAPSHOT_DIR: Path | None = None   # folder with SXM_snapshot_*.json for offline values (second argument)


def _latest_snapshot() -> tuple[str, dict] | None:
    """(timestamp, parameters) of the newest SXM_snapshot_*.json in SNAPSHOT_DIR: values only, no types or options."""
    files = sorted(SNAPSHOT_DIR.glob('SXM_snapshot_*.json')) if SNAPSHOT_DIR else []
    if not files:
        return None
    data = json.loads(files[-1].read_text(encoding='utf-8'))
    parameters = data['parameters']
    for spec in SECTIONS:                          # derived values (DNC q, f_peak, tau_us), as the bridge adds them
        if spec.post and spec.name in parameters:
            parameters[spec.name] = spec.post(parameters[spec.name], False)
    return data.get('timestamp', files[-1].stem), parameters


def _snapshot_value(parameters: dict, path: str) -> str:
    try:
        return repr(_walk(parameters, path.split('.'), 'snapshot'))
    except SXMBridgeError:
        return '?'


def _live(w: AnfatecSXMWriter | None, name: str, snapshot: dict | None = None) -> tuple[str, str, str]:
    """(type, accepts, current value) from the running GUI, or '?' when it cannot be read.

    Without the GUI, the value comes from ``snapshot`` (old/ capture) when given."""
    if w is None:
        return '?', '?', _snapshot_value(snapshot, PARAMS[name][0]) if snapshot else '?'
    try:
        value = repr(w.get(name))
    except SXMBridgeError:
        return '?', '?', '?'
    try:
        kind = w.kind(name)
    except SXMWriteError:
        return 'derived', '-', value
    except SXMBridgeError:
        return '?', '?', value
    if kind == 'edit':
        commit = EDIT_COMMIT.get(name)
        accepts = f"number, commit '{commit}'" if commit else "number, needs `commit=`"
    elif kind == 'check':
        accepts = 'True / False'
    elif kind == 'button':
        accepts = f"press: `w.press('{name}')`"
    else:
        accepts = ' / '.join(f'`{o}`' for o in w.options(name))
    return _KIND_WORDS[kind], accepts, value


def _status(name: str, description: str) -> str:
    if '(status bar)' in description:
        return 'read-only'
    notes = ['confirmed' if name in VERIFIED else 'untested on instrument']
    if 'unconfirmed' in description or 'no caption' in description:
        notes.append('name by position')
    return ', '.join(notes)


def build() -> str:
    strict = AnfatecSXMBridge(strict=True)              # a non-strict read does not raise when SXM is closed
    live = False
    for spec in SECTIONS:
        try:
            strict.section(spec.name)
            live = True
            break
        except SXMBridgeError:
            pass
    w = AnfatecSXMWriter(AnfatecSXMBridge(strict=False)) if live else None
    snap = None if live else _latest_snapshot()

    if live:
        source = 'Type, accepted values and current value were read from the running SXM GUI.'
    else:
        source = ('**SXM was not running when this was generated**, so type and accepted values show `?`. '
                  + (f'Values are from the snapshot taken {snap[0]} (saved earlier), not current. ' if snap else '')
                  + 'Run `python -m sxm_anfatec.tools.make_param_doc` on the instrument PC to fill them in.')

    out = [
        '# SXM parameter names',
        '',
        f'Generated by `tools/make_param_doc.py` from `PARAMS` in `writer.py` on '
        f'{datetime.date.today().isoformat()}. Do not edit by hand: change `PARAMS` and regenerate.',
        '',
        source,
        '',
        '## Using a name',
        '',
        '```',
        'python -m sxm_anfatec.writer dnc.tc                 # value, type, accepted values',
        'python -m sxm_anfatec.writer dnc.tc "3 ms"          # show what would be sent',
        'python -m sxm_anfatec.writer dnc.tc "3 ms" --apply  # send it',
        '```',
        '',
        '```python',
        'from sxm_anfatec import AnfatecSXMWriter',
        'w = AnfatecSXMWriter()',
        "w.get('dnc.tc'); w.dnc.tc.options",
        "w.set('dnc.tc', '3 ms')",
        "w.set('dnc.drive', 0.5, commit='enter')   # number fields need commit= until listed in EDIT_COMMIT",
        '```',
        '',
        'The bridge path works wherever a name does, and is what `AnfatecSXMBridge.get()` reads.',
        '',
        'Column notes:',
        '',
        '- **Status**: `confirmed` = a write was seen to take effect on the instrument (`VERIFIED`); '
        '`untested on instrument` = tested only against a stand-in process; '
        '`name by position` = the field has no caption, so the name comes from its place in the window '
        'and may be wrong; `read-only` = computed by SXM, nothing to set.',
        '- **DDE**: the same parameter over DDE (`dde.py`), where known. '
        'DDE is write-only; reading always goes through the bridge.',
        '- Values are in the units SXM shows in its GUI.',
        '',
    ]
    for group, window in GROUPS.items():
        out += [f'## `{group}`: {window}', '',
                '| Name | Meaning | Type | Accepts | Value | Status | DDE | Bridge path |',
                '|---|---|---|---|---|---|---|---|']
        for name, (path, description) in PARAMS.items():
            if name.split('.')[0] != group:
                continue
            meaning = re.sub(r'\s*\((?:DDE [^)]*|status bar)\)', '', description)
            kind, accepts, value = _live(w, name, snap[1] if snap else None)
            out.append('| ' + ' | '.join(_cell(c) for c in (
                f'`{name}`', meaning, kind, accepts, value, _status(name, description), _dde(description),
                f'`{path}`')) + ' |')
        out.append('')

    out += [
        '## Other DDE commands',
        '',
        'Used by `sxm_ncafm_control`; not GUI parameters, so not in `PARAMS`.',
        '',
        '| Command | What it does |',
        '|---|---|',
        "| `SetChannel(n, v)` | set output channel n (channel 0 = Z when feedback is off) |",
        "| `a:=GetChannel(n); writeln(a);` | read channel n (reply has a decimal comma) |",
        "| `FeedPara('enable', 1)` / `FeedPara('enable', 0)` | feedback off (1) / on (0) |",
        "| `ScanPara('EditNN', v)` | any scan-window number field by its Edit number |",
        '',
        'Only five Edit numbers are known in this code (Edit22, 23, 24, 27, 32; see the DDE column). '
        'The full list is in Anfatec\'s *SXM-Software 28.8 Language Description* manual, which is not '
        'in this repository.',
        '',
    ]
    return '\n'.join(out)


def main(argv: list | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    global SNAPSHOT_DIR
    target = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / 'PARAMETERS.md'
    SNAPSHOT_DIR = Path(argv[1]) if len(argv) > 1 else None
    target.write_text(build(), encoding='utf-8')
    print(f'wrote {target}')


if __name__ == '__main__':
    main()
