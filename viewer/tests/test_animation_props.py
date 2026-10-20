import sys
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import animationprops


class AnimationPropsTests(unittest.TestCase):
    def test_companion_disk_cache_survives_memory_cache_and_invalidates_on_manifest(self):
        import serve
        import facial
        import animationcamera
        data = SimpleNamespace(_stamp=("manifest", 1))
        with tempfile.TemporaryDirectory() as root, patch.object(serve, "CACHE", root), \
                patch.object(serve, "DATA", data), patch.object(serve, "need_data"), \
                patch.object(facial, "companion", return_value=None) as face, \
                patch.object(animationcamera, "companion", return_value=None), \
                patch.object(animationprops, "companion", return_value=[]):
            first = serve.animation_companions("anm_test")
            self.assertEqual(serve.animation_companions("anm_test"), first)
            self.assertEqual(json.loads(gzip.decompress(first))["props"], [])
            self.assertEqual(face.call_count, 1)
            data._stamp = ("manifest", 2)
            serve.animation_companions("anm_test")
            self.assertEqual(face.call_count, 2)

    def test_timeline_visibility_is_relative_to_each_shot(self):
        tree = {
            "_characterList": [{"_characterMotionKeys": {"thisList": [
                {"_frame": 0, "_bodyClipName": "path/shot_01"},
                {"_frame": 60, "_bodyClipName": "path/shot_02"},
                {"_frame": 120, "_bodyClipName": "path/shot_03"},
            ]}}],
            "CharaPropList": [{"_name": "pfb_chr_prop1104_01", "_keys": {"thisList": [
                {"_frame": 0, "IsVisible": 0},
                {"_frame": 90, "IsAttach": 1, "AttachLocator": "Chest", "IsVisible": 1},
                {"_frame": 130, "IsVisible": 0},
            ]}}],
        }
        result = animationprops.timeline_setups(tree)
        self.assertEqual(result["shot_01"], [])
        events = result["shot_02"][0]["events"]
        self.assertFalse(events[0]["visible"])
        self.assertEqual(events[1]["time"], .5)
        self.assertEqual(events[1]["node"], "Chest")
        last = result["shot_03"][0]["events"]
        self.assertTrue(last[0]["visible"])
        self.assertAlmostEqual(last[1]["time"], 1 / 6)

    def test_unknown_and_detached_locations_are_not_guessed(self):
        tree = {"_characterList": [{"_characterMotionKeys": {"thisList": [{"_frame": 0, "_bodyClipName": "shot"}]}}],
                "CharaPropList": [{"_name": "pfb_chr_prop1104_01", "_keys": {"thisList": [
                    {"_frame": 0, "IsAttach": 1, "AttachLocator": "UnknownLocator", "IsVisible": 1},
                ]}}]}
        self.assertEqual(animationprops.timeline_setups(tree)["shot"], [])

    def test_explicit_mirror_and_missing_download(self):
        name = "pfb_chr_prop1003_00"
        cache = {"models": {name: "model"}, "motions": {}, "states": {}, "timelines": {}}
        data = SimpleNamespace(entries={"model": SimpleNamespace(local=False)})
        with patch.object(animationprops, "index", return_value=cache):
            result = animationprops.companion(data, "anm_eve_type00_prop1003_00_r_mirror")
        self.assertEqual(result[0]["anchor"], "left")
        self.assertFalse(result[0]["available"])

    def test_ambiguous_database_mapping_is_not_selected(self):
        cache = {"models": {}, "motions": {}, "states": {"anm_eve_type00_book_loop": {"one", "two"}}, "timelines": {}}
        with patch.object(animationprops, "index", return_value=cache):
            self.assertEqual(animationprops.companion(None, "anm_eve_type00_book_loop"), [])


if __name__ == "__main__":
    unittest.main()
