"""Where the game is installed and where its downloaded data (meta, master/, dat/) lives.

Older installs keep the data in LocalLow\\Cygames\\Umamusume, newer ones in the install
folder under UmamusumePrettyDerby_Data\\Persistent. Both are checked, and the user can pick
the folder by hand when neither is found (saved to %LOCALAPPDATA%\\UmaStudio\\game.json).
"""
import json
import os
import re
import time

CONFIG = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UmaStudio")
SETTINGS = os.path.join(CONFIG, "game.json")
LOCALLOW = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Cygames", "Umamusume")
DATA_DIR = "UmamusumePrettyDerby_Data"
DLL = os.path.join(DATA_DIR, "Plugins", "x86_64", "CySpringPlugin.dll")

_cache = {"at": 0.0, "game": None, "data": LOCALLOW}


def steam_game():
    """The install folder, from Steam's library list."""
    import winreg
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
            text = open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8").read()
        except OSError:
            continue
        libraries += [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', text)]
    for lib in libraries:
        game = os.path.join(lib, "steamapps", "common", "UmamusumePrettyDerby")
        if os.path.exists(os.path.join(game, DLL)):
            return game
    return None


def has_data(folder):
    return bool(folder) and os.path.isfile(os.path.join(folder, "meta"))


def split(path):
    """A picked folder -> (install folder or None, data folder or None). Takes the install
    folder, its _Data folder, the Persistent folder or the old LocalLow one."""
    p = os.path.normpath(os.path.expandvars(path.strip().strip('"')))
    game = next((c for c in (p, os.path.dirname(p), os.path.dirname(os.path.dirname(p)))
                 if os.path.isdir(os.path.join(c, DATA_DIR))), None)
    data = p if has_data(p) else None
    if not data and game and has_data(os.path.join(game, DATA_DIR, "Persistent")):
        data = os.path.join(game, DATA_DIR, "Persistent")
    return game, data


def picked():
    try:
        return json.load(open(SETTINGS, encoding="utf-8")).get("path") or None
    except (OSError, ValueError):
        return None


def pick(path):
    """Remember a folder the user chose. Raises ValueError when there is no game in it."""
    game, data = split(path)
    if not game and not data:
        raise ValueError("No Uma Musume files in that folder. Pick the game's install folder "
                         "(UmamusumePrettyDerby) or its data folder (the one with a file called meta).")
    os.makedirs(CONFIG, exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"path": os.path.normpath(path.strip().strip('"'))}, f)
    _cache["at"] = 0.0
    return paths()


def _resolve():
    game = data = None
    if picked():
        game, data = split(picked())
    game = game or steam_game()
    if not data:
        found = [d for d in (game and os.path.join(game, DATA_DIR, "Persistent"), LOCALLOW) if has_data(d)]
        # both can exist after the game moved its data: the one it wrote last is the live one
        data = max(found, key=lambda d: os.path.getmtime(os.path.join(d, "meta"))) if found else LOCALLOW
    return game, data


def paths():
    """(install folder or None, data folder), looked up again every few seconds."""
    if time.time() - _cache["at"] > 3:
        _cache["game"], _cache["data"] = _resolve()
        _cache["at"] = time.time()
    return _cache["game"], _cache["data"]


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
