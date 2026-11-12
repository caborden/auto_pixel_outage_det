"""Compare fresh RMS and mean pixel maps with an operator-reviewed reference."""
import argparse
from http.client import IncompleteRead
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PORTAL = "https://portal.nersc.gov/project/dune/data/2x2/DQM/PixelMaps/RealTime"
METRICS = ("rms", "mean")

# Interior of the gonum axes, measured on a 2369 x 4876 native RMS map.
ROI = (.042, .075, .997, .985)



def read(source, name):
    if source.startswith(("https://", "http://")):
        request = Request(f"{source.rstrip('/')}/{name}?t={time.time_ns()}",
                          headers={"Cache-Control": "no-cache"})
        with urlopen(request, timeout=15) as response:
            return response.read()
    return (Path(source) / name).read_bytes()


def decode(data):
    if not data.endswith(bytes.fromhex("0000000049454e44ae426082")):
        raise ValueError("missing PNG end marker")
    with Image.open(io.BytesIO(data)) as im:
        if im.format != "PNG":
            raise ValueError("expected PNG")
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if not .46 < im.width / im.height < .52 or im.width < 160:
            raise ValueError(f"unexpected map size {im.size}")
        return im.convert("RGB")


class StaleFrame(ValueError):
    pass


def acquire(args, group):
    name = f"iog_{group}_last_update.txt"
    stamp = read(args.source, name).decode().split()
    moment = datetime.strptime(" ".join(stamp), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo(args.timezone))
    age = time.time() - moment.timestamp()
    if args.max_age and not -15 <= age <= args.max_age:
        raise StaleFrame(f"source age {age:.0f}s")
    images = {m: decode(read(args.source, f"iog_{group}_{m}.png")) for m in METRICS}
    if read(args.source, name).decode().split() != stamp:
        raise ValueError("update changed during download")
    if images["rms"].size != images["mean"].size:
        raise ValueError("map dimensions disagree")
    return int(moment.timestamp()), images


def acquire_with_retry(args, group, result):
    for attempt in (1, 2):
        result["attempts"] = attempt
        try:
            return acquire(args, group)
        except StaleFrame:
            raise
        except (OSError, ValueError, SyntaxError, EOFError, IncompleteRead) as exc:
            if attempt == 2:
                raise
            result["retry_reason"] = str(exc)
        time.sleep(3)


def plot_box(im):
    return tuple(round(f * n) for f, n in zip(ROI, im.size * 2))


def occupancy(im, boxes, wiki=False):
    # Threshold before averaging so scattered hits count by their area.
    crop = im.crop(plot_box(im))
    if wiki:
        # Compare displayed coverage at the resolution of the wiki plot interiors.
        crop = crop.resize((160, 320), Image.Resampling.BOX)
    mask = crop.convert("L").point(lambda v: 255 if v < 242 else 0)
    # Integrate the binary mask inside each projected chip footprint.
    scaled = np.rint(boxes * (mask.width, mask.height, mask.width, mask.height)).astype(int)
    pixels = np.asarray(mask)
    return np.array([pixels[top:bottom, left:right].mean() / 255
                     for left, top, right, bottom in scaled])


def title_id(im):
    return hashlib.sha256(im.crop((0, 0, im.width, round(im.height * .065))).tobytes()).hexdigest()


def save_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)


def baseline(folder, stamp, maps):
    target = folder / "baseline"
    if target.exists():
        raise ValueError("baseline already exists; use a new --work directory")
    with tempfile.TemporaryDirectory(dir=folder) as staging:
        stage = Path(staging) / "baseline"
        stage.mkdir()
        for m, im in maps.items():
            im.save(stage / f"{m}.png")
        save_json(stage / "source.json", stamp)
        stage.rename(target)


def evidence(path, refs, maps, boxes, group, stamp, reference_stamp):
    canvas = Image.new("RGB", (860, 2 * 910), "white")
    pen = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=18)
    for row, metric in enumerate(METRICS):
        for col, (label, src, ts) in enumerate((("Reference", refs, reference_stamp), ("Current", maps, stamp))):
            original = src[metric]
            scale = min(420 / original.width, 850 / original.height)
            thumb = original.resize((round(original.width * scale), round(original.height * scale)),
                                    Image.Resampling.LANCZOS)
            x, y = 5 + col*430, 56 + row*910
            canvas.paste(thumb, (x, y))
            pen.text((x, y-50), f"{label}   /   IOG {group}   /   ADC {metric.upper()}", font=font, fill="black")
            pen.text((x, y-26), (time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))
                      if ts is not None else "Shifter wiki  /  2026-11-10"), font=font, fill="black")
            left, top, right, bottom = plot_box(thumb)
            for x0, y0, x1, y1 in boxes:
                pen.rectangle((x+left+x0*(right-left), y+top+y0*(bottom-top),
                               x+left+x1*(right-left), y+top+y1*(bottom-top)),
                              outline="#0082ff", width=2)
    canvas.save(path)


def evaluate(folder, group, stamp, maps, args):
    wiki = args.reference == "wiki"
    refdir = Path(__file__).with_name("wiki_reference") if wiki else folder / "baseline"
    reference_stamp = None if wiki else json.loads((refdir / "source.json").read_text())
    statefile = folder / "state.json"
    state = json.loads(statefile.read_text()) if statefile.exists() else {}
    if state and state["geometry"] != args.geometry:
        raise ValueError("geometry changed; start a new --work directory")
    if state and state.get("reference", "captured") != args.reference:
        raise ValueError("reference changed; use separate --work directories")
    if stamp <= max(state.get("stamp", 0), reference_stamp or 0):
        return {"status": "waiting_for_new_frame"}
    titles = {m: title_id(im) for m, im in maps.items()}
    refs = {}
    chips = args.chips[group]
    boxes = chips[:, 2:]
    eligible = np.ones(len(chips), dtype=bool)
    ratios = []
    for m in METRICS:
        filename = f"iog_{group}_{m}.png" if wiki else f"{m}.png"
        with Image.open(refdir / filename) as im:
            refs[m] = im.convert("RGB")
        if not wiki and refs[m].size != maps[m].size:
            raise ValueError("map size changed since baseline")
        previous_title = state.get("titles", {}).get(m)
        if previous_title is None and not wiki:
            previous_title = title_id(refs[m])
        if titles[m] == previous_title:
            return {"status": "waiting_for_new_frame"}
        expected = occupancy(refs[m], boxes, wiki)
        eligible &= expected >= args.min_coverage
        ratios.append(occupancy(maps[m], boxes, wiki) / np.maximum(expected, 1e-9))
    candidates = eligible & (np.minimum(*ratios) <= args.ratio) & (np.maximum(*ratios) <= args.secondary_ratio)
    hits = set(np.flatnonzero(candidates).tolist())
    consecutive = state.get("scoring") == "chip-v1" and stamp - state["stamp"] <= args.max_gap
    previous = set(state.get("hits", [])) if consecutive else set()
    active = set(state.get("active", []))
    events = []
    for tile in range(1, 9):
        region = set(np.flatnonzero(chips[:, 0] == tile).tolist())
        persistent = hits & previous & region
        # Each tile occupies 100 consecutive indices in physical row-major order.
        supported = {i for i in persistent if args.allow_single_chip or any(
            n in persistent and abs(n//10-i//10)+abs(n%10-i%10) == 1
            for n in (i-1, i+1, i-10, i+10))}
        if supported and tile not in active:
            path = folder / f"{stamp}-T{tile}.png"
            indices = sorted(supported)
            evidence(path, refs, maps, boxes[indices], group, stamp, reference_stamp)
            active.add(tile)
            events.append({"tile": tile, "event": "opened", "image": str(path),
                           "chips": sorted(chips[indices, 1].astype(int).tolist())})
        elif tile in active and consecutive and not (hits | previous) & region:
            active.remove(tile)
            events.append({"tile": tile, "event": "resolved"})
    save_json(statefile, dict(scoring="chip-v1", geometry=args.geometry, reference=args.reference, stamp=stamp, titles=titles, hits=sorted(hits), active=sorted(active)))
    return dict(status="evaluated", stamp=stamp, active=sorted(active), events=events)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("baseline", "check", "watch"))
    p.add_argument("--reference", choices=("wiki", "captured"),
                   help="default: wiki for monitoring, captured for baseline")
    p.add_argument("--work", type=Path, help="default: runs/wiki or runs/default for captured")
    p.add_argument("--source", default=PORTAL, help="RealTime URL or local directory")
    p.add_argument("--groups", type=int, nargs="+", choices=range(1, 9), default=list(range(1, 9)))
    p.add_argument("--interval", type=float, default=20)
    p.add_argument("--timezone", default="America/Chicago")
    p.add_argument("--max-age", type=int, default=120, help="seconds; 0 disables for offline replay")
    p.add_argument("--max-gap", type=int, default=120)
    p.add_argument("--ratio", type=float, default=.35)
    p.add_argument("--secondary-ratio", type=float, default=None,
                   help="maximum retained coverage in the corroborating panel (default: at least 0.60)")
    p.add_argument("--allow-single-chip", action="store_true",
                   help="open on one persistent chip without a neighboring chip")
    p.add_argument("--min-coverage", type=float, default=.10)
    p.add_argument("--geometry", choices=("CRS_DAQ", "pacmon"), default="CRS_DAQ")
    args = p.parse_args()
    if args.reference is None:
        args.reference = "captured" if args.action == "baseline" else "wiki"
    if args.action == "baseline" and args.reference == "wiki":
        p.error("wiki references are bundled; run check or watch")
    if args.work is None:
        args.work = Path("runs/wiki" if args.reference == "wiki" else "runs/default")
    with np.load(Path(__file__).with_name(f"chips_{args.geometry}.npz")) as layout:
        args.chips = {group: layout[str(group)] for group in args.groups}
    if args.secondary_ratio is None:
        args.secondary_ratio = max(.60, args.ratio)
    if args.interval <= 0 or args.max_gap <= 0 or args.max_age < 0 or not (
            0 < args.ratio <= args.secondary_ratio < 1 and 0 < args.min_coverage <= 1):
        p.error("invalid time interval or coverage threshold")
    while True:
        failed = False
        for group in args.groups:
            folder = args.work / f"iog_{group}"
            acquisition = {}
            try:
                folder.mkdir(parents=True, exist_ok=True)
                stamp, maps = acquire_with_retry(args, group, acquisition)
                if args.action == "baseline":
                    baseline(folder, stamp, maps)
                    result = {"status": "baseline_saved", "stamp": stamp}
                else:
                    result = evaluate(folder, group, stamp, maps, args)
            except (OSError, ValueError, SyntaxError, EOFError, IncompleteRead) as exc:
                result = {"status": "unavailable", "reason": str(exc)}
                failed = True
            print(json.dumps({"io_group": group, "reference": args.reference, **acquisition, **result}), flush=True)
        if args.action != "watch":
            return int(failed)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
