"""Reads the game's installed data directly, with the game closed.

Needs the asset manifest (asset path -> file hash, per-file key, dependencies) and the
bundle base key. Both are read from the installed game (offline.py); failing that, from
the files the UmaStudio plugin writes on a game launch to %LOCALAPPDATA%\\UmaStudio
(manifest.tsv, keys.json). Bundles in LocalLow/.../dat are XOR-encrypted after a plain
256-byte header with an 88-byte stream built from the base key and the file's own key.
"""
import json
import os
import re
import sqlite3
import struct
import threading

import numpy as np

LOCALLOW = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Cygames", "Umamusume")
DAT = os.path.join(LOCALLOW, "dat")
MASTER = os.path.join(LOCALLOW, "master", "master.mdb")
CONFIG = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UmaStudio")
HEADER = 256

# shared outfits (uniforms, tracksuits...) keep one texture per skin tone, bust size and sock
# type, the game fills the material at runtime. slot -> texture suffix.
SLOTS = {"_MainTex": "diff", "_ToonMap": "shad_c", "_TripleMaskMap": "base", "_OptionMaskMap": "ctrl"}


PARTS = ("body", "zekken")


def generic_texture_names(body_type, body_sub, skin, bust, socks, kind, part="body"):
    """Candidate texture names for one slot, best first (the game's naming). Tracksuits
    have two extra parts: the number bib (zekken, left blank) and its frame (waku)."""
    t = f"tex_bdy{body_type:04d}_{body_sub:02d}"
    if part == "zekken":
        return [f"{t}_zekken0_{bust}_{kind}", f"{t}_zekken0_0_{kind}"]
    if part == "waku":
        return [f"{t}_00_waku0_{kind}"]
    t += "_00"
    return [f"{t}_{skin}_{bust}_{socks:02d}_{kind}", f"{t}_{skin}_{bust}_{kind}", f"{t}_{skin}_{bust}_00_{kind}",
            f"{t}_0_{bust}_{socks:02d}_{kind}", f"{t}_0_{bust}_00_{kind}", f"{t}_0_{bust}_{kind}",
            f"{t}_0_0_00_{kind}", f"{t}_0_0_{kind}"]


def material_part(material_name):
    """Shared-outfit materials are numbered; on tracksuits _2 is the number bib."""
    return "zekken" if material_name.endswith("_2") else "body"


class Entry:
    __slots__ = ("name", "hash", "key", "deps", "cat", "size")

    def __init__(self, name, h, key, deps, cat, size):
        self.name, self.hash, self.key, self.deps, self.cat, self.size = name, h, key, deps, cat, size

    @property
    def file(self):
        return os.path.join(DAT, self.hash[:2], self.hash)

    @property
    def local(self):
        return os.path.exists(self.file)


def decrypt(data, base, key):
    """A dat/ file's bytes (uint8 array, changed in place) -> the plain bundle."""
    if len(data) > HEADER:
        kb = struct.pack("<q", key)
        stream = np.frombuffer(bytes(b ^ kb[j] for b in base for j in range(8)), dtype=np.uint8)
        data[HEADER:] ^= stream[np.arange(HEADER, len(data)) % len(stream)]
    return data.tobytes()


def read_manifest(path):
    entries = {}
    with open(path, encoding="utf-8") as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 6:
                entries[p[0]] = Entry(p[0], p[1], int(p[2]), [d for d in p[3].split(";") if d], p[4], int(p[5]))
    return entries


class GameData:
    GAME_CACHE = os.path.join(CONFIG, "manifest-game.tsv")   # rows read from the game's meta

    def __init__(self, progress=None):
        self._lock = threading.Lock()
        self._stamp = None
        self._failed = None   # the game files' stamp when reading them last failed
        self.entries = {}
        self.names = []
        self.base = b""
        self.source = None    # "game" or "plugin"
        self.error = None     # why the game's own files could not be read, if they could not
        self.progress = progress or (lambda stage, k: None)   # (what, 0..1) while loading

    # --- loading ------------------------------------------------------------------

    def ready(self):
        """True when the manifest is loaded (reloaded when its source changes): from the
        installed game, or else from the UmaStudio plugin's files."""
        with self._lock:
            try:
                if self._from_game():
                    return True
            except Exception as e:
                self.error = str(e)
            return self._from_plugin()

    def _from_game(self):
        import offline
        if not os.path.exists(offline.META):
            return False
        st = os.stat(offline.META)
        stamp = ("game", st.st_size, st.st_mtime_ns)
        if stamp == self._stamp:
            return True
        if stamp == self._failed:   # unchanged since it failed: don't redo the slow read
            raise RuntimeError(self.error)
        try:
            self._load_game(offline, st, stamp)
        except Exception:
            self._failed = stamp
            raise
        return True

    def _load_game(self, offline, st, stamp):
        stamp_file = self.GAME_CACHE + ".stamp"
        key = f"{st.st_size} {st.st_mtime_ns}"
        game = offline.game_folder()
        if not (os.path.exists(self.GAME_CACHE) and os.path.exists(stamp_file) and open(stamp_file).read() == key):
            os.makedirs(CONFIG, exist_ok=True)
            tmp = self.GAME_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write("v1\n")
                rows = offline.manifest_rows(game, lambda k: self.progress("Reading the game's file list", 0.7 * k))
                for row in rows:
                    f.write("\t".join(str(x) for x in row) + "\n")
            os.replace(tmp, self.GAME_CACHE)
            with open(stamp_file, "w") as f:
                f.write(key)
        self.progress("Reading the game's file list", 0.7)
        entries = read_manifest(self.GAME_CACHE)
        self.progress("Finding the bundle key", 0.85)
        base = offline.bundle_key(game, lambda cand: self._opens(cand, entries))
        self.progress("Almost ready", 0.95)
        self.entries, self.names, self.base = entries, sorted(entries), base
        self._stamp, self.source, self.error = stamp, "game", None

    @staticmethod
    def _opens(base, entries):
        """True when `base` decrypts a small downloaded bundle into one UnityPy can read."""
        import UnityPy
        tried = 0
        for e in entries.values():
            if not e.cat.startswith("manifest") and e.size and e.size < 200_000 and e.key and e.local:
                try:
                    env = UnityPy.load(decrypt(np.fromfile(e.file, dtype=np.uint8), base, e.key))
                    return bool(env.objects)
                except Exception:
                    tried += 1
                    if tried >= 3:
                        return False
        return False

    def _from_plugin(self):
        mf = os.path.join(CONFIG, "manifest.tsv")
        kf = os.path.join(CONFIG, "keys.json")
        if not (os.path.exists(mf) and os.path.exists(kf)):
            return False
        stamp = ("plugin", os.path.getmtime(mf))
        if stamp != self._stamp:
            self.progress("Reading the plugin's file list", 0.5)
            self.base = bytes.fromhex(json.load(open(kf, encoding="utf-8"))["base_keys"])
            self.entries = read_manifest(mf)
            self.names = sorted(self.entries)
            self._stamp, self.source = stamp, "plugin"
        return True

    # --- bundles --------------------------------------------------------------------

    def read(self, name):
        e = self.entries[name]
        return decrypt(np.fromfile(e.file, dtype=np.uint8), self.base, e.key)

    def export(self, name, root, deps=True, seen=None):
        """Decrypt a bundle (and its dependencies) to root/<path>.unity3d. Returns the
        files, skipping ones that are already current."""
        seen = set() if seen is None else seen
        out, queue = [], [name]
        while queue:
            n = queue.pop()
            if n in seen:
                continue
            seen.add(n)
            e = self.entries.get(n)
            if e is None:
                raise KeyError(f"not in the manifest: {n}")
            if not e.local:
                raise FileNotFoundError(f"not downloaded by the game yet: {n}")
            dst = os.path.join(root, n.replace("/", os.sep) + ".unity3d")
            stamp = dst + ".src"
            if not (os.path.exists(dst) and os.path.exists(stamp) and open(stamp).read() == e.hash):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(self.read(n))
                with open(stamp, "w") as f:
                    f.write(e.hash)
            out.append(dst)
            if deps:
                queue.extend(e.deps)
        return out

    def prefix(self, prefix):
        """All names starting with prefix (the list is sorted)."""
        import bisect
        i = bisect.bisect_left(self.names, prefix)
        out = []
        while i < len(self.names) and self.names[i].startswith(prefix):
            out.append(self.names[i])
            i += 1
        return out

    # --- characters --------------------------------------------------------------------

    def charas(self):
        db = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
        try:
            names = dict(db.execute("SELECT [index], text FROM text_data WHERE category = 6"))
            dress_names = dict(db.execute("SELECT [index], text FROM text_data WHERE category = 14"))
            charas = [{"id": r[0], "height": r[1], "shape": r[2], "bust": r[3], "tail": r[4], "skin": r[5], "socks": r[6],
                       "name": names.get(r[0], f"Chara {r[0]}")}
                      for r in db.execute("SELECT id, height, shape, bust, tail_model_id, skin, socks FROM chara_data ORDER BY id")]
            dresses = [{"id": r[0], "chara": r[1], "body_type": r[2], "body_sub": r[3], "head_sub": r[4], "tail": r[5],
                        "name": dress_names.get(r[0], f"Outfit {r[0]}")}
                       for r in db.execute("SELECT id, chara_id, body_type, body_type_sub, head_sub_id, tail_model_id "
                                           "FROM dress_data ORDER BY id")]
        finally:
            db.close()
        out = []
        for c in charas:
            if not self.prefix(f"3d/chara/head/chr{c['id']:04d}_"):
                continue
            ds = [{"id": d["id"], "name": d["name"], "own": d["chara"] == c["id"]}
                  for d in dresses if d["chara"] in (0, c["id"]) and self.body_paths(c, d)]
            if ds:
                out.append({**c, "dresses": ds})
        mobs = self.mobs(dresses)
        out += mobs
        self._charas = {c["id"]: c for c in charas + mobs}
        self._dresses = {d["id"]: d for d in dresses}
        return out

    def mobs(self, dresses):
        """Background Umas: the generic head's face and hair parts, recoloured per mob, on the
        shared outfits. Height and shape are fixed for them, as in the game."""
        db = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            names = dict(db.execute("SELECT [index], text FROM text_data WHERE category = 59"))
            hair = {r["id"]: dict(r) for r in db.execute("SELECT * FROM mob_hair_color_set")}
            dress = {r["id"]: dict(r) for r in db.execute("SELECT * FROM mob_dress_color_set")}
            rows = [dict(r) for r in db.execute("SELECT * FROM mob_data ORDER BY mob_id")]
        finally:
            db.close()
        head = "3d/chara/head/chr0001_00/pfb_chr0001_00_"
        shared = [d for d in dresses if not d["chara"]]
        out = []
        for r in rows:
            face, hairm = f"face{r['chara_face_model']:03d}", f"hair{r['chara_hair_model']:03d}"
            if not all(head + part in self.entries and self.entries[head + part].local for part in (face, hairm)):
                continue
            c = {"id": r["mob_id"], "height": 1, "shape": 0, "bust": r["chara_bust_size"], "tail": 1,
                 "skin": r["chara_skin_color"], "socks": r["socks"], "name": names.get(r["mob_id"], f"Mob {r['mob_id']}"),
                 "mob": {"face": r["chara_face_model"], "hair": r["chara_hair_model"], "dress": r["dress_id"],
                         "hair_cutoff": r["hair_cutoff"], "sex": r["sex"],
                         "hair_colors": hair.get(r["chara_hair_color"], {}), "dress_colors": dress.get(r["dress_color_id"], {})}}
            ds = [{"id": d["id"], "name": d["name"], "own": d["id"] == r["dress_id"]}
                  for d in shared if self.body_paths(c, d)]
            ds.sort(key=lambda d: not d["own"])
            if ds:
                out.append({**c, "dresses": ds})
        return out

    def body_paths(self, c, d):
        if d["chara"]:
            folder = f"3d/chara/body/bdy{c['id']:04d}_{d['body_sub']:02d}/"
            return [p for p in self.prefix(folder + "pfb_")]
        # shared outfits: pfb_bdy<type>_<sub>_<variant>_<height>_<shape>_<bust>. the variant follows
        # the character's sock type when the outfit has one for it. loading more than one draws
        # doubled legs, ffs.
        folder = f"3d/chara/body/bdy{d['body_type']:04d}_{d['body_sub']:02d}/"
        variants = {}
        for p in self.prefix(folder + "pfb_"):
            m = re.search(r"pfb_bdy\d+_\d+_(\d+)_(\d)_(\d)_(\d)$", p)
            if m:
                variants.setdefault((int(m.group(2)), int(m.group(3)), int(m.group(4))), {})[m.group(1)] = p
        shape = (c["height"], c["shape"], c["bust"])
        by_variant = variants.get(shape) or next((v for k, v in sorted(variants.items()) if k[0] == c["height"]), None) \
            or (next(iter(variants.values())) if variants else None)
        if not by_variant:
            return []
        return [by_variant.get(f"{c.get('socks', 0):02d}") or by_variant[min(by_variant)]]

    def pack_paths(self, chara_id, dress_id, motions=True):
        if not getattr(self, "_charas", None):
            self.charas()
        c, d = self._charas[chara_id], self._dresses[dress_id]
        bodies = self.body_paths(c, d)
        if not bodies:
            raise ValueError("no body model for that outfit")
        body_dir = bodies[0].rsplit("/", 1)[0] + "/"
        paths = list(bodies)
        if d["chara"]:
            paths += self.prefix(body_dir + "textures/")
        else:
            # only this character's skin / bust / socks textures, not the whole set
            by_name = {p.rsplit("/", 1)[1]: p for p in self.prefix(body_dir + "textures/")}
            for part in PARTS:
                for kind in SLOTS.values():
                    for n in generic_texture_names(d["body_type"], d["body_sub"], c["skin"], c["bust"], c["socks"], kind, part):
                        if n in by_name:
                            paths.append(by_name[n])
                            break
        if c.get("mob"):
            # the outfit's recolour mask, for background Umas
            paths += [p for p in self.prefix(body_dir + "textures/") if p.endswith("_area")]
        # physics for the outfit (variant 00, as the game loads it): spring chains, the bust (per bust
        # size on shared bodies) and skirt-vs-leg data. named after the folder, not the prefab, which
        # carries height/shape/bust suffixes on shared bodies.
        folder = body_dir.rstrip("/").rsplit("/", 1)[1]
        bust = f"bust{c['bust']}" if not d["chara"] else "bust"
        paths += [p for p in self.prefix(body_dir + "clothes/")
                  if p.endswith((f"pfb_{folder}_cloth00", f"pfb_{folder}_{bust}_cloth00", f"ast_{folder}_skirt00"))]
        if c.get("mob"):
            # the generic head: just this mob's face and hair parts, their textures and hair physics
            head_dir = "3d/chara/head/chr0001_00/"
            parts = (f"face{c['mob']['face']:03d}", f"hair{c['mob']['hair']:03d}")
            paths += [p for p in self.prefix(head_dir) if any(f"_{x}" in p for x in parts) and "_wet" not in p
                      and ("/clothes/" not in p or p.endswith("_cloth00"))]
            tail_id = "0001"
        else:
            head_dir = f"3d/chara/head/chr{c['id']:04d}_{d['head_sub']:02d}/"
            if not self.prefix(head_dir + "pfb_"):
                head_dir = f"3d/chara/head/chr{c['id']:04d}_00/"
            paths += [p for p in self.prefix(head_dir) if "/clothes/" not in p or p.endswith("_cloth00")]
            tail_id = f"{c['id']:04d}"
        tail = d["tail"] if d["tail"] > 0 else c["tail"]
        if tail > 0:
            tail_dir = f"3d/chara/tail/tail{tail:04d}_00/"
            paths += self.prefix(tail_dir + "pfb_")
            paths += [p for p in self.prefix(tail_dir + "clothes/") if p.endswith("_cloth00")]
            paths += [p for p in self.prefix(tail_dir + "textures/") if f"_{tail_id}_" in p or "_0000_" in p]
        if motions:
            paths += [p for p in self.prefix(f"3d/motion/event/body/chara/chr{c['id']:04d}_00/") if not p.endswith("_mirror")]
        return c, d, [p for p in paths if p in self.entries and self.entries[p].local]

    def build_pack(self, chara_id, dress_id, packs_root, motions=True):
        c, d, paths = self.pack_paths(chara_id, dress_id, motions)
        safe = "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in f"{c['id']} {c['name']} - {d['name']}")
        folder = os.path.join(packs_root, safe)
        seen, keep = set(), set()
        for p in paths:
            keep.update(os.path.normcase(f) for f in self.export(p, folder, seen=seen))
        # drop anything an older build left behind (the converter loads every bundle in the folder)
        for dirpath, _, files in os.walk(folder):
            for f in files:
                full = os.path.join(dirpath, f)
                base = full[:-4] if f.endswith(".src") else full
                if base.endswith(".unity3d") and os.path.normcase(base) not in keep:
                    os.remove(full)
        # what the converter needs to texture a shared outfit the way the game would
        info = {"chara": c["id"], "skin": c["skin"], "bust": c["bust"], "socks": c["socks"],
                "body_type": d["body_type"], "body_sub": d["body_sub"], "shared": not d["chara"]}
        if c.get("mob"):
            info["mob"] = c["mob"]
        # only when it changed: a newer file in the pack makes the viewer convert it again
        path = os.path.join(folder, "pack.json")
        old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
        if old != info:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(info, f)
        return safe
