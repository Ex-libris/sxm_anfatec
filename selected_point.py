"""Passive Select-tree reads: Windows accessibility and query-only tree messages.

No clicks, expansion, selection changes, or foreign process-memory access.
Coordinates are displayed values, not a verified spectroscopy acquisition position.
"""
from contextlib import contextmanager
import datetime as dt
import math
import re
import time

TVM_GETNEXTITEM = 0x110A
TVM_MAPHTREEITEMTOACCID = 0x112B
TVGN_NEXT, TVGN_PARENT, TVGN_CHILD, TVGN_CARET = 1, 3, 4, 9
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?'
COORDINATE = re.compile(r'^\s*([XYZ])\s*(?:[|:=]\s*)?(' + NUMBER + r')\s*([^\d\s]*)\s*$', re.I)


def parse_coordinates(labels):
    out = {}
    for label in labels:
        match = COORDINATE.fullmatch(label)
        if not match:
            continue
        axis, number, unit = match.groups()
        axis = axis.upper()
        if axis in out:
            raise ValueError('Duplicate coordinate ' + axis + ' in selected Point')
        value = float(number)
        if not math.isfinite(value):
            raise ValueError('Non-finite coordinate in selected Point')
        out[axis] = {'value': value, 'unit_displayed': unit or None, 'text': label}
    if not all(axis in out for axis in ('X', 'Y')):
        raise ValueError('Selected Point does not expose both X and Y coordinate labels')
    return out


def read_point(tree):
    """Read only the selected Point (or a coordinate under it); reject races."""
    selected = tree.selected()
    if not selected:
        raise ValueError('No item is selected in Select')
    selected_text = tree.text(selected)
    point = selected
    if selected_text.strip().casefold() != 'point':
        # A selected image/line must never be replaced by an arbitrary child Point.
        if not COORDINATE.fullmatch(selected_text):
            raise ValueError('The selected Select item is not a Point or its coordinate')
        point = tree.parent(selected)
        if not point or tree.text(point).strip().casefold() != 'point':
            raise ValueError('Selected coordinate is not a direct child of Point')
    point_text = tree.text(point)
    parents, ancestor, seen = [], tree.parent(point), {point}
    while ancestor:
        if ancestor in seen or len(parents) >= 8:
            raise ValueError('Select parent path is cyclic or exceeds the read limit')
        seen.add(ancestor)
        parents.append((ancestor, tree.text(ancestor)))
        ancestor = tree.parent(ancestor)
    children = tree.children(point)
    labels = [tree.text(child) for child in children]
    coordinates = parse_coordinates(labels)
    # Reject values if selection, ancestry, children, or labels changed mid-read.
    if tree.selected() != selected or tree.children(point) != children:
        raise ValueError('Select selection or Point children changed during the read')
    if tree.text(selected) != selected_text or tree.text(point) != point_text:
        raise ValueError('Select labels changed during the read')
    if [tree.text(child) for child in children] != labels:
        raise ValueError('Select coordinates changed during the read')
    expected_parent = parents[0][0] if parents else 0
    if tree.parent(point) != expected_parent:
        raise ValueError('Select parent changed during the read')
    for index, (handle, label) in enumerate(parents):
        next_parent = parents[index + 1][0] if index + 1 < len(parents) else 0
        if tree.text(handle) != label or tree.parent(handle) != next_parent:
            raise ValueError('Select image path changed during the read')
    return {'selected_text': selected_text, 'point_text': point_text,
            'parent_path': [label for _, label in reversed(parents)],
            'coordinate_labels': labels, 'coordinates': coordinates,
            'association': 'highlighted Select Point at GUI read; not verified as the measured spectroscopy position',
            'coordinate_frame': 'Select display frame; rotation/offset convention unverified',
            'z_meaning': 'displayed image-point Z; not assumed to be controller Topo or absolute tip height'}


@contextmanager
def accessible_client(hwnd):
    """Acquire a local COM accessibility proxy; never read target process memory."""
    import ctypes as ct
    import ctypes.wintypes as wt
    import uuid
    import pythoncom
    pythoncom.CoInitialize()
    dispatch = None
    try:
        oleacc = ct.WinDLL('oleacc')
        get = oleacc.AccessibleObjectFromWindow
        get.argtypes = [wt.HWND, wt.DWORD, ct.c_void_p, ct.POINTER(ct.c_void_p)]
        get.restype = ct.c_long
        iid = ct.create_string_buffer(uuid.UUID('00020400-0000-0000-C000-000000000046').bytes_le)
        pointer = ct.c_void_p()
        hr = get(hwnd, 0xfffffffc, iid, ct.byref(pointer))  # OBJID_CLIENT, IID_IDispatch
        if hr < 0 or not pointer.value:
            raise RuntimeError(f'Select accessibility is unavailable (0x{hr & 0xffffffff:08X})')
        try:
            # ObjectFromAddress queries its own reference; release our original.
            dispatch = pythoncom.ObjectFromAddress(pointer.value, pythoncom.IID_IDispatch)
        finally:
            vtable = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
            ct.WINFUNCTYPE(wt.ULONG, ct.c_void_p)(vtable[2])(pointer)
        yield dispatch
    finally:
        dispatch = None
        pythoncom.CoUninitialize()


class NativeTree:
    def __init__(self, hwnd, dispatch, query):
        self.hwnd, self.dispatch, self.query = hwnd, dispatch, query
        self.deadline = time.monotonic() + 1.5

    def message(self, msg, wparam=0, lparam=0):
        if time.monotonic() > self.deadline:
            raise RuntimeError('Select read exceeded its time budget')
        return self.query(self.hwnd, msg, wparam, lparam)

    def selected(self):
        return self.message(TVM_GETNEXTITEM, TVGN_CARET)

    def parent(self, item):
        return self.message(TVM_GETNEXTITEM, TVGN_PARENT, item)

    def children(self, item):
        children = []
        child = self.message(TVM_GETNEXTITEM, TVGN_CHILD, item)
        while child:
            if child in children or len(children) >= 16:
                raise RuntimeError('Select Point children exceed the bounded read limit')
            children.append(child)
            child = self.message(TVM_GETNEXTITEM, TVGN_NEXT, child)
        return children

    def text(self, item):
        accid = self.message(TVM_MAPHTREEITEMTOACCID, item)
        if not accid:
            raise RuntimeError('Select tree does not expose item accessibility IDs')
        # DISPID_ACC_NAME, DISPATCH_PROPERTYGET. No mutating accessibility calls.
        label = self.dispatch.Invoke(-5003, 0, 2, True, accid)
        if not isinstance(label, str) or not label.strip() or len(label) > 1024:
            raise RuntimeError('Select tree returned an empty or oversized label')
        return label.strip()


def read_tree(hwnd, query):
    with accessible_client(hwnd) as dispatch:
        tree = NativeTree(hwnd, dispatch, query)
        try:
            return read_point(tree)
        finally:
            tree.dispatch = None  # Release proxy before COM is uninitialized.
            dispatch = None


def read_selection():
    from . import bridge
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')
    forms = [h for h in bridge._top_windows() if bridge._norm(bridge._text(h)) == 'select']
    if len(forms) != 1:
        raise RuntimeError(f'Expected one SXM window titled Select, found {len(forms)}')
    form = forms[0]
    trees = [h for h in bridge._children(form) if bridge._cls(h).casefold() in ('ttreeview', 'systreeview32')]
    if len(trees) != 1:
        raise RuntimeError(f'Expected one tree in Select, found {len(trees)}')
    result = read_tree(trees[0], bridge._msg)
    result.update(read_start_utc=started,
                  read_end_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds'),
                  source='SXM Select tree; passive Windows accessibility and tree queries',
                  form_class=bridge._cls(form), tree_class=bridge._cls(trees[0]))
    return result
