"""Export real clips; exercise lost acknowledgements, cancellation and failures."""
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_exports import ExportManager, ExportJob
import serve


def png_frame(index, width=32, height=24):
    image = Image.new("RGBA", (width, height))
    for y in range(height // 2):
        for x in range(width // 2):
            image.putpixel((x, y), (20 + index * 15, 80, 200, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = ExportManager()
        self.addCleanup(self.manager.close)

    def start(self, fmt="png", frames=8, transport="png", ffmpeg=None, vid=None):
        return self.manager.start(self.temp.name, self.temp.name, ffmpeg, 32, 24, 30, fmt,
                                  "test", frames, transport, vid)

    def send(self, job, index, data):
        until = time.monotonic() + 10
        while not job.frame(index, data)["accepted"]:
            self.assertLess(time.monotonic(), until, job.status())
            time.sleep(.01)

    def test_lost_start_and_frame_acknowledgements_do_not_duplicate_output(self):
        vid = "a" * 32
        self.assertEqual(self.start(vid=vid), self.start(vid=vid))
        job = self.manager.get(vid)
        for i in range(8):
            self.send(job, i, png_frame(i))
            self.send(job, i, png_frame(i))
        job.finish()
        job.finish()
        self.assertTrue(job.complete.wait(5))
        self.assertEqual(job.status()["state"], "done")
        paths = sorted(job.path.glob("*.png"))
        self.assertEqual(len(paths), 8)
        with Image.open(paths[-1]) as frame:
            self.assertEqual(frame.getpixel((0, 0)), (125, 80, 200, 255))
            self.assertEqual(frame.getpixel((31, 23))[3], 0)

    def test_incomplete_frames_and_bad_dimensions_are_rejected(self):
        job = self.manager.get(self.start())
        with self.assertRaisesRegex(ValueError, "missing frames"):
            job.finish()
        with self.assertRaisesRegex(ValueError, "out of order"):
            job.frame(1, png_frame(1))
        with self.assertRaisesRegex(ValueError, "wrong size"):
            job.frame(0, png_frame(0, 64))
        with self.assertRaisesRegex(ValueError, "already running"):
            self.start()

    def test_cancel_removes_partial_output_and_allows_next_export(self):
        job = self.manager.get(self.start())
        self.send(job, 0, png_frame(0))
        before = time.monotonic()
        job.finish(cancel=True)
        self.assertLess(time.monotonic() - before, .2)
        self.assertTrue(job.complete.wait(5))
        self.assertEqual(job.status()["state"], "cancelled")
        self.assertFalse(job.path.exists())
        self.start()

    def test_stall_times_out_and_keeps_diagnostic(self):
        with patch.object(ExportJob, "STALL_SECONDS", .1):
            job = self.manager.get(self.start())
            self.assertTrue(job.complete.wait(3))
        self.assertEqual(job.status()["state"], "error")
        self.assertIn("stopped making progress", job.status()["error"])
        self.assertFalse(self.manager.active())

    def test_blocked_encoder_keeps_status_and_cancel_responsive_with_bounded_queue(self):
        entered, killed = threading.Event(), threading.Event()

        class Pipe:
            def write(self, data):
                entered.set()
                if not killed.wait(5):
                    raise TimeoutError("Test encoder was not cancelled")
                raise BrokenPipeError()

            def close(self):
                pass

        class Encoder:
            stdin = Pipe()
            stdout = io.BytesIO()

            def poll(self):
                return -9 if killed.is_set() else None

            def kill(self):
                killed.set()

            def wait(self, timeout):
                if not killed.wait(timeout):
                    raise subprocess.TimeoutExpired("test encoder", timeout)
                return -9

        with patch("video_exports.subprocess.Popen", return_value=Encoder()), \
                patch.object(ExportJob, "_kill", lambda job: job.proc.kill()):
            job = self.manager.get(self.start("mp4", frames=3, ffmpeg="test"))
            self.send(job, 0, png_frame(0))
            self.assertTrue(entered.wait(2))
            self.send(job, 1, png_frame(1))
            self.send(job, 2, png_frame(2))
            self.assertEqual(job.queue.qsize(), 2)
            before = time.monotonic()
            job.finish()
            self.assertEqual(job.status()["state"], "encoding")
            job.finish(cancel=True)
            self.assertLess(time.monotonic() - before, .2)
            self.assertTrue(job.complete.wait(3))
            self.assertEqual(job.status()["state"], "cancelled")
            self.assertTrue(killed.is_set())

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_real_gif_mp4_webm_mov_frame_count_and_gif_transparency(self):
        ffmpeg = shutil.which("ffmpeg")
        for fmt in ("gif", "mp4", "webm", "mov"):
            with self.subTest(fmt=fmt):
                job = self.manager.get(self.start(fmt, ffmpeg=ffmpeg))
                for i in range(8):
                    self.send(job, i, png_frame(i))
                before = time.monotonic()
                job.finish()
                self.assertLess(time.monotonic() - before, .2, "finish must not wait for encoding")
                self.assertTrue(job.complete.wait(20), job.status())
                self.assertEqual(job.status()["state"], "done", job.status())
                result = subprocess.run([ffmpeg, "-v", "error", "-i", str(job.path),
                                         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                                        capture_output=True, timeout=20,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(result.stdout), 32 * 24 * 4 * 8)
                if fmt == "gif":
                    with Image.open(job.path) as gif:
                        self.assertEqual(gif.n_frames, 8)
                        self.assertEqual(gif.convert("RGBA").getpixel((31, 23))[3], 0)
                        self.assertEqual(gif.convert("RGBA").getpixel((0, 0))[3], 255)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_dead_encoder_reports_error_and_does_not_wedge_next_export(self):
        job = self.manager.get(self.start("mp4", ffmpeg=shutil.which("ffmpeg")))
        job._kill()
        self.assertTrue(job.complete.wait(10))
        self.assertEqual(job.status()["state"], "error")
        self.assertFalse(job.path.exists())
        self.start()

    def test_physics_retry_does_not_advance_simulation_twice(self):
        class Simulation:
            count = 0

            def step(self, raw):
                self.count += 1
                return bytes([self.count])

        sim = Simulation()
        with patch.dict(serve._springs, {"test": (sim, threading.Lock(), {})}):
            self.assertEqual(serve.spring_step("test", b"pose", "1"), b"\x01")
            self.assertEqual(serve.spring_step("test", b"pose", "1"), b"\x01")
            self.assertEqual(serve.spring_step("test", b"pose", "2"), b"\x02")
            self.assertEqual(sim.count, 2)


if __name__ == "__main__":
    unittest.main()
