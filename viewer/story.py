"""Story scenes as the game stores them: the timeline (what each character does, block by
block) and the voice lip-sync, read straight from the game files for the viewer.

Timeline frames are 30 per second. A block's clips count from the block's start.
"""
import struct

import UnityPy

FPS = 30
# per-character tracks the viewer plays, by the names the timeline uses
TRACKS = {"motion": "StoryTimelineCharaMotionTrackData", "add": "StoryTimelineCharaAddMotionTrackData",
          "eyebrow": "StoryTimelineCharaFaceEyebrowTrackData", "eye": "StoryTimelineCharaFaceEyeTrackData",
          "mouth": "StoryTimelineCharaFaceMouthTrackData", "ear": "StoryTimelineCharaFaceEarTrackData",
          "moveeye": "StoryTimelineCharaMoveEyeTrackData", "lips": "StoryTimelineCharaMouthTrackData",
          "turn": "StoryTimelineCharaTurnTrackData", "prop": "StoryTimelineCharaPropTrackData",
          "offset": "StoryTimelineOffsetTransformTrackData", "spring": "StoryTimelineCySpringResetTrackData"}
SKIP = {"m_Name", "m_GameObject", "m_Enabled", "m_Script", "DataVer", "Lerp", "IsEnableClip",
        "MixMotionWeightCurve", "TurnAnimationCurve"}


def paths(story_id):
    sid = str(story_id)
    base = f"story/data/{sid[:2]}/{sid[2:6]}/"
    return base + f"storytimeline_{sid}", f"lipsync/story/{sid[:2]}/{sid[2:6]}/story_{sid}"


def _clean(v):
    if isinstance(v, dict):
        if "m_Curve" in v:
            return [[k["time"], k["value"], k.get("inSlope", 0), k.get("outSlope", 0)] for k in v["m_Curve"]]
        return {k: _clean(x) for k, x in v.items() if k not in ("m_GameObject", "m_Enabled", "m_Script")}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def timeline(data):
    """Blocks with their start frame, text (voice cue), camera and character tracks."""
    objs = {}
    root = None
    for o in UnityPy.load(data).objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            d = o.read_typetree()
        except Exception:
            continue
        objs[o.path_id] = d
        if d.get("m_Name", "").startswith("storytimeline_"):
            root = d
    if root is None:
        raise ValueError("not a story timeline")

    def clips(track):
        out = []
        for ref in (track or {}).get("ClipList", []):
            c = objs.get(ref["m_PathID"])
            if c is not None:
                c = {k: _clean(v) for k, v in c.items() if k not in SKIP}
                if "FaceInfoList1" in c:
                    c["FaceInfoList1"] = {f["Name"]: f["Weight"] for f in c["FaceInfoList1"]}
                    c["FaceInfoList2"] = {f["Name"]: f["Weight"] for f in c.get("FaceInfoList2", [])}
                out.append(c)
        return out

    blocks, start = [], 0
    for b in root["BlockList"]:
        charas = []
        for ct in b["CharacterTrackList"]:
            entry = {"chara": ct.get("CharaId"), "dress": ct.get("DressId"), "position": clips(ct)}
            entry.update({k: clips(ct.get(t)) for k, t in TRACKS.items()})
            charas.append(entry)
        blocks.append({"index": b["BlockIndex"], "start": start, "length": b["BlockLength"],
                       "text": clips(b.get("TextTrack")), "camera": clips(b.get("CameraTrack")),
                       "bg": clips(b.get("BgTrack")), "charas": charas})
        start += b["BlockLength"]
    return {"id": root.get("StoryId"), "title": root.get("Title"), "fps": FPS, "length": start, "blocks": blocks}


def lipsync(data):
    """Per voice cue: rows of [ms, a, i, u, e, o, dB] (CRI LipSync analysis, 60 per second)."""
    raw = None
    for o in UnityPy.load(data).objects:
        if o.type.name == "TextAsset":
            s = o.read().m_Script
            raw = s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s)
    if not raw or raw[:4] != b"LSP ":
        raise ValueError("not lip-sync data")
    cues = {}
    off = 0x10
    first = struct.unpack_from("<I", raw, 0x14)[0]
    while off < first:
        idx, at, size, _ = struct.unpack_from("<4I", raw, off)
        off += 16
        if not size or raw[at:at + 4] != b"LSD ":
            break
        count = struct.unpack_from("<H", raw, at + 12)[0]
        cues[idx] = [list(struct.unpack_from("<I6f", raw, at + 32 + 32 * k)) for k in range(count)]
    return cues
