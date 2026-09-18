"""Offline candidate, tail-only OCR and catalog edge-case regression checks."""
from concurrent.futures import CancelledError
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import av
import numpy as np
from PIL import Image, ImageDraw

from clips import validate_clip
from profiles import validate_profile
from recognition import analyze
from analysis_common import DEFAULTS
from jacket_recognition import _candidates, identify, load_catalog, phash
from text_recognition import read_ending
from video_sampling import samples


class RecognitionTests(unittest.TestCase):
    def test_boundary_buffer_matches_redecode_and_falls_back_at_memory_limit(self):
        import jacket_recognition as jackets
        from boundary_buffer import BoundaryBuffer
        path = self.video()
        outer = self
        for interval, limit in ((.5, None), (.37, None), (.5, 1)):
            profile = deepcopy(self.profile)
            profile["recognition"]["sample_seconds"] = interval
            class CheckedBuffer(BoundaryBuffer):
                def __init__(self, *args):
                    super().__init__(*args)
                    if limit is not None:
                        self.native_limit = limit
                def get(self, boundary):
                    rows = super().get(boundary)
                    if rows is not None:
                        expected = list(samples(path, max(.13, boundary-interval), boundary+.001, .1,
                                                outer.cancel, region=self.region, reference_size=self.reference_size))
                        signature = lambda rows: [(t, im.size, im.tobytes()) for t, im in rows]
                        outer.assertEqual(signature(rows), signature(expected))
                    return rows
            with self.subTest(interval=interval, limit=limit), patch("jacket_recognition.write_json"):
                with patch("jacket_recognition.BoundaryBuffer", return_value=None):
                    expected = jackets.detect(path, .13, 9.7, deepcopy(profile), self.root, self.cancel, lambda _: None)
                with patch("jacket_recognition.BoundaryBuffer", CheckedBuffer):
                    actual = jackets.detect(path, .13, 9.7, deepcopy(profile), self.root, self.cancel, lambda _: None)
                self.assertEqual(actual["candidates"], expected["candidates"])
                metric = "misses" if limit else "hits"
                self.assertGreater(actual["boundary_buffer"][metric], 0)

    def test_boundary_buffer_rejects_missing_sampling_phase_and_bounds_roi_memory(self):
        from boundary_buffer import BoundaryBuffer
        frame = av.VideoFrame.from_image(self.images[0])
        buffer = BoundaryBuffer(0, .1, [0, 0, 1, 1], [80, 80], self.cancel)
        buffer.matched(0, {"image_url": "a"})
        for t in (0, .04, .1, .14):
            buffer.observe(t, frame)
        buffer.matched(.14, {"image_url": "b"})
        self.assertIsNone(buffer.get(.14))  # .04 is required but was not retained.
        buffer.roi_limit = 1
        buffer.observe(.2, frame)
        buffer.matched(.2, {"image_url": "c"})
        self.assertIsNone(buffer.get(.2))
        self.assertLessEqual(buffer.roi_bytes, buffer.roi_limit)
        buffer.roi_limit = 80 * 80 * 3 * 2
        for t, song in ((.3, "d"), (.4, "e")):
            buffer.observe(t, frame)
            buffer.matched(t, {"image_url": song})
        self.assertIsNone(buffer.get(.3))  # The older ROI window was evicted.
        self.assertIsNotNone(buffer.get(.4))
        self.assertLessEqual(buffer.roi_bytes, buffer.roi_limit)
        buffer.finish_scan()
        self.assertEqual(buffer.native_bytes, 0)
        self.assertFalse(buffer.recent)

    def test_replaceable_recognizer_preserves_contract_and_rejects_bad_ranges(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        path = self.video()
        candidate = {"song_id": "audio:123", "image_url": None, "title": "Audio match", "start": 1, "end": 3}
        recognizer = SimpleNamespace(METHOD="audio-test", detect=Mock(return_value={"candidates": [candidate]}))
        with patch("jacket_recognition.detect", side_effect=AssertionError("wrong engine")):
            result = analyze(path, 0, 5, self.profile, self.root, self.cancel, lambda _: None, recognizer=recognizer)
        self.assertEqual(result["clips"][0]["song_id"], "audio:123")
        self.assertEqual(result["clips"][0]["analysis"]["method"], "audio-test")
        self.assertNotIn("analysis", candidate)
        recognizer.detect.return_value = {"candidates": [candidate, {**candidate, "start": 2, "end": 4}]}
        with self.assertRaises(ValueError):
            analyze(path, 0, 5, self.profile, self.root, self.cancel, lambda _: None, recognizer=recognizer)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cancel = threading.Event()
        self.profile = {"version": 1, "name": "test", "size": [160, 90],
                        "regions": {"jacket": [0, 0, .5, 8 / 9]},
                        "recognition": {"sample_seconds": .5, "stable_seconds": .5}}
        self.images = []
        for seed in range(3):
            small = Image.fromarray(np.random.default_rng(seed).integers(0, 255, (8, 8, 3), dtype=np.uint8))
            self.images.append(small.resize((80, 80), Image.Resampling.NEAREST))
        directory = self.root / "jackets"
        directory.mkdir()
        for index in range(2):
            self.images[index].save(directory / f"{index}.png")
        (directory / "_manifest.json").write_text(json.dumps([
            {"image_url": "0.png", "title": "A"}, {"image_url": "1.png", "title": "B"}]), encoding="utf-8")

    def video(self):
        path = self.root / "test.mp4"
        with av.open(str(path), "w") as container:
            stream = container.add_stream("libx264", rate=10)
            stream.width, stream.height = 160, 90
            stream.pix_fmt = "yuv420p"
            stream.codec_context.gop_size = 80
            for index in range(100):
                # A, unknown, repeated A, B; nonzero origin and non-keyframe boundaries.
                which = 0 if index < 23 or 42 <= index < 64 else (1 if index >= 64 else 2)
                image = Image.new("RGB", (160, 90), "white")
                image.paste(self.images[which], (0, 0))
                frame = av.VideoFrame.from_image(image)
                frame.pts, frame.time_base = 30 + index, Fraction(1, 10)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return path

    def test_real_video_candidates_refine_and_keep_unknown_repeated_songs(self):
        path = self.video()
        result = analyze(path, 0, 10, {**self.profile, "regions": {"jacket": [0, 0, 1, 1]}},
                         self.root, self.cancel, lambda _: None)
        self.assertEqual(result["warnings"], [])
        self.assertEqual([row["title"] for row in result["clips"]], ["A", "", "A", "B"])
        self.assertEqual(len(result["clips"]), 4)
        for row, expected in zip(result["clips"], [0, 2.3, 4.2, 6.4]):
            self.assertAlmostEqual(row["start"], expected, delta=.11)
            self.assertFalse(row["reviewed"])
            validate_clip(row)
        self.assertTrue(result["clips"][0]["partial_start"])
        self.assertTrue(result["clips"][-1]["partial_end"])
        self.cancel.set()
        result = analyze(path, 0, 10, self.profile, self.root, self.cancel, lambda _: None)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["clips"], [])

    def test_tail_ocr_uses_distinct_end_frames_and_preserves_conflicts(self):
        path = self.video()
        profile = {**self.profile, "regions": {**self.profile["regions"],
                   "player": [.5, 0, 1, .3], "achievement": [.5, .3, 1, .6]}}
        row = {"start": 0, "end": 6.4}
        calls = []
        def read(image):
            calls.append(image.size)
            return ("Player★", .95) if len(calls) % 2 else ("100.1234%", .99)
        result = read_ending(path, row, profile, read, self.cancel)
        self.assertEqual(result["player"], "Player★")
        self.assertEqual(result["achievement"], 100.1234)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(3.4 <= item["seconds"] < 6.4 for item in result["ocr_evidence"]))
        with patch("text_recognition.frame_at", return_value={"image": Image.new("RGB", (160, 90)), "seconds": 6.0}):
            result = read_ending(path, row, profile, read, self.cancel)
            self.assertNotIn("player", result)  # A repeated frame is not independent evidence.
        counter = iter([("Alice", .99), ("Bob", .99), ("Alice", .99)])
        result = read_ending(path, row, {**profile, "regions": {"player": [.5, 0, 1, .3]}},
                             lambda _: next(counter), self.cancel)
        self.assertNotIn("player", result)
        self.assertEqual(len(result["ocr_evidence"]), 3)

    def test_player_readings_a_glyph_apart_settle_on_the_frequent_spelling(self):
        from text_recognition import same_player
        for left, right in (("OH1", "0H1"), ("OH 1", "OH1"), ("BANGBOO", "BANGB00"), ("KAI☆1", "KAI☆I")):
            self.assertTrue(same_player(left, right), (left, right))
        for left, right in (("ALICE", "BOB"), ("AB", "AC"), ("A", "B")):
            self.assertFalse(same_player(left, right), (left, right))

        profile = {**self.profile, "regions": {"player": [0, 0, 1, 1]}}
        image = self.images[0]
        def ending(reads):
            values = iter(reads)
            with patch("text_recognition.frame_at",
                       side_effect=lambda path, seconds: {"seconds": seconds, "image": image}):
                return read_ending("unused", {"start": 0, "end": 10}, profile,
                                   lambda _: (next(values), .9), self.cancel)
        # The same player keeps playing, so a stray glyph settles on the frequent spelling.
        self.assertEqual(ending(["OH1", "0H1", "OH1"])["player"], "OH1")
        self.assertEqual(ending(["OH 1", "OH1", "0H1"])["player"], "OH1")
        # A name that is not close is a real conflict and still yields nothing.
        self.assertNotIn("player", ending(["ALICE", "ALICE", "BOB"]))
        # One reading on its own is not evidence, however it is spelled.
        self.assertNotIn("player", ending(["OH1", "", ""]))

    def test_achievement_tolerates_one_stray_glyph_from_the_percent_sign(self):
        profile = {**self.profile, "regions": {"achievement": [0, 0, 1, 1]}}
        image = self.images[0]
        # A region that includes '%' reads it as one extra glyph; the reading is still usable.
        for text, expected in (("100.9871%", 100.9871), ("100.98718", 100.9871),
                               ("100.9871×", 100.9871), ("99.5", 99.5)):
            values = iter([text] * 3)
            with patch("text_recognition.frame_at",
                       side_effect=lambda path, seconds: {"seconds": seconds, "image": image}):
                result = read_ending("unused", {"start": 0, "end": 10}, profile,
                                     lambda _: (next(values), .9), self.cancel)
            self.assertEqual(result.get("achievement"), expected, text)
        # Readings that are not a score, or drift further than one glyph, stay out.
        for text in ("abc", "1000.5", "100.98718888"):
            values = iter([text] * 3)
            with patch("text_recognition.frame_at",
                       side_effect=lambda path, seconds: {"seconds": seconds, "image": image}):
                result = read_ending("unused", {"start": 0, "end": 10}, profile,
                                     lambda _: (next(values), .9), self.cancel)
            self.assertNotIn("achievement", result, text)

    def test_catalog_single_tie_and_damage(self):
        catalog = load_catalog(self.root, self.cancel, lambda _: None)
        self.assertEqual(identify(self.images[0], catalog[:1], DEFAULTS)["title"], "A")
        self.assertIsNone(identify(self.images[0], [catalog[0], dict(catalog[0])], DEFAULTS)["image_url"])
        (self.root / "jackets/0.png").write_bytes(b"broken")
        self.assertEqual(len(load_catalog(self.root, self.cancel, lambda _: None)), 1)
        (self.root / "jackets/1.png").unlink()
        with self.assertRaises(ValueError):
            load_catalog(self.root, self.cancel, lambda _: None)

    def test_player_fullwidth_consensus_preserves_raw_evidence_and_real_conflicts(self):
        from text_recognition import normalize_player
        self.assertEqual(normalize_player(" ＯＨ　１ "), "OH1")
        self.assertEqual(normalize_player("한글 이름★"), "한글 이름★")
        profile = {**self.profile, "regions": {"player": [0, 0, 1, 1], "player2": [0, 0, 1, 1]}}
        originals = ["ＯＨ１", "ＡＬＩＣＥ", "OH 1", "ALICE", "OH1", "BOB"]
        reads = iter(originals)
        image = self.images[0]
        with patch("text_recognition.frame_at", side_effect=lambda path, seconds: {"seconds": seconds, "image": image}):
            result = read_ending("unused", {"start": 0, "end": 10}, profile,
                                 lambda _: (next(reads), .9), self.cancel)
        self.assertEqual(result["player"], "OH1")
        self.assertNotIn("player2", result)
        self.assertEqual([e["text"] for e in result["ocr_evidence"]], originals)

    def test_padded_offset_jacket_and_tracking_song_change(self):
        catalog = load_catalog(self.root, self.cancel, lambda _: None)
        region = Image.new("RGB", (120, 110), "#272727")
        region.paste(self.images[0], (25, 15))
        self.assertGreater((phash(region) ^ catalog[0]["hash"]).bit_count(), DEFAULTS["hash_distance"])
        found = identify(region, catalog, DEFAULTS, cancel=self.cancel)
        self.assertEqual(found["title"], "A")
        self.assertEqual(found["sub_rect"], [25, 15, 105, 95])
        region.paste(self.images[1], (25, 15))
        with patch("jacket_recognition.phash", wraps=phash) as hash_image:
            changed = identify(region, catalog, DEFAULTS, previous=found, cancel=self.cancel)
            self.assertEqual(changed["title"], "B")
            self.assertEqual(hash_image.call_count, 1)
        self.cancel.set()
        with self.assertRaises(CancelledError):
            identify(region, catalog, DEFAULTS, cancel=self.cancel)

    def test_scan_recovers_overcropped_calibration_in_serial_and_parallel(self):
        from jacket_recognition import detect
        path = self.video()
        results = []
        for workers in (1, 2):
            with patch("jacket_recognition.calibrate_locations", return_value=[(0, [8, 8, 72, 72])]), \
                    patch("jacket_recognition.write_json"):
                result = detect(path, 0, 10, deepcopy(self.profile), self.root, self.cancel,
                                lambda _: None, workers=workers)
            self.assertEqual([r["title"] for r in result["candidates"]], ["A", "", "A", "B"])
            for row, expected in zip(result["candidates"], (0, 2.3, 4.2, 6.4)):
                self.assertAlmostEqual(row["start"], expected, delta=.11)
            results.append(result["candidates"])
        self.assertEqual(results[0], results[1])
        catalog = load_catalog(self.root, self.cancel, lambda _: None)
        with patch("jacket_recognition.phash", wraps=phash) as hashes:
            result = identify(self.images[2], catalog, DEFAULTS, {"sub_rect": [8, 8, 72, 72]}, local_only=True)
        self.assertIsNone(result["image_url"])
        self.assertLessEqual(hashes.call_count, 17)  # No exhaustive search on blank/unknown scenes.

    def test_achievement_survives_conflicting_player_names(self):
        readings = iter([("Alice", .99), ("99.1234%", .99), ("Bob", .99),
                         ("99.1234%", .99), ("Alice", .99), ("99.1234%", .99)])
        profile = {**self.profile, "regions": {"player": [0, 0, 1, 1], "achievement": [0, 0, 1, 1]}}
        with patch("text_recognition.frame_at", side_effect=lambda path, t: {"seconds": t, "image": self.images[0]}):
            result = read_ending("unused", {"start": 0, "end": 10}, profile,
                                 lambda _: next(readings), self.cancel)
        self.assertNotIn("player", result)
        self.assertEqual(result["achievement"], 99.1234)

    def test_parallel_scan_matches_serial_and_cancels(self):
        from jacket_recognition import scan_jackets
        catalog = load_catalog(self.root, self.cancel, lambda _: None)
        frames = [(i / 10, self.images[(i // 7) % 2]) for i in range(40)]
        with self.assertLogs(level="INFO") as logs:
            serial = scan_jackets(frames, catalog, DEFAULTS, self.cancel, 1, lambda _: None)
        self.assertIn("result_wait=", "\n".join(logs.output))
        self.assertIn("decode=", "\n".join(logs.output))
        parallel = scan_jackets(frames, catalog, DEFAULTS, self.cancel, 2, lambda _: None)
        self.assertEqual(serial, parallel)
        def progress(text):
            if isinstance(text, dict) and text.get("done", 0) > 0:
                self.cancel.set()
        with self.assertRaises(CancelledError):
            scan_jackets(frames * 3, catalog, DEFAULTS, self.cancel, 2, progress)
        import multiprocessing
        self.assertEqual(multiprocessing.active_children(), [])

    def test_sparse_calibration_and_global_boundary_progress(self):
        path = self.video()
        reports = []
        profile = {**self.profile, "regions": {"jacket": [0, 0, 1, 1]}}
        with patch("jacket_recognition.identify", wraps=identify) as matches:
            result = analyze(path, 0, 10, profile, self.root, self.cancel, reports.append, workers=1)
        searches = [call for call in matches.call_args_list
                    if call.kwargs.get("search", True) and not call.kwargs.get("local_only", False)]
        self.assertEqual(len(searches), 1)
        self.assertGreater(len(matches.call_args_list), len(searches))
        self.assertEqual([row["title"] for row in result["clips"]], ["A", "", "A", "B"])
        events = [item for item in reports if isinstance(item, dict)]
        self.assertEqual(set(item["step"] for item in events), {1, 2, 3, 4, 5})
        self.assertEqual(events[-1]["done"], events[-1]["total"])
        parallel = analyze(path, 0, 10, profile, self.root, self.cancel, lambda _: None, workers=2)
        self.assertEqual(parallel["clips"], result["clips"])
        for left, right in zip(result["clips"], result["clips"][1:]):
            self.assertEqual(left["end"], right["start"])

    def test_unknown_fixed_crop_never_searches(self):
        from jacket_recognition import scan_jackets
        catalog = load_catalog(self.root, self.cancel, lambda _: None)
        with patch("jacket_recognition.phash", wraps=phash) as hashes:
            rows = scan_jackets([(i, self.images[2]) for i in range(50)], catalog, DEFAULTS,
                                self.cancel, 1, lambda _: None)
        self.assertEqual(hashes.call_count, 50)
        self.assertTrue(all(row[1]["image_url"] is None for row in rows))

    def test_strong_location_stops_and_cached_location_is_verified(self):
        from jacket_recognition import calibrate_locations
        from profiles import offset_path, load_profile
        catalog = load_catalog(self.root, self.cancel, lambda _: None)
        profile = {**self.profile}
        with patch("jacket_recognition.frame_at", return_value={"image": self.images[0].resize((160, 90))}) as frame:
            # Use an exact square image/ROI to make the early-stop threshold unambiguous.
            profile = {"version": 1, "name": "cache", "size": [80,80], "regions": {"jacket": [0,0,1,1]}}
            frame.return_value = {"image": self.images[0]}
            anchors = calibrate_locations("unused", 0, 600, profile, catalog, DEFAULTS, self.cancel, lambda _: None)
            self.assertEqual(frame.call_count, 1)
            self.assertEqual(anchors, [(0, [0,0,80,80])])
            self.assertIn("jacket_offset", profile)
            with patch("jacket_recognition.identify", wraps=identify) as match:
                calibrate_locations("unused", 0, 600, profile, catalog, DEFAULTS, self.cancel, lambda _: None)
            self.assertEqual(match.call_count, 1)
            self.assertFalse(match.call_args.kwargs["search"])
        path = self.video()
        result = analyze(path, 0, 10, self.profile, self.root, self.cancel, lambda _: None)
        saved = load_profile(offset_path(self.root, result["profile"]))
        self.assertEqual(saved["jacket_offset"], result["profile"]["jacket_offset"])
        changed = {**saved, "regions": {"jacket": [0,0,1,1]}}
        self.assertNotIn("jacket_offset", validate_profile(changed))

    def test_analysis_end_is_clamped_to_video_duration(self):
        path = self.video()
        expected = [seconds for seconds, _ in samples(path, 0, 10, .5, self.cancel)]
        for end in (10.01, 15):
            self.assertEqual([seconds for seconds, _ in samples(path, 0, end, .5, self.cancel)], expected)
        result = analyze(path, 0, 10.01, self.profile, self.root, self.cancel, lambda _: None)
        self.assertTrue(result['clips'])
        self.assertEqual(result['clips'][-1]['end'], 10)
        self.assertTrue(all(row['analysis']['range'] == [0, 10] for row in result['clips']))
        with self.assertRaises(ValueError):
            analyze(path, 10, 11, self.profile, self.root, self.cancel, lambda _: None)

    def test_parallel_decoder_matches_pts_pixels_and_cancels(self):
        path = self.video()
        def signature(rows):
            return [(seconds, image.tobytes()) for seconds, image in rows]
        baseline = signature(samples(path, .3, 9.7, .37, self.cancel, decoder_threads=1))
        metrics = {"decode_s": 0., "convert_s": 0., "decoded_frames": 0, "sample_frames": 0}
        parallel = signature(samples(path, .3, 9.7, .37, self.cancel, decoder_threads=8, metrics=metrics))
        self.assertEqual(baseline, parallel)
        self.assertEqual(metrics["sample_frames"], len(parallel))
        self.assertGreater(metrics["decoded_frames"], len(parallel))
        iterator = samples(path, 0, 10, .5, self.cancel, decoder_threads=8)
        next(iterator)
        self.cancel.set()
        with self.assertRaises(CancelledError):
            next(iterator)
        self.cancel.clear()
        iterator = samples(path, 0, 10, .5, self.cancel, decoder_threads=8)
        next(iterator)
        iterator.close()

    def test_parallel_ocr_orders_results_errors_and_cancel(self):
        from text_recognition import ocr_clips
        import threading
        profile = {**self.profile, "regions": {**self.profile["regions"], "player": [.5, 0, 1, .3]}}
        rows = [{"start": i, "end": i + 1} for i in range(6)]
        warnings, reports, threads = [], [], set()
        barrier = threading.Barrier(3)
        def ending(path, row, profile, read, cancel):
            threads.add(threading.get_ident())
            if row["start"] < 3:
                barrier.wait(timeout=5)
            if row["start"] == 4:
                raise ValueError("bad frame")
            return {"player": str(row["start"])}
        with patch("text_recognition.os.cpu_count", return_value=6), patch("text_recognition.make_ocr", return_value=lambda _: None), patch("text_recognition.read_ending", side_effect=ending):
            ocr_clips("unused", rows, profile, self.root, self.cancel, reports.append, warnings)
        self.assertEqual(len(threads), 3)
        self.assertEqual([row["start"] for row in rows], list(range(6)))
        self.assertEqual(rows[5]["player"], "5")
        self.assertIn("ocr_error", rows[4])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(reports[-1]["done"], 6)
        self.cancel.set()
        with patch("text_recognition.make_ocr", return_value=lambda _: None):
            with self.assertRaises(CancelledError):
                ocr_clips("unused", rows, profile, self.root, self.cancel, reports.append, [], 3)
        self.assertFalse(any(t.name.startswith("clip-ocr") for t in threading.enumerate()))

    def test_gpu_failure_resumes_without_duplicate_samples(self):
        from video_sampling import analysis_samples
        warnings = []
        def decode(path, start, end, interval, cancel, **kwargs):
            if kwargs.get("gpu"):
                yield 0., self.images[0]
                raise RuntimeError("device unavailable")
            self.assertEqual(start, 1.)
            yield 1., self.images[1]
        with patch("video_sampling.samples", side_effect=decode):
            rows = list(analysis_samples("unused", 0, 2, 1, self.cancel, 2, {}, True, lambda _: None, warnings))
        self.assertEqual([r[0] for r in rows], [0, 1])
        self.assertEqual(len(warnings), 1)


    def test_profile_options_and_time_based_flicker(self):
        for options in ({"sample_seconds": 0}, {"stable_seconds": float("nan")}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                validate_profile({**self.profile, "recognition": options})
        # Profiles shared while the reader still had a language setting stay loadable.
        shared = validate_profile({**self.profile, "recognition": {"ocr_language": "korean"}})
        self.assertEqual(shared["recognition"], {})
        def row(key):
            return {"image_url": key, "title": key}
        result = _candidates([(0, row("A")), (.1, row("B")), (.2, row("A")),
                              (5, row("B")), (6, row("B"))], 0, 8, 1)
        self.assertEqual([(r["title"], r["start"], r["end"]) for r in result], [("A", 0, 5), ("B", 5, 8)])

    def test_ocr_failure_preserves_candidates(self):
        path = self.video()
        profile = {**self.profile, "regions": {**self.profile["regions"], "player": [.5, 0, 1, .3]}}
        with patch("text_recognition.make_ocr", side_effect=RuntimeError("model unavailable")):
            result = analyze(path, 0, 10, profile, self.root, self.cancel, lambda _: None)
        self.assertEqual(len(result["clips"]), 4)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(all(not row["reviewed"] for row in result["clips"]))


if __name__ == "__main__":
    unittest.main()
