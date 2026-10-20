"""Store editable projects separately from rendered videos and game assets."""
import json
import re
from pathlib import Path


def save_project(folder, project):
    if (not isinstance(project, dict) or project.get("format") != "UmaStudio animation"
            or project.get("version") not in (1, 2) or not isinstance(project.get("name"), str)
            or not project["name"].strip() or len(project["name"]) > 100
            or not isinstance(project.get("keys"), list) or not 1 <= len(project["keys"]) <= 500):
        raise ValueError("Invalid UmaStudio animation project")
    text = json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False)
    if len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise ValueError("Animation projects must be smaller than 16 MB")
    name = re.sub(r'[^\w\-. ]', "_", project["name"]).strip(" .") or "animation"
    # Prefix also keeps Windows device names (CON, NUL, etc.) usable.
    stem = "Animation - " + name
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(10000):
        path = folder / (stem + (f" ({n})" if n else "") + ".umaanim")
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(text)
            return str(path)
        except FileExistsError:
            continue
    raise ValueError("Too many saved versions of this animation")
