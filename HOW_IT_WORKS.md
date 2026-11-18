# What SXM metadata recording does

SXM writes the original measurement. This companion records extra context and
creates a separate enriched copy. Its recorder uses read-only GUI access, DDE
notifications and Topo channel reads; it does not send measurement commands or
change instrument settings.

## From measurement to enriched copy

1. A background recorder listens for SXM events. Started by hand, it can also sample
   Topo into a bounded recent-history buffer (10 readings per second, 30 seconds of
   history); the automatic recorder does not, because the SXM driver accepts one
   handle and the tuning programs need it.
2. When SXM announces a saved spectroscopy through `SpectSave`, the recorder
   requests a GUI snapshot. Readable DNC, AFL, lock-in and spectroscopy settings,
   timestamps, read errors and recent Topo history are saved together.
   The live scan angle, centre and range are exported too. When readable, the
   highlighted Point in **Select** contributes its displayed X/Y/Z and parent
   image labels as a separate source; missing/ambiguous selections are reported.
3. The recorder saves a fingerprint of the raw file with that capture. The absolute
   path in the event identifies the measurement folder automatically.
4. A separate worker checks that the raw content still matches, waits for file
   stability, and copies the metadata into that measurement folder.
5. It writes a new file under `enriched`, adding `;Meta.*` comment headers before
   the original bytes. Bias/dz variation in the saved data determines whether the
   name ends in `_bias_sweep.dat`, `_z_sweep.dat`, `_mixed_sweep.dat` or
   `_constant_axes.dat`. Available point counts, timing and sweep summaries are
   also included. Original filenames are recorded in the header.

The original `.dat` is never opened for writing by this companion. A temporary
file is completed before the enriched copy is published. An existing different
enriched output is preserved by choosing a versioned name. This protection does
not prevent SXM itself from overwriting an original filename.

## Where files go

```text
Measurement folder selected in SXM/
  spectrum.dat                         raw SXM file
  enriched/spectrum_bias_sweep.dat      enriched copy
  metadata/session_<connection>/       captures and association evidence
  metadata/enrichment_status.json      completed, pending or blocked results

%LOCALAPPDATA%/SXMMetadataRecorder/
  recordings/session_<run>/            durable captures and event journals
  recordings/status.json               recorder health
  recordings/routing_status.json       discovered folders and recovery results
  supervisor_status.json               background supervisor health
  *.log                                process diagnostics
  PAUSED                               persistent user stop, when present
```

Daily sessions are separated, not erased. Archive measurement-folder metadata
with the data and retain central captures needed for unfinished recovery. Event
journals rotate; captures accumulate on disk. This is not a replacement for backups.

## What runs in the background

Windows Task Scheduler starts a supervisor in the signed-in user's desktop. The
supervisor starts one recorder and restarts it after an exit or stalled heartbeat.
The recorder contains the listeners, readers and enrichment worker. A separate
status window can be opened or closed without affecting recording.

SXM disconnection clears connection-specific Topo history; reconnection starts a
new connection identity. Closing SXM leaves the recorder waiting. Reboot recovery
starts after sign-in, because GUI access requires that user's desktop. The saved
captures permit unfinished enrichment to resume without reading today's settings
and pretending they belonged to yesterday's experiment.

**Stop completely** disables automatic startup and recovery, sets the persistent
stop, requests shutdown and checks worker locks. **Enable / restart automatic
recording** enables startup and clears the stop. See [STOP_AND_RESTART.md](STOP_AND_RESTART.md)
for user instructions, removal and troubleshooting.

## Timing and uncertainty

This is event-triggered capture, not an atomic snapshot of every instrument at
the exact acquisition-start instant. GUI fields are read sequentially after the
event; read-start/end times and event-to-read delay are retained. Filesystem
stability does not prove that SXM closed the file. Fingerprints prevent changed
raw content from being assigned a stale capture.

Absolute Topo readings and recent history provide useful height context. The
exact feedback-open reference for a z sweep is not independently verified; it
must not be treated as known merely because save-time Topo is available. Unreadable
fields, uncertain GUI mappings and unknown modulation units are recorded as such.
The Select point may refer to a different or older image; its coordinates and Z
are not substituted for the saved spectroscopy position or controller height.
Scan geometry is retained for later analysis without assuming a rotation convention.
The exporter currently supports single-spectrum tables; matrix exports are
reported unsupported instead of being silently interpreted as single spectra.

Metadata never captured while the watcher was stopped cannot be recovered.
Ambiguous associations remain pending/blocked. An enriched copy marked complete
means the copy was created, not that every possible instrument field was readable.

## Resource use and failure behavior

Queues and the Topo history buffer are bounded. Worker activity includes event
handling, GUI snapshots, low-rate channel reads, file hashes and short status
updates. Completed old folders are not continuously scanned; saved captures are
examined for recovery on startup. Startup work and disk usage can therefore grow
with the capture archive. CPU and RAM depend on the instrument PC and data sizes;
no fixed footprint or hard real-time synchronization is promised.

If the companion fails, SXM can continue writing its raw measurements. Metadata
or enrichment for the affected interval can be missing. Errors remain visible in
status/capture files. Shutdown keeps completed files and durable captures; it may
leave incomplete work for recovery. The supervisor may terminate its own stuck
recorder child after a graceful shutdown timeout, never SXM or unrelated tools.

For field definitions and provenance, see [SPECTROSCOPY_METADATA.md](SPECTROSCOPY_METADATA.md).
For association and output rules, see [WORKFLOW.md](WORKFLOW.md).
