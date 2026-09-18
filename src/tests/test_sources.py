"""Offline range download regression checks."""
import json
from pathlib import Path
import tempfile
import threading
import sys
import time
import unittest
from unittest.mock import patch
import sources


class SourceTests(unittest.TestCase):
    def test_ffmpeg_remaining_time_handles_start_progress_and_completion(self):
        messages = []
        with patch('yt_dlp.utils.progress.time.monotonic', return_value=110):
            receive = sources.download_progress(100, messages.append, step=3)
            receive('out_time_us=0')
            receive('out_time_us=20000000')
            receive('out_time_us=100000000')
        progress = [v for v in messages if isinstance(v, dict)]
        self.assertEqual([v['eta_seconds'] for v in progress], [None, None, 0])
        self.assertTrue(all(v['step'] == 3 for v in progress))

    def test_eta_excludes_startup_and_tracks_speed_changes_and_stalls(self):
        with patch('yt_dlp.utils.progress.time.monotonic') as clock:
            estimate = sources.remaining_time(1000)
            clock.return_value = 500  # A long metadata/connection wait must not inflate ETA.
            self.assertIsNone(estimate(10))
            clock.return_value = 502
            self.assertIsNone(estimate(30))
            clock.return_value = 504
            self.assertAlmostEqual(estimate(50), 95)
            for second in range(505, 516):
                clock.return_value = second
                completed = 50 + (second - 504) * 20
                eta = estimate(completed)
            self.assertAlmostEqual(eta, (1000 - completed) / 20)
            for second in range(516, 527):
                clock.return_value = second
                completed = 270 + (second - 515) * 5
                eta = estimate(completed)
            self.assertAlmostEqual(eta, (1000 - completed) / 5)
            clock.return_value = 540  # No recent progress: unknown rather than stale optimism.
            self.assertIsNone(estimate(completed))
            clock.return_value = 541
            self.assertIsNone(estimate(2))  # A restarted stream needs fresh samples.
            self.assertEqual(estimate(1000), 0)

    def test_streamed_download_output_and_progress(self):
        messages = []
        began = time.monotonic()
        received = []
        def report(value):
            received.append(time.monotonic() - began)
            messages.append(value)
        script = "import sys,time; print('download starting',flush=True); print('out_time_us=5000000',file=sys.stderr,flush=True); time.sleep(.5); print('{}')"
        output = sources.run_command([sys.executable, "-c", script], threading.Event(), report,
                                     on_line=sources.download_progress(10, report))
        self.assertIn("{}", output)
        self.assertIn("download starting", messages)
        self.assertNotIn("{}", messages)
        self.assertTrue(any(isinstance(v, dict) and v["percent"] == 50 for v in messages))
        self.assertLess(min(received), .5)

    def test_video_metadata_dates(self):
        from datetime import datetime
        data = dict(id="abcdefghijk", title="title", uploader="channel", duration=100,
                    upload_date="20260915", was_live=False)
        with patch.object(sources, "_command", return_value=["yt-dlp"]), patch.object(sources, "run_command") as run:
            run.return_value = json.dumps(data)
            result = sources.inspect_source("https://youtu.be/abcdefghijk", threading.Event(), lambda _: None)
            self.assertEqual(result["video_date"], "2026-09-15")
            data.update(was_live=True, release_timestamp=1789477200, timestamp=1789500000)
            run.return_value = json.dumps(data)
            result = sources.inspect_source("https://youtu.be/abcdefghijk", threading.Event(), lambda _: None)
            self.assertEqual(result["video_date"], datetime.fromtimestamp(data["release_timestamp"]).astimezone().strftime("%Y-%m-%d %H시"))

    def test_bundled_runtime_without_system_path(self):
        with patch.object(sources.shutil, "which", return_value=None):
            runtime = sources.find_tool("deno")
            result = sources.run_command([runtime, "eval", "console.log(6 * 7)"], threading.Event(), lambda _: None)
            self.assertEqual(result.strip(), "42")
            command = sources._command()
            self.assertEqual(command[command.index("--js-runtimes") + 1], "deno:" + runtime)

    def test_urls(self):
        canonical = "https://www.youtube.com/watch?v=abcdefghijk"
        for value in (canonical + "&list=secret", "https://youtu.be/abcdefghijk?t=12",
                      "https://m.youtube.com/live/abcdefghijk"):
            self.assertEqual(sources.youtube_url(value), canonical)
        for value in ("https://youtube.com.evil/watch?v=abcdefghijk",
                      "https://user@youtube.com/watch?v=abcdefghijk", "file:///tmp/a",
                      "https://youtube.com/playlist?list=x", "https://youtu.be/a/b"):
            with self.assertRaises(ValueError):
                sources.youtube_url(value)

    def test_bounded_success_and_cleanup(self):
        metadata = dict(url="https://www.youtube.com/watch?v=abcdefghijk", id="abcdefghijk",
                        title="song", duration=200, live_status="not_live", is_live=False)
        def download(args, *_, **kwargs):
            self.assertEqual(args[args.index("--download-sections") + 1], "*10-20")
            self.assertIn("--no-force-keyframes-at-cuts", args)
            self.assertNotIn("--force-keyframes-at-cuts", args)
            self.assertIn('-c copy', args[args.index('--downloader-args') + 1])
            self.assertIn('ffmpeg_i:-request_size 8388608 -initial_request_size 8388608 -short_seek_size 8388608 -multiple_requests 1', args)
            path = Path(args[args.index("--output") + 1].replace("%(ext)s", "mkv"))
            path.write_bytes(b"synthetic media")
            return json.dumps(dict(filepath=str(path), section_start=10, section_end=20))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(sources, "_media_duration", return_value=10) as duration, patch.object(sources, "inspect_source", return_value=metadata), patch.object(sources, "find_tool", return_value="/tools/ffmpeg"), patch.object(sources, "_command", return_value=["yt-dlp"]), patch.object(sources, "run_command", side_effect=download) as run:
                first = sources.download_section(metadata["url"], 10, 20, root, threading.Event(), lambda _: None)
                duration.return_value = 14  # Original keyframe boundaries extend the requested range.
                second = sources.download_section(metadata["url"], 10, 20, root, threading.Event(), lambda _: None)
                self.assertNotEqual(first, second)
                self.assertTrue(first.is_file())
                self.assertFalse(json.loads(first.with_name("source.json").read_text())["alignment_verified"])
                self.assertEqual(json.loads(second.with_name('source.json').read_text())['local_duration'], 14)
                duration.return_value = 250
                with self.assertRaises(RuntimeError):
                    sources.download_section(metadata["url"], 10, 20, root, threading.Event(), lambda _: None)
                run.side_effect = RuntimeError("range unavailable")
                with self.assertRaises(RuntimeError):
                    sources.download_section(metadata["url"], 10, 20, root, threading.Event(), lambda _: None)
                self.assertEqual(run.call_count, 4)
                self.assertEqual(len(list(root.iterdir())), 2)

    def test_invalid_and_live(self):
        with patch.object(sources, "inspect_source") as inspect, patch.object(sources, "run_command") as run:
            for a, b in ((0, float("nan")), (-1, 5), (2, 2), (True, 3), ("0", 10)):
                with self.assertRaises(ValueError):
                    sources.download_section("unused", a, b, Path("unused"), threading.Event(), lambda _: None)
            inspect.assert_not_called()
            inspect.return_value = dict(url="https://www.youtube.com/watch?v=abcdefghijk",
                                        duration=100, is_live=True, live_status="is_live")
            with self.assertRaises(ValueError):
                sources.download_section("https://www.youtube.com/watch?v=abcdefghijk", 0, 5, Path("unused"), threading.Event(), lambda _: None)
            run.assert_not_called()

    def test_nuitka_dispatch(self):
        with patch.object(sources, "__compiled__", object(), create=True), patch.object(sources, "find_tool", return_value="deno"):
            self.assertEqual(sources._command()[:2], [sys.executable, "--yt-dlp"])

    def test_running_process_cancellation(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as temporary:
            heartbeat = Path(temporary) / "heartbeat"
            child = "import pathlib,time; p=pathlib.Path(" + repr(str(heartbeat)) + "); " + "exec('while True: p.write_text(str(time.time())); time.sleep(0.05)')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(30)"
            timer = threading.Timer(0.6, cancel.set)
            timer.start()
            began = time.monotonic()
            try:
                with self.assertRaises(InterruptedError):
                    sources.run_command([sys.executable, "-c", parent], cancel, lambda _: None)
            finally:
                timer.cancel()
            self.assertLess(time.monotonic() - began, 5)
            self.assertTrue(heartbeat.exists())
            stamp = heartbeat.read_text()
            time.sleep(0.2)
            self.assertEqual(heartbeat.read_text(), stamp)

    def test_precancel(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(sources.subprocess, "Popen") as popen:
            with self.assertRaises(InterruptedError):
                sources.run_command(["unused"], cancel, lambda _: None)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
