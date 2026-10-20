"""Explicit prop associations from game motion names, master data and cut-in timelines.

Unknown, detached and world-space arrangements are left for a user-saved setup;
we do not substitute arbitrary props based on animation titles.
"""
import re
import sqlite3

import UnityPy
import gamepaths
import umaconv

PROP = re.compile(r"pfb_chr_prop(\d{4})_(\d{2})$")
NODES = {"Hand_Attach_R": "right", "Hand_Attach_L": "left", "Chest": "chest", "Head": "head"}


def index(data):
    key = (data._profile, data._stamp)
    cached = getattr(data, "_prop_links", None)
    if cached and cached["key"] == key:
        return cached
    models = {p.rsplit("/", 1)[-1]: p for p in data.prefix("3d/chara/prop/") if PROP.fullmatch(p.rsplit("/", 1)[-1])}
    motions = {p.rsplit("/", 1)[-1]: p for p in data.prefix("3d/motion/") if "/prop/" in p}
    states = {}
    try:
        with sqlite3.connect(f"file:{gamepaths.master()}?mode=ro", uri=True) as db:
            for pid, state in db.execute("SELECT prop_id, use_state_name FROM character_prop_animation WHERE scene_type=1"):
                if state.startswith(("type", "chr")):
                    states.setdefault(("anm_eve_" + state).lower(), set()).add(f"pfb_chr_prop{pid // 100:04d}_{pid % 100:02d}")
    except sqlite3.Error:
        pass  # Older masters need not have the table.
    cached = {"key": key, "models": models, "motions": motions, "states": states, "timelines": {}}
    data._prop_links = cached
    return cached


def timeline_setups(tree):
    """Map body clip names to attached-prop events in that clip's local seconds."""
    result = {}
    characters = tree.get("_characterList", [])
    if len(characters) != 1:
        return result
    shots = characters[0].get("_characterMotionKeys", {}).get("thisList", [])
    for shot in shots:
        leaf = shot.get("_bodyClipName", "").replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf:
            continue
        start = shot.get("_frame", 0)
        end = min((s["_frame"] for s in shots if s.get("_frame", 0) > start), default=float("inf"))
        # Complex retiming cannot be mapped safely to the raw clip player.
        if shot.get("_motionHeadTime", 0) or shot.get("_playSpeed", 1) != 1:
            continue
        setups = []
        for prop in tree.get("CharaPropList", []):
            name = prop.get("_name", "")
            if not PROP.fullmatch(name):
                continue
            events = []
            state = {"visible": False, "node": None, "pos": [0, 0, 0], "quat": [0, 0, 0, 1], "scale": [1, 1, 1]}
            keys = sorted(prop.get("_keys", {}).get("thisList", []), key=lambda k: k.get("_frame", 0))
            for key in keys:
                frame = key.get("_frame", 0)
                if frame >= end:
                    break
                attach = key.get("AttachData", {})
                node = attach.get("AttachLocator") or key.get("AttachLocator")
                if attach.get("IsAttach") or key.get("IsAttach"):
                    state["node"] = node if node in NODES else None
                if attach.get("IsDetach") or key.get("IsDetach") or key.get("IsUseWorldSpace"):
                    state["node"] = None
                state["visible"] = bool(key.get("IsVisible")) and state["node"] is not None
                for field, flag, default in (("_pos", "IsUsePosition", 0), ("_rot", "IsUseRotation", 0), ("_scale", "IsUseScale", 1)):
                    if key.get(flag):
                        values = [key.get(field, {}).get(axis, default) for axis in "xyz"]
                        if field == "_pos":
                            state["pos"] = [-values[0], values[1], values[2]]
                        elif field == "_scale":
                            state["scale"] = values
                        else:
                            x, y, z, w = umaconv.euler_to_quat(*values)
                            state["quat"] = [x, -y, -z, w]
                event = {"time": max(0, (frame - start) / 60), **state}
                if frame <= start:
                    events = [event]
                else:
                    events.append(event)
            if any(e["visible"] for e in events):
                if not events or events[0]["time"] > 0:
                    events.insert(0, {"time": 0, "visible": False})
                node = next(e["node"] for e in events if e["visible"])
                setups.append({"name": name, "anchor": NODES[node], "node": node, "grip": False, "events": events})
        result[leaf] = setups
    return result


def companion(data, body):
    cached = index(data)
    leaf = body.replace("\\", "/").rsplit("/", 1)[-1].lower()
    mirror = leaf.endswith("_mirror")
    stem = leaf.removesuffix("_mirror")
    setups = []
    match = re.fullmatch(r"anm_eve_type\d+_prop(\d{4})_(\d{2})_([rl])", stem)
    if match:
        pid, variant, hand = match.groups()
        setups = [{"name": f"pfb_chr_prop{pid}_{variant}", "anchor": "right" if hand == "r" else "left", "grip": False}]
    elif len(cached["states"].get(stem, ())) == 1:
        setups = [{"name": next(iter(cached["states"][stem])), "anchor": "right", "grip": False}]
    cutin = re.fullmatch(r"anm_cti_(crd\d+_\d+)_\d+", stem)
    if cutin:
        tag = cutin[1]
        path = f"cutt/cutin/skill/{tag}/{tag}"
        if path not in cached["timelines"]:
            entries = {}
            if path in data.entries and data.entries[path].local:
                for obj in UnityPy.load(data.read(path)).objects:
                    if obj.type.name == "MonoBehaviour":
                        tree = obj.read_typetree()
                        if "CharaPropList" in tree:
                            entries.update(timeline_setups(tree))
            cached["timelines"][path] = entries
        setups = cached["timelines"][path].get(stem, [])
    result = []
    for raw in setups:
        p = {**raw}
        path = cached["models"].get(p["name"])
        if not path:
            continue
        p["available"] = data.entries[path].local
        # A paired prop clip animates the prop's own rig. Never put it on the character mixer.
        pid = p["name"].removeprefix("pfb_chr_")
        motion = next((cached["motions"][n] for n in (stem + "_" + pid, stem + "_prop") if n in cached["motions"]), None)
        if motion and data.entries[motion].local:
            p["motionPath"] = motion
        if mirror:
            p["anchor"] = {"right": "left", "left": "right"}.get(p["anchor"], p["anchor"])
            if p.get("events") or motion:
                # Mirrored cut-in rigs need more than swapping hands.
                continue
        result.append(p)
    return result
