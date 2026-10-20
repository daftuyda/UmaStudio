"""Reads what the viewer needs straight from the installed game, so no game plugin is
needed:

    the asset manifest   the game's encrypted `meta` database, opened with the game's own
                         libnative.dll (SQLite3 Multiple Ciphers) and the cipher settings
                         the game ships in resources.assets (Cute.Core.ProjectPrefs)
    the bundle base key  a static array in global-metadata.dat, or a candidate recovered
                         from local bundles when metadata is protected, validated by
                         parsing decrypted Unity bundles

Every value comes from the user's own install at run time; none is stored in this
repository. Manifest rows are cached separately per region and data folder. Protected
JP native libraries can use the compatible reader from an installed Global client;
the manifest, preferences and assets always come from the selected region.

    python offline.py      self-test: prints counts only
"""
import bisect
import ctypes
import os
import sqlite3
import struct
from pathlib import Path

import gamepaths

def game_folder():
    game = gamepaths.game()
    if not game:
        raise RuntimeError("the game was not found in Steam's libraries, pick its folder by hand")
    return game


# --- the manifest ----------------------------------------------------------------------

def project_prefs(game):
    """The game's build settings (Cute.Core.ProjectPrefs, a ScriptableObject in
    resources.assets): an int count, then (string key, string value) pairs."""
    import UnityPy
    for o in UnityPy.load(os.path.join(gamepaths.unity_data_dir(game), "resources.assets")).objects:
        if o.type.name != "MonoBehaviour":
            continue
        raw = o.get_raw_data()
        if b"sqlite3mcKey" not in raw:
            continue
        for start in range(0, min(len(raw), 256), 4):   # the list follows the MonoBehaviour header
            try:
                prefs = _string_pairs(raw, start)
            except (struct.error, UnicodeDecodeError, ValueError):
                continue
            if "sqlite3mcKey" in prefs:
                return prefs
    raise RuntimeError("the game's database settings were not found in resources.assets")


def _string_pairs(raw, pos):
    count = struct.unpack_from("<i", raw, pos)[0]
    if not 0 < count < 1000:
        raise ValueError("not a list")
    pos += 4
    out = {}
    for _ in range(count):
        pair = []
        for _ in range(2):
            n = struct.unpack_from("<i", raw, pos)[0]
            if not 0 <= n <= len(raw) - pos - 4:
                raise ValueError("bad string")
            pair.append(raw[pos + 4:pos + 4 + n].decode("utf-8"))
            pos += 4 + n
            pos += -pos % 4
        out[pair[0]] = pair[1]
    return out


def manifest_rows(game, progress=None):
    """(name, hash, key, deps, category, size) for every asset the game knows.
    progress(0..1) is called as the rows come in."""
    path = gamepaths.meta()
    with open(path, "rb") as f:
        plain = f.read(16) == b"SQLite format 3\0"
    if plain:
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            columns = {r[1] for r in db.execute("PRAGMA table_info(a)")}
            # Older JP manifests have no per-file encryption key.
            key = "e" if "e" in columns else "0"
            rows = list(db.execute(f"SELECT n, h, {key}, d, m, l FROM a"))
            if progress:
                progress(1)
            return [(n, h, k or 0, d or "", m or "", size or 0) for n, h, k, d, m, size in rows]
        finally:
            db.close()
    game = game or game_folder()
    prefs = project_prefs(game)
    lib = _manifest_library(game)
    vp, cp, ci = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
    lib.sqlite3_open_v2.argtypes = [cp, ctypes.POINTER(vp), ci, cp]
    lib.sqlite3mc_config.argtypes = [vp, cp, ci]
    lib.sqlite3_key.argtypes = [vp, cp, ci]
    lib.sqlite3_prepare_v2.argtypes = [vp, cp, ci, ctypes.POINTER(vp), vp]
    lib.sqlite3_step.argtypes = [vp]
    lib.sqlite3_column_text.argtypes = [vp, ci]
    lib.sqlite3_column_text.restype = cp
    lib.sqlite3_column_int64.argtypes = [vp, ci]
    lib.sqlite3_column_int64.restype = ctypes.c_int64
    lib.sqlite3_finalize.argtypes = [vp]
    lib.sqlite3_close.argtypes = [vp]

    cipher = int(prefs.get("sqlite3mcCipherType") or 0)
    key = prefs["sqlite3mcKey"].encode("utf-8")
    errors = []
    # the cipher is set for this connection. older builds of the library only take the
    # "default:" form. the library exports no sqlite3_errmsg, so failures are SQLite codes
    # (26 = not a database: wrong key or cipher).
    for param in (b"cipher", b"default:cipher"):
        db, stmt = vp(), vp()
        rc_open = lib.sqlite3_open_v2(gamepaths.meta().encode("utf-8"), ctypes.byref(db), 1, None)   # SQLITE_OPEN_READONLY
        try:
            if rc_open:
                errors.append(f"open {rc_open}")
                continue
            rc_cipher = lib.sqlite3mc_config(db, param, cipher) if cipher else 0
            rc_key = lib.sqlite3_key(db, key, len(key))
            rc = lib.sqlite3_prepare_v2(db, b"SELECT count(*) FROM a", -1, ctypes.byref(stmt), None)
            if rc:
                errors.append(f"{param.decode()}: open {rc_open}, cipher {rc_cipher} (want {cipher}), "
                              f"key {rc_key}, query {rc}")
                continue
            total = lib.sqlite3_column_int64(stmt, 0) if lib.sqlite3_step(stmt) == 100 else 0
            lib.sqlite3_finalize(stmt)
            stmt = vp()
            rc = lib.sqlite3_prepare_v2(db, b"SELECT n, h, e, d, m, l FROM a", -1, ctypes.byref(stmt), None)
            if rc:
                raise RuntimeError(f"Could not read the game's manifest columns (SQLite {rc}).")
            rows = []
            text = lambda i: (lib.sqlite3_column_text(stmt, i) or b"").decode("utf-8")
            while (rc := lib.sqlite3_step(stmt)) == 100:   # SQLITE_ROW
                rows.append((text(0), text(1), lib.sqlite3_column_int64(stmt, 2), text(3), text(4),
                             lib.sqlite3_column_int64(stmt, 5)))
                if progress and total and len(rows) % 8192 == 0:
                    progress(len(rows) / total)
            if rc != 101:  # SQLITE_DONE
                raise RuntimeError(f"The game's manifest read stopped early (SQLite {rc}).")
            return rows
        finally:
            lib.sqlite3_finalize(stmt)
            lib.sqlite3_close(db)
    raise RuntimeError("could not open the game's asset manifest: " + "; ".join(errors))


def _manifest_library(game):
    """Use the selected game's reader. Protected JP builds may need the compatible
    library from an installed Global client; only its library is used, never its data."""
    candidates = [game]
    if gamepaths.region() == "jp":
        compatible = gamepaths.paths("global")[0]
        if compatible and compatible != game:
            candidates.append(compatible)
    for candidate in candidates:
        try:
            plugins = os.path.join(gamepaths.unity_data_dir(candidate), "Plugins", "x86_64")
            # Keep the directory handle alive until dependent libraries have loaded.
            with os.add_dll_directory(plugins):
                return ctypes.CDLL(os.path.join(plugins, "libnative.dll"))
        except (OSError, RuntimeError):
            continue
    raise RuntimeError(
        "The game's database reader could not be loaded. This JP build protects its native "
        "libraries; an installed Global client is needed as a compatible reader. "
        "Choose its install folder in the Global settings, then switch back to Japan (JP)."
        if gamepaths.region() == "jp" else
        "The game's database reader could not be loaded. Check the install folder in Settings.")


# --- the bundle key --------------------------------------------------------------------

# global-metadata.dat v31: header section indices and record sizes.
STRINGS, FIELD_DEFAULTS, DEFAULT_DATA, FIELDS, TYPES, IMAGES = 2, 7, 8, 11, 19, 20
TYPE_SIZE, FIELD_SIZE, IMAGE_SIZE = 88, 12, 40


def bundle_key_candidates(game):
    """The static arrays of Cute.AssetBundle.Assembly's <PrivateImplementationDetails>;
    one of them is the bundle base key."""
    path = os.path.join(gamepaths.unity_data_dir(game), "il2cpp_data", "Metadata", "global-metadata.dat")
    d = open(path, "rb").read()
    head = struct.unpack_from("<46i", d, 0)
    if head[1] != 31:
        raise RuntimeError(f"global-metadata.dat version {head[1]} is not supported")
    sec = lambda i: (head[2 + 2 * i], head[3 + 2 * i])

    def string(idx):
        o = sec(STRINGS)[0] + idx
        return d[o:d.index(b"\0", o)].decode("utf-8", "replace")

    o, n = sec(DEFAULT_DATA)
    defaults, starts = {}, [o + n]
    fo, fn = sec(FIELD_DEFAULTS)
    for i in range(fn // 12):
        field, _, data = struct.unpack_from("<iii", d, fo + i * 12)
        defaults[field] = o + data
        starts.append(o + data)
    starts.sort()

    io, isz = sec(IMAGES)
    for i in range(isz // IMAGE_SIZE):
        name_idx, _, type_start, type_count = struct.unpack_from("<IiiI", d, io + i * IMAGE_SIZE)
        if string(name_idx) != "Cute.AssetBundle.Assembly.dll":
            continue
        out = []
        for t in range(type_start, type_start + type_count):
            to = sec(TYPES)[0] + t * TYPE_SIZE
            if string(struct.unpack_from("<I", d, to)[0]) != "<PrivateImplementationDetails>":
                continue
            field_start = struct.unpack_from("<i", d, to + 32)[0]
            field_count = struct.unpack_from("<H", d, to + 68)[0]
            for f in range(field_start, field_start + field_count):
                if f in defaults:
                    s = defaults[f]
                    out.append(d[s:starts[bisect.bisect_right(starts, s)]])
        return out
    raise RuntimeError("Cute.AssetBundle.Assembly was not found in global-metadata.dat")


def bundle_key(game, opens, entries=None):
    """The candidate for which `opens(candidate)` is true (a test bundle decrypts)."""
    if opens(b""):  # Older JP clients store plain Unity bundles.
        return b""
    try:
        candidates = bundle_key_candidates(game)
    except (OSError, RuntimeError):
        candidates = []
    for cand in sorted(candidates, key=len):
        if cand and opens(cand):
            return cand
    # Current JP builds protect the metadata. Repeated zero bytes in downloaded
    # bundles reveal candidates for the eleven-byte XOR key. Accept one only after
    # the caller has successfully parsed independent decrypted Unity bundles.
    for cand in sampled_bundle_keys(entries or {}):
        if opens(cand):
            return cand
    raise RuntimeError("The downloaded bundles could not be opened. Download character assets "
                       "in the selected game first, then try again.")


def sampled_bundle_keys(entries, limit=32):
    """Bounded, read-only fallback for clients without readable IL2CPP metadata."""
    import numpy as np
    if limit <= 0:
        return
    counts = np.zeros((11, 256), dtype=np.int64)
    seen, sampled = set(), 0
    for e in entries.values():
        if e.cat.startswith("manifest") or not e.key or not 1024 <= e.size < 200_000 or not e.local:
            continue
        with open(e.file, "rb") as f:
            raw = f.read(200_000)
        if not raw.startswith(b"UnityFS\0"):
            continue
        data = np.frombuffer(raw, dtype=np.uint8)
        positions = np.arange(256, len(data))
        file_key = np.frombuffer(struct.pack("<q", e.key), dtype=np.uint8)
        decoded = data[256:] ^ file_key[positions % 8]
        local = np.zeros_like(counts)
        for i in range(11):
            local[i] = np.bincount(decoded[positions % 88 // 8 == i], minlength=256)
        counts += local
        for frequencies in (local, counts):
            candidate = bytes(frequencies.argmax(axis=1).tolist())
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
        sampled += 1
        if sampled >= limit:
            break


if __name__ == "__main__":
    import time
    import umadata
    t = time.time()
    data = umadata.GameData()
    ok = data.ready()
    print(f"source: {data.source or 'none'}; ready {ok}; {len(data.entries)} manifest rows; "
          f"{sum(e.local for e in list(data.entries.values())[:2000])} of the first 2000 downloaded; "
          f"{time.time() - t:.1f}s")
    if data.error:
        print("game files:", data.error)
