"""Parallel scheduling and real lossless VOD fragment assembly, without YouTube requests."""
import hashlib
from concurrent.futures import CancelledError
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

import av
from fragments import download_files, selected_tracks
from live import parse_window
from sources import download_section, find_tool, run_command


class FragmentTests(unittest.TestCase):
    def test_fragment_remaining_time_and_download_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = [(str(n), Path(directory) / str(n), None) for n in range(3)]
            messages = []
            with patch('live._segment', return_value=b'data'), patch('yt_dlp.utils.progress.time.monotonic', return_value=100):
                download_files(jobs, 1, threading.Event(), messages.append)
            self.assertEqual(messages[0]['mode'], 'parallel')
            self.assertEqual([m['eta_seconds'] for m in messages[1:]], [None, None, 0])

    def test_refills_before_slow_first_request_finishes_and_respects_limit(self):
        for workers in (1, 2, 4, 16):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                jobs = [(str(n), root / str(n), None) for n in range(workers + 3)]
                lock, refilled = threading.Lock(), threading.Event()
                active, maximum = 0, 0
                def fetch(url, cancel, *_):
                    nonlocal active, maximum
                    with lock:
                        active += 1
                        maximum = max(maximum, active)
                    if url == '0' and workers > 1:
                        self.assertTrue(refilled.wait(3), 'Did not refill while the first request was slow')
                    if int(url) >= workers:
                        refilled.set()
                    with lock:
                        active -= 1
                    return url.encode()
                with patch('live._segment', side_effect=fetch):
                    download_files(jobs, workers, threading.Event(), lambda _: None)
                self.assertLessEqual(maximum, workers)
                self.assertEqual([p.read_text() for _, p, _ in jobs], [str(n) for n in range(workers + 3)])

    def test_rate_limit_stops_without_retries_or_more_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch('live._fetch', side_effect=HTTPError('url', 429, 'limited', {}, None)) as fetch:
            jobs = [('https://media.example/a', Path(directory) / str(n), None) for n in range(10)]
            with self.assertRaisesRegex(ValueError, '요청이 너무 많아'):
                download_files(jobs, 1, threading.Event(), lambda _: None)
            fetch.assert_called_once()
            self.assertEqual(fetch.call_args.args[4], 1)
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises((InterruptedError, CancelledError)):
                download_files([('https://media.example/a', Path(directory) / 'a', None)], 1, cancelled, lambda _: None)

    def test_unsupported_format_keeps_quality_and_worker_validation(self):
        with patch('live._segment') as fetch:
            self.assertIsNone(selected_tracks([{'protocol': 'https'}], 0, 1, threading.Event()))
            fetch.assert_not_called()
        for workers in (0, 17, True, 1.5):
            with self.assertRaises(ValueError):
                download_section('unused', 0, 1, '.', threading.Event(), lambda _: None, workers=workers)

    def test_real_separate_video_audio_hls_and_dash_preserve_packets_and_sync(self):
        ffmpeg = find_tool('ffmpeg')
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = [ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=8',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
                    '-map', '0:v', '-c:v', 'libx264', '-g', '10', '-sc_threshold', '0',
                    '-hls_time', '1', '-hls_list_size', '0', '-hls_segment_type', 'fmp4',
                    '-hls_fmp4_init_filename', 'v-init.mp4', '-hls_segment_filename', str(root / 'v%d.m4s'), str(root / 'v.m3u8'),
                    '-map', '1:a', '-c:a', 'aac', '-hls_time', '1', '-hls_list_size', '0', '-hls_segment_type', 'fmp4',
                    '-hls_fmp4_init_filename', 'a-init.mp4', '-hls_segment_filename', str(root / 'a%d.m4s'), str(root / 'a.m3u8')]
            run_command(args, cancel, lambda _: None, cwd=root)
            def packets(path, kind):
                with av.open(str(path)) as container:
                    stream = next(s for s in container.streams if s.type == kind)
                    return [(hashlib.sha256(bytes(p)).digest(), float(p.pts * p.time_base))
                            for p in container.demux(stream) if p.size and p.pts is not None]
            originals = {kind: packets(root / f'{prefix}.m3u8', kind) for prefix, kind in [('v', 'video'), ('a', 'audio')]}
            for protocol in ('m3u8_native', 'http_dash_segments'):
                with self.subTest(protocol=protocol):
                    formats = []
                    for prefix in ('v', 'a'):
                        url = f'https://media.example/{prefix}.m3u8?secret=private'
                        fmt = dict(url=url, protocol=protocol, vcodec='h264' if prefix == 'v' else 'none',
                                   acodec='aac' if prefix == 'a' else 'none', http_headers={'X-Test': 'keep'})
                        if protocol == 'http_dash_segments':
                            window = parse_window((root / f'{prefix}.m3u8').read_bytes(), url)
                            fmt['fragments'] = [{'url': window['segments'][0]['init']}] + [
                                {'url': s['url'], 'duration': s['duration']} for s in window['segments']]
                        formats.append(fmt)
                    metadata = dict(url='https://www.youtube.com/watch?v=abcdefghijk', duration=8,
                                    is_live=False, live_status='not_live', _download_formats=formats)
                    fetched = []
                    def fetch(url, *args):
                        self.assertEqual(args[-1], {'X-Test': 'keep'})
                        name = Path(urlsplit(url).path).name
                        fetched.append(name)
                        return (root / name).read_bytes()
                    with patch('live._fetch', side_effect=fetch):
                        result = download_section(metadata['url'], 2.2, 4.4, root / 'cache', cancel,
                                                  lambda _: None, snapshot=metadata, workers=3)
                    self.assertNotIn('v0.m4s', fetched)
                    self.assertNotIn('v5.m4s', fetched)
                    shifts = []
                    for kind in ('video', 'audio'):
                        copied = packets(result, kind)
                        original = originals[kind]
                        index = next(i for i, p in enumerate(original) if p[0] == copied[0][0])
                        self.assertEqual([p[0] for p in copied], [p[0] for p in original[index:index + len(copied)]])
                        shifts.append(copied[0][1] - original[index][1])
                    self.assertAlmostEqual(*shifts, delta=.002)
                    saved = result.with_name('source.json').read_text(encoding='utf-8')
                    self.assertNotIn('private', saved)
                    self.assertNotIn('_download_formats', saved)
                    self.assertGreater(json.loads(saved)['local_duration'], 2)
