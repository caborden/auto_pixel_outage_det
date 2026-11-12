"""Build the monitor's portable chip footprints from the channel XY used by PlotMetrics."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def build(layout, output):
    arrays = {}
    for module in range(4):
        config = json.loads((layout / f"geometry_mod{module}_v4.json").read_text())
        pixels = defaultdict(list)
        for key, xy in config["geometry"].items():
            face, tile, chip, _ = map(int, key.split("-"))
            pixels[face, (tile-1) % 8 + 1, chip].append(xy)
        coords = np.array(list(config["geometry"].values()))
        half = config["pixel_pitch"] / 2
        xmin, ymin = coords.min(axis=0) - half
        xmax, ymax = coords.max(axis=0) + half
        for face in (1, 2):
            records = []
            for tile in range(1, 9):
                boxes = []
                for (f, t, chip), points in pixels.items():
                    if (f, t) == (face, tile):
                        x0, y0 = np.min(points, axis=0) - half
                        x1, y1 = np.max(points, axis=0) + half
                        boxes.append((tile, chip, (x0-xmin)/(xmax-xmin), (ymax-y1)/(ymax-ymin),
                                      (x1-xmin)/(xmax-xmin), (ymax-y0)/(ymax-ymin)))
                if len(boxes) != 100:
                    raise ValueError(f"expected 100 chips on module {module} face {face} tile {tile}")
                boxes.sort(key=lambda b: b[3])
                for row in range(0, 100, 10):
                    records.extend(sorted(boxes[row:row+10], key=lambda b: b[2]))
            arrays[str(2*module + face)] = np.asarray(records)
    np.savez_compressed(output, **arrays)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.layout, args.output)
