"""Read-only DDE subscriptions. No vendor-module import or execute transactions."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import time


def listen(emit, stop):
    """Connect and pump on this same thread; caller handles reconnects."""
    u = C.WinDLL('user32', use_last_error=True)
    handle = C.c_void_p
    callback_type = C.WINFUNCTYPE(handle, W.UINT, W.UINT, handle, handle,
                                 handle, handle, C.c_size_t, C.c_size_t)

    def bind(name, result, *args):
        fn = getattr(u, name)
        fn.restype, fn.argtypes = result, args
        return fn

    init = bind('DdeInitializeW', W.UINT, C.POINTER(W.DWORD), callback_type, W.DWORD, W.DWORD)
    create = bind('DdeCreateStringHandleW', handle, W.DWORD, W.LPCWSTR, C.c_int)
    free = bind('DdeFreeStringHandle', W.BOOL, W.DWORD, handle)
    connect = bind('DdeConnect', handle, W.DWORD, handle, handle, handle)
    disconnect = bind('DdeDisconnect', W.BOOL, handle)
    uninit = bind('DdeUninitialize', W.BOOL, W.DWORD)
    transact = bind('DdeClientTransaction', handle, handle, W.DWORD, handle, handle,
                    W.UINT, W.UINT, W.DWORD, C.POINTER(W.DWORD))
    query = bind('DdeQueryStringW', W.DWORD, W.DWORD, handle, W.LPWSTR, W.DWORD, C.c_int)
    getdata = bind('DdeGetData', W.DWORD, handle, handle, W.DWORD, W.DWORD)
    peek = bind('PeekMessageW', W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
    translate = bind('TranslateMessage', W.BOOL, C.POINTER(W.MSG))
    dispatch = bind('DispatchMessageW', C.c_ssize_t, C.POINTER(W.MSG))
    instance, conversation, disconnected = W.DWORD(), None, False

    @callback_type
    def callback(kind, fmt, conv, topic, item, data, one, two):
        nonlocal disconnected
        try:
            if kind == 0x80C2:  # XTYP_DISCONNECT
                disconnected = True
            elif kind == 0x4010:  # XTYP_ADVDATA
                name = C.create_unicode_buffer(256)
                query(instance, item, name, len(name), 1200)
                size = getdata(data, None, 0, 0)
                if size > 1024 * 1024:
                    emit('dde_error', error='Oversize event rejected', size=size)
                    return 0
                buf = C.create_string_buffer(size or 1)
                copied = getdata(data, buf, size, 0)
                raw = buf.raw[:copied]
                encoding = 'utf-16-le' if fmt == 13 else 'mbcs'
                emit('dde', item=name.value, clipboard_format=fmt,
                     raw_hex=raw.hex(), value=raw.decode(encoding, errors='replace').rstrip('\x00\r\n'))
                return 0x8000  # DDE_FACK
        except Exception as exc:
            emit('dde_error', error=repr(exc))
        return 0

    code = init(C.byref(instance), callback, 0x10, 0)  # APPCMD_CLIENTONLY
    if code:
        raise RuntimeError(f'DdeInitialize: {code}')
    try:
        service, topic = create(instance, 'SXM', 1200), create(instance, 'Remote', 1200)
        try:
            conversation = connect(instance, service, topic, None)
        finally:
            free(instance, service)
            free(instance, topic)
        if not conversation:
            raise RuntimeError('SXM DDE unavailable; waiting for SXM')
        accepted = []
        for name in ('SpectSave', 'SaveFileName', 'MicState', 'Scan', 'ScanLine', 'Command'):
            item = create(instance, name, 1200)
            try:
                # Synchronous ADVSTART returns a success flag, not a data handle.
                ok = transact(None, 0, conversation, item, 1, 0x1030, 1000, None)
                emit('subscription', item=name, accepted=bool(ok))
                if ok:
                    accepted.append(name)
            finally:
                free(instance, item)
        if not accepted:
            raise RuntimeError('SXM accepted no subscriptions')
        emit('dde_connected', subscriptions=accepted)
        msg = W.MSG()
        while not stop.is_set() and not disconnected:
            while peek(C.byref(msg), None, 0, 0, 1):
                translate(C.byref(msg))
                dispatch(C.byref(msg))
            stop.wait(0.01)
        if disconnected:
            raise RuntimeError('SXM disconnected')
    finally:
        if conversation:
            disconnect(conversation)
        uninit(instance)
