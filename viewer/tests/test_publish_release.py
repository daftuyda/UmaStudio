from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_release as release

SHA = "a" * 40
REF = {"object": {"type": "commit", "sha": SHA}}


class PublishReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        self.notes = self.dist / "notes.md"
        for name in ("UmaStudio-0.1.2.zip", "UmaStudio-0.1.2.zip.sha256", "notes.md"):
            (self.dist / name).write_text("fixture")
        self.api = self.enterContext(patch.object(release, "api"))
        self.run = self.enterContext(patch.object(release.subprocess, "run"))

    def publish(self):
        return release.publish("owner/repo", "0.1.2", SHA, self.dist, self.notes)

    def test_new_tag_uses_tested_commit_and_publishes_only_after_upload(self):
        self.api.side_effect = [None, None, REF]
        self.assertEqual(self.publish(), "created")
        self.api.assert_called_with("owner/repo", "git/refs", {"ref": "refs/tags/v0.1.2", "sha": SHA})
        self.assertIn("--draft", self.run.call_args_list[0].args[0])
        self.assertIn("--verify-tag", self.run.call_args_list[0].args[0])
        self.assertIn("--draft=false", self.run.call_args_list[1].args[0])

    def test_existing_published_release_is_not_changed(self):
        self.api.return_value = {"draft": False}
        self.assertEqual(self.publish(), "published")
        self.api.assert_called_once()
        self.run.assert_not_called()

    def test_existing_tag_on_another_commit_is_not_moved_or_published(self):
        self.api.side_effect = [None, {"object": {"type": "commit", "sha": "b" * 40}}]
        self.assertEqual(self.publish(), "other-commit")
        self.assertEqual(self.api.call_count, 2)
        self.run.assert_not_called()

    def test_annotated_tag_at_tested_commit_can_publish(self):
        self.api.side_effect = [None, {"object": {"type": "tag", "sha": "c" * 40}}, REF]
        self.assertEqual(self.publish(), "created")
        self.assertEqual(self.api.call_count, 3)

    def test_interrupted_draft_upload_can_resume(self):
        self.api.side_effect = [{"draft": True}, REF]
        self.assertEqual(self.publish(), "created")
        self.assertEqual(self.run.call_args_list[0].args[0][1:3], ["release", "upload"])
        self.assertIn("--clobber", self.run.call_args_list[0].args[0])

    def test_failed_upload_does_not_publish(self):
        self.api.side_effect = [None, None, REF]
        self.run.side_effect = subprocess.CalledProcessError(1, "gh")
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.run.assert_called_once()

    def test_api_failure_does_not_create_a_tag(self):
        self.api.side_effect = RuntimeError("GitHub unavailable")
        with self.assertRaisesRegex(RuntimeError, "GitHub unavailable"):
            self.publish()
        self.api.assert_called_once()
        self.run.assert_not_called()
