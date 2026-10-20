"""A newly built Windows app must never display an older running app's UI."""
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serve


class OldRelease(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'{"error":"not found"}')


class DesktopStartupTests(unittest.TestCase):
    def test_old_release_stays_open_and_new_release_gets_its_own_server(self):
        old = ThreadingHTTPServer(("127.0.0.1", 0), OldRelease)
        thread = threading.Thread(target=old.serve_forever, daemon=True)
        thread.start()
        new = None
        new_started = False
        try:
            old_url = f"http://127.0.0.1:{old.server_port}"
            self.assertFalse(serve.matching_instance(old_url))
            new, url = serve.server_for_window(old.server_port)
            self.assertIsNotNone(new)
            self.assertNotEqual(new.server_port, old.server_port)
            threading.Thread(target=new.serve_forever, daemon=True).start()
            new_started = True
            self.assertTrue(serve.matching_instance(url.rstrip("/")))
            reused, reused_url = serve.server_for_window(old.server_port)
            self.assertIsNone(reused)
            self.assertEqual(reused_url, url)
            self.assertTrue(thread.is_alive())
        finally:
            if new:
                if new_started:
                    new.shutdown()
                new.server_close()
            old.shutdown()
            old.server_close()

    def test_different_version_is_not_reused(self):
        with patch.object(serve.urllib.request, "urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = json.dumps({**serve.instance_info(), "version": "0.1.1"}).encode()
            self.assertFalse(serve.matching_instance("http://127.0.0.1:27620"))

    def test_unrelated_startup_errors_are_not_hidden(self):
        with patch.object(serve, "StudioServer", side_effect=OSError(errno.EACCES, "denied")):
            with self.assertRaises(OSError):
                serve.server_for_window(27620)

    def test_earlier_preview_with_same_version_is_not_reused(self):
        with patch.object(serve.urllib.request, "urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = json.dumps({**serve.instance_info(), "ui": "region-settings"}).encode()
            self.assertFalse(serve.matching_instance("http://127.0.0.1:27620"))


if __name__ == "__main__":
    unittest.main()
