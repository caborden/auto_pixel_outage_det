"""Regenerate tile layout and reference graphic from pacmon geometry."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def tile_rows(layout):
    groups = {}
    for module in range(4):
        path = layout / f"geometry_mod{module}_v4.json"
        positions = defaultdict(list)
        for key, xy in json.loads(path.read_text())["geometry"].items():
            face, tile, _, _ = map(int, key.split("-"))
            positions[face, tile].append(xy)
        for face in (1, 2):
            centers = [(np.mean(points, axis=0), (tile - 1) % 8 + 1)
                       for (f, tile), points in positions.items() if f == face]
            centers.sort(key=lambda item: -item[0][1])
            assert len(centers) == 8
            groups[str(2 * module + face)] = [
                [t for _, t in sorted(centers[i:i+2], key=lambda item: item[0][0])]
                for i in range(0, 8, 2)]
    return groups


def draw_sheet(groups, name):
    sheet = Image.new("RGB", (1000, 870), "#f5f7f9")
    pen = ImageDraw.Draw(sheet)
    def label(x, y, value, size, color="#142c40", anchor="la"):
        pen.text((x, y), value, fill=color, font=ImageFont.load_default(size=size), anchor=anchor)
    label(40, 24, f"2x2 pixel maps   /   {name} geometry", 38)
    label(40, 80, "As viewed in ADC mean, RMS and rate plots. Numbers match Grafana tile_id.", 22)
    for column in range(4):
        for side in range(2):
            group = 2 * column + side + 1
            x, y = 60 + 234 * column, 145 + 324 * side
            label(x, y, f"I/O group {group}", 26)
            label(x, y + 34, f"Module {column}", 17)
            for row, tiles in enumerate(groups[str(group)]):
                for col, tile in enumerate(tiles):
                    left, top = x + col * 64, y + 62 + row * 56
                    pen.rectangle((left, top, left + 62, top + 54), fill=("#205071" if side == 0 else "#17665c"))
                    label(left+31, top+27, str(tile), 32, "white", "mm")
    label(40, 804, "Default v4 geometry   /   +X right, +Y up   /   Even groups use geometry ID = tile_id + 8.", 20)
    sheet.save(f"tile_guide_{name}.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    parser.add_argument("--name", required=True, choices=["CRS_DAQ", "pacmon"])
    args = parser.parse_args()
    groups = tile_rows(args.layout)
    Path(f"tiles_{args.name}.json").write_text(json.dumps(groups, indent=2) + "\n")
    draw_sheet(groups, args.name)
