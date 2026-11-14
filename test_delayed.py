import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

from delayed import score
from monitor import evaluate


class DelayedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        with np.load(Path(__file__).with_name("chips_CRS_DAQ.npz")) as data:
            self.chips = data["2"]
        self.args = SimpleNamespace(reference="delayed", geometry="CRS_DAQ", max_gap=120,
                                    ratio=.35, secondary_ratio=.60, min_coverage=.10,
                                    reconfig_ratio=.75, exclusions={}, allow_single_chip=False,
                                    chips={2: self.chips})

    def test_delayed_strip_replay(self):
        for step in range(15):
            source = "wiki_reference" if step < 12 else "tests/fixtures"
            maps = {}
            for metric in ("mean", "rms"):
                with Image.open(Path(__file__).parent/source/f"iog_2_{metric}.png") as src:
                    im = src.convert("RGB")
                ImageDraw.Draw(im).text((1, 1), str(step), fill="black")
                maps[metric] = im
            result = evaluate(self.path, 2, 10000+30*step, maps, self.args)
            if step < 12:
                self.assertEqual(result["active"], [])
            if step == 13:
                self.assertEqual(result["events"][0]["chips"], [13, 14, 15, 16])
                self.assertTrue(Path(result["events"][0]["image"]).exists())
        self.assertEqual(result["active"], [8])

    def test_reconfiguration_and_broad_loss(self):
        healthy = np.full((2, 800), .8)
        mask = np.ones(800, dtype=bool)
        state = dict(last_good=[.8]*9, active=[3], history=[[ts,healthy.tolist(),[.8]*9] for ts in (600,630,660,910,940,970)])
        def frame(values, stamp):
            nonlocal state
            result, extra, hits = score(values, mask, self.chips, stamp, state, self.args)
            state = dict(extra, active=[3], stamp=stamp, hits=sorted(hits or []))
            json.dumps(state)
            return result
        low = frame(healthy*.4, 1000)
        self.assertEqual(low["status"], "likely_reconfiguration")
        self.assertEqual(low["active"], [3])
        self.assertEqual(len(state["history"]), 6)
        self.assertEqual(frame(healthy, 1030)["status"], "likely_reconfiguration")
        self.assertEqual(frame(healthy, 1060)["status"], "reconfiguration_cleared")
        tile = healthy.copy()
        tile[:, :100] *= .25
        result = frame(tile, 1090)
        self.assertEqual([b["tile"] for b in result["broad_tile_losses"]], [1])
        self.assertFalse(result["candidates"])
        self.assertEqual(state["frozen"]["0"], [.8,.8])

    def test_broad_episode_and_review(self):
        from review import page
        from datetime import datetime
        from zoneinfo import ZoneInfo
        folder = self.path / "iog_2"
        folder.mkdir()
        log = self.path / "watch-test.log"
        tile = self.chips[self.chips[:, 0] == 8, 2:]
        from monitor import plot_box
        for step in range(18):
            maps = {}
            for metric in ("mean", "rms"):
                with Image.open(Path(__file__).parent/"wiki_reference"/f"iog_2_{metric}.png") as src:
                    im = src.convert("RGB")
                pen = ImageDraw.Draw(im)
                if 12 <= step <= 15:
                    x,y,X,Y = plot_box(im)
                    pen.rectangle((x+tile[:,0].min()*(X-x), y+tile[:,1].min()*(Y-y),
                                   x+tile[:,2].max()*(X-x), y+tile[:,3].max()*(Y-y)), fill="white")
                pen.text((1,1),str(step),fill="black")
                maps[metric] = im
            result = evaluate(folder, 2, 10000+step*30, maps, self.args)
            with log.open("a") as f:
                print(json.dumps(dict(result, io_group=2)),file=f)
            if step == 13:
                self.assertEqual(result["events"][0]["kind"], "broad")
                self.assertEqual(result["active"], [8])
            if step == 17:
                self.assertEqual(result["events"][0]["event"], "resolved")
        self.assertEqual(len(list(folder.glob("*.png"))), 1)
        end = datetime.fromtimestamp(11000, ZoneInfo("America/Chicago")).isoformat()[:16]
        html = page(self.path, {"group":["2"],"tile":["8"],"end":[end]})
        for phrase in ("broad opened", "broad resolved", "T8.png", "broad_pending"):
            self.assertIn(phrase, html)
