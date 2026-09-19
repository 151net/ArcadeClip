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

    def run_download(self, cancel=None, report=lambda message: None, source=None, workers=3):
        return jackets.download_jackets(self.directory, cancel or threading.Event(), report,
                                        source, workers)

    def test_catalog_summary_counts_shared_jackets_once(self):
        self.run_download()
        folder = self.directory / "jackets" / jackets.OFFICIAL_SOURCE
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
        (self.directory / "jackets" / jackets.OFFICIAL_SOURCE / "a.png").write_bytes(b"damaged")
        self.assertFalse(jackets.catalog_status(self.directory))
        self.assertEqual(self.run_download()["downloaded"], 1)

    def test_cancel_retains_completed_files_and_resumes(self):
        self.songs = [{"image_url": f"{n:02d}.png", "title": f"곡 {n}"} for n in range(12)]
        cancel = threading.Event()
        def report(message):
            if message.startswith("자켓 다운로드 1 "):
                cancel.set()
        result = self.run_download(cancel, report)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["complete"])
        kept = sorted(path.name for path in (self.directory / "jackets" / jackets.OFFICIAL_SOURCE).glob("*.png"))
        self.assertTrue(kept, "a finished jacket must survive cancellation")
        self.assertLess(len(kept), len(self.songs))
        self.assertEqual(list((self.directory / "jackets" / jackets.OFFICIAL_SOURCE).glob("*.part")), [])
        self.assertFalse(jackets.catalog_status(self.directory))
        result = self.run_download()
        self.assertEqual(result["skipped"], len(kept))
        self.assertEqual(result["downloaded"], len(self.songs) - len(kept))
        self.assertTrue(result["complete"])

    def test_bad_manifest_preserves_existing_catalog_and_blocks_paths(self):
        self.run_download()
        manifest = self.directory / "jackets" / jackets.OFFICIAL_SOURCE / "_manifest.json"
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
        saved = json.loads((self.directory / "jackets" / jackets.OFFICIAL_SOURCE / "_manifest.json").read_bytes())
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
        self.assertFalse((self.directory / "jackets" / jackets.OFFICIAL_SOURCE / "a.png").exists())
        self.assertEqual(list(self.directory.rglob("*.part")), [])
        self.bodies.clear()
        self.assertTrue(self.run_download()["complete"])

    def test_http_not_found_is_reported_without_retrying(self):
        url = jackets.IMAGE_BASE + "b.png"
        self.bodies[url] = urllib.error.HTTPError(url, 404, "missing", {}, None)
        result = self.run_download()
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(self.calls.count(url), 1)

    def test_official_servers_are_fetched_without_certificate_checks(self):
        original = self.respond
        contexts = []
        def respond(request, **kwargs):
            context = kwargs["context"]
            contexts.append((request.full_url, context.check_hostname, context.verify_mode))
            if context is None or context.verify_mode == ssl.CERT_REQUIRED:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("missing issuer"))
            return original(request, **kwargs)
        with patch("jackets.urllib.request.urlopen", side_effect=respond):
            self.assertTrue(self.run_download()["complete"])
        self.assertEqual({(check, mode) for _, check, mode in contexts}, {(False, ssl.CERT_NONE)})
        self.assertEqual(sorted(url for url, _, _ in contexts),
                         sorted([jackets.MANIFEST_URL, jackets.IMAGE_BASE + "a.png",
                                 jackets.IMAGE_BASE + "b.png"]))

    def test_another_server_is_used_and_its_certificate_checked(self):
        source = {"manifest": "https://mirror.example/songs.json", "images": "https://mirror.example/cover"}
        self.bodies[source["manifest"]] = json.dumps(self.songs).encode()
        contexts = []
        original = self.respond
        def respond(request, **kwargs):
            contexts.append(kwargs["context"])
            return original(request, **kwargs)
        with patch("jackets.urllib.request.urlopen", side_effect=respond):
            self.assertTrue(self.run_download(source=source)["complete"])
        self.assertEqual(contexts, [None, None, None])
        self.assertEqual(sorted(self.calls), sorted([source["manifest"],
                                                     "https://mirror.example/cover/a.png",
                                                     "https://mirror.example/cover/b.png"]))

    def test_addresses_decide_whether_certificates_are_checked(self):
        official = jackets.normalize_source(None)
        self.assertEqual((official["manifest"], official["images"]),
                         (jackets.MANIFEST_URL, jackets.IMAGE_BASE))
        self.assertFalse(official["verify"])
        mirror = jackets.normalize_source({"images": "https://mirror.example/cover"})
        self.assertEqual(mirror["images"], "https://mirror.example/cover/")
        self.assertTrue(mirror["verify"])
        self.assertFalse(jackets.normalize_source(
            {"images": "https://mirror.example/cover/", "verify": False})["verify"])
        self.assertTrue(jackets.normalize_source({"verify": True})["verify"])
        for bad in ("ftp://mirror.example/cover/", "mirror.example/cover/", "javascript:alert(1)"):
            with self.subTest(address=bad), self.assertRaises(ValueError):
                jackets.normalize_source({"images": bad})

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

    def test_servers_keep_their_own_folder_and_a_legacy_one_is_moved(self):
        root = self.directory / "jackets"
        root.mkdir()
        (root / "a.png").write_bytes(self.png)
        (root / "_manifest.json").write_bytes(json.dumps(self.songs).encode())
        self.assertEqual(jackets.migrate_legacy(self.directory), jackets.OFFICIAL_SOURCE)
        self.assertTrue((root / jackets.OFFICIAL_SOURCE / "a.png").is_file())
        self.assertFalse((root / "a.png").exists())
        self.assertEqual(jackets.installed_sources(self.directory), [jackets.OFFICIAL_SOURCE])

        mirror = {"manifest": "https://mirror.example/songs.json", "images": "https://mirror.example/cover/"}
        self.bodies[mirror["manifest"]] = json.dumps(self.songs).encode()
        self.assertEqual(self.run_download(source=mirror)["source"], "mirror.example")
        self.assertEqual(jackets.installed_sources(self.directory),
                         [jackets.OFFICIAL_SOURCE, "mirror.example"])
        self.assertTrue((root / "mirror.example/b.png").is_file())

    def test_one_server_is_used_by_default_and_a_choice_is_kept(self):
        mirror = {"manifest": "https://mirror.example/songs.json", "images": "https://mirror.example/cover/"}
        self.bodies[mirror["manifest"]] = json.dumps(self.songs).encode()
        self.run_download(source=mirror)
        # Only one server is installed, so it is the one used without being chosen.
        self.assertEqual(jackets.selected_source(self.directory), "mirror.example")
        self.run_download()
        # With several, the official one wins until the reader says otherwise.
        self.assertEqual(jackets.selected_source(self.directory), jackets.OFFICIAL_SOURCE)
        jackets.select_source(self.directory, "mirror.example")
        self.assertEqual(jackets.selected_source(self.directory), "mirror.example")
        self.assertEqual([f.name for f in jackets.catalog_folders(self.directory)], ["mirror.example"])
        jackets.select_source(self.directory, jackets.ALL_SOURCES)
        self.assertEqual([f.name for f in jackets.catalog_folders(self.directory)],
                         [jackets.OFFICIAL_SOURCE, "mirror.example"])
        # A folder that disappeared must not keep the recognizer pointed at nothing.
        jackets.select_source(self.directory, "gone.example")
        self.assertEqual(jackets.selected_source(self.directory), jackets.OFFICIAL_SOURCE)

    def test_counts_and_status_follow_the_selection(self):
        mirror = {"manifest": "https://mirror.example/songs.json", "images": "https://mirror.example/cover/"}
        self.bodies[mirror["manifest"]] = json.dumps(self.songs).encode()
        self.run_download()
        self.run_download(source=mirror)
        quiet = threading.Event()
        jackets.select_source(self.directory, jackets.OFFICIAL_SOURCE)
        songs, files, size = jackets.catalog_summary(self.directory, quiet, lambda _: None)
        self.assertEqual((songs, files), (3, 2))
        jackets.select_source(self.directory, jackets.ALL_SOURCES)
        both_songs, both_files, both_size = jackets.catalog_summary(self.directory, quiet, lambda _: None)
        # The same songs on two servers stay one list of songs but two sets of files.
        self.assertEqual((both_songs, both_files), (3, 4))
        self.assertGreater(both_size, size)
        self.assertTrue(jackets.catalog_status(self.directory))
        (self.directory / "jackets/mirror.example/a.png").write_bytes(b"damaged")
        self.assertFalse(jackets.catalog_status(self.directory))
        jackets.select_source(self.directory, jackets.OFFICIAL_SOURCE)
        self.assertTrue(jackets.catalog_status(self.directory))

    def test_cancel_before_start_and_size_limit(self):
        cancel = threading.Event()
        cancel.set()
        self.assertTrue(self.run_download(cancel)["cancelled"])
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            jackets._fetch(jackets.MANIFEST_URL, threading.Event(), 2)


if __name__ == "__main__":
    unittest.main()
