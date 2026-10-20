"""Camera and character-placement clips paired with a body animation.

Camera scale X stores vertical FOV, rather than a spatial scale. The camera
rig's forward convention is already -Z after umaconv's X reflection.
"""
import UnityPy
import umaconv


def convert(data, path):
    if not data.entries[path].local:
        return None
    for obj in UnityPy.load(data.read(path)).objects:
        if obj.type.name == "AnimationClip":
            clip = umaconv.convert_clip(obj, {0: "Root"})
            if clip["unmapped"]:
                return None  # A different rig needs its own mapping.
            return clip
    return None


def companion(data, body):
    key = (data._profile, data._stamp)
    cache = getattr(data, "_camera_cache", None)
    if cache is None or cache["key"] != key:
        files = {}
        for path in data.prefix("3d/motion/"):
            if "/camera/" in path or "/position/" in path:
                files[path.rsplit("/", 1)[-1]] = path
        cache = {"key": key, "files": files, "clips": {}}
        data._camera_cache = cache
    leaf = body.rsplit("/", 1)[-1]
    if leaf in cache["clips"]:
        return cache["clips"][leaf]
    mirror = leaf.endswith("_mirror")
    stem = leaf.removesuffix("_mirror")
    def local(name):
        path = cache["files"].get(name)
        return path if path and data.entries[path].local else None
    cam_path = local(stem + "_cam_mirror") if mirror else None
    reflected = mirror and not cam_path
    cam_path = cam_path or local(stem + "_cam")
    result = None
    if cam_path:
        camera = convert(data, cam_path)
        if camera and {"position", "quaternion", "scale"} <= {t["type"] for t in camera["tracks"]}:
            pos_path = local(stem + "_pos_mirror") if mirror else None
            pos_reflected = mirror and not pos_path
            pos_path = pos_path or local(stem + "_pos")
            result = {"camera": camera, "position": convert(data, pos_path) if pos_path else None,
                      "mirrored": reflected, "positionMirrored": pos_reflected}
    cache["clips"][leaf] = result
    return result
