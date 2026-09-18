"""Real FFmpeg checks for accurate cuts, publication, and cancellation."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import av
from PIL import ImageChops, ImageStat

from export import export_clips
from media import frame_at
from sources import find_tool, run_command


class ExportTests(unittest.TestCase):
    def test_gpu_export_and_cpu_retry(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.mp4"
            run_command([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60:duration=1",
                         "-c:v", "libx264", "-preset", "ultrafast", str(source)], cancel, lambda _: None)
            row = dict(source=str(source), start=0, end=1)
            messages = []
            result = export_clips([row], root / "gpu", cancel, messages.append, gpu=True, fps=60)
            self.assertFalse(result["failed"])
            with av.open(result["saved"][0]) as container:
                self.assertEqual(float(container.streams.video[0].average_rate), 60)
            commands = []
            def fail_gpu(args, *rest, **kwargs):
                commands.append(list(args))
                if "h264_nvenc" in args:
                    Path(args[-1]).write_bytes(b"partial")
                    raise RuntimeError("simulated GPU failure")
                return run_command(args, *rest, **kwargs)
            with patch("export._gpu_encoder", return_value="h264_nvenc"), patch("export.run_command", side_effect=fail_gpu):
                result = export_clips([row, row], root / "retry", cancel, messages.append, gpu=True, fps=60)
            self.assertFalse(result["failed"])
            self.assertEqual(len(result["saved"]), 2)
            self.assertEqual(sum("h264_nvenc" in args for args in commands), 1)
            self.assertEqual(sum("libx264" in args for args in commands), 2)

    def test_custom_formats_size_resolution_and_audio(self):
        import numpy as np
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.mp4"
            run_command([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=30:duration=2",
                         "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2",
                         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)], cancel, lambda _: None)
            levels = []
            for fmt in ("mp4", "mkv", "mov", "webm"):
                result = export_clips([dict(source=str(source), start=0, end=2)], root / fmt, cancel, lambda _: None,
                                      format=fmt, height=480, fps=24, gain=-6, channels=1, target_mb=.5, audio_kbps=96)
                self.assertEqual(result["failed"], [])
                output = Path(result["saved"][0])
                self.assertEqual(output.suffix, "." + fmt)
                with av.open(str(output)) as container:
                    video = container.streams.video[0]
                    self.assertEqual(video.height, 480)
                    self.assertEqual(float(video.average_rate), 24)
                    self.assertEqual(container.streams.audio[0].channels, 1)
                    samples = np.concatenate([f.to_ndarray().flatten() for f in container.decode(audio=0)])
                    levels.append(float(np.sqrt(np.mean(samples ** 2))))
                self.assertGreater(output.stat().st_size, 10000)
                self.assertLess(output.stat().st_size, 800000)
            with av.open(str(source)) as container:
                samples = np.concatenate([f.to_ndarray().flatten() for f in container.decode(audio=0)])
                original = float(np.sqrt(np.mean(samples ** 2)))
            for level in levels:
                self.assertAlmostEqual(level / original, .5, delta=.08)

    def test_invalid_ranges_and_precancel_do_not_start_encoder(self):
        with tempfile.TemporaryDirectory() as name, patch("export.find_tool") as tool:
            for rows in ([], [{"source": "x", "start": True, "end": 2}],
                         [{"source": "x", "start": 1, "end": float("nan")}],
                         [{"source": "x", "start": 2, "end": 1}]):
                with self.assertRaises(ValueError):
                    export_clips(rows, name, threading.Event(), lambda _: None)
            cancel = threading.Event()
            cancel.set()
            result = export_clips([{"source": "x", "start": 0, "end": 1}], name, cancel, lambda _: None)
            self.assertTrue(result["cancelled"])
            tool.assert_not_called()

    def test_real_exports_preserve_source_timing_and_completed_files(self):
        try:
            ffmpeg = find_tool("ffmpeg")
            find_tool("ffprobe")
        except RuntimeError as error:
            self.skipTest(str(error))
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "한글 source.mp4"
            run_command([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=4",
                         "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
                         "-c:v", "libx264", "-g", "40", "-c:a", "aac", "-output_ts_offset", "3", str(source)],
                        cancel, lambda _: None)
            fast = export_clips([{"source": str(source), "start": 1.2, "end": 2.7}],
                                root / "fast", cancel, lambda _: None, mode="fast")
            self.assertEqual(fast["failed"], [])
            self.assertEqual(len(fast["saved"]), 1)
            with av.open(fast["saved"][0]) as container:
                self.assertIsNotNone(next(container.decode(video=0), None))
            for mode in ("accurate", "fast"):
                muted = export_clips([{"source": str(source), "start": 1.2, "end": 2.7}],
                                     root / (mode + "-muted"), cancel, lambda _: None, mode=mode, audio=False)
                self.assertFalse(muted["failed"])
                with av.open(muted["saved"][0]) as container:
                    self.assertFalse(container.streams.audio)
                    self.assertIsNotNone(next(container.decode(video=0), None))
            original = hashlib.sha256(source.read_bytes()).digest()
            row = {"source": str(source), "start": 1.2, "end": 2.7}
            output = root / "출력 clips"
            result = export_clips([row, row], output, cancel, lambda _: None)
            self.assertEqual(result["failed"], [])
            self.assertEqual(len(result["saved"]), 2)
            self.assertNotEqual(*result["saved"])
            clip = Path(result["saved"][0])
            reference = frame_at(source, 1.2)["image"]
            actual = frame_at(clip, 0)["image"]
            self.assertLess(sum(ImageStat.Stat(ImageChops.difference(reference, actual)).mean) / 3, 8)
            with av.open(str(clip)) as container:
                video, audio = container.streams.video[0], container.streams.audio[0]
                self.assertAlmostEqual(float(video.duration * video.time_base), 1.5, delta=.11)
                self.assertAlmostEqual(float(audio.duration * audio.time_base), 1.5, delta=.1)
                self.assertAlmostEqual(float(video.start_time * video.time_base),
                                       float(audio.start_time * audio.time_base), delta=.05)
            def stop_after_first(message):
                if message.startswith("클립 저장 2"):
                    cancel.set()
            cancelled = export_clips([row, row], output, cancel, stop_after_first)
            self.assertTrue(cancelled["cancelled"])
            self.assertEqual(len(cancelled["saved"]), 1)
            self.assertTrue(Path(cancelled["saved"][0]).is_file())
            self.assertFalse(list(output.glob(".export-*")))
            cancel.clear()
            failed = export_clips([{**row, "source": str(root / "missing.mp4")}, {**row, "end": 9}],
                                  output, cancel, lambda _: None)
            self.assertEqual(len(failed["failed"]), 2)
            self.assertEqual(failed["saved"], [])
            silent = root / "silent.mp4"
            run_command([ffmpeg, "-v", "error", "-i", str(source), "-an", "-vf", "scale=80:60",
                         "-c:v", "libx264", str(silent)], cancel, lambda _: None)
            result = export_clips([{**row, "source": str(silent)}], output, cancel, lambda _: None)
            self.assertEqual(result["failed"], [])
            with av.open(result["saved"][0]) as container:
                self.assertFalse(container.streams.audio)
                self.assertEqual((container.streams.video[0].width, container.streams.video[0].height), (80, 60))
            with patch("export.run_command", side_effect=RuntimeError("disk full")):
                self.assertEqual(len(export_clips([row], output, cancel, lambda _: None)["failed"]), 1)
            self.assertFalse(list(output.glob(".export-*")))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original)


if __name__ == "__main__":
    unittest.main()
