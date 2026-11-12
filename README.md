# Pixel-map outage monitor

Python 3.10+ on macOS or Linux. Run from this directory.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v
python monitor.py watch --groups 3
```

The default reference is the CRS shifter wiki. Start watch directly, or use check for one sweep. Omit --groups for all eight groups. State and incident images default to runs/wiki. Run one process per work directory. Each JSON line identifies the reference and IO group.

The included wiki_reference directory contains the individual plot panels cropped from the supplied *CRS Shifting Examples* printout. Page 2 is the expected RMS figure and page 3 starts with its expected mean counterpart. Both show November 10, 2026 data. Each IO group has its own RMS and mean images, including normally quiet areas and excluded chips. The missing-IO-channel example on page 4 is used only as a test fixture. Its IO group 1 right-hand loss maps to tile 6. The separate hot-pixel rate graph supplies no PNG reference.

For a different detector configuration you can still capture a healthy reference and select it explicitly:

```sh
python monitor.py baseline --groups 3
# Inspect runs/default/iog_3/baseline/{mean,rms}.png
python monitor.py watch --reference captured --groups 3
```

Captured mode uses the original runs/default directory, native-resolution comparison and existing incident state. Use a fresh --work directory for a new capture.

## Decision rule

Fetch the producer timestamp before and after both PNGs, check source age (120 s default, America/Chicago), PNG integrity and complete decoding. Repeated producer timestamps or unchanged title bands in either image do not advance confirmation. Watch retries on the next sweep. Stale or unreadable input leaves open incidents intact. The writer updates separate files, so these tests reduce races but cannot guarantee an atomic acquisition. The watch interval is a pause after each sequential sweep.

Crop the detector axes using normalized bounds measured from a native group 3 PNG. Divide the crop into 16 columns and 32 rows, or 8 by 8 cells per tile. In wiki mode, area-resample each plot interior to 160 by 320 pixels before thresholding, to compare the native PNG and the smaller printed reference on a common grid. Compare the fraction of non-white area in each cell with its own baseline in both maps. The proposed defaults require baseline coverage of at least 10% and current coverage at most 35% of baseline in each metric. Two neighboring cells must meet both tests in the same locations on two distinct frames no more than 120 s apart. The monitor saves one comparison PNG with both metrics per newly affected tile, outlining the confirmed cells. Two successive clear observations close that tile's incident. Additional losses on a tile already open are part of that incident. Work state persists across restarts.

Use --ratio, --min-coverage, and --max-gap to tune against labeled data. Regression tests cover the printed examples; the wiki provides one labeled missing-channel image pair and no independent time sequence. Sensitivity and false-alarm rates across runs remain unmeasured. Bright or normally sparse cells below the baseline floor are insensitive; small losses within a cell can be missed. A changing run mode can affect both maps. A repeatedly malformed but valid producer rendering can mimic a detector outage. This tool measures a map symptom.

## Offline and on-site sources

Use --source /path/to/RealTime to read the same per-group mean/rms PNGs and last_update.txt files from disk. For historical frames add --max-age 0 to each command. Replay by copying successive complete file sets into that directory and running check once per set in chronological order. The checked-in test creates a temporary example and exercises the actual CLI, including a truncated transfer, confirmation, evidence and recovery. For live use keep source-age checks enabled. The working directory must survive service restarts.

## Tile maps

The guide follows the website layout (odd groups above even groups). PlotMetrics derives the geometry face from group parity, maps each block of four I/O channels to one tile, and adds 8 to the geometry tile key for even groups. The Grafana tile_id stays in the range 1–8. The generator reads channel XY coordinates from the four default v4 files, sorts tile centers by descending Y and then ascending X. The two local checkouts disagree on group 8's top row. Verify the geometry files actually loaded at startup, especially geometry_mod3_v4.json. Default is CRS_DAQ; pass --geometry pacmon for the standalone checkout. Regenerate from the relevant checkout if the deployed geometry changes:

```sh
python geometry_sheet.py ../CRS_DAQ/pacmon/layout --name CRS_DAQ
python geometry_sheet.py ../pacmon/layout --name pacmon
```

## Deployment and Grafana

Start locally; then run watch under a service manager on an always-on monitoring host with read access to the source and persistent storage. Reading the mounted RealTime directory avoids the portal transfer. Polling the portal instead monitors the view available to shifters. Neither requires altering the DAQ process.

InfluxDB integration is a next step. Inspect recent production data for packet_adc_per_channel or packet_adc_per_chip with positive n_packets. Use the full channel identity (io_group, io_channel, chip, channel), or the full chip identity for chip-level checks, and compare the set seen in a rolling window with a known-good set during comparable data taking. Missing series count as missing only when independent run/ingestion health is good. The CRS_DAQ checkout enables the per-chip measurement; finer reporting depends on the running build.

For InfluxDB v2 result export, provision an endpoint, org, bucket, and scoped write token on the monitoring host. Use environment or service secrets. A writer should emit explicit 0/1 outage samples for every evaluated tile, with stable io_group and tile_id tags and acquisition timestamps. Track source health separately; stale/unavailable frames must not resolve an incident. Use a read-capable Grafana data source and a multi-dimensional alert on the latest fresh value per tile. Route via the existing Slack contact point. To share saved comparisons, expose them via a shifter-accessible HTTP path; local paths in JSON are only usable on the monitoring host. This prototype does not send notifications or write to InfluxDB.
