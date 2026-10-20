"""Readable names for the game's motion files, one row per motion set.

Most motions come as a set of parts: _s (start), _loop, _e (end), _pose (a single still
frame), _act01.. (reactions), training _suc/_bad/_cmn, card cut-in shots. The pose list
shows a set once, named from the game's own data where it has names (characters, cards,
songs, training) and from the file name's words otherwise (romaji translated).
"""
import os
import re
import sqlite3

import gamepaths
import textlabels

# not poses on their own: additive layers, hand grips, tail-only and helper clips
NOT_POSES = re.compile(r"(_plus|_ik_add|_tail|_add|plusend)$|^anm_eve_type00_prop\d+")

PART_ORDER = {"s": 0, "loop": 1, "sl": 2, "e": 3, "pose": 4, "in": 5, "out": 6}
PART_LABEL = {"s": "Start", "loop": "Loop", "sl": "Loop 2", "e": "End", "pose": "Still", "in": "In", "out": "Out"}
DEFAULT_PART = ("loop", "pose", "sl", "s")
TRAINING_PART = {"suc": "Success", "bad": "Fail", "cmn": "Common"}

WORDS = {
    # romaji
    "akire": "exasperated", "koshiate": "hands on hips", "udekumi": "arms crossed", "sudachi": "standing",
    "muneate": "hand on chest", "tekumi": "hands together", "soppo": "looking away", "kuchiate": "hand to mouth",
    "gatts": "fist pump", "chuni": "dramatic pose", "ikaku": "threatening", "tekagami": "hand mirror",
    "janken": "rock paper scissors", "budo": "martial arts", "banzai": "banzai", "kabuki": "kabuki",
    # the game's shorthand
    "byebye": "bye-bye", "hightouch": "high five", "homestand": "standing (home)", "homewalk": "walking (home)",
    "homeeat": "eating (home)", "ng": "no good", "ok": "OK", "runin": "running in", "runout": "running out",
    "walkin": "walking in", "walkout": "walking out", "jumpin": "jumping in", "skipin": "skipping in",
    "pdk": "paddock", "mot": "motion", "idle": "idle", "act": "action", "eatbase": "eating", "watchbase": "watch",
    "sitdown": "sitting down", "getup": "getting up", "lookback": "looking back", "goaway": "go away",
    "handsup": "hands up", "gameplay": "playing games", "cointoss": "coin toss", "eartouch": "touching ears",
    "cellphone": "flip phone", "icecream": "ice cream", "cracker": "party popper", "typewrite": "typing",
    "schoolbag": "school bag", "phoneselfie": "selfie", "muscle": "flexing", "quiet": "shh", "gun": "finger gun",
    "turnr": "turn right", "turnl": "turn left", "limitbreak": "limit break", "succession": "inheritance",
    # plain words, kept so compounds can be split
    "advice": "advice", "angry": "angry", "appeal": "appeal", "bag": "bag", "ball": "ball", "billiards": "billiards",
    "binder": "binder", "bit": "bit", "board": "board", "book": "book", "bow": "bow", "brush": "brushing",
    "camera": "camera", "cane": "cane", "card": "card", "chair": "chair", "check": "checking", "cheer": "cheering",
    "clap": "clapping", "clock": "clock", "come": "come here", "cook": "cooking", "count": "counting",
    "crouch": "crouching", "cry": "crying", "cup": "cup", "dance": "dance", "diary": "diary", "disc": "disc",
    "doll": "doll", "drink": "drinking", "eat": "eating", "factor": "inheritance", "fall": "falling", "fan": "fan",
    "fight": "fighting", "fish": "fishing", "flower": "flower", "fruits": "fruit", "glass": "glass", "golf": "golf",
    "good": "good", "guard": "guard", "hammer": "hammer", "hand": "hand", "happy": "happy", "hello": "hello",
    "hit": "hit", "hold": "holding", "honey": "honey", "hope": "hopeful", "hot": "hot", "hug": "hug",
    "hungry": "hungry", "hurry": "hurrying", "icing": "icing", "idea": "idea", "joke": "joking", "joy": "joy",
    "key": "key", "kick": "kick", "kiss": "kiss", "lantern": "lantern", "laptop": "laptop", "laugh": "laughing",
    "letter": "letter", "lip": "lip", "long": "long", "look": "looking", "magic": "magic", "main": "main",
    "maintenance": "maintenance", "map": "map", "massage": "massage", "mic": "mic", "moonwalk": "moonwalk",
    "mug": "mug", "near": "coming close", "newspaper": "newspaper", "oh": "oh", "opera": "opera", "pain": "pain",
    "paper": "paper", "peace": "peace sign", "phone": "phone", "picture": "picture", "play": "playing",
    "point": "pointing", "pose": "pose", "present": "giving a present", "promise": "promise", "punch": "punch",
    "puppet": "puppet", "push": "push", "quiz": "quiz", "request": "asking", "run": "running", "sad": "sad",
    "salute": "salute", "see": "looking", "selfie": "selfie", "send": "sending off", "serving": "serving",
    "set": "set", "shake": "shaking", "shaker": "shaker", "shock": "shocked", "shots": "shot", "show": "showing",
    "shy": "shy", "sing": "singing", "skip": "skipping", "sleep": "sleeping", "slide": "sliding", "smell": "smelling",
    "snap": "snap", "sneeze": "sneeze", "sorry": "sorry", "speech": "speech", "stand": "standing",
    "start": "start", "step": "step", "stomp": "stomping", "stone": "stone", "stop": "stop", "stretch": "stretching",
    "study": "studying", "supporter": "supporter", "surprise": "surprised", "swim": "swimming", "swing": "swing",
    "sword": "sword", "table": "table", "tablet": "tablet", "take": "taking", "talk": "talking", "teacup": "teacup",
    "think": "thinking", "throw": "throwing", "tired": "tired", "tradition": "tradition", "training": "training",
    "turn": "turn", "umbrella": "umbrella", "unique": "unique", "up": "up", "violin": "violin", "walk": "walking",
    "watch": "watch", "wood": "wood", "write": "writing", "yell": "yelling", "back": "back", "side": "side",
    "ballet": "ballet", "neckup": "neck up", "runaway": "running away", "runback": "running back",
    "pair": "pair", "breath": "breathing", "nail": "nails",
}


# extra words people search for, by the file name's word
TAGS = {
    "byebye": "wave waving goodbye", "hello": "wave waving hi greeting", "gatts": "yes celebrate", "banzai": "cheer celebrate",
    "cheer": "celebrate", "udekumi": "arms folded", "chuni": "chuunibyou cool", "akire": "sigh annoyed", "soppo": "pout ignore",
    "koshiate": "akimbo", "muneate": "proud", "tekumi": "pray clasp", "kuchiate": "giggle", "shy": "embarrassed blush",
    "laugh": "smile happy", "cry": "tears", "angry": "mad", "think": "ponder", "peace": "v sign", "point": "finger",
    "bow": "greeting thanks", "sleep": "tired nap", "run": "jog", "walk": "stroll", "phone": "smartphone",
}


# --- names from the game's data ------------------------------------------------------------

_names = None
_names_key = None


def game_names():
    global _names, _names_key
    key = textlabels.source_key()
    if _names is None or key != _names_key:
        n = {"chara": {}, "card": {}, "song": {}, "command": {}, "training": {}}
        try:
            db = sqlite3.connect(f"file:{gamepaths.master()}?mode=ro", uri=True)
            try:
                for key, cat in (("chara", 6), ("card", 5), ("song", 16), ("command", 55)):
                    n[key] = textlabels.names(db, cat)
                for cutin, cmd, base, level in db.execute(
                        "SELECT cutin_file_id, command_id, base_command_id, command_level FROM single_mode_training"):
                    n["training"][cutin] = (n["command"].get(cmd) or n["command"].get(base), level)
            finally:
                db.close()
        except sqlite3.Error:
            pass
        _names = n
        _names_key = textlabels.source_key()
    return _names


def chara(cid):
    return game_names()["chara"].get(int(cid)) or f"Uma {int(cid)}"


def card(cid):
    """'Special Week · [Special Dreamer]' for a card id (chara id + two digits)."""
    title = game_names()["card"].get(int(cid))
    who = chara(cid[:4]) if len(cid) == 6 else None
    if who and title:
        return f"{who} · {title}"
    return who or f"Card {cid}"


# --- words ---------------------------------------------------------------------------------------

def _segment(s):
    """Splits run-together words ('phoneselfie', 'drinkmugb') into known ones; a lone
    trailing letter is a variant. None when it cannot."""
    best = {0: []}
    for i in range(len(s)):
        if i not in best:
            continue
        for j in range(len(s), i, -1):
            w = s[i:j]
            if w in WORDS and (j not in best or len(best[j]) > len(best[i]) + 1):
                best[j] = best[i] + [WORDS[w]]
    if len(s) in best:
        return best[len(s)]
    if len(s) - 1 in best and s[-1] in "abcd":
        return best[len(s) - 1] + [s[-1].upper()]
    return None


def words(rest):
    """'byebye09_02' -> 'bye-bye 9-2', 'udekumi01' -> 'arms crossed 1'."""
    out, nums = [], []

    def flush():
        if nums:
            out.append("-".join(nums))
            nums.clear()

    for tok in [t for t in rest.lower().split("_") if t]:
        m = re.fullmatch(r"([a-z]*)(\d*)([a-z]?)", tok)
        if not m:
            flush()
            out.append(tok)
            continue
        word, num, tail = m.groups()
        if word:
            flush()
            out.extend(_segment(word) or [word])
        if num:
            nums.append(str(int(num)))
        if tail:
            flush()
            out.append(tail.upper())
    flush()
    return " ".join(out)


def cap(s):
    return s[:1].upper() + s[1:]


# --- one file ------------------------------------------------------------------------------------

def describe(leaf):
    """(set key, title, sub, part key, part label) for one motion file name."""
    name = leaf.lower()
    mirror = name.endswith("_mirror")
    if mirror:
        name = name[:-len("_mirror")]
    title, sub = None, ""
    part, plabel = None, None

    # parts that name what follows a common stem
    m = re.match(r"^(.*)_(s|loop|sl|e|pose|in|out)$", name)
    if m:
        stem, part = m.group(1), m.group(2)
        plabel = PART_LABEL[part]
    else:
        m = re.match(r"^(.*)_act(\d+)$", name)
        if m:
            stem, part, plabel = m.group(1), f"act{int(m.group(2)):02d}", f"Action {int(m.group(2))}"
        else:
            m = re.match(r"^(anm_tra_.*)_(suc|bad|cmn)(\d+)$", name)
            if m:
                stem, part = m.group(1), m.group(2) + m.group(3)
                plabel = TRAINING_PART[m.group(2)] + ("" if int(m.group(3)) == 1 else f" {int(m.group(3))}")
            else:
                stem = name

    g = game_names()
    if m := re.match(r"^anm_res_chr(\d{4})_(\d+)(.*)$", stem):
        title = f"{chara(m.group(1))} · victory {int(m.group(2))}"
        sub = words(m.group(3))
    elif m := re.match(r"^anm_res_crd(\d+)_(\d+)(.*)$", stem):
        title, sub = f"{card(m.group(1))} victory", words(m.group(3))
    elif m := re.match(r"^anm_res_type(\d+)_(.*)$", stem):
        title, sub = f"Victory pose {int(m.group(1))}", words(m.group(2))
    elif m := re.match(r"^anm_cti_crd(\d+)_(\d+)_(\d+)(.*)$", stem):
        # one cut-in, shot by shot
        title = f"{card(m.group(1))} cut-in"
        stem = f"anm_cti_crd{m.group(1)}_{m.group(2)}"
        part = f"{int(m.group(3)):02d}{m.group(4)}"
        plabel = cap(f"shot {int(m.group(3))} {words(m.group(4))}".strip())
    elif m := re.match(r"^anm_eve_type\d+_(.*)$", stem):
        title = cap(words(m.group(1)))
    elif m := re.match(r"^anm_eve_chr(\d{4})_(\d+)_?(.*)$", stem):
        title = f"{chara(m.group(1))} · {words(m.group(3)) or 'reactions'}"
    elif m := re.match(r"^anm_hom_type(\d+)_(\d+)_(\d+)(?:_(\d+))?$", stem):
        title = f"Home spot {int(m.group(1))} · set {int(m.group(3))}" + (f"-{int(m.group(4))}" if m.group(4) else "")
    elif m := re.match(r"^anm_liv_son(\d{4})_?(.*)$", stem):
        title = g["song"].get(int(m.group(1)), f"Song {m.group(1)}")
        sub = words(m.group(2))
    elif m := re.match(r"^anm_tra_type(\d+)_(\d{6})_(.*)$", stem):
        command, level = g["training"].get(int(m.group(2)), (None, None))
        title = f"{command} training" + (f" · Lv{level}" if level else "") if command else f"Training #{m.group(2)}"
        if m.group(1) != "00":
            title += " (alt)"
        sub = "take " + "-".join(str(int(x)) for x in m.group(3).split("_") if x.isdigit())
    elif m := re.match(r"^anm_sce_chr(\d{4})_\d+_(.*)$", stem):
        title = f"{chara(m.group(1))} · story {words(m.group(2))}"
    elif m := re.match(r"^anm_sce_type\d+_(.*)$", stem):
        title = cap("story " + words(m.group(1)))
    elif m := re.match(r"^anm_prg_(\d+)_cut(\d+)_chr(\d{4})(.*)$", stem):
        title = f"Prologue {int(m.group(1))} · {chara(m.group(3))}"
        sub = f"cut {int(m.group(2))} {words(m.group(4))}".strip()
    elif m := re.match(r"^anm_rup_type(\d+)_(.*)$", stem):
        title, sub = f"Rank up {int(m.group(1))}", words(m.group(2))
    elif m := re.match(r"^anm_fac_type\d+_(.*)$", stem):
        title = cap(words(m.group(1)))
    if title is None:
        title = cap(words(re.sub(r"^anm_[a-z]+_", "", stem)))
    if mirror:
        title += " · mirrored"
        stem += "_mirror"
    return stem, title, sub, part, plabel


def family(title):
    """The list folds similar sets together: 'Arms crossed 3-2' -> ('Arms crossed', '3-2'),
    'Gold Ship · [card] cut-in' -> ('Gold Ship', '[card] cut-in')."""
    mirrored = title.endswith(" · mirrored")
    if mirrored:
        title = title[:-len(" · mirrored")]
    if " · " in title:
        fam, name = title.split(" · ", 1)
        name = cap(name)
    else:
        m = re.match(r"^(.*?)\s+(\d+(?:-\d+)*)$", title)
        fam, name = (m.group(1), m.group(2)) if m else (title, "")
    if mirrored:
        name = f"{name} (mirrored)".strip()
    return fam, name


def group(names, key=lambda n: n, own=None):
    """Motion sets from file names (or paths: key gives the file name). Each set:
    {title, sub, parts: [[label, item]...], default: index into parts, chara}. own: the
    loaded character's id, whose name is left out of titles."""
    sets = {}
    for item in names:
        leaf = key(item).rsplit("/", 1)[-1]
        if NOT_POSES.search(leaf.lower().replace("_mirror", "")):
            continue
        stem, title, sub, part, plabel = describe(leaf)
        if own and title.startswith(chara(own) + " · "):
            title = cap(title[len(chara(own)) + 3:])
        s = sets.setdefault(stem, {"title": title, "sub": sub, "parts": [], "chara": None,
                                   "tags": " ".join(v for k, v in TAGS.items() if k in stem)})
        s["parts"].append((part, plabel or "Play", item))
        m = re.search(r"chr(\d{4})|crd(\d{4})", leaf)
        if m:
            s["chara"] = int(m.group(1) or m.group(2))
    out = []
    for s in sets.values():
        s["parts"].sort(key=lambda p: (p[0] is not None, PART_ORDER.get(p[0], 10), p[0] or ""))
        keys = [p[0] for p in s["parts"]]
        default = next((keys.index(k) for k in DEFAULT_PART if k in keys), 0)
        fam, short = family(s["title"])
        # Numbered shots form a sequence. Start/loop/end sets play one pass of the
        # loop; still poses, reactions and success/failure alternatives stay separate.
        sequence = [i for i, k in enumerate(keys) if k and re.fullmatch(r"\d+", k)]
        if len(sequence) != len(keys):
            sequence = [keys.index(k) for k in ("s", "loop", "sl", "e") if k in keys]
            if len(sequence) < 2:
                sequence = [keys.index(k) for k in ("in", "out") if k in keys]
        out.append({"title": s["title"], "sub": s["sub"], "chara": s["chara"], "family": fam, "name": short, "tags": s["tags"],
                    "parts": [[label, it] for _, label, it in s["parts"]], "default": default,
                    "sequence": sequence if len(sequence) > 1 else []})
    # alphabetical, but numbers in number order: "set 2" before "set 2-1" before "set 10"
    out.sort(key=lambda st: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", f"{st['title']} {st['sub']}".lower())])
    return out
