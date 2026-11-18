# Raw measurements, recorded metadata, enriched copies

For ordinary daily use, follow `AUTOMATIC_RECORDING.md`: one-time setup, then
folder discovery and background recovery are automatic. The fixed-folder controls
below remain available as an advanced option.

The recorder never opens measurement `.dat` files for writing. SXM keeps saving
them in its usual folder. Enrichment is a separate, retryable background job:

```text
20261110/
  au111_angii_0002.dat          original SXM output
  au111_angii_0003.dat          original SXM output
  metadata/
    session_.../
      session.json
      events.jsonl
      capture_....json
    status.json
    enrichment_status.json
    launcher.log
  enriched/
    au111_angii_0002_bias_sweep.dat  original content plus ;Meta.* block
    au111_angii_0003_bias_sweep.dat
```

Stop the old recorder before updating the package on the instrument PC. Open
`python -m sxm_anfatec.tools.recorder_control --advanced`, Browse to the SXM data folder,
and leave **Automatically create copies** selected. Browse sets the metadata
folder to `DATA_FOLDER/metadata`; an explicitly chosen metadata folder is also
supported. Start background and optionally enable startup at sign-in.

The alternative command is:

```console
python -m sxm_anfatec.recorder --watch "D:\Infinity_009_Data\SPM data\20261110"
```

## Publication rules

1. Record the save event and GUI/Topo snapshot independently of enrichment.
2. Observe `.dat` stability for two seconds and record its SHA-256. Stability
   alone does not prove the file is closed; the save event and parsing checks
   are also required.
3. Match the filename, save capture and file hash. Multiple save captures for
   the same filename in one session are blocked rather than guessed. Earlier
   stability observations before the save notification are ignored.
4. Build the enriched content in memory. Removing the new metadata block must
   reproduce the original content byte for byte.
5. Write and flush a temporary file in `enriched`, re-read the original hash,
   then atomically publish the copy. No original is moved, renamed or modified.

If a previous enriched result exists, identical content is reused. Different
content receives a deterministic version suffix; previous results are never
overwritten. The manifest records exact source/output hashes and capture paths.
Temporary files left by a hard process termination are not valid published files;
they may be removed later, and are never watched as raw data.

An exception, absent file, unavailable metadata, unsupported matrix format, or
hash mismatch leaves the job pending/blocked with its reason. Recording proceeds
independently. On restart, captures in the configured metadata folder are scanned
again and missing copies can be regenerated. An old raw file with no matching
capture stays raw; current GUI settings are never attached retrospectively.

`enrichment_status.json` reports each job, its output or its reason for waiting.
`complete` means a verified copy was published, not that every GUI field or the
exact reference height is known. Unavailable fields and save-time observations
remain labelled in its header. Units come from the capture's original session,
not from a later recorder session's configuration.

## Limits

This workflow protects originals from the recorder/exporter. It cannot prevent
SXM itself from overwriting a reused filename, or prevent disk/hardware failure;
ordinary backups remain separate from enrichment. The existing spectroscopy
options reader and exact start-height detection still need instrument validation.
SXM re-import of enriched copies is not yet verified. Current single-spectrum
copies were checked with the sxm_viewer parser; matrix enrichment remains blocked.

The JSONL journal is independent of Python logging filters, flushes every record
and syncs periodically. Journal failures/loss counts are exposed in status. Logs
rotate; saved captures and enriched files are retained until explicitly removed.

## Offline checks

```console
python -m unittest sxm_anfatec.tools.test_recorder sxm_anfatec.tools.test_spectroscopy_metadata sxm_anfatec.tools.test_enrichment_workflow -v
```

Tests cover end-to-end automatic output, original byte/mtime preservation,
deferred captures, restart recovery, missing-copy regeneration, ambiguous saves,
source changes during publication, prior output protection, per-session units,
and JSONL writing with Python logging globally disabled.

## Spectroscopy identification

Enriched filenames retain the original stem and add `_bias_sweep`, `_z_sweep`,
`_mixed_sweep`, or `_constant_axes`. The header stores `Meta.OriginalFilename`,
`Meta.Spectroscopy.Type`, and its source. Classification is based on variation
of the saved Bias/dz columns, not the GUI mode at save time. A single point or
constant axes do not establish a swept axis; both varying is explicitly mixed.
The original filename and `.dat` extension are preserved in the raw folder.
