"""Convert Unity Humanoid clips in a private Editor project, then cache plain motion data."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

IMPORT_VERSION = 1
MAX_FILE = 32 * 1024 * 1024


def find_unity(preferred=""):
    if preferred:
        path = Path(preferred).expanduser()
        if path.name.lower() != "unity.exe" or not path.is_file():
            raise ValueError("Choose the installed Unity Editor's Unity.exe file.")
        return path.resolve()
    roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Unity/Hub/Editor"]
    candidates = [p for root in roots for p in root.glob("*/Editor/Unity.exe")]
    # Prefer the LTS version used for this bridge; newer installations also work.
    candidates.sort(key=lambda p: (p.parts[-3].startswith("2022.3"), p.parts[-3]), reverse=True)
    if candidates:
        return candidates[0]
    raise ValueError("Importing Humanoid .anim files requires an installed Unity Editor (2022.3 or newer). "
                     "Set its Unity.exe location in Animate. Saved converted projects play without Unity.")


def validate_bones(bones):
    if not isinstance(bones, list) or not 15 <= len(bones) <= 1500:
        raise ValueError("Invalid character skeleton.")
    out, indices, names = [], set(), set()
    for b in bones:
        if not isinstance(b, dict):
            raise ValueError("Invalid skeleton bone.")
        name, index, parent = b.get("name"), b.get("index"), b.get("parent")
        if (not isinstance(name, str) or not name or len(name) > 150 or name in names
                or type(index) is not int or type(parent) is not int or index in indices
                or (parent != -1 and parent not in indices)):
            raise ValueError("The skeleton must contain uniquely named bones in parent-first order.")
        clean = dict(name=name, index=index, parent=parent)
        for key, width in (("p", 3), ("q", 4), ("s", 3)):
            values = b.get(key)
            if (not isinstance(values, list) or len(values) != width
                    or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000 for v in values)):
                raise ValueError("Invalid bone transform.")
            clean[key] = values
        if math.hypot(*clean["q"]) < 1e-6 or any(v <= 0 for v in clean["s"]):
            raise ValueError("Invalid bone rotation or scale.")
        indices.add(index)
        names.add(name)
        out.append(clean)
    required = {"Position", "Hip", "Waist", "Spine", "Chest", "Neck", "Head"}
    required.update(f"{n}_{side}" for side in ("L", "R") for n in ("Arm", "Elbow", "Wrist", "Thigh", "Knee", "Ankle"))
    if not required.issubset(names):
        raise ValueError("This skeleton is missing bones required for Humanoid import. Choose a full-size Uma.")
    return out


def qmul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X, w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z]


def relative_motion(result, bones):
    """Store rest-relative tracks, so full-pose correction keys remain independent."""
    if result.get("error"):
        raise ValueError(result["error"].split("\n")[0])
    fps, frames, duration = result.get("fps"), result.get("frames"), result.get("duration")
    if fps != 60 or not isinstance(frames, int) or not 2 <= frames <= 7201 or not isinstance(duration, (int, float)) or not 0 < duration <= 120:
        raise ValueError("The converter returned an invalid animation.")
    rest = {b["name"]: b for b in bones}
    tracks = []
    for t in result.get("tracks", []):
        if t.get("bone") not in rest or t.get("type") not in ("position", "quaternion"):
            raise ValueError("The converter returned an unknown bone track.")
        b = rest[t["bone"]]
        width = 4 if t["type"] == "quaternion" else 3
        values = t.get("values", [])
        if len(values) != frames * width or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError("The converter returned incomplete or non-finite motion.")
        output = []
        previous = None
        for i in range(0, len(values), width):
            v = values[i:i+width]
            if width == 4:
                # Input rest is Unity space, converted result is Three.js space.
                x, y, z, w = b["q"]
                v = qmul([-x, y, z, w], v)
                norm = math.hypot(*v)
                if norm < 1e-7:
                    raise ValueError("The converter returned an invalid rotation.")
                v = [x / norm for x in v]
                if previous and sum(x*y for x, y in zip(previous, v)) < 0:
                    v = [-x for x in v]
                previous = v
            else:
                p = [-b["p"][0], b["p"][1], b["p"][2]]
                v = [x-y for x, y in zip(v, p)]
            output.extend(round(x, 6) for x in v)
        if all(abs(v-output[i % width]) <= 1e-5 for i, v in enumerate(output)):
            output = output[:width]
        # Most positional tracks are just rest values; omit these entirely.
        if width == 3 and len(output) == 3 and all(abs(v) < 1e-5 for v in output):
            continue
        tracks.append(dict(bone=t["bone"], type=t["type"], values=output))
    if not tracks or not any(len(t["values"]) > 4 for t in tracks):
        raise ValueError("No moving Humanoid bones were found in this clip.")
    return dict(name=str(result.get("name") or "Imported animation")[:100], fps=fps, frames=frames,
                duration=round((frames-1)/fps, 8), loop=bool(result.get("loop")), tracks=tracks)


class ImportManager:
    def __init__(self, folder, script):
        self.folder, self.script = Path(folder), Path(script)
        self.jobs = {}
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()

    def start(self, body):
        if not isinstance(body.get("filename"), str) or not body["filename"].lower().endswith(".anim"):
            raise ValueError("Choose a Unity .anim file.")
        encoded = body.get("data", "")
        if not isinstance(encoded, str) or len(encoded) > MAX_FILE * 4 // 3 + 4:
            raise ValueError("Animation files must be smaller than 32 MB.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as e:
            raise ValueError("Invalid animation file data.") from e
        if not data or len(data) > MAX_FILE:
            raise ValueError("Animation files must be between 1 byte and 32 MB.")
        bones = validate_bones(body.get("bones"))
        key = hashlib.sha256(data + json.dumps(bones, sort_keys=True).encode() + self.script.read_bytes() + str(IMPORT_VERSION).encode()).hexdigest()
        cache = self.folder / "cache" / (key + ".json")
        job = dict(id=uuid.uuid4().hex, state="running", message="Waiting for the animation converter…")
        with self.state_lock:
            if any(j["state"] == "running" for j in self.jobs.values()):
                raise ValueError("An animation import is already running. Wait for it to finish.")
            # Bound memory, retaining the most recent finished import for polling.
            self.jobs = {job["id"]: job}
        if cache.is_file():
            job.update(state="complete", message="Loaded converted animation.", motion=json.loads(cache.read_text(encoding="utf-8")))
        else:
            try:
                editor = find_unity(body.get("unity", "").strip())
            except Exception:
                job["state"] = "failed"
                raise
            threading.Thread(target=self._run, args=(job, data, bones, editor, cache), daemon=True).start()
        return dict(id=job["id"])

    def status(self, job_id):
        with self.state_lock:
            return dict(self.jobs.get(job_id, dict(state="failed", error="This import is no longer available.")))

    def _run(self, job, data, bones, editor, cache):
        try:
            with self.lock:
                project = self.folder / "unity-project"
                assets = project / "Assets"
                (assets / "Editor").mkdir(parents=True, exist_ok=True)
                (project / "Packages").mkdir(exist_ok=True)
                (project / "ProjectSettings").mkdir(exist_ok=True)
                shutil.copyfile(self.script, assets / "Editor/UmaAnimationImport.cs")
                (assets / "Input.anim").write_bytes(data)
                (project / "Packages/manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
                (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: " + editor.parent.parent.name + "\n", encoding="utf-8")
                result_path = project / ("result-" + job["id"] + ".json")
                request_path = project / "request.json"
                request_path.write_text(json.dumps(dict(bones=bones, fps=60, output=str(result_path))), encoding="utf-8")
                log = project / "converter.log"
                job["message"] = "Retargeting with Unity in the background. First import can take a few minutes…"
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                args = [str(editor), "-batchmode", "-nographics", "-projectPath", str(project),
                        "-executeMethod", "UmaAnimationImport.Run", "-studioRequest", str(request_path), "-logFile", str(log)]
                proc = subprocess.Popen(args, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    code = proc.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise ValueError("Unity did not finish within five minutes. Check the Editor's license and the import log: " + str(log))
                if not result_path.is_file():
                    raise ValueError(f"Unity could not run the converter (exit {code}). Open Unity once to check its license. Import log: {log}")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                motion = relative_motion(result, bones)
                if code:
                    raise ValueError(f"Unity exited with code {code}. Import log: {log}")
                cache.parent.mkdir(parents=True, exist_ok=True)
                temp = cache.with_suffix(".tmp")
                temp.write_text(json.dumps(motion, separators=(",", ":")), encoding="utf-8")
                temp.replace(cache)
                result_path.unlink()
                with self.state_lock:
                    job.update(state="complete", message="Animation ready.", motion=motion)
        except Exception as e:
            with self.state_lock:
                job.update(state="failed", error=str(e), message="Animation import failed.")
