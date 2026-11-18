"""Background controls. Run as a script or python -m sxm_anfatec.tools.recorder_control."""
from pathlib import Path
import json
import os
import subprocess
import sys


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))
CONFIG = Path.home() / 'SXM_metadata_recorder.json'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'SXM Metadata Recorder'


def launch(config):
    output = Path(config['output']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    executable = str(pythonw if pythonw.exists() else Path(sys.executable))
    args = [executable, '-m', 'sxm_anfatec.recorder', '--output', str(output)]
    if config.get('watch'):
        args += ['--watch', config['watch']]
    if not config.get('topo', True):
        args.append('--no-topo')
    if not config.get('auto_enrich', True):
        args.append('--no-enrich')
    if config.get('modulation_unit') in ('uV', 'mV', 'V'):
        args += ['--modulation-unit', config['modulation_unit']]
    with (output / 'launcher.log').open('ab') as log:
        subprocess.Popen(args, cwd=PACKAGE_PARENT, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)


def advanced_main():
    if '--autorun' in sys.argv:
        launch(json.loads(CONFIG.read_text(encoding='utf-8')))
        return
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('SXM metadata recorder')
    root.geometry('850x520')
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        config = {}
    output = tk.StringVar(value=config.get('output', str(Path.home() / 'SXM_metadata')))
    watch = tk.StringVar(value=config.get('watch', ''))
    topo = tk.BooleanVar(value=config.get('topo', True))
    auto_enrich = tk.BooleanVar(value=config.get('auto_enrich', True))
    modulation_unit = tk.StringVar(value=config.get('modulation_unit') or 'unknown')
    frame = ttk.Frame(root, padding=14)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    for row, (label, variable) in enumerate((('Metadata folder', output), ('SXM data folder', watch))):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w')
        ttk.Entry(frame, textvariable=variable, width=65).grid(row=row, column=1, sticky='ew')
        def browse(v=variable):
            result = filedialog.askdirectory()
            if result:
                v.set(result)
                if v is watch:
                    output.set(str(Path(result) / 'metadata'))
        ttk.Button(frame, text='Browse', command=browse).grid(row=row, column=2)
    ttk.Checkbutton(frame, text='Record Topo (requires an available driver connection)',
                    variable=topo).grid(row=2, column=0, columnspan=3, sticky='w', pady=8)
    ttk.Checkbutton(frame, text='Automatically create copies in DATA FOLDER / enriched (original files are never edited)',
                    variable=auto_enrich).grid(row=3, column=0, columnspan=3, sticky='w')
    unitbar = ttk.Frame(frame)
    unitbar.grid(row=4, column=0, columnspan=3, sticky='w', pady=6)
    ttk.Label(unitbar, text='Modulation amplitude label unit (only if known for this session): ').pack(side='left')
    ttk.Combobox(unitbar, textvariable=modulation_unit, values=('unknown', 'uV', 'mV', 'V'),
                 state='readonly', width=10).pack(side='left')
    status = tk.Text(frame, height=12, wrap='word')
    status.grid(row=6, column=0, columnspan=3, sticky='nsew', pady=10)
    frame.rowconfigure(6, weight=1)

    def save():
        if not output.get().strip():
            raise ValueError('Choose a metadata folder')
        if watch.get() and not Path(watch.get()).is_dir():
            raise ValueError('Choose an existing SXM data folder, or leave it empty')
        if auto_enrich.get() and not watch.get():
            raise ValueError('Choose the SXM data folder for automatic enriched copies')
        data = {'output': str(Path(output.get()).resolve()), 'watch': watch.get(), 'topo': topo.get(),
                'auto_enrich': auto_enrich.get(),
                'modulation_unit': None if modulation_unit.get() == 'unknown' else modulation_unit.get()}
        CONFIG.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data

    def action(fn):
        try:
            fn()
        except Exception as exc:
            messagebox.showerror('SXM metadata recorder', str(exc))

    def stop():
        folder = Path(output.get())
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'STOP').touch()

    def startup(enable):
        import winreg
        if enable:
            save()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                pythonw = Path(sys.executable).with_name('pythonw.exe')
                if not pythonw.exists():
                    raise RuntimeError('pythonw.exe is required for automatic background startup')
                command = subprocess.list2cmdline([str(pythonw), str(Path(__file__).resolve()), '--autorun'])
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        messagebox.showinfo('Background startup', 'Starts at sign-in for this Windows user.' if enable else 'Automatic startup disabled.')

    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=3, sticky='w', pady=8)
    for label, fn in [('Start background', lambda: launch(save())), ('Stop', stop),
                      ('Enable at sign-in', lambda: startup(True)), ('Disable at sign-in', lambda: startup(False))]:
        ttk.Button(buttons, text=label, command=lambda f=fn: action(f)).pack(side='left', padx=3)

    def refresh():
        try:
            import datetime as dt
            data = json.loads((Path(output.get()) / 'status.json').read_text(encoding='utf-8'))
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(data['utc'])).total_seconds()
            heading = 'RUNNING' if data.get('running') and age < 10 else 'STOPPED / NO RECENT HEARTBEAT'
            text = heading + '\n' + json.dumps(data, indent=2)
        except (OSError, ValueError, KeyError):
            text = 'No recorder status yet. Startup errors appear in launcher.log in the metadata folder.'
        status.configure(state='normal')
        status.delete('1.0', 'end')
        status.insert('1.0', text)
        status.configure(state='disabled')
        root.after(1000, refresh)
    refresh()
    root.mainloop()  # Closing this window leaves the background recorder running.


def main():
    if '--advanced' in sys.argv or '--autorun' in sys.argv:
        advanced_main()
    else:
        from sxm_anfatec.tools.recorder_status import main as status_main
        status_main()


if __name__ == '__main__':
    main()
