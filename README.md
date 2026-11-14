# Pixel outage monitor

The monitor reads NERSC RMS and mean maps and scores physical chip footprints against delayed history. Install requirements.txt in the GPVM virtual environment. Use check for one sweep or watch for continuous acquisition. The sole watcher defaults to runs/delayed and resumes prior delayed state. Use one writer per work directory. The acquisition checks source age, PNG integrity, both producer timestamp reads and both image titles. Transient downloads retry once after three seconds.

```sh
mkdir -p runs/delayed
.venv/bin/python -m unittest -v
.venv/bin/python -u monitor.py watch >> "runs/delayed/watch-$(date +%Y%m%d-%H%M%S).log" 2>&1
```

## Review

Run `.venv/bin/python review.py --port 8766` in another session. Forward port 8766 over SSH and open http://127.0.0.1:8766/. Refresh to read new records. The current state always shows the latest processed timestamps. The timeline uses Chicago time and includes legacy delayed broad observations, inferred reconfiguration holds, incident transitions and available evidence. Pick an IO group, tile and time window for up to 80 recent decisions. The server reads delayed watch-*.log files on request and serves named evidence PNGs under the work directory. Keep stdout logging enabled. Old logs cannot supply missing measurements or recreate unsaved images. Retention is manual.

## Detection

The delayed rule uses the wiki for eligibility. It skips footprints below the existing coverage floor in either panel, plus chips with at most 10% nonwhite coverage in the central 56% of width and height in both wiki panels. These are image-inferred exclusions. Optional --exclusions FILE takes additional reviewed chip IDs, for example {"4":[[1,110],[1,100]]}, keyed by IO group. The left side of delayed evidence images shows the wiki mask; numeric reference values in the log are the per-chip medians.

The first three accepted frames aged 300–900 seconds establish a reference. Start under healthy conditions; an outage present throughout startup can enter the median. In each panel divide current coverage by the chip's history median, then divide by the median ratio of eligible peers on that tile. Where at least half the eligible chips and at least ten peers remain, exclude preliminary loss candidates from the peer estimate. The ordinary 35%/60%, adjacency and two-frame rules operate on these adjusted ratios. Fewer than ten eligible chips pauses that tile. Freeze a suspect chip's reference from first suspicion; feed its frozen value into history until it clears. Mature history also substitutes baseline for a tile whose median retained coverage exceeds 150% in either panel. Seed frames during warm-up have less protection.

For broad loss, report a tile if its median ratios are at most 50% in one panel and 80% in the other. Log the other tiles' median ratios for comparison. Freeze its eligible chip references. When the median ratios among eligible chips on other tiles are both at least 75%, two consecutive broad observations within max-gap open a broad incident or expand a localized incident. Save one tile-outline image on that transition. Other widespread changes pause the tile. Two evaluated clear frames resolve it. A separate group-level check runs even during warm-up after prior activity is available. By default an ADC mean coverage drop to 75% or less of recent activity, with at least four tile means at or below 85%, marks likely_reconfiguration. Recent activity is the median of at least three accepted measurements in the last two minutes, otherwise the preceding good measurement within max-gap. Hold the pre-drop anchor, skip history updates and preserve active incidents. Two fresh frames at or above 85% group recovery and with at least five tiles at or above 80% clear the hold. A prolonged hold can require warming up again. A sustained group failure can also trigger this status; review prolonged holds. All thresholds are provisional; --reconfig-ratio changes the group entry threshold.

Delayed JSON includes status, active incidents, window_frames and, on scored frames, candidates (current, reference, peer and adjusted arrays ordered RMS then mean), previous_stamp, broad_tile_losses and insufficient_tiles. State is saved per IO group for restart. Each scored frame also records tile decisions, eligible chip counts, coverage and retained ratios. Group holds record the group and tile mean ratios. Comparable history after a deliberate change of configuration may require a fresh work directory.

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
