"""Asset-format and region-isolation regressions; no installed game is required."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gamepaths
import offline
import umadata
import umaconv


class AssetTests(unittest.TestCase):
    def test_two_influence_meshes_pad_skin_slots_without_changing_weights(self):
        joints, weights = umaconv.skin_attributes([[1, 2], [3, 0]], [[.2, .8], [1, 0]], 2, 4)
        np.testing.assert_array_equal(joints, [[1, 2, 0, 0], [3, 0, 0, 0]])
        np.testing.assert_allclose(weights, [[.2, .8, 0, 0], [1, 0, 0, 0]])

    def test_legacy_plain_manifest_without_install_or_key_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "日本語" / "meta"
            path.parent.mkdir()
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE a (n TEXT,h TEXT,d TEXT,m TEXT,l INTEGER)")
            db.execute("INSERT INTO a VALUES ('3d/日本語','abcdef',NULL,'chara',4096)")
            db.commit()
            db.close()
            with patch.object(gamepaths, "meta", return_value=str(path)), \
                    patch.object(offline, "project_prefs", side_effect=AssertionError("No install needed")):
                self.assertEqual(offline.manifest_rows(None),
                                 [("3d/日本語", "abcdef", 0, "", "chara", 4096)])

    def test_plain_bundle_passes_through_unchanged(self):
        raw = b"UnityFS\0" + bytes(range(256)) * 3
        self.assertEqual(umadata.decrypt(np.frombuffer(raw, dtype=np.uint8).copy(), b"", 123), raw)
        encrypted = umadata.decrypt(np.frombuffer(raw, dtype=np.uint8).copy(), b"sample", 0)
        self.assertNotEqual(encrypted, raw)
        self.assertEqual(umadata.decrypt(np.frombuffer(encrypted, dtype=np.uint8).copy(), b"sample", 0), raw)
        with patch.object(offline, "bundle_key_candidates", side_effect=AssertionError("No metadata needed")):
            self.assertEqual(offline.bundle_key(None, lambda key: key == b""), b"")

    def test_missing_metadata_recovers_and_validates_sampled_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A synthetic zero-heavy serialized file exercises the 88-byte stream
            # alignment beyond the unencrypted 256-byte header.
            base = b"test-key-11"
            plain = b"UnityFS\0" + bytes(256 - 8) + bytes(8192)
            key = 112233445566
            encrypted = umadata.decrypt(np.frombuffer(plain, dtype=np.uint8).copy(), base, key)
            path = Path(tmp) / "aa"
            path.mkdir()
            (path / "aabbcc").write_bytes(encrypted)
            e = umadata.Entry("3d/test", "aabbcc", key, [], "chara", len(encrypted))
            with patch.object(gamepaths, "dat", return_value=tmp), \
                    patch.object(offline, "bundle_key_candidates", side_effect=FileNotFoundError):
                found = offline.bundle_key(None, lambda candidate: candidate == base, {e.name: e})
                self.assertEqual(found, base)
                self.assertEqual(umadata.decrypt(np.frombuffer(encrypted, dtype=np.uint8).copy(), found, key),
                                 plain)
                # A candidate which cannot open a bundle must never be accepted.
                with self.assertRaisesRegex(RuntimeError, "Download character assets"):
                    offline.bundle_key(None, lambda candidate: False, {e.name: e})

    def test_sampling_is_bounded(self):
        class Entry:
            cat, key, size, local = "chara", 17, 4096, True
            def __init__(self, path):
                self.file = path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle"
            path.write_bytes(b"UnityFS\0" + bytes(4088))
            # A late missing file would fail if the sample bound were ignored.
            entries = {str(i): Entry(path) for i in range(3)}
            entries["late"] = Entry(Path(tmp) / "does-not-exist")
            list(offline.sampled_bundle_keys(entries, limit=3))

    def test_jp_never_uses_global_plugin_exports(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(umadata, "CONFIG", tmp), \
                patch.object(gamepaths, "config_cache", return_value=os.path.join(tmp, "jp")), \
                patch.object(gamepaths, "region", return_value="jp"):
            Path(tmp, "manifest.tsv").write_text("v1\n3d/wrong\taabb\t0\t\tchara\t10\n", encoding="utf-8")
            Path(tmp, "keys.json").write_text(json.dumps({"base_keys": ""}), encoding="utf-8")
            data = umadata.GameData()
            self.assertFalse(data._from_plugin())
            self.assertFalse(data.entries)

    def test_source_change_discards_loaded_characters_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(gamepaths, "cache_key", return_value="jp-new"), \
                patch.object(gamepaths, "meta", return_value=os.path.join(tmp, "missing")), \
                patch.object(gamepaths, "config_cache", return_value=tmp), \
                patch.object(gamepaths, "region", return_value="jp"):
            data = umadata.GameData()
            data._profile = "global-old"
            data.entries = {"old": object()}
            data.names = ["old"]
            data._charas = {"old": object()}
            self.assertFalse(data.ready())
            self.assertEqual(data.entries, {})
            self.assertIsNone(data._charas)

    def test_protected_jp_error_explains_compatible_reader(self):
        with patch.object(gamepaths, "region", return_value="jp"), \
                patch.object(gamepaths, "paths", return_value=(None, "")), \
                patch.object(gamepaths, "unity_data_dir", side_effect=RuntimeError("stale")):
            with self.assertRaisesRegex(RuntimeError, "installed Global client"):
                offline._manifest_library("missing-jp")


if __name__ == "__main__":
    unittest.main()
