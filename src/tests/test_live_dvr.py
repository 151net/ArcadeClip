"""DVR seeks use fragment PTS and retain a frozen, bounded download window."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import av

from live_dvr import inspect_dvr, locate, refresh_urls, download_dvr
from sources import download_section, find_tool, run_command, inspect_source
from youtube_preview import fetch_preview


class LiveDVRTests(unittest.TestCase):
    def test_long_download_only_probes_boundaries_before_original_download(self):
        fmt = dict(url='https://media.example/video', vcodec='h264', acodec='none')
        metadata = dict(window=dict(start_utc=None, dvr=dict(origin=12, formats=[fmt])))
        progress = []
        with patch('live_dvr._locate', side_effect=[dict(sequence=100), dict(sequence=459)]) as locate, \
             patch('live_dvr.sample') as sample, patch('fragments.download_vod') as download:
            download_dvr(metadata, 500, 2300, '.', threading.Event(), progress.append, 4)
            self.assertEqual(locate.call_count, 2)
            sample.assert_not_called()
            track = download.call_args.args[1][0]
            self.assertEqual(len(track['segments']), 360)
            self.assertEqual(track['time_origin'], 12)
            self.assertEqual(progress[0]['step'], 1)

    def test_forbidden_refreshes_urls_once_without_moving_the_selection(self):
        old = dict(format_id='160', protocol='http_dash_segments_generator', vcodec='h264', acodec='none',
                   url='https://media.example/video?id=broadcast&token=old')
        fresh = {**old, 'url': 'https://media.example/video?id=broadcast&token=new'}
        dvr = dict(source_url='https://www.youtube.com/watch?v=abcdefghijk', formats=[old], preview=old,
                   origin=12, end=7212, first=3, last=1500, target_duration=5)
        def forbidden(code):
            error = ValueError('fragment unavailable')
            error.__cause__ = HTTPError('https://media.example/video', code, 'error', {}, None)
            return error
        result = dict(sequence=20, start=102, end=107)
        with patch('sources._command', return_value=['yt-dlp']), \
             patch('sources.run_command', return_value=json.dumps(dict(id='abcdefghijk', formats=[fresh]))) as command, \
             patch('live_dvr._locate', side_effect=[forbidden(403), result]) as seek:
            self.assertIs(locate(dvr, 90, threading.Event()), result)
            self.assertEqual(command.call_count, 1)
            self.assertEqual([call.args[1] for call in seek.call_args_list], [90, 90])
        self.assertEqual((dvr['origin'], dvr['end'], dvr['first'], dvr['last']), (12, 7212, 3, 1500))
        self.assertEqual(dvr['preview']['url'], fresh['url'])
        for code, attempts in ((403, 2), (429, 1), (404, 1)):
            with patch('live_dvr.refresh_urls') as refresh, patch('live_dvr._locate', side_effect=forbidden(code)) as seek:
                with self.assertRaises(ValueError):
                    locate(dvr, 90, threading.Event())
                self.assertEqual(seek.call_count, attempts)
                self.assertEqual(refresh.call_count, attempts - 1)
        metadata = dict(window={'dvr': dvr})
        with patch('live_dvr.refresh_urls') as refresh, \
             patch('live_dvr._download_dvr', side_effect=[forbidden(403), Path('clip.mkv')]) as download:
            self.assertEqual(download_dvr(metadata, 90, 100, '.', threading.Event(), lambda _: None, 2), Path('clip.mkv'))
            self.assertEqual([call.args[1:3] for call in download.call_args_list], [(90, 100), (90, 100)])
            refresh.assert_called_once()
        with patch('sources._command', return_value=[]), \
             patch('sources.run_command', return_value=json.dumps(dict(id='abcdefghijk', formats=[{**fresh, 'url': 'https://media.example/video?id=other'}]))):
            with self.assertRaises(ValueError):
                refresh_urls(dvr, threading.Event())
            self.assertEqual(dvr['preview']['url'], fresh['url'])

    def test_metadata_prefers_dvr_and_falls_back_to_available_hls(self):
        data = json.dumps(dict(is_live=True, live_status='is_live', formats=[]))
        wide = dict(duration=7200, start_utc=10000, dvr={'first': 0})
        limited = dict(duration=3600, start_utc=13600)
        with patch('sources._command', return_value=['yt-dlp']), patch('sources.run_command', return_value=data) as run, \
             patch('live_dvr.inspect_dvr', return_value=wide) as dvr, patch('live.inspect_window', return_value=limited) as hls:
            metadata = inspect_source('https://youtu.be/abcdefghijk', threading.Event(), lambda _: None)
            self.assertEqual(metadata['duration'], 7200)
            self.assertIn('--live-from-start', run.call_args.args[0])
            hls.assert_not_called()
            dvr.return_value = None
            metadata = inspect_source('https://youtu.be/abcdefghijk', threading.Event(), lambda _: None)
            self.assertEqual(metadata['duration'], 3600)
            hls.assert_called_once()

    def test_expired_beginning_and_variable_fragment_durations(self):
        formats = [dict(url='https://media.example/video', protocol='http_dash_segments_generator',
                        vcodec='h264', acodec='none', height=144, target_duration=5),
                   dict(url='https://media.example/audio', protocol='http_dash_segments_generator', vcodec='none', acodec='aac')]
        def head(fmt, cancel, sequence=None):
            return {'X-Head-Seqnum': '1001'} if sequence is None else ({} if sequence >= 100 else None)
        def sample(dvr, sequence, cancel):
            # The first fragment was short. A naive sequence * 5 seek is wrong.
            return dict(sequence=sequence, start=sequence * 5 - 2, end=sequence * 5 + 3)
        with patch('live_dvr._head', side_effect=head), patch('live_dvr.sample', side_effect=sample):
            window = inspect_dvr(dict(formats=formats, release_timestamp=10000), threading.Event())
            self.assertEqual(window['dvr']['first'], 100)
            self.assertEqual(window['dvr']['last'], 1000)
            self.assertEqual(window['duration'], 4505)
            self.assertEqual(locate(window['dvr'], 10, threading.Event())['sequence'], 102)
            with self.assertRaises(ValueError):
                locate(window['dvr'], 4505, threading.Event())

    def test_real_independent_mp4_fragments_preview_and_bounded_av_download(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg = find_tool('ffmpeg')
            run_command([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=12',
                         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=12', '-c:v', 'libx264', '-g', '120',
                         '-sc_threshold', '0', '-force_key_frames', '0,2,7', '-c:a', 'aac',
                         '-hls_time', '2', '-hls_list_size', '0', '-hls_segment_type', 'fmp4',
                         '-hls_segment_filename', str(root / 'part%d.m4s'), str(root / 'video.m3u8')],
                        cancel, lambda _: None, cwd=root)
            for index in range(3):
                body = (root / 'init.mp4').read_bytes() + (root / f'part{index}.m4s').read_bytes()
                for kind in ('video', 'audio'):
                    (root / f'{kind}{index}.mp4').write_bytes(body)
            formats = [dict(url='https://media.example/video?token=private', protocol='http_dash_segments_generator',
                            vcodec='h264', acodec='none', height=144, target_duration=5),
                       dict(url='https://media.example/audio?token=private', protocol='http_dash_segments_generator',
                            vcodec='none', acodec='aac')]
            fetched = []
            def fetch(url, *_):
                parsed = urlsplit(url)
                index = int(parse_qs(parsed.query)['sq'][0])
                name = parsed.path.strip('/') + str(index) + '.mp4'
                fetched.append(name)
                return (root / name).read_bytes()
            with patch('live_dvr._head', return_value={'X-Head-Seqnum': '3'}), patch('live_dvr._segment', side_effect=fetch):
                window = inspect_dvr(dict(formats=formats, release_timestamp=10000), cancel)
                metadata = dict(url='https://www.youtube.com/watch?v=abcdefghijk', is_live=True,
                                live_status='is_live', duration=window['duration'], window=window)
                self.assertEqual(locate(window['dvr'], 2.8, cancel)['sequence'], 1)
                self.assertEqual(fetch_preview(metadata, 8, cancel, lambda _: None).size, (160, 90))
                fetched.clear()
                downloaded = []
                def download_fetch(url, *args):
                    downloaded.append((urlsplit(url).path, int(parse_qs(urlsplit(url).query)['sq'][0])))
                    return fetch(url, *args)
                with patch('live._segment', side_effect=download_fetch), patch('sources.inspect_source') as inspect:
                    path = download_section(metadata['url'], 2.8, 8, root / 'output', cancel, lambda _: None,
                                            snapshot=metadata, workers=2)
                    inspect.assert_not_called()
                self.assertCountEqual(downloaded, [('/video', 1), ('/video', 2), ('/audio', 1), ('/audio', 2)])
                with av.open(str(path)) as media:
                    self.assertAlmostEqual(media.duration / av.time_base, 10, delta=.3)
                    self.assertEqual(len(list(media.decode(video=0))), 100)
                with av.open(str(path)) as media:
                    self.assertTrue(list(media.decode(audio=0)))
                text = path.with_name('source.json').read_text(encoding='utf-8')
                self.assertNotIn('private', text)
                saved = json.loads(text)
                self.assertEqual(saved['time_basis'], 'youtube_dvr')
                self.assertEqual(saved['requested_start'], 2.8)
                self.assertAlmostEqual(saved['selected_start_utc'], window['start_utc'] + 2.8)


if __name__ == '__main__':
    unittest.main()
