"""English display labels, independent of the region supplying the models."""
from functools import lru_cache
from contextlib import closing
from pathlib import Path
import re
import sqlite3

import gamepaths

JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
LABELS = {5: "Outfit", 6: "Uma", 14: "Outfit", 16: "Song", 55: "Training", 59: "Background Uma"}


def stamp(path):
    try:
        s = Path(path).stat()
        return str(path), s.st_mtime_ns, s.st_size
    except OSError:
        return str(path), None, None


def source_key():
    global_master = Path(gamepaths.paths("global")[1]) / "master" / "master.mdb"
    return gamepaths.region(), stamp(gamepaths.master()), stamp(global_master)


@lru_cache(maxsize=4)
def global_labels(key):
    out = {}
    try:
        with closing(sqlite3.connect(Path(key[0]).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            for cat, idx, text in db.execute(
                    "SELECT category, [index], text FROM text_data WHERE category IN (5,6,14,16,55,59)"):
                if text and not JAPANESE.search(text):
                    out.setdefault(cat, {})[idx] = text
    except sqlite3.Error:
        pass
    return out


def names(db, category):
    original = dict(db.execute("SELECT [index], text FROM text_data WHERE category=?", (category,)))
    if gamepaths.region() != "jp":
        return original
    translated = dict(global_labels(source_key()[2]).get(category, {}))
    if category == 6:
        # JP ships official Latin-script names, including characters absent from Global.
        for idx, text in db.execute("SELECT [index], text FROM text_data WHERE category=372"):
            if text and not JAPANESE.search(text):
                translated.setdefault(idx, text)
    return {idx: translated.get(idx) or (text if text and not JAPANESE.search(text)
                                       else f"{LABELS.get(category, 'Item')} {idx}")
            for idx, text in original.items()}
