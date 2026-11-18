# Everyday use: open SXM and measure

## One-time setup on the instrument PC

1. Copy the updated `sxm_anfatec` folder to a stable location on the instrument PC.
   Stop the old recorder before replacing its files. Do not move this folder after setup.
2. Double-click **SXM Metadata.vbs** inside that folder.
3. Click **Enable / restart automatic recording** once for each Windows account that uses the instrument.
4. Close the status window. It is independent of the background recorder.

The existing Python 3.10+ installation with pywin32 is used. Ordinary users do not
run commands or choose Python environments. If the launcher cannot find the runtime,
the instrument maintainer completes this one-time setup. Local Windows policy may
require the maintainer to register the scheduled task; an error is shown if registration
is denied. Setup must be done on the measuring PC, not the development PC.

Registration uses the already signed-in Windows account; it does not request or
save a password. If an older version shows error `0x8007052E` (or `-2147023570`),
close the status window, install the corrected package, then reopen it and enable
automatic recording again. Setup errors are explained in the window and saved to
`%LOCALAPPDATA%\SXMMetadataRecorder\startup_error.json` when possible.

## Each day

Sign in to Windows, open SXM, create/select a data folder in SXM, and measure normally.
There is no folder to select in the recorder. Each absolute `.dat` path reported by
SXM's `SpectSave` event determines where metadata and enriched copies belong:

```text
today's data folder/
  spectrum_0001.dat                     original SXM output
  metadata/session_<SXM-connection>/    saved GUI/Topo captures
  metadata/enrichment_status.json      results or reasons for waiting
  enriched/spectrum_0001_bias_sweep.dat enriched copy
```

Change folders during the day or start a new folder tomorrow: the next save event
discovers it automatically. Raw measurements are opened read-only by this software.
Completed old folders are not continuously polled. Sessions and recovery records
are retained rather than erased when a new day starts.

## Restart and shutdown behavior

* **Close the status window:** recording continues.
* **Close/reopen SXM:** DDE reconnects automatically; a fresh connection ID is used.
* **Topo history:** off in automatic recording (`--no-topo`). The SXM driver opens
  without sharing, so a background reader holding it would lock sxm_ncafm_control and
  sxm_sts_control out. Topo history needs a recorder started by hand (RECORDER.md).
* **Recorder exits or its heartbeat stalls:** a separate supervisor restarts its
  own child. A stalled heartbeat has a 60-second detection threshold.
* **Supervisor exits:** Windows Task Scheduler starts it again, with a repeating
  one-minute trigger and restart settings. Duplicate launches are prevented by a lock.
* **PC restarts:** the scheduled task starts after that Windows account signs in.
  This runs in the interactive desktop needed for GUI reads, not as a system service.
* **Stop completely:** disables automatic startup/recovery and verifies shutdown.
  The stop survives restarts. **Enable / restart automatic recording** resumes it.
* **Remove automatic startup:** performs a complete stop, then removes the task.
  All recorded data is retained.

See [STOP_AND_RESTART.md](STOP_AND_RESTART.md) for the user instructions and
[HOW_IT_WORKS.md](HOW_IT_WORKS.md) for the capture process, files and limitations.

Saving still works normally if recording is unavailable. Metadata from an interval
when the recorder was absent cannot be recreated. An interrupted capture without
a durable snapshot/fingerprint stays unresolved; later GUI settings are never
substituted for missing acquisition settings.

## Durable capture and recovery

The recorder first saves the snapshot and a read-only raw-file fingerprint under
`%LOCALAPPDATA%\SXMMetadataRecorder\recordings`. These central records survive a
recorder/SXM restart. A worker then verifies that the saved raw bytes are still the
same, waits for stability, copies the captured metadata into the measurement
folder, and builds the enriched copy. File-read errors or unavailable drives are
retried; changed source content is never matched to stale settings.

Recovery compares UTC event times across reboots. Monotonic counters are used for
within-capture latency, not as a cross-reboot timeline. If the Windows clock moved
backwards enough to make event order ambiguous, enrichment waits rather than
claiming a verified association.

Original-byte preservation, versioned outputs, matrix limitations and uncertainty
labels described in `WORKFLOW.md` still apply. A verified filename/hash does not
make the exact acquisition-start height or unreadable GUI fields known.

## Status and maintenance

Double-click **SXM Metadata.vbs** at any time to view connection status, pending
copies and the latest data folder. The window also opens the metadata/enriched
folders. Detailed recovery and process logs live under the local state directory
above. Central captures and measurement-folder metadata should be archived with
the experiment; event logs rotate, while captures remain available for recovery.

For an explicit fixed-folder configuration, the older advanced controls remain
available through `python -m sxm_anfatec.tools.recorder_control --advanced`.
Do not run fixed-folder and automatic modes together.

Windows integration follows Microsoft's documentation for
[interactive task registration](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertaskdefinition)
and [logon triggers](https://learn.microsoft.com/en-us/windows/win32/taskschd/logon-trigger-example--scripting-).
XML validation checks syntax only; it does not prove account registration works.
The corrected setup was also checked by registering and removing a disabled
temporary task on the development PC. Actual restart/reboot behavior must still
be checked once on the instrument PC.
