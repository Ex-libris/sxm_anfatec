# Stop, restart or remove SXM metadata recording

These controls apply to the current Windows account. They stop metadata collection
and enrichment; they do not close SXM, stop its measurement, or change controller
settings. Raw data, captured metadata and existing enriched copies are retained.

## Stop completely

1. Double-click **SXM Metadata.vbs** in the installed `sxm_anfatec` folder.
2. Click **Stop completely**.
3. Wait for **Stopped completely. Automatic startup is disabled**. Shutdown can
   take up to about 30 seconds. An error means shutdown or startup disabling was
   not fully confirmed; follow the troubleshooting instructions below.
4. Close the window if desired.

This disables the account's scheduled startup/recovery task, removes the earlier
recorder's startup entry if present, and requests shutdown of the supervisor and
recorder. It also stops the earlier fixed-folder recorder identified by its saved
configuration. The program checks worker locks before confirming shutdown.
A persistent stop flag prevents automatic recording after a reboot or SXM restart.
The disabled Windows task remains registered until removed explicitly.

No background capture or enrichment continues after confirmed shutdown. A file
already being processed may finish before shutdown is confirmed. Metadata for new
measurements taken while stopped is unavailable and cannot be reconstructed later.
Saved, unfinished enrichment work is retried after restart using its original capture.

## Restart

1. Double-click **SXM Metadata.vbs**.
2. Click **Enable / restart automatic recording**.
3. Look for **Connected to SXM**, or **Waiting for SXM** if SXM is not open yet.
4. Close the window and measure normally.

This clears the persistent stop, re-enables automatic startup/recovery, and starts
the watcher. No folder selection is needed: new spectroscopy save events supply
the data folder. Repeated clicks do not create duplicate watchers for the same state folder.

If enabling reports Windows error `0x8007052E` / `-2147023570`, update the package
and reopen the window before retrying. The corrected version uses your existing
Windows sign-in without supplying a username/password pair. A setup error file,
when available, is saved as `%LOCALAPPDATA%\SXMMetadataRecorder\startup_error.json`.

Closing the status window alone leaves recording running. Closing SXM alone leaves
the watcher waiting to reconnect. Use **Stop completely** when you want it off.

## Remove automatic startup

Click **Remove automatic startup** in the same window. This first performs a
complete stop, then deletes this account's scheduled task. It keeps the program
and all data. **Enable / restart automatic recording** can register it again.

To remove the program files too, wait for successful removal, close the window,
then ask the maintainer to remove the installed package. Archive metadata first;
do not delete measurement folders or the central recordings as a routine stop step.

## If stopping reports an error

The window must not be treated as confirming success after an error. Keep the
error text and the files in `%LOCALAPPDATA%\SXMMetadataRecorder` for the maintainer.
Try **Stop completely** again. Do not end every `python.exe`/`pythonw.exe` process:
other instrument tools may use them too.

If Windows policy denies changing the scheduled task, ask the maintainer to disable
the current account's `SXM Metadata Recorder_<account identifier>` task in Windows
Task Scheduler. The persistent `PAUSED` flag still requests a stop, but the program
reports the task-disabling failure rather than claiming everything succeeded.
If a legacy recorder was launched manually with a different, unsaved output folder,
it is outside these controls; stop it through the advanced controls for that folder.
Different Windows accounts must each disable their own installation.

## Maintainer fallback without the window

From the directory containing `sxm_anfatec`, use its configured Python interpreter:

```bat
python -B -m sxm_anfatec.startup stop
python -B -m sxm_anfatec.startup start
python -B -m sxm_anfatec.startup remove
```

Run the one command you need in the same Windows account as the installation.
`stop` preserves the task disabled; `remove` deletes it after confirmed shutdown.
These commands report failures rather than silently succeeding. Administrator
rights are not normally needed for this per-user installation.

## Check once on the instrument PC

After setup, confirm a test spectrum produces a separate enriched copy. Stop
completely, confirm the stopped message, and reopen the status window to check it
remains stopped. When convenient, verify this also holds after a Windows restart.
Then restart automatic recording and confirm another test spectrum is enriched.
This checks the actual instrument's Windows policy and SXM behavior.
