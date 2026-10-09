# sxm_anfatec

Python access to the **Anfatec SXM** controller software (`Femto_28_4.exe`, the Delphi
program that runs Scienta Omicron / Anfatec SPM controllers on Windows). Every way a
Python program can talk to SXM lives here, so that tools built on it
([sxm_ncafm_control](https://github.com/Ex-libris/sxm_ncafm_control), STS and other
scripts) share one copy instead of each carrying their own.

**Author**: Benjamin Mallada. Builds on the scripts and documentation Anfatec provides for
their SXM controllers: https://www.anfatec.de/support/02_spm/sxm_softwaresupport.html

## What is in it

| Module | How it talks to SXM | Use it for | Affects the instrument? |
|---|---|---|---|
| `bridge` | read-only Win32 queries of the SXM windows (`WM_GETTEXT`, `BM_GETCHECK`, ...) | reading any value the GUI shows | no |
| `writer` | the window messages the GUI itself reacts to (combo select, click, `WM_SETTEXT`) | setting GUI parameters, including ones DDE cannot set (DNC time constant, output gain, Amplitude Tau, ...) | **yes** |
| `dde` | DDE, service `SXM`, topic `Remote` | `ScanPara`, `DNCPara`, `SetChannel`, `FeedPara`; `MockDDEClient` for offline use | **yes** |
| `sxm_remote` | Anfatec's DDE wrapper (ctypes), used by `dde` | vendor code, kept close to the original | |
| `driver` | `DeviceIoControl` on the kernel driver `\\.\SXM` | fast reads and writes of hardware channels; `CHANNELS` (names, units, scales) | writes: **yes** |
| `monitor` | `bridge` | live window of every value the bridge reads | no |
| `PARAMETERS.md` | | every `writer` parameter name, its meaning and DDE equivalent | |

```python
from sxm_anfatec import AnfatecSXMBridge, AnfatecSXMWriter, SXMIOCTL, CHANNELS, RealDDEClient

sxm = AnfatecSXMBridge()
sxm.scan.range                  # live read
sxm.dynamic.q                   # parsed from the DNC status bar

w = AnfatecSXMWriter()
w.dnc                           # table of the DNC window's parameters
w.set('dnc.tc', '3 ms')         # set, then read back through the bridge

drv = SXMIOCTL()                # one handle per program (the driver allows only one)
drv.read_scaled('df')
```

From the command line (run from the folder that **contains** `sxm_anfatec`):

```
python -m sxm_anfatec                       # version and location of this copy
python -m sxm_anfatec.writer                # every parameter: value, accepted values, meaning
python -m sxm_anfatec.writer dnc.tc "3 ms"  # show what would be sent; add --apply to send
python -m sxm_anfatec.bridge                # print and save a snapshot of everything readable
python -m sxm_anfatec.monitor               # live view
python -m sxm_anfatec.tools.control_dump    # diagnostic: every control in the SXM windows
python -m sxm_anfatec.tools.make_param_doc  # regenerate PARAMETERS.md (on the instrument PC)
```

Windows only. Needs `pywin32` for `driver`; the rest uses only the standard library.

## Getting it

No installation is needed: Python finds `sxm_anfatec` when the folder sits next to the
program that uses it (both run from the same parent folder):

```
Desktop\
  sxm_anfatec\          <- this repository
  sxm_ncafm_control\
```

- **Git**: `git clone https://github.com/Ex-libris/sxm_anfatec.git` next to your program.
- **Offline PC**: copy the folder. Copying a newer folder over it is the whole update.
- **pip** (optional): `pip install git+https://github.com/Ex-libris/sxm_anfatec.git`.

## Versions

`sxm_anfatec.__version__` is the date and commit of this copy, e.g. `2026.10.09+abc1234`
(from git in a checkout, from the `VERSION` file in an exported copy). A program that
needs a recent change calls `sxm_anfatec.require('2026.10.09')` at startup, which stops
with a clear message when an older copy is found, instead of failing halfway through a
measurement.

## Status

- `bridge`: used on the instrument by sxm_ncafm_control.
- `writer`: `dnc.output_gain` confirmed to take effect on the instrument. Other kinds
  of control are tested only against a stand-in process. Number fields need an explicit
  `commit='change'|'enter'` until each is confirmed and recorded in `EDIT_COMMIT`.
- `dde`, `driver`: used on the instrument by sxm_ncafm_control.
