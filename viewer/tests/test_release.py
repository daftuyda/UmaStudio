import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_release


class ReleaseTests(unittest.TestCase):
    def test_tag_must_match_app_version_without_importing_the_app(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "serve.py"
            source.write_text('raise RuntimeError("must not execute")\nVERSION = "0.1.2"\n')
            self.assertEqual(package_release.release_version(source, "v0.1.2"), "0.1.2")
            with self.assertRaisesRegex(ValueError, "does not match"):
                package_release.release_version(source, "v0.1.3")

    def test_download_contains_runtime_license_notes_and_valid_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            app = repo / "dist/UmaStudio"
            files = ("UmaStudio.exe", "_internal/static/index.html", "_internal/static/settings.js",
                     "_internal/static/surface-collision.js", "_internal/python313.dll")
            for relative in files:
                path = app / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            (repo / "LICENSE").write_text("license")
            (repo / "README.md").write_text("readme")
            (repo / "docs/releases").mkdir(parents=True)
            (repo / "docs/releases/0.1.2.md").write_text("release notes")
            (repo / "dist/local-cache.dat").write_bytes(b"must not ship")
            archive, checksum = package_release.package(repo / "dist", "0.1.2", repo)
            with ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                self.assertEqual(bundle.read("UmaStudio/RELEASE_NOTES.md"), b"release notes")
                self.assertIn("UmaStudio/_internal/python313.dll", bundle.namelist())
                self.assertTrue(all(name.startswith("UmaStudio/") for name in bundle.namelist()))
                self.assertNotIn("local-cache.dat", bundle.namelist())
            self.assertEqual(checksum.read_text(),
                             f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")

    def test_incomplete_build_is_not_packaged(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FileNotFoundError, "Incomplete Windows build"):
                package_release.package(Path(temp), "0.1.2")
