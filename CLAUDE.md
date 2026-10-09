# CLAUDE.md

Guidance for Claude Code in this repository. User-facing overview: `README.md`.

## What this is

The single source of every module that talks to the Anfatec SXM software (`bridge`,
`writer`, `dde`, `sxm_remote`, `driver`). Programs (sxm_ncafm_control, sxm_sts_control,
scripts) import it; none of them keeps a copy. Change SXM communication **here**, never
in a consumer.

## Layout and imports

- The repository folder **is** the package (flat layout, like sxm_ncafm_control). It is
  imported as `sxm_anfatec` when its parent folder is on `sys.path`: run programs as
  `python -m ...` from that parent. Inside the package use relative imports
  (`from .bridge import ...`).
- `tools/` and `examples/` are subpackages: run as `python -m sxm_anfatec.tools.<name>`.
- `__init__.py` exports names lazily. `import sxm_anfatec` must stay free of side
  effects: no DDE conversation (importing `sxm_remote` opens one and raises `DDEError`
  if SXM is closed), no driver handle, no `win32file` import.
- Keep the public names stable (`AnfatecSXMBridge`, `AnfatecSXMWriter`, `SXMIOCTL`,
  `CHANNELS`, `RealDDEClient`, `MockDDEClient`, the writer's short names). Consumers
  and their tests depend on them. `MockDDEClient` must match `RealDDEClient` method for
  method.
- `sxm_remote.py` is Anfatec's vendor code: keep edits minimal.

## Rules from the instrument

- `bridge` sends query messages only. It must never click, type, set text or touch the
  driver: it is safe to run during a measurement.
- `writer` changes the instrument. Never guess a control: identify it structurally (form
  -> group box -> control), raise when ambiguous, read every write back.
- The driver allows one handle per process; `SXMIOCTL` serializes I/O on `_io_lock`.
- Nothing here can be tested against real hardware on the development PC. Say what was
  tested (offline, stand-in, instrument) when reporting a change.

## Versions and delivery

- `__version__` = commit date + commit (`_version.py`); exported copies carry a
  `VERSION` file instead of `.git`. Consumers call `sxm_anfatec.require('YYYY.MM.DD')`;
  raise that minimum in a consumer when it starts relying on a change made here.
- The measuring PC is offline. On the author's development PC, the hourly "Sync dev"
  task exports `origin/main` of this repo into `dev/to_microscope/sxm_anfatec` (with
  `VERSION`); the author copies that folder to the measuring PC desktop. So a change
  reaches the instrument only after it is pushed or merged to `main`.

## Checks

No hardware needed:

```
cd ..        # the folder containing sxm_anfatec
python -c "import sxm_anfatec as s; print(s.__version__); from sxm_anfatec import AnfatecSXMWriter, CHANNELS, MockDDEClient"
python -m sxm_anfatec.writer dnc        # lists parameters; 'not readable' when SXM is closed
```

Then run the sxm_ncafm_control tests, which exercise the bridge, writer and mock DDE.
