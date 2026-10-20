from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import facial
import gamepaths
import motionnames
import textlabels


class EnglishNamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for region, rows in (
            ("global", [(6, 1001, "Special Week"), (14, 100101, "Special Dreamer")]),
            ("jp", [(6, 1001, "スペシャルウィーク"), (6, 1115, "オルフェーヴル"),
                    (372, 1115, "Orfevre"), (14, 100101, "スペシャルドリーマー"), (14, 111501, "総攬")])):
            folder = self.root / region / "master"
            folder.mkdir(parents=True)
            with closing(sqlite3.connect(folder / "master.mdb")) as db:
                db.execute("CREATE TABLE text_data(category INTEGER, [index] INTEGER, text TEXT)")
                db.executemany("INSERT INTO text_data VALUES(?,?,?)", rows)
                db.commit()
        self.enterContext(patch.object(gamepaths, "region", return_value="jp"))
        self.enterContext(patch.object(gamepaths, "paths", side_effect=lambda r=None: (None, str(self.root / (r or gamepaths.region())))))
        self.enterContext(patch.object(gamepaths, "master", side_effect=lambda: str(self.root / gamepaths.region() / "master/master.mdb")))
        textlabels.global_labels.cache_clear()

    def test_jp_names_use_global_labels_and_jp_only_latin_names(self):
        with closing(sqlite3.connect(gamepaths.master())) as db:
            self.assertEqual(textlabels.names(db, 6), {1001: "Special Week", 1115: "Orfevre"})
            self.assertEqual(textlabels.names(db, 14), {100101: "Special Dreamer", 111501: "Outfit 111501"})
            self.assertEqual(db.execute("SELECT text FROM text_data WHERE category=6 AND [index]=1115").fetchone()[0], "オルフェーヴル")

    def test_jp_names_work_without_global_install(self):
        with patch.object(gamepaths, "paths", return_value=(None, str(self.root / "missing"))):
            with closing(sqlite3.connect(gamepaths.master())) as db:
                self.assertEqual(textlabels.names(db, 6)[1115], "Orfevre")

    def test_region_switch_does_not_keep_old_names(self):
        self.assertEqual(motionnames.game_names()["chara"][1115], "Orfevre")
        with patch.object(gamepaths, "region", return_value="global"):
            self.assertNotIn(1115, motionnames.game_names()["chara"])
        self.assertEqual(motionnames.game_names()["chara"][1115], "Orfevre")


class SequenceGroupingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(motionnames, "game_names", return_value={
            "chara": {1001: "Special Week"}, "card": {}, "song": {}, "training": {}}))

    def test_cut_in_shots_are_numeric_and_not_input_order(self):
        result = motionnames.group([f"anm_cti_crd100101_001_{i:02}" for i in (10, 2, 1)])[0]
        self.assertEqual([p[0] for p in result["parts"]], ["Shot 1", "Shot 2", "Shot 10"])
        self.assertEqual(result["sequence"], [0, 1, 2])

    def test_start_loop_end_excludes_stills_and_reactions(self):
        result = motionnames.group(["anm_eve_type00_wave_" + p for p in ("e", "pose", "act01", "loop", "s")])[0]
        self.assertEqual([result["parts"][i][0] for i in result["sequence"]], ["Start", "Loop", "End"])

    def test_training_outcomes_are_not_sequential_shots(self):
        result = motionnames.group(["anm_tra_type00_100001_01_" + p for p in ("suc01", "bad01", "cmn01")])[0]
        self.assertEqual(result["sequence"], [])


class FacialWeightsTests(unittest.TestCase):
    def test_locator_weights_preserve_timing_and_mirror_sides(self):
        clip = {"fps": 30, "frames": 2, "duration": 1 / 30, "tracks": [
            {"bone": "Eye_L__CloseA", "type": "position", "values": [0, 0, 0, .01, 0, 0]},
            {"bone": "Mouth__WaraiA", "type": "position", "values": [.005, 0, 0]},
            {"bone": "Cheek_Ctrl", "type": "scale", "values": [0, 0, 0]}]}
        result = facial.weights(clip, mirrored=True)
        self.assertEqual(result["tracks"], [
            {"channel": "eye_r", "shape": "CloseA", "values": [0, 1]},
            {"channel": "mouth", "shape": "WaraiA", "values": [.5]}])
        self.assertEqual(result["fps"], 30)


if __name__ == "__main__":
    unittest.main()
