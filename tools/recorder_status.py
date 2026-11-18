"""Simple user controls; closing this window does not stop recording."""
from pathlib import Path
import datetime as dt
import os
import queue
import threading

from sxm_anfatec.supervisor import state_directory, read_json, busy_workers
from sxm_anfatec import startup


def main():
    import tkinter as tk
    from tkinter import ttk, messagebox
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    root.title('SXM Metadata')
    root.geometry('760x490')
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='SXM Metadata', font=('Segoe UI', 18)).pack(anchor='w')
    ttk.Label(frame, text='Open SXM, choose your data folder and measure normally.\n'
              'Original files stay untouched; enriched copies appear in an enriched subfolder.').pack(anchor='w', pady=10)
    status = tk.StringVar(value='Checking background recording…')
    ttk.Label(frame, textvariable=status, wraplength=680, justify='left').pack(anchor='w', pady=10)
    enabled = startup.installed()
    automatic = tk.StringVar(value='Automatic startup is enabled.' if enabled else 'One-time setup: enable automatic recording for this Windows account.')
    ttk.Label(frame, textvariable=automatic, wraplength=680).pack(anchor='w', pady=5)

    def action(fn):
        try:
            fn()
        except Exception as exc:
            messagebox.showerror('SXM Metadata', str(exc))

    results = queue.Queue()
    controls = []
    working = False

    def lifecycle(fn, progress, success):
        nonlocal working
        if working:
            return
        working = True
        automatic.set(progress)
        for button in controls:
            button.configure(state='disabled')

        def worker():
            # Task Scheduler COM objects belong to the thread that creates them.
            try:
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    fn()
                finally:
                    pythoncom.CoUninitialize()
                results.put((True, success))
            except Exception as exc:
                results.put((False, str(exc)))
        threading.Thread(target=worker, daemon=False).start()

    def enable():
        lifecycle(startup.install, 'Starting automatic recording…',
                  'Automatic startup and restart recovery are enabled for this Windows account.')

    def stop():
        lifecycle(lambda: startup.stop_completely(state), 'Stopping and checking shutdown…',
                  'Stopped completely. Automatic startup is disabled; this survives a PC restart.')

    def remove():
        lifecycle(startup.uninstall, 'Stopping and removing automatic startup…',
                  'Stopped. Automatic startup registration removed. All recorded files are retained.')

    def open_folder(enriched):
        folders = read_json(state / 'recordings/routing_status.json').get('folders', [])
        target = Path(folders[-1]) / ('enriched' if enriched else 'metadata') if folders else state / 'recordings'
        if not target.is_dir():
            raise RuntimeError('No measurement folder has been recorded yet.')
        os.startfile(target)

    buttons = ttk.Frame(frame)
    buttons.pack(anchor='w', pady=10)
    for text, fn in [('Enable / restart automatic recording', enable), ('Stop completely', stop),
                     ('Remove automatic startup', remove)]:
        button = ttk.Button(buttons, text=text, command=lambda f=fn: action(f))
        button.pack(side='left', padx=3)
        controls.append(button)
    buttons2 = ttk.Frame(frame)
    buttons2.pack(anchor='w', pady=5)
    for text, fn in [('Open enriched folder', lambda: open_folder(True)),
                     ('Open metadata folder', lambda: open_folder(False)),
                     ('Stop / restart help', lambda: os.startfile(Path(__file__).resolve().parents[1] / 'STOP_AND_RESTART.md'))]:
        ttk.Button(buttons2, text=text, command=lambda f=fn: action(f)).pack(side='left', padx=3)
    ttk.Label(frame, text='You can close this window. Background recording continues.').pack(anchor='w', pady=10)

    def refresh():
        nonlocal working
        try:
            success, message = results.get_nowait()
        except queue.Empty:
            pass
        else:
            working = False
            for button in controls:
                button.configure(state='normal')
            automatic.set(message if success else 'Action needs attention. See the error and Stop / restart help.')
            if not success:
                messagebox.showerror('SXM Metadata', message)
        supervisor = read_json(state / 'supervisor_status.json')
        child = read_json(state / 'recordings/status.json')
        routing = read_json(state / 'recordings/routing_status.json')
        try:
            age = (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(supervisor['utc'])).total_seconds()
        except (KeyError, ValueError):
            age = float('inf')
        if (state / 'PAUSED').exists():
            try:
                busy = busy_workers(state)
                text = ('Stop requested; waiting for background workers to finish.' if busy else
                        'Stopped. Choose Enable / restart automatic recording to resume.')
            except OSError as exc:
                text = 'Unable to confirm shutdown: ' + str(exc)
        elif not supervisor.get('running') or age > 15:
            text = 'Background recorder is not running or is restarting.'
        elif child.get('dde') != 'connected':
            text = 'Waiting for SXM. It will reconnect automatically when SXM opens.'
        else:
            text = 'Connected to SXM. Following spectroscopy save folders automatically.'
            if child.get('topo') != 'reading':
                text += '\nTopo readings are unavailable; GUI metadata can still be captured.'
        folders = routing.get('folders', [])
        if folders:
            text += '\nLatest data folder: ' + folders[-1]
        counts = routing.get('counts', {})
        if counts:
            text += '\nEnriched copies: {complete}; pending: {pending}; blocked: {blocked}.'.format(**counts)
        if child.get('gui_errors'):
            text += '\nSome GUI fields were unavailable; the captures record which ones.'
        if child.get('journal_error') or child.get('enrichment_error'):
            text += '\nRecording needs attention. Details are in the metadata status files.'
        status.set(text)
        root.after(1500, refresh)
    refresh()
    def close():
        if working:
            messagebox.showinfo('SXM Metadata', 'Please wait for the stop/start action to finish before closing this window.')
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.mainloop()


if __name__ == '__main__':
    main()
