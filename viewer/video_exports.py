"""Bounded video streaming. HTTP handlers never wait for the encoder's pipe."""
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid


FORMATS = {
    "mp4": ("mp4", ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                    "-preset", "veryfast", "-movflags", "+faststart"]),
    "webm": ("webm", ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
                      "-crf", "24", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-auto-alt-ref", "0"]),
    "mov": ("mov", ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"]),
    # A whole-clip palette holds all raw frames until EOF. A fresh palette per
    # frame streams immediately, with bounded memory and transparent backgrounds.
    "gif": ("gif", ["-filter_complex",
                     "[0:v]split[a][b];[a]palettegen=reserve_transparent=1:stats_mode=single[p];"
                     "[b][p]paletteuse=new=1:dither=bayer:bayer_scale=3:alpha_threshold=128"]),
}
TERMINAL = {"done", "cancelled", "error"}
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def unique(path):
    candidate = path
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


class ExportJob:
    STALL_SECONDS = 90

    def __init__(self, folder, logs, ffmpeg, w, h, fps, fmt, name, frames, transport, vid):
        self.id = vid
        self.w, self.h, self.fmt, self.frames = w, h, fmt, frames
        self.size = w * h * 4
        self.transport = transport
        self.lock = threading.RLock()
        self.queue = queue.Queue(maxsize=2)
        self.cancelled = threading.Event()
        self.complete = threading.Event()
        self.state, self.error = "rendering", None
        self.accepted = self.written = self.encoded = 0
        self.started = self.activity = time.monotonic()
        self.finished = None
        self.proc = None
        folder, logs = Path(folder), Path(logs)
        folder.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-. ]", "_", str(name))[:120].strip(". ") or "video"
        # Prefix keeps Windows device names (CON, AUX...) valid too.
        if safe.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)],
                                         *[f"LPT{i}" for i in range(10)]}:
            safe = "Video - " + safe
        self.path = unique(folder / (safe if fmt == "png" else safe + "." + FORMATS[fmt][0]))
        self.log_path = logs / f"export-{self.id}.log"
        if fmt == "png":
            self.path.mkdir()
        else:
            if not ffmpeg:
                raise RuntimeError("Video export needs ffmpeg. Install ffmpeg and restart UmaStudio.")
            input_args = (["-f", "image2pipe", "-vcodec", "png", "-framerate", str(fps), "-probesize", "32", "-analyzeduration", "0"]
                          if transport == "png" else
                          ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps)])
            with self.log_path.open("wb") as log:
                self.proc = subprocess.Popen(
                    [ffmpeg, "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
                     *input_args, "-i", "pipe:0", *FORMATS[fmt][1], str(self.path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, creationflags=HIDDEN)
            threading.Thread(target=self._progress, daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def status(self):
        with self.lock:
            return {"id": self.id, "state": self.state, "accepted": self.accepted,
                    "encoded": self.encoded, "frames": self.frames,
                    "elapsed": round((self.finished or time.monotonic()) - self.started, 1),
                    "path": str(self.path) if self.state == "done" else None, "error": self.error}

    def frame(self, index, data):
        if self.transport == "png":
            if (not 33 <= len(data) <= self.size + 1048576 or
                    data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or
                    struct.unpack(">II", data[16:24]) != (self.w, self.h)):
                raise ValueError("Video PNG frame has the wrong size or format.")
        elif len(data) != self.size:
            raise ValueError("Video frame has the wrong size.")
        with self.lock:
            if self.state in TERMINAL:
                raise ValueError(self.error or f"Export is {self.state}.")
            # A lost acknowledgement can be retried without duplicating a frame.
            if 0 <= index < self.accepted:
                return {"accepted": True, "next": self.accepted}
            if self.state != "rendering" or index != self.accepted or index >= self.frames:
                raise ValueError("Video frames arrived out of order.")
            try:
                self.queue.put_nowait(data)
            except queue.Full:
                return {"accepted": False, "next": self.accepted}
            self.accepted += 1
            self.activity = time.monotonic()
            return {"accepted": True, "next": self.accepted}

    def finish(self, cancel=False):
        with self.lock:
            if self.state not in TERMINAL:
                if cancel:
                    self.state = "cancelling"
                    self.cancelled.set()
                elif self.state == "rendering":
                    if self.accepted != self.frames:
                        raise ValueError(f"Export is missing frames ({self.accepted}/{self.frames}).")
                    self.state = "encoding"
                    self.activity = time.monotonic()
        return self.status()

    def _progress(self):
        try:
            for line in self.proc.stdout:
                if line.startswith(b"frame="):
                    count = int(line.split(b"=", 1)[1])
                    with self.lock:
                        if count > self.encoded:
                            self.encoded = count
                            self.activity = time.monotonic()
        except (OSError, ValueError):
            pass
        finally:
            self.proc.stdout.close()

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            # PATH can point to a Chocolatey shim; terminate its encoder child too.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=HIDDEN, timeout=10)
            if self.proc.poll() is None:
                self.proc.kill()

    def _watch(self):
        while not self.complete.wait(.25):
            if time.monotonic() - self.activity > self.STALL_SECONDS:
                with self.lock:
                    self.error = "Export stopped making progress. Try a smaller output size or restart the app."
                self.cancelled.set()
            if self.cancelled.is_set():
                try:
                    self._kill()
                except (OSError, subprocess.TimeoutExpired):
                    if self.proc and self.proc.poll() is None:
                        self.proc.kill()
                return

    def _work(self):
        try:
            while not self.cancelled.is_set():
                try:
                    data = self.queue.get(timeout=.1)
                except queue.Empty:
                    if self.state == "encoding":
                        break
                    if self.proc and self.proc.poll() is not None:
                        raise RuntimeError("Encoder exited before all frames arrived.")
                    continue
                if self.fmt == "png":
                    path = self.path / f"{self.path.name} {self.written + 1:04d}.png"
                    if self.transport == "png":
                        path.write_bytes(data)
                    else:
                        from PIL import Image
                        Image.frombytes("RGBA", (self.w, self.h), data).save(path, compress_level=3)
                    self.encoded += 1
                else:
                    self.proc.stdin.write(data)
                self.written += 1
                self.activity = time.monotonic()
                del data
            if self.proc:
                if self.cancelled.is_set():
                    # The monitor interrupts a blocked pipe before we close it.
                    self._kill()
                self.proc.stdin.close()
                if self.proc.wait(timeout=self.STALL_SECONDS) != 0 and not self.cancelled.is_set():
                    raise RuntimeError("Encoder failed.")
            if not self.cancelled.is_set() and self.written != self.frames:
                raise RuntimeError("Not all video frames were written.")
        except Exception as exc:
            if not self.cancelled.is_set():
                self.error = str(exc)
            try:
                self._kill()
            except (OSError, subprocess.TimeoutExpired):
                pass
        finally:
            if self.proc:
                try:
                    self.proc.stdin.close()
                    self.proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            with self.lock:
                if self.error:
                    try:
                        details = self.log_path.read_text(errors="replace")[-1200:]
                    except OSError:
                        details = ""
                    self.error += (" " + details.strip()) if details.strip() else ""
                    self.state = "error"
                elif self.cancelled.is_set():
                    self.state = "cancelled"
                else:
                    self.encoded = self.written
                    self.state = "done"
                try:
                    if self.state != "done":
                        if self.fmt == "png":
                            shutil.rmtree(self.path)
                        else:
                            self.path.unlink(missing_ok=True)
                except OSError as exc:
                    self.error = f"{self.error or 'Export cancelled.'} Could not remove partial output: {exc}"
                    self.state = "error"
                while not self.queue.empty():
                    self.queue.get_nowait()
                self.finished = time.monotonic()
            self.complete.set()


class ExportManager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.RLock()

    def active(self):
        with self.lock:
            return any(not job.complete.is_set() for job in self.jobs.values())

    def start(self, folder, logs, ffmpeg, w, h, fps, fmt, name, frames, transport="rgba", vid=None):
        vid = vid or uuid.uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", vid):
            raise ValueError("Invalid export ID.")
        if (fmt not in {*FORMATS, "png"} or not 2 <= w <= 8192 or not 2 <= h <= 8192 or
                w * h > 16777216 or not 1 <= fps <= 120 or not 1 <= frames <= 72000 or
                transport not in ("rgba", "png")):
            raise ValueError("Invalid video format, size, frame rate or length.")
        if fmt == "mp4" and (w % 2 or h % 2):
            raise ValueError("MP4 width and height must be even.")
        with self.lock:
            if vid in self.jobs:
                return vid
            if self.active():
                raise ValueError("An export is already running. Finish or cancel it first.")
            # Keep recent results so retries/polls are idempotent, without leaking jobs.
            self.jobs = dict(list(self.jobs.items())[-15:])
            job = ExportJob(folder, logs, ffmpeg, w, h, fps, fmt, name, frames, transport, vid)
            self.jobs[job.id] = job
            return job.id

    def get(self, vid):
        with self.lock:
            if vid not in self.jobs:
                raise ValueError("Export no longer exists. Start the export again.")
            return self.jobs[vid]

    def close(self):
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            job.finish(cancel=True)
        for job in jobs:
            job.complete.wait(12)
