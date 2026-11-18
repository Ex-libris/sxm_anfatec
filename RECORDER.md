# Spectroscopy metadata recorder and automatic enriched copies

For automatic daily operation, use **SXM Metadata.vbs** and `AUTOMATIC_RECORDING.md`.
The instructions below describe the optional fixed-folder advanced controls.

Runs in the logged-in Windows desktop session. Requires Python 3.10+ and the
existing sxm_anfatec dependencies (pywin32 for Topo). It only subscribes/reads:
no instrument settings are changed and no spectroscopy is started automatically.

From the directory containing `sxm_anfatec`, on the **instrument PC**:

```console
python -m sxm_anfatec.tools.recorder_control --advanced
```

Choose the SXM data folder using Browse; the metadata folder defaults to its `metadata` subfolder.
Keep **Automatically create copies** enabled. Enriched files go to the data folder's `enriched` subfolder.
Click **Start background**. Closing this control window leaves recording running.
Reopen it to inspect status or stop. **Enable at sign-in** creates a current-user
Windows Run entry (no administrator rights); **Disable at sign-in** removes it.
Sign-in startup uses the saved configuration and the same Python installation.
This is a background process with a control window, not a system service or tray icon.

Alternatively:

```console
python -m sxm_anfatec.recorder --watch "D:\SXM\data" --output "D:\SXM\metadata"
python -m sxm_anfatec.recorder status --output "D:\SXM\metadata"
python -m sxm_anfatec.recorder stop --output "D:\SXM\metadata"
```

## What is recorded

* DDE subscriptions: SpectSave, SaveFileName, MicState, Scan, ScanLine, Command.
  Each subscription's success is reported. Raw event bytes and decoded payloads
  are saved. DDE connects, pumps messages and disconnects on the same thread;
  reconnect attempts occur every five seconds.
* Full shared-bridge snapshot on SpectSave, SaveFileName, changed MicState/Scan,
  baseline startup and newly stable `.dat` observations. State changes are
  **candidates**, not confirmed spectroscopy-start events.
* Topo channel 0, raw counts and scaled values with the current driver calibration,
  at a default requested 10 Hz, with a bounded 30-second history. Actual timestamps
  determine achieved rate. The buffer preceding each trigger is included in its
  snapshot. The GUI reads have separate start/end times and trigger latency.
* Files in the chosen folder (non-recursive): changes and SHA-256 after two seconds
  without an observed size/mtime change. This is evidence of stability, **not proof
  that SXM closed the file**. Existing files are baselined at recorder startup.

The session folder contains `session.json`, `events.jsonl` and `capture_<id>.json`.
`status.json` in the metadata folder reports current availability, missing GUI
sections, dropped records and a heartbeat. A stopped/crashed process must not be
inferred alive from its last status: check heartbeat age. `launcher.log` captures
background startup failures. A Windows lock prevents duplicate recorders using
the same output folder; use only one recorder overall.

Event logs rotate at 10 MB with five backups **per session**; older diagnostic
events eventually expire. Snapshots and old sessions are retained, so archive or
remove old diagnostic sessions after analysis. Session rollover is not automatic.
Snapshots use temporary files and atomic replacement. Interrupted temporary
files are not treated as completed captures. The next run creates a new session;
pending enriched copies are recovered from saved captures in earlier session folders.

## Instrument test

1. Start before the measurement. Check accepted subscriptions, Topo status and
   GUI section errors. Open required SXM panels once if they have not been created.
2. Perform a normal single spectrum and save as usual; repeat at a second point.
3. Perform your normal z spectroscopy and a small matrix, recording which filenames
   correspond to each operation. No diagnostic tool should move the tip for you.
4. Inspect notification payloads, GUI changes, Topo timing and file records together.
   Establish feedback-open timing, per-spectrum matrix notifications, filename
   semantics, dz sign/offset composition and the meaning of controller Topo.

**Exact start-event reference height remains unverified.** Matching single-spectrum
files are enriched automatically into separate copies using SpectSave filenames
and stable-file SHA-256 evidence. Missing fields and save-time provenance remain
explicit. Matrix files and ambiguous associations remain pending/blocked; raw files
are never edited. A save event
can occur after feedback restoration; a polled sample is not an exact latched
reference. A GUI snapshot is sequential, and displayed edits need not have been
committed to the controller. Fast GUI transitions can be missed.

## Sharing the controller

The current driver opens without sharing. If ncAFM/STS already owns its handle,
the standalone recorder logs Topo unavailable and retries; GUI/DDE recording
continues. `--no-topo` disables the attempts. For embedding in an application,
`Recorder(..., driver=existing_driver)` uses its existing thread-safe handle and
does not close it. Do not replace that application's driver or open a competing
handle. A common driver-owning background service is future integration work.

## Offline verification

`python -m unittest sxm_anfatec.tools.test_recorder` exercises capture dispatch,
bounded history, missing GUI data, serialization, file observation and offline
Windows lifecycle. It does not validate event timing or controller calibration.

## Automatic copy workflow

See `WORKFLOW.md` for layout, recovery, publication checks and limitations.
Use `--no-enrich` for capture-only operation. When `--watch` is supplied and
`--output` is omitted, metadata defaults to `DATA_FOLDER/metadata`.
The journal now writes directly to JSONL and flushes each record, independently
of Python logging configuration. Status includes journal counts/errors and
enrichment complete/pending/blocked counts.
