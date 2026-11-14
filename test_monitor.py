"""Run an offline incident through the same CLI as the live service."""
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import monitor


class IncidentTest(unittest.TestCase):
    def test_changed_timestamp_refetches_both_maps(self):
        group = 8
        from argparse import Namespace
        args = Namespace(source="unused", max_age=0, timezone="America/Chicago")
        a, b = b"2026-11-11 15:00:00", b"2026-11-11 15:00:30"
        responses = [a, b"old rms", b"old mean", b, b, b"new rms", b"new mean", b]
        details = {}
        def fake_decode(data):
            return Image.new("RGB", (200, 400), "red" if data.startswith(b"new") else "blue")
        with patch.object(monitor, "read", side_effect=responses) as read, \
             patch.object(monitor, "decode", side_effect=fake_decode), \
             patch.object(monitor.time, "sleep") as sleep:
            _, maps = monitor.acquire_with_retry(args, group, details)
        names = [call.args[1] for call in read.call_args_list]
        self.assertEqual(names, ["iog_8_last_update.txt", "iog_8_rms.png",
                                  "iog_8_mean.png", "iog_8_last_update.txt"] * 2)
        self.assertEqual(details, {"attempts": 2, "retry_reason": "update changed during download"})
        self.assertTrue(all(im.getpixel((0, 0)) == (255, 0, 0) for im in maps.values()))
        sleep.assert_called_once_with(3)
