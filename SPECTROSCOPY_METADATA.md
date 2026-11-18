# Enriched spectroscopy headers

`spectroscopy_metadata.py` creates separate `.dat` copies with a `;Meta.*` header.
It reads an existing diagnostic session; it does not connect to the instrument.
The background recorder automatically creates copies in DATA_FOLDER/enriched when
matching saved captures are available. The command below is also available for
manual export. Raw files are opened read-only.

From the directory containing `sxm_anfatec`:

```console
python -m sxm_anfatec.spectroscopy_metadata --session "D:\data\session_..." --output-directory "D:\data\enriched" "D:\data\spectrum.dat"
```

Several input files may be supplied. Existing destinations and in-place writes are
refused. Matching requires one `SpectSave` filename and one file-stability capture
with the same filename and original SHA-256; ambiguous sessions are refused.
Publication uses a flushed temporary file, a second source-hash check and an
atomic operation that refuses to replace an existing destination.
Source headers, column headings and data bytes are preserved. Our metadata block
can be replaced without duplication by the library's `enriched_bytes` function.

## Useful fields

* DNC used f0, separately from last-sweep fPeak/Q; uses the existing ncAFM four-field
  positional mapping, explicitly identified as such. The used f0 is a displayed
  setting, not a measurement of instantaneous PLL frequency.
* Lock-in time constant, roll-off, frequency display, phase and link for Lia1-3;
  Lia1 modulation amplitude, input gain and output routing.
* AFL, PLL, feedback and spectroscopy GUI settings, captured at save time. These
  can differ from acquisition settings. Unknown and missing fields stay explicit.
* Number of saved rows, acquisition time per point, delays, averaging/SNR header,
  median/min/max point interval and first/last recorded time.
* For dz and Bias: first/last, min/max, span, net change, total recorded travel,
  median nonzero step, directional leg count and mean travel rate. Travel sums
  absolute changes between saved rows, including reverse legs; it excludes
  unsaved lead-in/return motion. Cycles are not guessed from leg count.
* Mean and range of `Topo - dz` when both columns exist; controller Topo at save
  is labelled separately. Exact start-event reference is still unavailable.

`NominalPointAcquisitionSum_s` is rows times the header's acquisition-per-point
value. It excludes overhead and does not correct for unrecorded repetitions or
averaging effects. `FirstToLastPoint_s` is the difference of saved timestamps;
`DataClockLast_s` is the last timestamp on SXM's own axis. True wall-clock elapsed
time requires a verified start event and is not guessed from these values.

The bridge cannot read the modulation amplitude's switchable unit label. The
displayed number is retained with unknown unit unless `--modulation-unit uV`,
`mV` or `V` is explicitly supplied for the captured session. This is the oscillator
rms setting, not a calibrated junction amplitude; bias range, routing and any
external attenuation must be accounted for separately.

## Compatibility and checks

The four supplied single-spectrum files were exported and read through the
current sxm_viewer parser: axes, channel arrays and coordinates remained equal.
Removing our block reconstructs the original files byte for byte.

SXM's own re-import has not been tested. Matrix export is intentionally unsupported:
the current viewer's matrix parser expects its coordinate rows at the very top,
and per-spectrum metadata association needs separate validation.

```console
python -m unittest sxm_anfatec.tools.test_spectroscopy_metadata -v
```

Automatic/CLI outputs are named `ORIGINAL_STEM_bias_sweep.dat`,
`ORIGINAL_STEM_z_sweep.dat`, `ORIGINAL_STEM_mixed_sweep.dat`, or
`ORIGINAL_STEM_constant_axes.dat`, using saved axis variation. Headers retain the
original filename and record the classification and its source.
# Scan geometry and the selected image point

New captures include live scan geometry in the enriched header:
`GUI.Scan.Angle_deg`, `GUI.Scan.CenterX_nm`, `GUI.Scan.CenterY_nm`,
`GUI.Scan.Range_nm`, and `GUI.Scan.Pixel`. The angle was already present in the
full Scan JSON snapshot; it is now explicitly exported with the geometry.

When the SXM **Select** window exposes a highlighted **Point**, or a coordinate
under that Point, its displayed values are captured as `GUI.Select.XDisplayed`,
`GUI.Select.YDisplayed`, and `GUI.Select.ZDisplayed`. `GUI.Select.ImageTreePath`
records the parent image labels, and `CoordinateLabels` retains the original text.
Read-start/end times and the accessibility source are stored with the capture.
These values remain separate from `Acquisition.Position_xy`, which comes from the
original `.dat` `x/y-Pos` header, and from the controller Topo measurements.

The reader does not click, expand, select, move the tip, or access foreign process
memory. It reads the existing tree through Windows accessibility and query-only
messages, including collapsed children when exposed by the control. It rejects
a changing selection, changing coordinates, missing X/Y, duplicate axes and
ambiguous windows. It records an unavailable reason if the window/control cannot
be read. The rest of the snapshot and raw measurement are retained.

Units are preserved only when shown in the Select labels. In particular, displayed
image-point Z is not automatically an absolute height or a controller Topo value.
The selected Point may belong to an older image; the live Scan angle/centre are
not proven to describe that image. The parent label is context, not a unique file ID.
No coordinate rotation is applied: `Position.TransformApplied` is false. Axis
direction, rotation sign, origin and the relationship between the displayed and
saved coordinate frames need instrument verification before projection.

This applies to new captures. Historical captures cannot recover a Select point
that was never read. Offline tests include a separate native Windows tree-control
process; actual Delphi/SXM compatibility still needs a test spectrum on the instrument.

