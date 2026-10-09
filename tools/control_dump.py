"""
control_dump.py  (python -m sxm_anfatec.tools.control_dump)

PASSIVE diagnostic for Anfatec Femto_28_4.exe.

It ONLY:
  - finds the running Femto_28_4.exe process
  - enumerates its Windows GUI controls
  - reads control text using WM_GETTEXT

It DOES NOT:
  - use DDE
  - access SXM.sys
  - issue IOCTLs
  - read/write process memory
  - click/type anything
  - change any SXM parameter
"""

import ctypes
import ctypes.wintypes as wt
import os
from pathlib import Path


TARGET_EXE = "femto_28_4.exe"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E

SMTO_ABORTIFHUNG = 0x0002


# ------------------------------------------------------------
# Correct pointer-sized Windows types
# ------------------------------------------------------------

if ctypes.sizeof(ctypes.c_void_p) == 8:
    LONG_PTR = ctypes.c_int64
    ULONG_PTR = ctypes.c_uint64
else:
    LONG_PTR = ctypes.c_long
    ULONG_PTR = ctypes.c_ulong

LPARAM = LONG_PTR
WPARAM = ULONG_PTR
LRESULT = LONG_PTR
DWORD_PTR = ULONG_PTR


WNDENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL,
    wt.HWND,
    wt.LPARAM
)


# ------------------------------------------------------------
# Function prototypes
# ------------------------------------------------------------

user32.EnumWindows.argtypes = [
    WNDENUMPROC,
    wt.LPARAM
]
user32.EnumWindows.restype = wt.BOOL


user32.EnumChildWindows.argtypes = [
    wt.HWND,
    WNDENUMPROC,
    wt.LPARAM
]
user32.EnumChildWindows.restype = wt.BOOL


user32.GetWindowThreadProcessId.argtypes = [
    wt.HWND,
    ctypes.POINTER(wt.DWORD)
]
user32.GetWindowThreadProcessId.restype = wt.DWORD


user32.GetClassNameW.argtypes = [
    wt.HWND,
    wt.LPWSTR,
    ctypes.c_int
]
user32.GetClassNameW.restype = ctypes.c_int


user32.GetWindowRect.argtypes = [
    wt.HWND,
    ctypes.POINTER(wt.RECT)
]
user32.GetWindowRect.restype = wt.BOOL


user32.GetParent.argtypes = [
    wt.HWND
]
user32.GetParent.restype = wt.HWND


user32.IsWindowVisible.argtypes = [
    wt.HWND
]
user32.IsWindowVisible.restype = wt.BOOL


user32.IsWindowEnabled.argtypes = [
    wt.HWND
]
user32.IsWindowEnabled.restype = wt.BOOL


user32.GetDlgCtrlID.argtypes = [
    wt.HWND
]
user32.GetDlgCtrlID.restype = ctypes.c_int


# Critical fix from v1:
#
# LPARAM / WPARAM / result are explicitly pointer-sized.

user32.SendMessageTimeoutW.argtypes = [
    wt.HWND,
    wt.UINT,
    WPARAM,
    LPARAM,
    wt.UINT,
    wt.UINT,
    ctypes.POINTER(DWORD_PTR)
]

user32.SendMessageTimeoutW.restype = LRESULT


kernel32.OpenProcess.argtypes = [
    wt.DWORD,
    wt.BOOL,
    wt.DWORD
]
kernel32.OpenProcess.restype = wt.HANDLE


kernel32.CloseHandle.argtypes = [
    wt.HANDLE
]
kernel32.CloseHandle.restype = wt.BOOL


kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD)
]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL


# ------------------------------------------------------------
# Process helpers
# ------------------------------------------------------------

def get_pid(hwnd):

    pid = wt.DWORD(0)

    user32.GetWindowThreadProcessId(
        hwnd,
        ctypes.byref(pid)
    )

    return pid.value


def get_process_name(pid):

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid
    )

    if not handle:
        return ""

    try:

        size = wt.DWORD(32768)

        buffer = ctypes.create_unicode_buffer(
            size.value
        )

        ok = kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size)
        )

        if not ok:
            return ""

        return os.path.basename(buffer.value)

    finally:

        kernel32.CloseHandle(handle)


# ------------------------------------------------------------
# GUI helpers
# ------------------------------------------------------------

def get_class_name(hwnd):

    buffer = ctypes.create_unicode_buffer(512)

    user32.GetClassNameW(
        hwnd,
        buffer,
        len(buffer)
    )

    return buffer.value


def get_rect(hwnd):

    r = wt.RECT()

    if not user32.GetWindowRect(
        hwnd,
        ctypes.byref(r)
    ):
        return 0, 0, 0, 0

    return (
        r.left,
        r.top,
        r.right - r.left,
        r.bottom - r.top
    )


def get_window_text(hwnd):

    # First ask how many characters are present.

    result = DWORD_PTR(0)

    ok = user32.SendMessageTimeoutW(
        hwnd,
        WM_GETTEXTLENGTH,
        WPARAM(0),
        LPARAM(0),
        SMTO_ABORTIFHUNG,
        250,
        ctypes.byref(result)
    )

    if not ok:
        return "<TIMEOUT>"

    length = int(result.value)

    # Give old Delphi controls plenty of room.
    capacity = max(length + 1, 2048)

    buffer = ctypes.create_unicode_buffer(
        capacity
    )

    result = DWORD_PTR(0)

    buffer_address = ctypes.addressof(buffer)

    ok = user32.SendMessageTimeoutW(
        hwnd,
        WM_GETTEXT,
        WPARAM(capacity),
        LPARAM(buffer_address),
        SMTO_ABORTIFHUNG,
        250,
        ctypes.byref(result)
    )

    if not ok:
        return "<TIMEOUT>"

    return buffer.value


# ------------------------------------------------------------
# Find ONLY genuine top-level Femto windows
# ------------------------------------------------------------

def find_femto_windows():

    found = []

    @WNDENUMPROC
    def callback(hwnd, lparam):

        # EnumWindows should already return top-level windows,
        # but explicitly reject child windows anyway.

        if user32.GetParent(hwnd):
            return True

        pid = get_pid(hwnd)

        if not pid:
            return True

        name = get_process_name(pid)

        if name.lower() == TARGET_EXE:
            found.append(
                (int(hwnd), pid)
            )

        return True

    user32.EnumWindows(
        callback,
        0
    )

    return found


# ------------------------------------------------------------
# Enumerate descendants
# ------------------------------------------------------------

def get_children(parent):

    controls = []

    @WNDENUMPROC
    def callback(hwnd, lparam):

        controls.append(
            int(hwnd)
        )

        return True

    user32.EnumChildWindows(
        parent,
        callback,
        0
    )

    return controls


def describe(hwnd):

    try:
        txt = get_window_text(hwnd)
    except Exception as exc:
        txt = "<ERROR: %s>" % exc

    txt = txt.replace(
        "\r", "\\r"
    ).replace(
        "\n", "\\n"
    )

    x, y, width, height = get_rect(hwnd)

    parent = user32.GetParent(hwnd)

    return (
        f"HWND=0x{int(hwnd):X} "
        f"PARENT=0x{int(parent or 0):X} "
        f"ID={user32.GetDlgCtrlID(hwnd)} "
        f"CLASS={get_class_name(hwnd)!r} "
        f"XYWH=({x},{y},{width},{height}) "
        f"VISIBLE={bool(user32.IsWindowVisible(hwnd))} "
        f"ENABLED={bool(user32.IsWindowEnabled(hwnd))} "
        f"TEXT={txt!r}"
    )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():

    print()
    print("Anfatec SXM passive GUI reader v2")
    print("=================================")
    print()

    windows = find_femto_windows()

    if not windows:

        print(
            "Femto_28_4.exe was not found."
        )

        print(
            "Leave the Anfatec SXM program running "
            "and try again."
        )

        return

    print(
        f"Found {len(windows)} genuine "
        f"Femto_28_4.exe top-level window(s)."
    )

    print()

    output = []

    for index, (top, pid) in enumerate(
        windows,
        start=1
    ):

        print(
            f"[{index}] PID={pid} "
            f"HWND=0x{top:X}"
        )

        output.append(
            "=" * 100
        )

        output.append(
            f"TOP WINDOW {index} "
            f"PID={pid} "
            f"HWND=0x{top:X}"
        )

        output.append(
            describe(top)
        )

        children = get_children(top)

        print(
            f"    {len(children)} "
            f"child/descendant controls"
        )

        for hwnd in children:

            output.append(
                describe(hwnd)
            )

        output.append("")

    outfile = Path(
        "sxm_controls_v2.txt"
    )

    outfile.write_text(
        "\n".join(output),
        encoding="utf-8"
    )

    print()
    print(
        "Passive scan complete."
    )

    print(
        f"Output: {outfile.resolve()}"
    )

    print()

    # Useful preliminary matches

    interesting = [
        "amplitude",
        "feedback",
        "70000",
        "7e5",
        "pull",
        "tau",
        "ki",
        "kp"
    ]

    matches = [
        line
        for line in output
        if any(
            word in line.lower()
            for word in interesting
        )
    ]

    print(
        "Potentially interesting controls:"
    )

    print(
        "---------------------------------"
    )

    if matches:

        for line in matches:
            print(line)

    else:

        print(
            "No obvious text matches."
        )

        print(
            "This is still useful; send me "
            "sxm_controls_v2.txt."
        )

    print()


if __name__ == "__main__":
    main()