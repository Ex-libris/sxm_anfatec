"""AnfatecSXMBridgeMonitor - live display of the values AnfatecSXMBridge reads from the SXM GUI.

    python -m sxm_anfatec.monitor

"Save JSON..." writes exactly the snapshot currently on screen. Values that
changed since the previous read are highlighted; sections that could not be
read are shown in red with the reason. Double-click a row to copy its path
(e.g. ``Scan.Range``) for use with ``sxm.get(...)``.
"""
from __future__ import annotations

import datetime
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from .bridge import AnfatecSXMBridge


def _fmt(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and not any(isinstance(v, (dict, list)) for v in value):
        return ', '.join(_fmt(v) for v in value)
    return json.dumps(value, ensure_ascii=False)


def _nodes(parameters: dict) -> list:
    """Flatten a snapshot into (path, parent_path, label, value_text, kind) rows."""
    rows = []

    def walk(path, parent, label, value):
        if isinstance(value, dict) and '_error' in value:
            rows.append((path, parent, label, value['_error'], 'error'))
        elif isinstance(value, dict):
            rows.append((path, parent, label, '', 'group'))
            for k, v in value.items():
                walk(f'{path}.{k}', path, k, v)
        elif isinstance(value, list) and any(isinstance(v, dict) for v in value):
            rows.append((path, parent, label, '', 'group'))
            for i, v in enumerate(value):
                walk(f'{path}.{i}', path, f'[{i}]', v)
        else:
            rows.append((path, parent, label, _fmt(value), 'leaf'))

    for name, value in parameters.items():
        walk(name, '', name, value)
    return rows


class Monitor:
    def __init__(self, root: tk.Tk, bridge: AnfatecSXMBridge):
        self.root, self.bridge = root, bridge
        self.snapshot = None
        self.previous = {}        # path -> value text from the previous read
        self.items = {}           # path -> tree item id
        self.layout = None        # row paths currently in the tree
        self.results = queue.Queue()
        self.busy = False
        self.last_read = 0.0

        root.title('AnfatecSXMBridge Monitor - Femto_28_4.exe')
        root.geometry('720x820')

        bar = ttk.Frame(root, padding=(8, 8, 8, 4))
        bar.pack(fill='x')
        self.refresh_button = ttk.Button(bar, text='Refresh', command=self.refresh)
        self.refresh_button.pack(side='left')
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text='Auto every', variable=self.auto).pack(side='left', padx=(12, 4))
        self.interval = tk.StringVar(value='2')
        ttk.Spinbox(bar, from_=1, to=60, width=4, textvariable=self.interval).pack(side='left')
        ttk.Label(bar, text='s').pack(side='left', padx=(4, 12))
        ttk.Label(bar, text='Filter').pack(side='left')
        self.filter = tk.StringVar()
        self.filter.trace_add('write', lambda *_: self._render(force=True))
        ttk.Entry(bar, textvariable=self.filter, width=18).pack(side='left', padx=4)
        self.save_button = ttk.Button(bar, text='Save JSON...', command=self.save, state='disabled')
        self.save_button.pack(side='right')

        body = ttk.Frame(root, padding=(8, 0, 8, 0))
        body.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(body, columns=('value',), show='tree headings')
        self.tree.heading('#0', text='Parameter')
        self.tree.heading('value', text='Value')
        self.tree.column('#0', width=300, stretch=False)
        self.tree.column('value', width=380)
        self.tree.tag_configure('error', foreground='#c0392b')
        self.tree.tag_configure('changed', background='#fff2a8')
        self.tree.tag_configure('section', font=('Segoe UI', 9, 'bold'))
        self.tree.bind('<Double-1>', self._copy_path)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

        self.status = tk.StringVar(value='Reading...')
        ttk.Label(root, textvariable=self.status, padding=(8, 4), anchor='w').pack(fill='x')

        self.refresh()
        root.after(200, self._poll)

    # -- reading happens off the UI thread; results come back through a queue --

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.state(['disabled'])
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        t0 = time.perf_counter()
        try:
            self.results.put((self.bridge.snapshot(), None, time.perf_counter() - t0))
        except Exception as exc:
            self.results.put((None, exc, time.perf_counter() - t0))

    def _poll(self):
        try:
            while True:
                snap, error, seconds = self.results.get_nowait()
                self.busy = False
                self.last_read = time.monotonic()
                self.refresh_button.state(['!disabled'])
                if error is not None:
                    self.status.set(f'Read failed: {error}')
                else:
                    self._show(snap, seconds)
        except queue.Empty:
            pass
        try:
            interval = max(1.0, float(self.interval.get()))
        except ValueError:
            interval = 2.0
        if self.auto.get() and not self.busy and time.monotonic() - self.last_read >= interval:
            self.refresh()
        self.root.after(200, self._poll)

    # -- display --

    def _show(self, snap: dict, seconds: float):
        self.snapshot = snap
        self.save_button.state(['!disabled'])
        self._render()
        params = snap['parameters']
        failed = [n for n, v in params.items() if '_error' in v]
        shown = sum(1 for r in _nodes(params) if r[4] == 'leaf')
        stamp = snap['timestamp'][11:19]
        note = f' · {len(failed)} section(s) unavailable' if failed else ''
        self.status.set(f'Read at {stamp} in {seconds:.2f} s · {shown} values{note}')

    def _render(self, force: bool = False):
        if self.snapshot is None:
            return
        rows = _nodes(self.snapshot['parameters'])
        term = self.filter.get().strip().casefold()
        if term:
            keep = {r[0] for r in rows if r[4] != 'group' and term in r[0].casefold()}
            for path in list(keep):  # keep ancestors of matches
                while '.' in path:
                    path = path.rsplit('.', 1)[0]
                    keep.add(path)
            rows = [r for r in rows if r[0] in keep]

        layout = [r[0] for r in rows]
        if force or layout != self.layout:
            closed = {p for p, i in self.items.items() if self.tree.exists(i) and not self.tree.item(i, 'open')}
            self.tree.delete(*self.tree.get_children())
            self.items = {}
            for path, parent, label, _, _ in rows:
                self.items[path] = self.tree.insert(self.items.get(parent, ''), 'end', text=label,
                                                    open=path not in closed)
            self.layout = layout

        current = {}
        for path, parent, _, text, kind in rows:
            tags = []
            if kind == 'error':
                tags.append('error')
            if not parent:
                tags.append('section')
            if kind == 'leaf' and path in self.previous and self.previous[path] != text:
                tags.append('changed')
            self.tree.item(self.items[path], values=(text,), tags=tags)
            current[path] = text
        if not force:
            self.previous = current

    def _copy_path(self, event):
        item = self.tree.identify_row(event.y)
        path = next((p for p, i in self.items.items() if i == item), None)
        if path:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self.status.set(f'Copied path: {path}')

    def save(self):
        snap = self.snapshot  # what is on screen now; auto-refresh may replace it while the dialog is open
        if snap is None:
            return
        stamp = datetime.datetime.fromisoformat(snap['timestamp']).strftime('%Y%m%d_%H%M%S')
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json',
                                            initialdir=os.getcwd(), initialfile=f'SXM_snapshot_{stamp}.json',
                                            filetypes=[('JSON', '*.json'), ('All files', '*.*')])
        if path:
            self.status.set(f'Saved {self.bridge.save_snapshot(path, snapshot=snap)}')


def main():
    root = tk.Tk()
    Monitor(root, AnfatecSXMBridge(strict=False))
    root.mainloop()


if __name__ == '__main__':
    main()
