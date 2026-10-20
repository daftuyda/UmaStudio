import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cyspring as c

ZERO = {"x": 0, "y": 0, "z": 0}


def collider(name, kind=c.SPHERE):
    return {"_collisionName": name, "_targetObjectName": "Chest", "_radius": .1,
            "_type": kind, "_offset": {"x": 1, "y": 2, "z": 3},
            "_offset2": {"x": 4, "y": 5, "z": 6}, "_isInner": False}


def spring(name, refs):
    return {"_boneName": name, "_collisionNameList": refs, "_childElements": [],
            "_stiffnessForce": .01, "_dragForce": .1, "_gravity": .01, "_collisionRadius": .01,
            "_limitAngleMin": ZERO, "_limitAngleMax": ZERO}


class CollisionTests(unittest.TestCase):
    def build(self, data, scale=(1, 1, 1)):
        nodes = [["Chest", -1, [0, 0, 0], [0, 0, 0, 1], scale],
                 ["Hair", 0, [0, 1, 0], [0, 0, 0, 1], [1, 1, 1]],
                 ["Tie", 0, [0, .5, 0], [0, 0, 0, 1], [1, 1, 1]],
                 ["Tail", 0, [0, -.5, 0], [0, 0, 0, 1], [1, 1, 1]]]
        with patch.object(c, "plugin", return_value=Mock()):
            return c.Simulation({"containers": data}, nodes)

    def test_shared_body_collider_remains_available_to_hair_body_and_tail(self):
        sim = self.build({
            "head": {"springParam": [spring("Hair", ["shared"])], "collisionParam": []},
            "body": {"springParam": [spring("Tie", ["shared"])], "collisionParam": [collider("shared")]},
            "tail": {"springParam": [spring("Tail", ["shared"])], "collisionParam": []},
        })
        for part in sim.parts:
            self.assertIn("shared", [col["name"] for col in part.collisions])
            self.assertEqual(struct.unpack_from("<i", part.roots[0].buf, c.ACTIVE_COL)[0], 1)

    def test_capsule_endpoints_both_follow_scale(self):
        sim = self.build({"head": {"springParam": [], "collisionParam": [collider("capsule", c.CAPSULE)]}}, (2, 3, 4))
        col = sim.parts[0].collisions[0]
        self.assertEqual(col["pos"], (2, 6, 12))
        self.assertEqual(col["pos2"], (8, 15, 24))

    def test_native_collision_count_never_exceeds_index_buffer(self):
        names = [str(n) for n in range(20)]
        sim = self.build({"head": {"springParam": [spring("Hair", names)], "collisionParam": [collider(n) for n in names]}})
        self.assertEqual(struct.unpack_from("<i", sim.parts[0].roots[0].buf, c.ACTIVE_COL)[0], 16)


if __name__ == "__main__":
    unittest.main()
