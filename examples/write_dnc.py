"""Example: change one SXM parameter through AnfatecSXMWriter, check it, and put it back.

    python -m sxm_anfatec.examples.write_dnc                               # look only: sends nothing
    python -m sxm_anfatec.examples.write_dnc --apply                       # dnc.tc -> next option, verify, restore
    python -m sxm_anfatec.examples.write_dnc --apply --name dnc.output_gain --value "±1"
    python -m sxm_anfatec.examples.write_dnc --apply --name amp.tau
    python -m sxm_anfatec.examples.write_dnc --apply --name dnc.drive --value 0.0 --commit enter

Names: ``python -m sxm_anfatec.writer`` lists them all. Without --value the next option of
a dropdown / radio group (or the other state of a tick box) is used. The original value
is always restored after --hold seconds, also on Ctrl+C.
"""
import argparse
import time

from ..writer import GROUPS, AnfatecSXMWriter, SXMWriteError


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', '--path', dest='name', default='dnc.tc',
                    help='short name (dnc.tc, amp.tau, dnc.output_gain, ...) or bridge path')
    ap.add_argument('--value', help='target value (default: next option)')
    ap.add_argument('--commit', choices=('change', 'enter'), help='required for number fields')
    ap.add_argument('--apply', action='store_true', help='actually send; otherwise dry run')
    ap.add_argument('--hold', type=float, default=3.0, help='seconds before restoring the original value')
    args = ap.parse_args()

    w = AnfatecSXMWriter()
    group = args.name.split('.')[0]
    if group in GROUPS:
        print(w.table(group), end='\n\n')
    print(w.describe(args.name))

    before, options = w.get(args.name), w.options(args.name)
    if args.value is not None:
        target = args.value
    elif options == [False, True]:
        target = not before
    elif options:
        i = [str(o) for o in options].index(str(before))
        target = options[i + 1] if i + 1 < len(options) else options[i - 1]
    else:
        raise SystemExit('Number field: give --value (and --commit).')

    plan = w.set(args.name, target, commit=args.commit, dry_run=True)
    print(f'\nplan: {plan["before"]!r} -> {plan["requested"]!r} via {plan["sent"]}')
    if not args.apply:
        print('Dry run, nothing sent. Add --apply to change it (it is restored afterwards).')
        return

    changed = None
    try:
        changed = w.set(args.name, target, commit=args.commit)
        print(f'set:      SXM now shows {changed["after"]!r}')
        time.sleep(args.hold)
    except SXMWriteError as exc:
        print(f'FAILED:   {exc}')
        changed = exc.result
    finally:
        if changed and changed.get('after') is not None and changed['after'] != before:
            restored = w.set(args.name, before, commit=args.commit)
            print(f'restored: SXM shows {restored["after"]!r}')


if __name__ == '__main__':
    main()
