"""Region-specific game locations, saved in %LOCALAPPDATA%/UmaStudio/game.json."""
import hashlib
import json
import os
import re
import time

CONFIG = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UmaStudio")
SETTINGS = os.path.join(CONFIG, "game.json")
LOCALLOW = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Cygames", "Umamusume")
DATA_DIR = "UmamusumePrettyDerby_Data"
REGIONS = {
    "global": {"label": "Global", "installs": ("UmamusumePrettyDerby",),
               "unity": (DATA_DIR,), "local": ("Umamusume",)},
    "jp": {"label": "Japan (JP)", "installs": ("UmamusumePrettyDerby_Jpn", "umamusume"),
           "unity": ("UmamusumePrettyDerby_Jpn_Data", "umamusume_Data"),
           "local": ("UmamusumePrettyDerby_Jpn",)},
}
_cache = {"at": 0.0, "region": None, "game": None, "data": LOCALLOW}
_settings_cache = {"at": 0.0, "path": None, "value": None}


def settings():
    if (_settings_cache["path"] == SETTINGS and _settings_cache["value"] is not None
            and time.time() - _settings_cache["at"] < 3):
        return _settings_cache["value"]
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            saved = {}
    except (OSError, ValueError):
        saved = {}
    profiles = saved.get("profiles", {})
    profiles = profiles if isinstance(profiles, dict) else {}
    profiles = {k: v for k, v in profiles.items() if k in REGIONS and isinstance(v, dict)}
    # Preserve the original single-folder setting as the Global profile.
    if saved.get("path") and "global" not in profiles:
        profiles["global"] = {"path": saved["path"]}
    result = {"region": saved.get("region") if saved.get("region") in REGIONS else "global",
              "profiles": profiles}
    _settings_cache.update(at=time.time(), path=SETTINGS, value=result)
    return result


def region():
    return settings()["region"]


def _region(value):
    value = value or region()
    if value not in REGIONS:
        raise ValueError("Choose Global or Japan (JP).")
    return value


def _path(value):
    if not isinstance(value, str):
        raise ValueError("A folder path must be text.")
    value = value.strip().strip('"')
    return os.path.abspath(os.path.expanduser(os.path.expandvars(value))) if value else ""


def unity_data_dir(game=None):
    game = game or paths()[0]
    if not game:
        raise RuntimeError("Game installation not found. Choose its install folder in Settings.")
    for spec in REGIONS.values():
        for name in spec["unity"]:
            folder = os.path.join(game, name)
            if os.path.isdir(folder):
                return folder
    raise RuntimeError("The selected installation has no supported Unity data folder.")


def steam_game(selected=None):
    """Find this region only; never use the other region's Steam installation."""
    selected = _region(selected)
    try:
        import winreg
    except ImportError:
        return None
    roots = []
    for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                roots.append(winreg.QueryValueEx(k, value)[0])
        except OSError:
            pass
    libraries = []
    for steam in roots:
        libraries.append(steam)
        try:
            with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        libraries += [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', text)]
    for lib in dict.fromkeys(libraries):
        for name in REGIONS[selected]["installs"]:
            folder = os.path.join(lib, "steamapps", "common", name)
            if any(os.path.isdir(os.path.join(folder, d)) for d in REGIONS[selected]["unity"]):
                return folder
    return None


def has_data(folder):
    return bool(folder) and os.path.isfile(os.path.join(folder, "meta"))


def split(path, selected=None):
    """Accept an install, Unity _Data, Persistent, or standalone downloaded-data folder."""
    selected = _region(selected)
    p = _path(path)
    if not p:
        return None, None
    parents = (p, os.path.dirname(p), os.path.dirname(os.path.dirname(p)))
    game = next((c for c in parents
                 if any(os.path.isdir(os.path.join(c, d)) for d in REGIONS[selected]["unity"])), None)
    # A known installation from another region is a likely accidental selection.
    for other, spec in REGIONS.items():
        if other != selected and any(os.path.isdir(os.path.join(c, d)) for c in parents for d in spec["unity"]):
            return None, None
    data = p if has_data(p) else None
    if not data and game:
        persistent = os.path.join(unity_data_dir(game), "Persistent")
        if has_data(persistent):
            data = persistent
    return game, data


def picked(selected=None):
    profile = settings()["profiles"].get(_region(selected), {})
    return profile.get("path") or profile.get("data") or profile.get("game") or None


def configure(selected, game="", data="", path=None):
    """Validate and atomically save one profile and activate it. Empty fields mean automatic."""
    selected = _region(selected)
    profile = {}
    if path is not None:
        path = _path(path)
        install, downloaded = split(path, selected)
        if not install and not downloaded:
            raise ValueError("No files for that region were found. Choose its install folder "
                             "or a data folder containing meta.")
        profile = dict(settings()["profiles"].get(selected, {}))
        profile.pop("path", None)
        if install:
            profile["game"] = install
            profile.pop("data", None)
        if downloaded:
            profile["data"] = downloaded
    else:
        game, data = _path(game), _path(data)
        if game:
            install, _ = split(game, selected)
            if not install:
                raise ValueError("That install folder does not match the selected region.")
            profile["game"] = install
        if data:
            _, downloaded = split(data, selected)
            if not downloaded:
                raise ValueError("Choose the data folder containing a file called meta.")
            profile["data"] = downloaded
    old = settings()
    saved = {"region": old["region"], "profiles": dict(old["profiles"])}
    saved["region"] = selected
    saved["profiles"][selected] = profile
    os.makedirs(CONFIG, exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)
    _settings_cache.update(at=time.time(), path=SETTINGS, value=saved)
    _cache["at"] = 0.0
    return paths()


def pick(path):
    return configure(region(), path=path)


def _resolve(selected):
    profile = settings()["profiles"].get(selected, {})
    game = data = None
    if profile.get("path"):
        game, data = split(profile["path"], selected)
    game = profile.get("game") or game or steam_game(selected)
    data = profile.get("data") or data
    if not data:
        candidates = []
        if game:
            try:
                candidates.append(os.path.join(unity_data_dir(game), "Persistent"))
            except RuntimeError:
                pass
        candidates += [os.path.join(os.path.dirname(LOCALLOW), n) for n in REGIONS[selected]["local"]]
        # The old shared LocalLow name is ambiguous when both Steam regions exist.
        # Only select it automatically for a recognized legacy JP installation.
        if selected == "jp" and game and os.path.isdir(os.path.join(game, "umamusume_Data")):
            candidates.append(LOCALLOW)
        found = [d for d in candidates if has_data(d)]
        data = max(found, key=lambda d: os.path.getmtime(os.path.join(d, "meta"))) if found else candidates[0]
    return game, data


def paths(selected=None):
    selected = _region(selected)
    if _cache["region"] != selected or time.time() - _cache["at"] > 3:
        _cache["game"], _cache["data"] = _resolve(selected)
        _cache.update(at=time.time(), region=selected)
    return _cache["game"], _cache["data"]


def cache_key():
    source = os.path.normcase(os.path.realpath(data()))
    return region() + "-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def config_cache():
    return os.path.join(CONFIG, "profiles", cache_key())


def describe():
    saved = settings()
    profiles = {}
    for key, spec in REGIONS.items():
        install, downloaded = paths(key)
        profile = saved["profiles"].get(key, {})
        profiles[key] = {"label": spec["label"], "game": install, "data": downloaded,
                         "gameOverride": profile.get("game", ""),
                         "dataOverride": profile.get("data", ""),
                         "path": profile.get("path", ""), "found": has_data(downloaded)}
    return {"region": saved["region"], "profiles": profiles}


def game():
    return paths()[0]


def data():
    return paths()[1]


def meta():
    return os.path.join(data(), "meta")


def master():
    return os.path.join(data(), "master", "master.mdb")


def dat():
    return os.path.join(data(), "dat")
