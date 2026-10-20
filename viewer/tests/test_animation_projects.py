import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from animation_projects import save_project


class AnimationProjectTests(unittest.TestCase):
    def test_roundtrip_and_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            project = dict(format="UmaStudio animation", version=1, name="Cafe wave", keys=[{"time": 0}])
            first = Path(save_project(temp, project))
            second = Path(save_project(temp, project))
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text(encoding="utf-8")), project)
            self.assertEqual(first.suffix, ".umaanim")

    def test_filename_stays_in_animation_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ["../../escape", r"..\..\escape", "CON", "NUL", "カフェ"]:
                path = Path(save_project(temp, dict(format="UmaStudio animation", version=1, name=name, keys=[{}])))
                self.assertEqual(path.resolve().parent, Path(temp).resolve())
                self.assertTrue(path.exists())

    def test_reject_invalid_or_excessive_content(self):
        with tempfile.TemporaryDirectory() as temp:
            project = dict(format="UmaStudio animation", version=1, name="Wave", keys=[{}])
            for changes in [dict(version=3), dict(keys=[]), dict(name=""), dict(extra=float("nan")),
                            dict(extra="x" * (16 * 1024 * 1024))]:
                with self.assertRaises(ValueError):
                    save_project(temp, {**project, **changes})
            self.assertEqual(list(Path(temp).iterdir()), [])
