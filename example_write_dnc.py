"""Example: talk to one Dynamic Non-Contact control through AnfatecSXMWriter.

    python example_write_dnc.py                          # look only: sends nothing
    python example_write_dnc.py --apply                  # TimeConstant -> neighbouring option, verify, restore
    python example_write_dnc.py --apply --path dnc.Range --value "±1"
    python example_write_dnc.py --apply --path dnc.unmapped_numeric_controls.3 --value 0.0 --commit enter

Without --value the next option of a combo / radio group is used. The original value is
always restored after --hold seconds, also on Ctrl+C.
"""
import argparse
import time
from collections.abc import Mapping

from AnfatecSXMWriter import AnfatecSXMWriter, SXMWriteError


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--path', default='Dynamic Non-Contact R / Phi.TimeConstant')
    ap.add_argument('--value', help='target value (default: neighbouring option)')
    ap.add_argument('--commit', choices=('change', 'enter'), help='required for edit fields')
    ap.add_argument('--apply', action='store_true', help='actually send; otherwise dry run')
    ap.add_argument('--hold', type=float, default=3.0, help='seconds before restoring the original value')
    args = ap.parse_args()

    w = AnfatecSXMWriter()
    dnc = w.bridge.dynamic
    print('DNC now: ' + '  '.join(f'{k}={dnc[k]!r}' for k in ('TimeConstant', 'RollOff', 'Range', 'Input Gain InA')))
    print(f'         Q={dnc.q:g}  fPeak={dnc.f_peak:g} Hz  tau={dnc.tau_us:g} us   (derived, read-only)')
    print('\nWritable DNC paths:')
    for p, kind in w.writable().items():
        if p.startswith('Dynamic Non-Contact'):
            print(f'  {kind:7} {p} = {w.bridge.get(p)!r}')

    before = w.bridge.get(args.path)
    if isinstance(before, Mapping):                     # unmapped_numeric_controls.N -> {'value', 'rect'}
        before = before['value']
    options = w.options(args.path)
    print(f'\n{args.path} = {before!r}' + (f'   options: {options}' if options else ''))

    if args.value is not None:
        target = args.value
    elif options and not isinstance(before, bool):
        i = [str(o) for o in options].index(str(before))
        target = options[i + 1] if i + 1 < len(options) else options[i - 1]
    elif options:
        target = not before
    else:
        raise SystemExit('Edit field: give --value (and --commit).')

    plan = w.set(args.path, target, commit=args.commit, dry_run=True)
    print(f'plan: {plan["before"]!r} -> {plan["requested"]!r} via {plan["sent"]}')
    if not args.apply:
        print('\nDry run, nothing sent. Add --apply to change it (it is restored afterwards).')
        return

    changed = None
    try:
        changed = w.set(args.path, target, commit=args.commit)
        print(f'set:      SXM now shows {changed["after"]!r}')
        print(f'          DNC status: {w.bridge.dynamic["Status"]}')
        time.sleep(args.hold)
    except SXMWriteError as exc:
        print(f'FAILED:   {exc}')
        changed = exc.result
    finally:
        if changed and changed.get('after') is not None and changed['after'] != before:
            restored = w.set(args.path, before, commit=args.commit)
            print(f'restored: SXM shows {restored["after"]!r}')


if __name__ == '__main__':
    main()
