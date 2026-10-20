from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import animationcamera


class CameraCompanionTests(unittest.TestCase):
    def data(self, names):
        entries = {p: SimpleNamespace(local=True) for p in names}
        return SimpleNamespace(_profile="jp", _stamp=1, entries=entries, prefix=lambda _: names)

    def test_camera_and_character_placement_are_paired_and_cached(self):
        cam = "3d/motion/cutin/camera/shot_cam"
        pos = "3d/motion/cutin/position/shot_pos"
        data = self.data([cam, pos])
        clip = {"tracks": [{"type": t} for t in ("position", "quaternion", "scale")]}
        with patch.object(animationcamera, "convert", return_value=clip) as convert:
            result = animationcamera.companion(data, "3d/motion/cutin/body/shot")
            self.assertIs(result["camera"], clip)
            self.assertIs(result["position"], clip)
            self.assertIs(animationcamera.companion(data, "shot"), result)
            self.assertEqual(convert.call_count, 2)
            data._profile = "global"
            animationcamera.companion(data, "shot")
            self.assertEqual(convert.call_count, 4)

    def test_missing_camera_and_unsupported_rig_allow_manual_view(self):
        data = self.data(["3d/motion/camera/shot_cam"])
        self.assertIsNone(animationcamera.companion(data, "other"))
        with patch.object(animationcamera, "convert", return_value=None):
            self.assertIsNone(animationcamera.companion(data, "shot"))

    def test_mirrored_body_can_use_reflected_original_camera(self):
        data = self.data(["3d/motion/camera/shot_cam"])
        clip = {"tracks": [{"type": t} for t in ("position", "quaternion", "scale")]}
        with patch.object(animationcamera, "convert", return_value=clip):
            result = animationcamera.companion(data, "shot_mirror")
            self.assertTrue(result["mirrored"])
            self.assertIsNone(result["position"])
