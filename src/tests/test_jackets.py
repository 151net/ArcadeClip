import io
import json
import ssl
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import jackets


class JacketTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.songs = [{"image_url": "a.png", "title": "곡 A"},
                      {"image_url": "b.png", "title": "曲 B"},
                      {"image_url": "a.png", "title": "곡 A DX"}]
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(image, format="PNG")
        self.png = image.getvalue()
        self.calls = []
        self.bodies = {}
        self.http = patch("jackets.urllib.request.urlopen", side_effect=self.respond)
        self.http.start()
        self.addCleanup(self.http.stop)
        interval = patch("jackets.REQUEST_INTERVAL", 0)
        interval.start()
        self.addCleanup(interval.stop)

    def respond(self, request, **kwargs):
        url = request.full_url
        self.calls.append(url)
        body = self.bodies.get(url, json.dumps(self.songs).encode() if url == jackets.MANIFEST_URL else self.png)
        if isinstance(body, Exception):
            raise body
        response = io.BytesIO(body)
        response.geturl = lambda: url
        return response

    def run_download(self, cancel=None, report=lambda message: None):
        return jackets.download_jackets(self.directory, cancel or threading.Event(), report)

    def test_catalog_summary_counts_shared_jackets_once(self):
        self.run_download()
        folder = self.directory / "jackets"
        expected_bytes = sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
        self.assertEqual(jackets.catalog_summary(self.directory, threading.Event(), lambda _: None),
                         (3, 2, expected_bytes))

    def test_download_deduplicates_verifies_and_skips_existing(self):
        result = self.run_download()
        self.assertEqual((result["total"], result["downloaded"], result["complete"]), (2, 2, True))
        self.assertTrue(jackets.catalog_status(self.directory))
        self.calls.clear()
        result = self.run_download()
        self.assertEqual((result["downloaded"], result["skipped"]), (0, 2))
        self.assertEqual(self.calls, [jackets.MANIFEST_URL])
        (self.directory / "jackets/a.png").write_bytes(b"damaged")
        self.assertFalse(jackets.catalog_status(self.directory))
        self.assertEqual(self.run_download()["downloaded"], 1)

    def test_cancel_retains_completed_file_and_resumes(self):
        cancel = threading.Event()
        def report(message):
            if message.startswith("자켓 다운로드 1"):
                cancel.set()
        result = self.run_download(cancel, report)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["complete"])
        self.assertTrue((self.directory / "jackets/a.png").is_file())
        self.assertFalse((self.directory / "jackets/b.png").exists())
        self.assertFalse(jackets.catalog_status(self.directory))
        result = self.run_download()
        self.assertEqual((result["downloaded"], result["skipped"], result["complete"]), (1, 1, True))

    def test_bad_manifest_preserves_existing_catalog_and_blocks_paths(self):
        self.run_download()
        manifest = self.directory / "jackets/_manifest.json"
        before = manifest.read_bytes()
        for bad in ([], [{"image_url": "../escape.png", "title": "x"}],
                    [{"image_url": "a.png", "title": None}],
                    [{"image_url": "a.png"}], [{"image_url": "a.png", "title": 123}]):
            self.bodies[jackets.MANIFEST_URL] = json.dumps(bad).encode()
            with self.assertRaises(ValueError):
                self.run_download()
            self.assertEqual(manifest.read_bytes(), before)

    def test_blank_official_titles_are_preserved_and_resume(self):
        self.songs = [{"image_url": "5bb69b619f266d0a.png", "title": "\u3000", "artist": "x0o0x_"},
                      {"image_url": "empty.png", "title": ""}]
        result = self.run_download()
        self.assertTrue(result["complete"])
        self.assertEqual(result["downloaded"], 2)
        saved = json.loads((self.directory / "jackets/_manifest.json").read_bytes())
        self.assertEqual(saved, self.songs)
        self.assertTrue(jackets.catalog_status(self.directory))
        self.calls.clear()
        result = self.run_download()
        self.assertEqual((result["downloaded"], result["skipped"], result["complete"]), (0, 2, True))
        self.assertEqual(self.calls, [jackets.MANIFEST_URL])

    def test_failed_images_are_not_committed_and_can_be_retried(self):
        self.bodies[jackets.IMAGE_BASE + "a.png"] = b"<html>not an image</html>"
        result = self.run_download()
        self.assertFalse(result["complete"])
        self.assertEqual(result["failed"][0]["name"], "a.png")
        self.assertFalse((self.directory / "jackets/a.png").exists())
        self.assertEqual(list(self.directory.rglob("*.part")), [])
        self.bodies.clear()
        self.assertTrue(self.run_download()["complete"])

    def test_http_not_found_is_reported_without_retrying(self):
        url = jackets.IMAGE_BASE + "b.png"
        self.bodies[url] = urllib.error.HTTPError(url, 404, "missing", {}, None)
        result = self.run_download()
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(self.calls.count(url), 1)

    def test_images_use_relaxed_tls_once_per_jacket(self):
        original = self.respond
        contexts = []
        def respond(request, **kwargs):
            if request.full_url == jackets.MANIFEST_URL:
                self.assertIsNone(kwargs["context"])
            else:
                context = kwargs["context"]
                contexts.append((context.check_hostname, context.verify_mode))
                if context.verify_mode == ssl.CERT_REQUIRED:
                    raise urllib.error.URLError(ssl.SSLCertVerificationError("missing issuer"))
            return original(request, **kwargs)
        with patch("jackets.urllib.request.urlopen", side_effect=respond):
            self.assertTrue(self.run_download()["complete"])
        self.assertEqual(contexts, [(False, ssl.CERT_NONE), (False, ssl.CERT_NONE)])
        self.assertEqual(self.calls, [jackets.MANIFEST_URL, jackets.IMAGE_BASE + "a.png",
                                      jackets.IMAGE_BASE + "b.png"])

    def test_transient_image_errors_are_requested_only_once(self):
        url = jackets.IMAGE_BASE + "a.png"
        for error in (urllib.error.HTTPError(url, 503, "busy", {}, None),
                      urllib.error.URLError("connection reset"), TimeoutError()):
            with self.subTest(error=error):
                self.calls.clear()
                self.bodies[url] = error
                result = self.run_download()
                self.assertEqual(self.calls.count(url), 1)
                self.assertEqual(len(result["failed"]), 1)
                self.assertFalse(result["complete"])

    def test_cancel_before_start_and_size_limit(self):
        cancel = threading.Event()
        cancel.set()
        self.assertTrue(self.run_download(cancel)["cancelled"])
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            jackets._fetch(jackets.MANIFEST_URL, threading.Event(), 2)


if __name__ == "__main__":
    unittest.main()
