"""Minimal fragment previews and debounced, stale-safe remote range selection."""
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sources import find_tool, run_command, inspect_source
from youtube_preview import YouTubePreview, fetch_preview


class YouTubePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_tool_failure_shows_guidance_and_emits_redacted_diagnostics(self):
        from tool_errors import ToolError
        error = ToolError('Peer certificate failed verification https://media.example/?token=secret', 4294967291)
        widget = YouTubePreview()
        diagnostics = []
        widget.diagnostic.connect(diagnostics.append)
        try:
            with patch('youtube_preview.fetch_preview', side_effect=error), self.assertLogs(level='ERROR'):
                widget.load(dict(duration=60))
                widget.show()
                widget.timer.stop()
                widget.request_preview()
                deadline = time.monotonic() + 3
                while widget.task and time.monotonic() < deadline:
                    QTest.qWait(20)
                self.assertIsNone(widget.task)
            self.assertIn(str(error), widget.status.text())
            self.assertEqual(len(diagnostics), 1)
            self.assertIn('4294967291', diagnostics[0])
            self.assertNotIn('token=secret', diagnostics[0])
        finally:
            widget.close()

    def test_preview_format_is_small_and_does_not_change_download_selection(self):
        formats = [dict(url='https://media.example/large', protocol='https', vcodec='h264', height=1080),
                   dict(url='https://media.example/small', protocol='https', vcodec='h264', height=144),
                   dict(url='https://media.example/audio', protocol='https', vcodec='none', height=None),
                   dict(url='https://media.example/storyboard', protocol='mhtml', vcodec='images', height=27)]
        data = dict(duration=60, formats=formats, requested_formats=[formats[0]])
        with patch('sources._command', return_value=[]), patch('sources.run_command', return_value=json.dumps(data)):
            result = inspect_source('https://youtu.be/abcdefghijk', threading.Event(), lambda _: None)
        self.assertEqual(result['_preview_format']['url'], formats[1]['url'])
        self.assertEqual(result['_download_formats'][0]['url'], formats[0]['url'])

    def test_isolated_ts_and_fmp4_preview_at_start_middle_and_last_frame(self):
        import av
        import io
        cancel = threading.Event()
        for kind, extension in (('mpegts', 'ts'), ('fmp4', 'm4s')):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                playlist = root / 'video.m3u8'
                run_command([find_tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                             'testsrc2=size=160x90:rate=2:duration=4', '-c:v', 'libx264', '-g', '4',
                             '-sc_threshold', '0', '-hls_time', '2', '-hls_list_size', '0',
                             '-hls_segment_type', kind, '-hls_segment_filename', str(root / ('v%d.' + extension)),
                             str(playlist)], cancel, lambda _: None, cwd=root)
                expected = []
                for index in range(2):
                    data = ((root / 'init.mp4').read_bytes() if kind == 'fmp4' else b'') + (root / f'v{index}.{extension}').read_bytes()
                    with av.open(io.BytesIO(data)) as container:
                        expected.append([frame.to_image().tobytes() for frame in container.decode(video=0)])
                metadata = dict(duration=4, _preview_format=dict(protocol='m3u8_native',
                                url='https://media.example/video.m3u8', vcodec='h264', acodec='none'))
                def fetch(url, *_):
                    return (root / Path(urlsplit(url).path).name).read_bytes()
                with patch('live._segment', side_effect=fetch):
                    for seconds in (0, .3, .99, 1.9, 2, 2.7, 3.9, 4):
                        with self.subTest(seconds=seconds):
                            image = fetch_preview(metadata, seconds, cancel, lambda _: None)
                            position = min(seconds, 3.9)
                            index = int(position // 2)
                            frame = min(math.ceil((position % 2) * 2), len(expected[index]) - 1)
                            self.assertEqual(image.tobytes(), expected[index][frame])

    def test_one_fragment_preview_vod_finished_live_and_live_with_cache(self):
        from live import parse_window
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            playlist = root / 'video.m3u8'
            run_command([find_tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                         'testsrc2=size=160x90:rate=10:duration=6', '-c:v', 'libx264', '-g', '10',
                         '-sc_threshold', '0', '-hls_time', '1', '-hls_list_size', '0',
                         '-hls_segment_type', 'fmp4', '-hls_segment_filename', str(root / 'v%d.m4s'),
                         str(playlist)], cancel, lambda _: None, cwd=root)
            fmt = dict(protocol='m3u8_native', url='https://media.example/video.m3u8', vcodec='h264', acodec='none')
            for kind in ('vod', 'was_live', 'live', 'dash'):
                with self.subTest(kind=kind):
                    metadata = dict(duration=6, is_live=kind == 'live', was_live=kind == 'was_live', _preview_format=fmt.copy())
                    body = playlist.read_bytes()
                    if kind == 'live':
                        body = body.replace(b'#EXT-X-ENDLIST', b'').replace(
                            b'#EXTM3U', b'#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2026-09-26T00:00:00Z')
                        metadata['window'] = parse_window(body, fmt['url'])
                    if kind == 'dash':
                        window = parse_window(body, fmt['url'])
                        metadata['_preview_format'].update(protocol='http_dash_segments',
                            fragments=[{'url': window['segments'][0]['init']}] + [
                                {'url': s['url'], 'duration': s['duration']} for s in window['segments']])
                    fetched = []
                    def fetch(url, *_):
                        name = Path(urlsplit(url).path).name
                        fetched.append(name)
                        return body if name.endswith('.m3u8') else (root / name).read_bytes()
                    with patch('live._segment', side_effect=fetch):
                        image = fetch_preview(metadata, 2.2, cancel, lambda _: None)
                        self.assertEqual(image.size, (160, 90))
                        self.assertEqual(fetched, ([] if kind == 'dash' else ['video.m3u8']) + ['init.mp4', 'v2.m4s'])
                        fetched.clear()
                        fetch_preview(metadata, 2.4, cancel, lambda _: None)
                        self.assertEqual(fetched, [])
                        fetch_preview(metadata, 6, cancel, lambda _: None)
                        self.assertEqual(fetched, ['init.mp4', 'v5.m4s'])

    def test_range_live_times_and_debounce_drop_stale_preview(self):
        widget = YouTubePreview()
        calls, started, release = [], threading.Event(), threading.Event()
        def fetch(metadata, seconds, cancel, report):
            calls.append(seconds)
            if len(calls) == 1:
                started.set()
                release.wait(5)
            return Image.new('RGB', (16, 16), 'red' if seconds == 20 else 'blue')
        def wait_for(predicate, timeout=3000):
            began = time.monotonic()
            while not predicate() and (time.monotonic() - began) * 1000 < timeout:
                QTest.qWait(10)
            self.assertTrue(predicate())
        try:
            with patch('youtube_preview.fetch_preview', side_effect=fetch):
                widget.show()
                widget.load(dict(duration=120, is_live=True))
                self.assertEqual(widget.start.text(), '-02:00.000')
                self.assertEqual(widget.end.text(), '-00:00.000')
                widget.start.setText('-01:30')
                widget.end.setText('-00:30')
                widget.edit_range(widget.start)
                self.assertEqual(widget.selected_times(), (30, 90))
                self.assertEqual(widget.timeline.selection, (30, 90))
                widget.timeline.changed.emit(40, 80)
                self.assertEqual(widget.selected_times(), (40, 80))
                widget.seek(10)
                QTest.qWait(600)
                widget.seek(20)
                QTest.qWait(600)
                self.assertEqual(calls, [])
                wait_for(started.is_set)
                widget.seek(30)
                release.set()
                wait_for(lambda: widget.task is None)
                self.assertTrue(widget.canvas.image.isNull())
                wait_for(lambda: not widget.canvas.image.isNull())
                self.assertEqual(calls, [20, 30])
                self.assertEqual(widget.canvas.image.pixelColor(0, 0).name(), '#0000ff')
                widget.handle_key(Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(widget.position, 35)
                widget.hide()
                self.assertFalse(widget.timer.isActive())
                widget.load(dict(duration=120, was_live=True))
                self.assertEqual(widget.start.text(), '00:00:00.000')
        finally:
            release.set()
            widget.close()

    def test_progressive_preview_seeks_one_frame_and_honors_cancellation(self):
        from concurrent.futures import CancelledError
        metadata = dict(duration=60, _preview_format=dict(protocol='https',
                        url='https://media.example/video.mp4', http_headers={'User-Agent': 'test'}))
        cancel = threading.Event()
        def run(args, *_, **kwargs):
            self.assertTrue(kwargs['windows_ca'])
            import certifi
            self.assertEqual(args[args.index('-tls_verify') + 1], '1')
            self.assertEqual(args[args.index('-ca_file') + 1], certifi.where())
            self.assertLess(args.index('-ca_file'), args.index('-i'))
            self.assertEqual(args[args.index('-ss') + 1], '12')
            self.assertEqual(args[args.index('-frames:v') + 1], '1')
            self.assertIn('-an', args)
            self.assertIn('User-Agent: test\r\n', args)
            Image.new('RGB', (160, 90)).save(args[-1])
        with patch('sources.find_tool', return_value='ffmpeg'), patch('sources.run_command', side_effect=run) as command:
            self.assertEqual(fetch_preview(metadata, 12, cancel, lambda _: None).size, (160, 90))
            cancel.set()
            with self.assertRaises(CancelledError):
                fetch_preview(metadata, 12, cancel, lambda _: None)
            self.assertEqual(command.call_count, 1)
