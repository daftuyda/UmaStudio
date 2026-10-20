"""The face-weight animation paired with a body motion.

The game's DrivenKeyLocator supplies the CRC paths. Its shape controls move
along negative Unity X by 0.01 for weight 1; umaconv mirrors that X coordinate.
These weights drive each loaded character's own facial targets.
Convention cross-checked against UmaViewer's FaceDrivenKeyTarget.ProcessLocator:
https://github.com/katboi01/UmaViewer/blob/master/Assets/Scripts/umamusume/Gallop/FaceDrivenKeyTarget.cs
"""
import re
import zlib

import UnityPy
import umaconv

SHAPE = re.compile(r"^(Eye|Eyebrow)_([LR])__(.+)$|^(Mouth)__(.+)$")


def locator_paths(data):
    transforms, names = {}, {}
    for obj in UnityPy.load(data.read("3d/animator/drivenkeylocator")).objects:
        if obj.type.name == "GameObject":
            names[obj.path_id] = obj.read().m_Name
        elif obj.type.name == "Transform":
            transforms[obj.path_id] = obj.read_typetree()

    def path(idx):
        t = transforms[idx]
        name = names[t["m_GameObject"]["m_PathID"]]
        parent = t["m_Father"]["m_PathID"]
        return path(parent) + "/" + name if parent in transforms else name

    out = {}
    for idx in transforms:
        full = path(idx)
        for relative in (full, full.split("/", 1)[-1]):
            out[zlib.crc32(relative.encode("utf-8"))] = full.rsplit("/", 1)[-1]
    return out


def weights(clip, mirrored=False):
    tracks = []
    controls = {t["bone"]: t for t in clip["tracks"] if t["type"] == "position"}
    for track in clip["tracks"]:
        match = SHAPE.fullmatch(track["bone"])
        if not match or track["type"] != "position":
            continue
        part, side, shape, mouth, mouth_shape = match.groups()
        if mouth:
            channel, shape = "mouth", mouth_shape
            if mirrored and shape.endswith(("_L", "_R")):
                shape = shape[:-1] + ("R" if shape[-1] == "L" else "L")
        else:
            if shape in ("XRange", "YRange"):
                continue  # Eye direction is driven by Eyeball_*_Ctrl, below.
            if mirrored:
                side = "R" if side == "L" else "L"
            channel = part.lower() + "_" + side.lower()
        tracks.append({"channel": channel, "shape": shape,
                       "values": [round(v * 100, 5) for v in track["values"][::3]]})
    def component(name, frame, axis):
        values = controls.get(name, {}).get("values", [0, 0, 0])
        return values[min(frame, len(values) // 3 - 1) * 3 + axis]
    if any(name.startswith("Eyeball_") for name in controls):
        for side in ("L", "R"):
            channel_side = ("R" if side == "L" else "L") if mirrored else side
            for axis, shape in ((0, "XRange"), (1, "YRange")):
                sign = (-1 if axis == 0 or side == "R" else 1) * (-1 if mirrored and axis == 0 else 1)
                values = [round(sign * 100 * (component(f"Eyeball_{side}_Ctrl", i, axis) +
                                             component("Eyeball_all_Ctrl", i, axis)), 5)
                          for i in range(clip["frames"])]
                tracks.append({"channel": "eye_" + channel_side.lower(), "shape": shape, "values": values})
    return {k: clip[k] for k in ("fps", "frames", "duration")} | {"tracks": tracks}


def companion(data, body):
    key = (data._profile, data._stamp)
    cache = getattr(data, "_facial_cache", None)
    if cache is None or cache["key"] != key:
        cache = {"key": key, "paths": None, "clips": {}, "files": {}}
        for path in data.prefix("3d/motion/"):
            if "/facial/" in path:
                cache["files"][path.rsplit("/", 1)[-1]] = path
        data._facial_cache = cache
    leaf = body.rsplit("/", 1)[-1]
    if leaf in cache["clips"]:
        return cache["clips"][leaf]
    mirrored = leaf.endswith("_mirror")
    stem = leaf.removesuffix("_mirror")
    def local(name):
        path = cache["files"].get(name)
        return path if path and data.entries[path].local else None
    path = local(stem + "_face_mirror") if mirrored else None
    mirror_weights = mirrored and path is None
    path = path or local(stem + "_face")
    result = None
    if path and data.entries[path].local:
        if cache["paths"] is None:
            cache["paths"] = locator_paths(data)
        for obj in UnityPy.load(data.read(path)).objects:
            if obj.type.name == "AnimationClip":
                clip = umaconv.convert_clip(obj, cache["paths"])
                if clip["unmapped"]:
                    raise ValueError(f"Unmapped facial controls in {leaf}")
                result = weights(clip, mirror_weights)
                break
    cache["clips"][leaf] = result
    return result
