"""Solver selection must preserve JP character data and work across region switches."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cyspring
import gamepaths


class PhysicsLibraryTests(unittest.TestCase):
    def setUp(self):
        cyspring.reset_plugin()
        self.addCleanup(cyspring.reset_plugin)
        self.enterContext(patch.object(cyspring, "find_game", return_value="jp-game"))
        self.enterContext(patch.object(cyspring, "plugin_path", side_effect=lambda game: game + "/spring.dll"))
        self.enterContext(patch.object(gamepaths, "region", return_value="jp"))
        self.enterContext(patch.object(gamepaths, "paths", return_value=("global-game", "global-data")))
        self.enterContext(patch.object(cyspring.os.path, "isfile", return_value=True))

    def test_protected_jp_uses_global_solver_and_caches_it(self):
        solver = Mock()
        with patch.object(cyspring.ctypes, "CDLL", side_effect=[OSError(1114, "protected"), solver]) as load:
            self.assertIs(cyspring.plugin(), solver)
            self.assertIs(cyspring.plugin(), solver)
            self.assertEqual(load.call_args_list, [call("jp-game/spring.dll"), call("global-game/spring.dll")])
            self.assertEqual(len(solver.NativeClothUpdate.argtypes), 17)

    def test_selected_library_is_preferred(self):
        with patch.object(cyspring.ctypes, "CDLL", return_value=Mock()) as load:
            cyspring.plugin()
            load.assert_called_once_with("jp-game/spring.dll")

    def test_missing_export_tries_compatible_library(self):
        solver = Mock()
        with patch.object(cyspring.ctypes, "CDLL", side_effect=[object(), solver]):
            self.assertIs(cyspring.plugin(), solver)

    def test_unavailable_solver_explains_global_install_requirement(self):
        with patch.object(cyspring.ctypes, "CDLL", side_effect=OSError("unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Global game folder"):
                cyspring.plugin()
            self.assertIsNone(cyspring._dll)

    def test_global_never_falls_back_to_jp_and_reset_reloads(self):
        with patch.object(gamepaths, "region", return_value="global"), \
             patch.object(cyspring, "find_game", return_value="global-game"), \
             patch.object(cyspring.ctypes, "CDLL", return_value=Mock()) as load:
            cyspring.plugin()
            cyspring.reset_plugin()
            cyspring.plugin()
            self.assertEqual(load.call_args_list, [call("global-game/spring.dll")] * 2)
            cyspring.reset_plugin()
            load.side_effect = OSError("unavailable")
            with self.assertRaisesRegex(RuntimeError, "install folder"):
                cyspring.plugin()
            self.assertEqual(load.call_count, 3)


if __name__ == "__main__":
    unittest.main()
