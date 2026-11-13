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

Fetch the producer timestamp before and after both PNGs, check source age (120 s default, America/Chicago), PNG integrity and complete decoding. Repeated producer timestamps or unchanged title bands in either image do not advance confirmation. On a timestamp race, download error or invalid PNG, the monitor waits three seconds and repeats the entire acquisition once for that group. JSON output includes attempts and, when retried, retry_reason. A stale timestamp skips the immediate retry. If both attempts fail, watch advances to the next group and returns on its next sweep. Stale or unreadable input leaves open incidents intact. The writer updates separate files, so these tests reduce races but cannot guarantee an atomic acquisition. The watch interval is a pause after each sequential sweep.

Within the plot crop, score each chip inside its own projected footprint. The bundled geometry tables contain the tile ID, chip ID and normalized bounding box of its channel centers, expanded by half a pixel pitch. Geometry Y increases upwards. Wiki mode area-resamples to 160 by 320 pixels first, giving about 8 by 8 image samples per chip. Captured mode uses the native resolution. PNG registration is approximate, especially at wiki resolution.

Each panel must have at least 10% reference coverage on the chip. With the defaults, either RMS or mean must retain at most 35% of its reference coverage, while the other may retain up to 60%. Raise --secondary-ratio to loosen corroboration or set it to .35 to restore the strict two-panel threshold. Lowering these retained-coverage cutoffs makes detection stricter. A chip already blank in the reference cannot establish a new loss.

Two edge-adjacent chips within one tile must pass on the same two fresh updates no more than 120 seconds apart. Use --allow-single-chip for persistent isolated losses when testing. Opening events list the supported chip IDs and the evidence outlines their footprints. Resolution requires two successive clear frames. The first evaluation after upgrading discards old grid hits and retains open tile incidents. Chip IDs are inferred from the map image and selected geometry.

Regression replay covers the wiki examples and the group 2 bottom-left strip in the November 11 19:42:57 images. The group 2 fixtures contain the cropped detector at wiki scoring resolution. Tests simulate an advancing title because only one affected acquisition is available for each example. False-positive rates across running conditions remain unmeasured.

## Offline and on-site sources

Use --source /path/to/RealTime to read the same per-group mean/rms PNGs and last_update.txt files from disk. For historical frames add --max-age 0 to each command. Replay by copying successive complete file sets into that directory and running check once per set in chronological order. The checked-in test creates a temporary example and exercises the actual CLI, including a truncated transfer, confirmation, evidence and recovery. For live use keep source-age checks enabled. The working directory must survive service restarts.

## Tile maps

The guide follows the website layout (odd groups above even groups). PlotMetrics derives the geometry face from group parity, maps each block of four I/O channels to one tile, and adds 8 to the geometry tile key for even groups. The Grafana tile_id stays in the range 1–8. The generator reads channel XY coordinates from the four default v4 files, sorts tile centers by descending Y and then ascending X. The two local checkouts disagree on group 8's top row. Verify the geometry files actually loaded at startup, especially geometry_mod3_v4.json. Default is CRS_DAQ; pass --geometry pacmon for the standalone checkout. Regenerate from the relevant checkout if the deployed geometry changes:

```sh
python geometry_sheet.py ../CRS_DAQ/pacmon/layout --name CRS_DAQ
python geometry_sheet.py ../pacmon/layout --name pacmon
python chip_geometry.py ../CRS_DAQ/pacmon/layout chips_CRS_DAQ.npz
python chip_geometry.py ../pacmon/layout chips_pacmon.npz
```

## Deployment and Grafana

Start locally; then run watch under a service manager on an always-on monitoring host with read access to the source and persistent storage. Reading the mounted RealTime directory avoids the portal transfer. Polling the portal instead monitors the view available to shifters. Neither requires altering the DAQ process.

InfluxDB integration is a next step. Inspect recent production data for packet_adc_per_channel or packet_adc_per_chip with positive n_packets. Use the full channel identity (io_group, io_channel, chip, channel), or the full chip identity for chip-level checks, and compare the set seen in a rolling window with a known-good set during comparable data taking. Missing series count as missing only when independent run/ingestion health is good. The CRS_DAQ checkout enables the per-chip measurement; finer reporting depends on the running build.

For InfluxDB v2 result export, provision an endpoint, org, bucket, and scoped write token on the monitoring host. Use environment or service secrets. A writer should emit explicit 0/1 outage samples for every evaluated tile, with stable io_group and tile_id tags and acquisition timestamps. Track source health separately; stale/unavailable frames must not resolve an incident. Use a read-capable Grafana data source and a multi-dimensional alert on the latest fresh value per tile. Route via the existing Slack contact point. To share saved comparisons, expose them via a shifter-accessible HTTP path; local paths in JSON are only usable on the monitoring host. This prototype does not send notifications or write to InfluxDB.

## Delayed-reference shadow run

Run `python monitor.py watch --reference delayed` separately from the existing wiki watcher. The default directories are runs/delayed and runs/wiki. Give each process its own work directory. Run one writer per directory. Both watchers use the same source; pair results by IO group and producer stamp when comparing logs.

The delayed rule uses the wiki for eligibility. It skips footprints below the existing coverage floor in either panel, plus chips with at most 10% nonwhite coverage in the central 56% of width and height in both wiki panels. These are image-inferred exclusions. Optional --exclusions FILE takes additional reviewed chip IDs, for example {"4":[[1,110],[1,100]]}, keyed by IO group. The left side of delayed evidence images shows the wiki mask; numeric reference values in the log are the per-chip medians.

The first three accepted frames aged 300–900 seconds establish a reference. Start under healthy conditions; an outage present throughout startup can enter the median. In each panel divide current coverage by the chip's history median, then divide by the median ratio of eligible peers on that tile. Where at least half the eligible chips and at least ten peers remain, exclude preliminary loss candidates from the peer estimate. The ordinary 35%/60%, adjacency and two-frame rules operate on these adjusted ratios. Fewer than ten eligible chips pauses that tile. Freeze a suspect chip's reference from first suspicion; feed its frozen value into history until it clears. Mature history also substitutes baseline for a tile whose median retained coverage exceeds 150% in either panel. Seed frames during warm-up have less protection.

For broad loss, report a tile if its median ratios are at most 50% in one panel and 80% in the other. Log the other tiles' median ratios for comparison. Freeze its eligible chip references and pause its incident transitions. A separate group-level check runs even during warm-up after prior activity is available. By default an ADC mean coverage drop to 75% or less of recent activity, with at least four tile means at or below 85%, marks likely_reconfiguration. Recent activity is the median of at least three accepted measurements in the last two minutes, otherwise the preceding good measurement within max-gap. Hold the pre-drop anchor, skip history updates and preserve active incidents. Two fresh frames at or above 85% group recovery and with at least five tiles at or above 80% clear the hold. A prolonged hold can require warming up again. A sustained group failure can also trigger this status; review prolonged holds. All thresholds are provisional; --reconfig-ratio changes the group entry threshold.

Delayed JSON includes status, active incidents, window_frames and, on scored frames, candidates (current, reference, peer and adjusted arrays ordered RMS then mean), previous_stamp, broad_tile_losses and insufficient_tiles. State is saved per IO group for restart. Inspect both watchers' logs for differences, including suppressed periods. Comparable history after a deliberate change of configuration may require a fresh work directory.
