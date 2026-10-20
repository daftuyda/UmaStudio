import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from animation_imports import ImportManager, find_unity, qmul, relative_motion, validate_bones


def skeleton():
    names = ["Position", "Hip", "Waist", "Spine", "Chest", "Neck", "Head"]
    names += [f"{n}_{side}" for side in ("L", "R") for n in ("Arm", "Elbow", "Wrist", "Thigh", "Knee", "Ankle")]
    return [dict(name=name, index=i, parent=i-1, p=[0, 1, 0], q=[0, 0, 0, 1], s=[1, 1, 1]) for i, name in enumerate(names)]


class AnimationImportTests(unittest.TestCase):
    def test_rejects_invalid_hierarchy_and_transforms(self):
        self.assertEqual(validate_bones(skeleton()), skeleton())
        for edit in [
            lambda b: b[0].update(parent=4),
            lambda b: b[1].update(name="Position"),
            lambda b: b[1].update(q=[0, 0, 0, 0]),
            lambda b: b[1].update(p=[float("nan"), 0, 0]),
            lambda b: b[1].update(name="MissingHip"),
        ]:
            bones = skeleton()
            edit(bones)
            with self.assertRaises(ValueError):
                validate_bones(bones)

    def test_relative_tracks_preserve_motion_and_quaternion_signs(self):
        bones = skeleton()
        # Unity and Three.js have opposite X handedness.
        bones[1]["p"] = [2, 1, 3]
        bones[1]["q"] = [0, .6, 0, .8]
        result = dict(name="Dance", fps=60, frames=2, duration=1/60, loop=True, tracks=[
            dict(bone="Hip", type="position", values=[-2, 1, 3, -1, 1.2, 3]),
            dict(bone="Hip", type="quaternion", values=[0, -.6, 0, .8, 0, .6, 0, -.8]),
        ])
        motion = relative_motion(result, bones)
        self.assertEqual(motion["tracks"][0]["values"], [0, 0, 0, 1, .2, 0])
        self.assertEqual(motion["tracks"][1]["values"], [0, 0, 0, 1])
        self.assertTrue(motion["loop"])
        result["tracks"][0]["values"][0] = float("inf")
        with self.assertRaises(ValueError):
            relative_motion(result, bones)
        with self.assertRaisesRegex(ValueError, "broken"):
            relative_motion(dict(error="broken"), bones)

    def test_explicit_editor_path_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "Unity.exe"
            exe.touch()
            self.assertEqual(find_unity(str(exe)), exe.resolve())
            with self.assertRaises(ValueError):
                find_unity(str(Path(temp) / "missing.exe"))

    def test_cached_import_needs_no_editor_and_rejects_invalid_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            script = folder / "script.cs"
            script.write_text("fixture")
            manager = ImportManager(folder, script)
            body = dict(filename="test.anim", data=base64.b64encode(b"animation").decode(), bones=skeleton())
            import hashlib
            from animation_imports import IMPORT_VERSION
            key = hashlib.sha256(b"animation" + json.dumps(body["bones"], sort_keys=True).encode() +
                                 script.read_bytes() + str(IMPORT_VERSION).encode()).hexdigest()
            (folder / "cache").mkdir()
            (folder / "cache" / (key + ".json")).write_text('{"name":"cached"}')
            with patch("animation_imports.find_unity", side_effect=AssertionError("must not need Unity")):
                job = manager.start(body)
            self.assertEqual(manager.status(job["id"])["motion"]["name"], "cached")
            for changes in [dict(filename="test.exe"), dict(data="%%%"), dict(bones=[])]:
                with self.assertRaises(ValueError):
                    manager.start({**body, **changes})

    def test_failed_converter_does_not_cache_partial_motion(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            script = folder / "script.cs"
            script.write_text("fixture")
            manager = ImportManager(folder, script)
            job = dict(id="test", state="running")
            with patch("animation_imports.subprocess.Popen") as popen:
                popen.return_value.wait.return_value = 1
                manager._run(job, b"bad", skeleton(), folder / "2022.3/Editor/Unity.exe", folder / "cache/failed.json")
            self.assertEqual(job["state"], "failed")
            self.assertIn("Import log", job["error"])
            self.assertFalse((folder / "cache/failed.json").exists())
