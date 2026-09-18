"""Frozen live playlist selection, discontinuity and bounded real-FFmpeg checks."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from urllib.parse import urlsplit
from unittest.mock import patch

from live import download_window, parse_window
from sources import download_section, find_tool, run_command


class LiveTests(unittest.TestCase):
    def test_window_timing_and_discontinuities(self):
        body = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:50\n#EXT-X-PROGRAM-DATE-TIME:2026-09-14T10:00:00Z\n#EXTINF:2,\na.ts\n#EXT-X-PROGRAM-DATE-TIME:2026-09-14T10:00:05Z\n#EXTINF:2,\nb.ts\n"
        window = parse_window(body, "https://media.example/live.m3u8")
        self.assertEqual(window["duration"], 4)
        self.assertEqual(window["segments"][1]["sequence"], 51)
        self.assertTrue(window["segments"][1]["discontinuity"])
        with tempfile.TemporaryDirectory() as root, patch("live._fetch") as fetch:
            with self.assertRaisesRegex(ValueError, "단절"):
                download_window({"window": window}, 1, 3, root, threading.Event(), lambda _: None)
            fetch.assert_not_called()
        for bad in (b"not hls", b"#EXTM3U\n#EXTINF:nan,\na.ts", b"#EXTM3U\n#EXTINF:1,\nfile:///secret",
                    b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n',
                    b"#EXTM3U\n#EXTINF:1,\na.ts\n#EXTINF:2,\n"):
            with self.assertRaises(ValueError):
                parse_window(bad, "https://media.example/live.m3u8")

    def test_real_selected_segments_only_and_no_snapshot_refresh(self):
        try:
            ffmpeg = find_tool("ffmpeg")
        except RuntimeError as error:
            self.skipTest(str(error))
        cancel = threading.Event()
        for segment_type in ("mpegts", "fmp4"):
            with self.subTest(segment_type=segment_type), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                playlist = root / "live.m3u8"
                run_command([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=8",
                             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
                             "-c:v", "libx264", "-g", "10", "-sc_threshold", "0", "-c:a", "aac",
                             "-hls_time", "1", "-hls_list_size", "0", "-hls_segment_type", segment_type,
                             "-hls_segment_filename", str(root / ("part%d.ts" if segment_type == "mpegts" else "part%d.m4s")),
                             str(playlist)], cancel, lambda _: None, cwd=root)
                window = parse_window(playlist.read_bytes(), "https://media.example/live.m3u8?token=private")
                for segment in window["segments"]:
                    segment["url"] += "?token=private"
                metadata = {"url": "https://www.youtube.com/watch?v=abcdefghijk", "id": "abcdefghijk",
                            "title": "live", "is_live": True, "live_status": "is_live", "window": window}
                fetched = []
                def fetch(url, *_):
                    name = Path(urlsplit(url).path).name
                    fetched.append(name)
                    return (root / name).read_bytes()
                with patch("live._fetch", side_effect=fetch), patch("sources.inspect_source") as inspect:
                    result = download_section(metadata["url"], 2.2, 4.4, root / "cache", cancel,
                                              lambda _: None, snapshot=metadata)
                    inspect.assert_not_called()
                extension = "ts" if segment_type == "mpegts" else "m4s"
                self.assertCountEqual(fetched, ([] if segment_type == "mpegts" else ["init.mp4"]) +
                                  [f"part{index}.{extension}" for index in (2, 3, 4)])
                saved = json.loads(result.with_name("source.json").read_text(encoding="utf-8"))
                # Stream copy retains codec delay/B-frame timestamps in the container.
                self.assertAlmostEqual(saved["local_duration"], 3, delta=.25)
                self.assertEqual((saved['acquired_start'], saved['acquired_end']), (2, 5))
                self.assertNotIn("private", result.with_name("source.json").read_text(encoding="utf-8"))
                self.assertEqual(sorted(path.name for path in result.parent.iterdir()), ["source.json", "video.mkv"])
                # Decoded samples must match the original selected segments, not just codec names.
                import av
                import hashlib
                def decoded_frames(path):
                    with av.open(str(path)) as container:
                        return [hashlib.sha256(frame.to_ndarray(format='rgb24').tobytes()).digest()
                                for frame in container.decode(video=0)]
                self.assertEqual(decoded_frames(result), decoded_frames(playlist)[20:50])
                def audio_packets(path):
                    with av.open(str(path)) as container:
                        result = []
                        for packet in container.demux(audio=0):
                            if not packet.size:
                                continue
                            payload = bytes(packet)
                            # MPEG-TS adds ADTS framing; Matroska stores the same AAC payload without it.
                            if segment_type == 'mpegts' and path == playlist:
                                self.assertEqual(payload[:2], b'\xff\xf1')
                                payload = payload[7:]
                            result.append(hashlib.sha256(payload).digest())
                        return result
                original_audio = audio_packets(playlist)
                copied_audio = audio_packets(result)
                self.assertTrue(copied_audio)
                self.assertTrue(any(original_audio[i:i+len(copied_audio)] == copied_audio
                                    for i in range(len(original_audio))))
                with patch("live._fetch", side_effect=urllib.error.HTTPError("url", 403, "expired", {}, None)), \
                     patch("live.run_command") as run:
                    with self.assertRaisesRegex(ValueError, "접근"):
                        download_window(metadata, 2.2, 4.4, root / "cache", cancel, lambda _: None)
                    run.assert_not_called()
                self.assertFalse(list((root / "cache").glob(".live-*")))


if __name__ == "__main__":
    unittest.main()
