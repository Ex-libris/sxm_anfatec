"""sxm_anfatec - Python access to the Anfatec SXM controller software (Femto_28_4.exe, Windows).

Every way of talking to SXM, in one place:

| Module       | Transport                                     | Use it for                                          |
|--------------|-----------------------------------------------|-----------------------------------------------------|
| `bridge`     | read-only Win32 queries of the SXM windows    | reading any value the GUI shows                     |
| `writer`     | the window messages the GUI itself reacts to  | setting GUI parameters, incl. ones DDE cannot set   |
| `dde`        | DDE (service SXM, topic Remote)               | ScanPara / DNCPara / SetChannel / FeedPara commands |
| `sxm_remote` | Anfatec's DDE wrapper, used by `dde`          | (vendor code, kept close to the original)           |
| `driver`     | IOCTL on the kernel driver `\\\\.\\SXM`         | fast reads and writes of hardware channels          |

    import sxm_anfatec
    from sxm_anfatec import AnfatecSXMBridge, AnfatecSXMWriter, SXMIOCTL, CHANNELS, RealDDEClient
    sxm_anfatec.__version__          # '2026.10.09+abc1234' (commit date + commit)
    sxm_anfatec.require('2026.10.09')   # raise if this copy is older

Names are imported on first use, so ``import sxm_anfatec`` opens nothing: no DDE
conversation, no driver handle.
"""
from __future__ import annotations

import importlib

from ._version import require, version

_EXPORTS = {
    'AnfatecSXMBridge': 'bridge', 'SXMBridgeError': 'bridge', 'SXMPathError': 'bridge',
    'AnfatecSXMWriter': 'writer', 'SXMWriteError': 'writer',
    'RealDDEClient': 'dde', 'MockDDEClient': 'dde',
    'SXMIOCTL': 'driver', 'CHANNELS': 'driver',
}

__all__ = sorted(_EXPORTS) + ['require', 'version', '__version__']


def __getattr__(name: str):
    if name == '__version__':
        return version()
    if name in _EXPORTS:
        return getattr(importlib.import_module(f'.{_EXPORTS[name]}', __name__), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(__all__))
