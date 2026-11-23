"""
DDE client bridge for SXM control.

This module acts as a thin bridge between the GUI and the SXM software,
translating Python method calls into Pascal-like commands that SXM expects.

DDE (Dynamic Data Exchange) is a Windows protocol that allows programs to
communicate with each other. The SXM software acts as a DDE server, accepting
commands in Pascal syntax (like "ScanPara('Edit23', 0.080);").

The module also maintains a "last written" cache so the GUI can show the most recent
values, even in cases where the underlying SXM system is write-only (you can send
parameters but not read them back).

Public API:
    - send_scanpara(edit_code: str, value: float) -> None
    - send_dncpara(index: int, value: float) -> None
    - read_channel(index: int) -> float
    - set_channel(index: int, value: float) -> None
    - read_topography() -> float
    - last_written(ptype: str, pcode: str) -> float | None
    - get_scan_para(name: str) -> float
    - go_xy(fx: float, fy: float) -> None
    - spect_para(param: int | str, value: float) -> None
    - spect_start() -> None
    - wait_spect_save(timeout_s: float) -> str | None

Spectroscopy and positioning follow Anfatec's Language Description 28.02:
``GoXY(x_rel, y_rel)`` moves the tip inside the *current* scan area, (0, 0) = upper left
corner of the image, (1, 1) = lower right; SXM applies centre, range and angle itself.
``SpectPara(n, v)`` writes Edit box n of the Spectroscopy window (which box is X or Y can
depend on the spectroscopy mode) in the unit its label currently shows.

Caution (vendor code): every DDE advise (ScanLine, SpectSave, MicState, ...) arriving while
``GetChannel``/``GetScanPara`` waits for its answer is taken as that answer. Read channels over
DDE only while the scan is stopped and no spectrum is running; use the driver otherwise.
"""

import math
import time
from typing import Dict, Optional, Tuple, Union

SPECT_STRING_PARAMS = ('AUTOSAVE', 'POINTS', 'REPEAT')


def _spect_param(param: Union[int, str]) -> str:
    """SpectPara's first argument as SXM expects it: an Edit box number or a quoted name."""
    if isinstance(param, str):
        if param.upper() not in SPECT_STRING_PARAMS:
            raise ValueError(f"SpectPara name must be one of {SPECT_STRING_PARAMS}, not {param!r}")
        return f"'{param.upper()}'"
    if isinstance(param, bool) or int(param) != param or param < 0:
        raise ValueError(f"SpectPara index must be a non-negative integer, not {param!r}")
    return str(int(param))


def _fraction(value: float, name: str) -> float:
    value = float(value)
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise ValueError(f"GoXY {name} must be between 0 and 1, not {value!r}")
    return value


def _number(value: float) -> str:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {value!r}")
    return f"{value:.9g}"


def _as_text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode('utf-8', errors='replace')
    return str(value).strip('\x00\r\n ')


def _pump_messages() -> None:
    """Dispatch pending Windows messages of this thread (DDE callbacks arrive this way)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


class BaseDDE:
    """Base class for DDE clients with last-written value caching."""

    def __init__(self) -> None:
        self._last: Dict[Tuple[str, str], float] = {}

    def _remember(self, ptype: str, pcode: str, value: float) -> None:
        self._last[(ptype.upper(), str(pcode))] = float(value)

    def last_written(self, ptype: str, pcode: str):
        return self._last.get((ptype.upper(), str(pcode)))


class RealDDEClient(BaseDDE):
    """Real DDE client that communicates with SXM via SXMRemote.DDEClient (Windows only)."""

    def __init__(self, app_name: str = "SXM", topic: str = "Remote") -> None:
        super().__init__()
        from . import sxm_remote as SXMRemote  # importing it opens a DDE conversation (raises if SXM is closed)

        self._dde = SXMRemote.DDEClient(app_name, topic)
        self._command_count = 0
        self._spect_saved: Optional[str] = None
        # The vendor client calls self.SpectSave(value) for the SpectSave advise: catch it here.
        self._dde.SpectSave = self._on_spect_save

    def _on_spect_save(self, value) -> None:
        self._spect_saved = _as_text(value)

    def send_scanpara(self, edit_code: str, value: float) -> None:
        if not (isinstance(edit_code, str) and edit_code.startswith("Edit")):
            raise ValueError("edit_code must look like 'EditNN' (e.g., 'Edit23').")
        self._dde.SendWait(f"ScanPara('{edit_code}', {value});")
        self._remember("EDIT", edit_code, value)
        self._command_count += 1

    def send_dncpara(self, index: int, value: float) -> None:
        if index < 0:
            raise ValueError("DNC index must be non-negative.")
        self._dde.SendWait(f"DNCPara({index}, {value});")
        self._remember("DNC", str(index), value)

    def read_channel(self, index: int) -> float:
        return float(self._dde.GetChannel(int(index)))

    def set_channel(self, index: int, value: float) -> None:
        self._dde.SendWait(f"SetChannel({index}, {value});")
        self._remember("Setchan", str(index), value)

    def read_topography(self) -> float:
        return self.read_channel(0)

    def feed_para(self, ptype: str, value: int) -> None:
        self._dde.SendWait(f"FeedPara('{ptype}', {int(value)});")
        self._remember("FEED", ptype, int(value))

    # -- scan parameters, tip position and spectroscopy --

    def get_scan_para(self, name: str) -> float:
        """GetScanPara(name): Range, X, Y, Angle, AspectRatio, Scan (1 = scanning), ... in label units."""
        value = self._dde.GetScanPara(str(name))
        if value is None:
            raise RuntimeError(f"GetScanPara('{name}') returned no value")
        return float(value)

    def go_xy(self, fx: float, fy: float) -> None:
        """Move the tip to fraction (fx, fy) of the current scan area; (0, 0) = upper left."""
        fx, fy = _fraction(fx, 'x'), _fraction(fy, 'y')
        self._dde.SendWait(f"GoXY({fx:.6f}, {fy:.6f});")
        self._command_count += 1

    def spect_para(self, param: Union[int, str], value: float) -> None:
        """SpectPara(n, v): Edit box n of the Spectroscopy window (unit = its label), or AUTOSAVE/POINTS/REPEAT."""
        p = _spect_param(param)
        self._dde.SendWait(f"SpectPara({p}, {_number(value)});")
        self._remember("SPECT", p.strip("'"), value)
        self._command_count += 1

    def spect_start(self) -> None:
        """Start one spectrum with the current settings. Returns at once; see wait_spect_save."""
        self._spect_saved = None
        self._dde.SendWait("SpectStart;")
        self._command_count += 1

    def wait_spect_save(self, timeout_s: float) -> Optional[str]:
        """File name from SXM's SpectSave advise after spect_start (AutoSave on), or None on timeout.

        Must run on the thread that created this client: DDE callbacks arrive through its messages.
        """
        end = time.monotonic() + float(timeout_s)
        while self._spect_saved is None and time.monotonic() < end:
            _pump_messages()
            time.sleep(0.02)
        return self._spect_saved


class MockDDEClient(BaseDDE):
    """Offline mock client for development and testing without SXM software."""

    def __init__(self) -> None:
        super().__init__()
        self._command_count = 0
        # Simulated scan frame and tip, for the positioning commands. ``sim_angle_sign`` is how
        # the simulated SXM turns the frame for a positive Angle (+1 counter-clockwise).
        self.sim = {'X': 0.0, 'Y': 0.0, 'Range': 100.0, 'AspectRatio': 1.0, 'Angle': 0.0, 'Scan': 0.0}
        self.sim_angle_sign = 1
        self.tip = [0.0, 0.0]
        self.spect_xy = [0.0, 0.0]
        self._spect_saved: Optional[str] = None
        self._spect_count = 0

    def _sim_position(self, fx: float, fy: float) -> Tuple[float, float]:
        s = self.sim
        width, height = s['Range'], s['Range'] / s['AspectRatio']
        u, v = (fx - 0.5) * width, (0.5 - fy) * height
        a = math.radians(self.sim_angle_sign * s['Angle'])
        return (s['X'] + u * math.cos(a) - v * math.sin(a),
                s['Y'] + u * math.sin(a) + v * math.cos(a))

    def send_scanpara(self, edit_code: str, value: float) -> None:
        if not (isinstance(edit_code, str) and edit_code.startswith("Edit")):
            raise ValueError("edit_code must look like 'EditNN' (e.g., 'Edit23').")
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} ScanPara('{edit_code}', {value});")
        self._remember("EDIT", edit_code, value)

    def send_dncpara(self, index: int, value: float) -> None:
        if index < 0:
            raise ValueError("DNC index must be non-negative.")
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} DNCPara({index}, {value});")
        self._remember("DNC", str(index), value)

    def read_channel(self, index: int) -> float:
        base = getattr(self, "_sim_base", 0.0)
        base += 0.001
        self._sim_base = base
        if int(index) == 0:
            return base
        if int(index) in (-2, -3):            # x-direction / y-direction: simulated tip position
            return self.tip[0 if int(index) == -2 else 1]
        return 0.0

    def set_channel(self, index: int, value: float) -> None:
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} SetChannel({int(index)}, {float(value)});")
        self._remember("CHAN", str(index), float(value))

    def read_topography(self) -> float:
        return self.read_channel(0)

    def feed_para(self, ptype: str, value: float) -> None:
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} FeedPara('{ptype}', {float(value)});")
        self._remember("FEED", ptype, float(value))

    def get_scan_para(self, name: str) -> float:
        for key, value in self.sim.items():
            if key.lower() == str(name).lower():
                return float(value)
        return 0.0

    def go_xy(self, fx: float, fy: float) -> None:
        fx, fy = _fraction(fx, 'x'), _fraction(fy, 'y')
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} GoXY({fx:.6f}, {fy:.6f});")
        self.tip = list(self._sim_position(fx, fy))

    def spect_para(self, param: Union[int, str], value: float) -> None:
        p = _spect_param(param)
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} SpectPara({p}, {_number(value)});")
        self._remember("SPECT", p.strip("'"), value)
        if p in ('1', '2'):
            self.spect_xy[int(p) - 1] = float(value)

    def spect_start(self) -> None:
        self._command_count += 1
        print(f"[MOCK] #{self._command_count:03d} SpectStart;")
        self.tip = list(self.spect_xy)
        self._spect_count += 1
        self._spect_saved = f"mock_spectrum_{self._spect_count:04d}.dat"

    def wait_spect_save(self, timeout_s: float) -> Optional[str]:
        return self._spect_saved
