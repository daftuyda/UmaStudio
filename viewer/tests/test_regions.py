"""Run with: python -m unittest discover -s viewer/tests -v"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gamepaths
import serve


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.installs = {}
        self.data = {}
        for region, name in (("global", "UmamusumePrettyDerby_Data"), ("jp", "UmamusumePrettyDerby_Jpn_Data")):
            game = self.root / region
            data = game / name / "Persistent"
            data.mkdir(parents=True)
            (data / "meta").write_bytes(b"test manifest")
            self.installs[region] = str(game)
            self.data[region] = str(data)
        for name, value in (("CONFIG", str(self.root / "config")),
                            ("SETTINGS", str(self.root / "config" / "game.json")),
                            ("LOCALLOW", str(self.root / "LocalLow" / "Cygames" / "Umamusume"))):
            self.enterContext(patch.object(gamepaths, name, value))
        self.enterContext(patch.object(gamepaths, "steam_game", side_effect=lambda r=None: self.installs[r or gamepaths.region()]))
        gamepaths._cache["at"] = 0
        gamepaths._settings_cache["at"] = 0

    def test_detects_both_regions_without_crossing_sources(self):
        self.assertEqual(gamepaths.paths("global"), (self.installs["global"], self.data["global"]))
        self.assertEqual(gamepaths.paths("jp"), (self.installs["jp"], self.data["jp"]))
        self.assertEqual(gamepaths.paths()[0], self.installs["global"])
        self.assertTrue(all(p["found"] for p in gamepaths.describe()["profiles"].values()))

    def test_install_unity_and_persistent_paths(self):
        for region in self.installs:
            for path in (self.installs[region], str(Path(self.data[region]).parent), self.data[region]):
                self.assertEqual(gamepaths.split(path, region), (self.installs[region], self.data[region]))
        self.assertEqual(gamepaths.split("", "global"), (None, None))
        self.assertEqual(gamepaths.split(self.data["jp"], "global"), (None, None))

    def test_remembers_independent_overrides_and_cache_keys(self):
        gamepaths.configure("global", self.installs["global"], self.data["global"])
        first = gamepaths.cache_key()
        gamepaths.configure("jp", self.installs["jp"], self.data["jp"])
        second = gamepaths.cache_key()
        self.assertNotEqual(first, second)
        self.assertEqual(gamepaths.settings()["profiles"]["global"]["data"], self.data["global"])
        self.assertEqual(gamepaths.region(), "jp")
        self.assertTrue(gamepaths.config_cache().endswith(second))
        # Automatic detection is independently reset for the selected profile.
        gamepaths.configure("global")
        self.assertEqual(first, gamepaths.cache_key())
        self.assertEqual(gamepaths.settings()["profiles"]["global"], {})
        self.assertEqual(gamepaths.settings()["profiles"]["jp"]["game"], self.installs["jp"])

    def test_unicode_and_standalone_data_folder(self):
        data = self.root / "ゲーム データ"
        data.mkdir()
        (data / "meta").write_bytes(b"test")
        gamepaths.configure("jp", self.installs["jp"], str(data))
        self.assertEqual(gamepaths.data(), str(data))
        self.assertEqual(gamepaths.game(), self.installs["jp"])
        self.assertIn("ゲーム", Path(gamepaths.SETTINGS).read_text(encoding="utf-8"))

    def test_migrates_single_path_setting(self):
        Path(gamepaths.CONFIG).mkdir()
        Path(gamepaths.SETTINGS).write_text(json.dumps({"path": self.data["global"]}), encoding="utf-8")
        self.assertEqual(gamepaths.paths()[1], self.data["global"])
        gamepaths.configure("jp")
        self.assertEqual(gamepaths.settings()["profiles"]["global"]["path"], self.data["global"])

    def test_invalid_selection_preserves_settings(self):
        gamepaths.configure("global")
        before = Path(gamepaths.SETTINGS).read_bytes()
        for kwargs in ({"selected": "other"}, {"selected": "jp", "game": self.installs["global"]},
                       {"selected": "jp", "data": str(self.root / "missing")}):
            with self.assertRaises(ValueError):
                gamepaths.configure(**kwargs)
            self.assertEqual(Path(gamepaths.SETTINGS).read_bytes(), before)

    def test_legacy_jp_unity_folder(self):
        install = self.root / "dmm"
        (install / "umamusume_Data").mkdir(parents=True)
        self.assertEqual(gamepaths.split(str(install), "jp")[0], str(install))
        self.assertEqual(gamepaths.unity_data_dir(str(install)), str(install / "umamusume_Data"))

    def test_missing_jp_does_not_use_global_locallow_data(self):
        Path(gamepaths.LOCALLOW).mkdir(parents=True)
        Path(gamepaths.LOCALLOW, "meta").write_bytes(b"global")
        with patch.object(gamepaths, "steam_game", return_value=None):
            _, data = gamepaths.paths("jp")
        self.assertNotEqual(data, gamepaths.LOCALLOW)
        self.assertFalse(gamepaths.has_data(data))

    def test_switch_resets_runtime_and_uses_separate_directories(self):
        self.enterContext(patch.object(serve, "ROOT", str(self.root / "output")))
        for name in ("PACKS", "BUNDLES", "CACHE", "LIBRARY_DIR", "ICONS", "PROPS", "PROP_THUMBS", "BGS",
                     "DATA", "_generation"):
            self.enterContext(patch.object(serve, name, getattr(serve, name)))
        self.enterContext(patch.object(serve, "_booted"))
        self.enterContext(patch.object(serve, "_warm", {}))
        self.enterContext(patch.object(serve, "_boot", {"done": True}))
        self.enterContext(patch.object(serve, "_sets", {"old": ["motion"]}))
        self.enterContext(patch.object(serve, "_springs", {"old": object()}))
        self.enterContext(patch.object(serve, "_charas", {"stamp": "old", "list": [1]}))
        self.enterContext(patch.object(serve, "_paths", {"key": "old", "map": {1: "bone"}}))
        thread = self.enterContext(patch.object(serve.threading, "Thread"))
        serve.source_directories()
        old_cache = serve.CACHE
        result = serve.change_source({"region": "jp"})
        self.assertTrue(result["ok"])
        self.assertNotEqual(old_cache, serve.CACHE)
        self.assertIn("jp-", serve.CACHE)
        self.assertEqual(serve._sets, {})
        self.assertEqual(serve._springs, {})
        self.assertIsNone(serve._charas["list"])
        self.assertFalse(serve._boot["done"])
        thread.return_value.start.assert_called_once()

    def test_switch_during_export_is_rejected(self):
        self.enterContext(patch.object(serve, "_boot", {"done": True}))
        self.enterContext(patch.object(serve, "_videos", {"active": {}}))
        with self.assertRaisesRegex(ValueError, "video export"):
            serve.change_source({"region": "jp"})
        self.assertFalse(Path(gamepaths.SETTINGS).exists())

    def test_previous_warmup_cannot_mark_new_source_finished(self):
        self.enterContext(patch.object(serve, "_generation", 2))
        state = {"finished": False}
        self.enterContext(patch.object(serve, "_warm", state))
        serve.warm(1)
        self.assertFalse(state["finished"])

    def test_old_browser_cannot_write_assets_to_new_region(self):
        gamepaths.configure("global")
        handler = serve.Handler.__new__(serve.Handler)
        handler.headers = {"X-Game-Source": gamepaths.cache_key(), "Host": "localhost", "X-Studio": "1"}
        handler.path = "/api/prop/thumb"
        gamepaths.configure("jp")
        self.assertFalse(handler._source_ok())
        with patch.object(handler, "_send") as send:
            handler._post()  # rejected before it can read or write a thumbnail
        self.assertEqual(send.call_args.args[0], 409)

    def test_switch_during_loading_is_rejected_without_saving(self):
        self.enterContext(patch.object(serve, "_boot", {"done": False}))
        with self.assertRaisesRegex(ValueError, "still loading"):
            serve.change_source({"region": "jp"})
        self.assertFalse(Path(gamepaths.SETTINGS).exists())


if __name__ == "__main__":
    unittest.main()
