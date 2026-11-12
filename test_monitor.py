"""Run an offline incident through the same CLI as the live service."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import monitor


class IncidentTest(unittest.TestCase):
    def test_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def frame(minute, missing=False):
                for metric in monitor.METRICS:
                    im = Image.new("RGB", (240, 490), "white")
                    draw = ImageDraw.Draw(im)
                    draw.text((90, 2), str(minute), fill="black")
                    draw.rectangle(monitor.plot_box(im), fill="#b44225")
                    if missing:
                        draw.rectangle((140, 45, 225, 100), fill="white")
                    im.save(root / f"iog_3_{metric}.png")
                (root / "iog_3_last_update.txt").write_text(f"2026-11-11\n15:{minute:02d}:00")
            def run(action):
                argv = ["monitor", action, "--reference", "captured", "--source", temp, "--work", str(root/"work"),
                        "--groups", "3", "--max-age", "0"]
                with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as out:
                    monitor.main()
                return json.loads(out.getvalue())
            frame(0)
            self.assertEqual(run("baseline")["status"], "baseline_saved")
            frame(1, True)
            self.assertEqual(run("check")["active"], [])
            self.assertEqual(run("check")["status"], "waiting_for_new_frame")
            frame(2, True)
            png = root/"iog_3_mean.png"
            png.write_bytes(png.read_bytes()[:-80])
            self.assertEqual(run("check")["status"], "unavailable")
            frame(2, True)
            event = run("check")["events"][0]
            self.assertEqual((event["event"], event["tile"]), ("opened", 2))
            self.assertTrue(Path(event["image"]).is_file())
            frame(3)
            self.assertEqual(run("check")["active"], [2])
            frame(4)
            self.assertEqual(run("check")["events"], [{"tile": 2, "event": "resolved"}])

    def test_wiki_examples(self):
        """Replay all healthy groups and the printed outage through the default CLI."""
        project = Path(monitor.__file__).parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def sweep(minute, outage=False):
                for group in range(1, 9):
                    (root / f"iog_{group}_last_update.txt").write_text(
                        f"2026-11-11\n15:{minute:02d}:00")
                    for metric in monitor.METRICS:
                        name = f"iog_{group}_{metric}.png"
                        src = "tests/fixtures" if outage and group == 1 else "wiki_reference"
                        with Image.open(project / src / name) as original:
                            im = original.resize((336, 688), Image.Resampling.NEAREST)
                        # The wiki has one outage frame. Simulate advancing titles.
                        ImageDraw.Draw(im).text((2, 2), str(minute), fill="black")
                        im.save(root / name)
                argv = ["monitor", "check", "--work", str(root/"work"), "--source", tmp,
                        "--max-age", "0"]
                with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(monitor.main(), 0)
                return [json.loads(line) for line in out.getvalue().splitlines()]

            for minute in (0, 1):
                self.assertTrue(all(not r["active"] for r in sweep(minute)))
            self.assertEqual(sweep(2, outage=True)[0]["active"], [])
            results = sweep(3, outage=True)
            self.assertEqual([r["active"] for r in results], [[6]] + [[] for _ in range(7)])
            self.assertTrue(Path(results[0]["events"][0]["image"]).is_file())
