"""Bounded parallel downloads of selected original HLS/DASH fragments."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import math
from pathlib import Path
import shutil
import tempfile
from threading import Event
import uuid

from i18n import tr


def download_files(jobs, workers, cancel, report):
    """Refill on completion; keep at most workers requests active across all tracks."""
    from live import _segment
    from sources import remaining_time
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError(tr('동시 다운로드 수는 1–16개로 지정해 주세요.'))
    if cancel.is_set():
        raise InterruptedError(tr('다운로드를 취소했습니다.'))
    stopped = Event()

    class Cancellation:
        def is_set(self):
            return cancel.is_set() or stopped.is_set()

    def fetch(job):
        url, path, headers = job
        path.write_bytes(_segment(url, Cancellation(), 128 * 1024 * 1024, headers))

    remaining = iter(jobs)
    estimate = remaining_time(len(jobs))
    report({'kind': 'download', 'step': 2, 'mode': 'parallel',
            'message': tr('원본 조각 병렬 다운로드 · 동시 최대 {workers}개', workers=workers)})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        completed = 0
        try:
            for job in remaining:
                pending.add(pool.submit(fetch, job))
                if len(pending) == workers:
                    break
            while pending:
                if cancel.is_set():
                    raise InterruptedError(tr('다운로드를 취소했습니다.'))
                done, pending = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                # Inspect every finished request before scheduling replacements after an error.
                for future in done:
                    future.result()
                for _ in done:
                    completed += 1
                    report({"kind": "download", "step": 2, "percent": round(completed / len(jobs) * 100),
                            "eta_seconds": estimate(completed),
                            "message": tr('원본 조각 다운로드 {done} / {total} · 동시 최대 {workers}개',
                                          done=completed, total=len(jobs), workers=workers)})
                    job = next(remaining, None)
                    if job is not None:
                        pending.add(pool.submit(fetch, job))
        finally:
            stopped.set()
            for future in pending:
                future.cancel()


def selected_tracks(formats, start, end, cancel):
    """None means unsupported: retain the exact selected formats via FFmpeg instead."""
    from live import _https, _segment, parse_window
    if not formats or any(f.get('has_drm') or f.get('protocol') not in
                          {'m3u8', 'm3u8_native', 'http_dash_segments'} for f in formats):
        return None
    tracks = []
    for fmt in formats:
        headers = fmt.get('http_headers') or {}
        if fmt['protocol'] in {'m3u8', 'm3u8_native'}:
            body = _segment(_https('', fmt['url']), cancel, 8 * 1024 * 1024, headers)
            if b'#EXT-X-ENDLIST' not in body:
                return None  # A rolling playlist has no trustworthy VOD time origin.
            try:
                window = parse_window(body, fmt['url'])
            except ValueError:
                return None  # Encrypted/ranged HLS remains the existing downloader's job.
            segments = window['segments']
            kind = 'hls'
        else:
            fragments = fmt.get('fragments')
            if not isinstance(fragments, list) or not fragments:
                return None
            seconds, init, segments = 0.0, None, []
            for index, fragment in enumerate(fragments):
                if fragment.get('range') or fragment.get('byte_range'):
                    return None
                url = _https(fmt.get('fragment_base_url') or fmt['url'], fragment.get('url') or fragment.get('path', ''))
                duration = fragment.get('duration')
                if index == 0 and duration is None:
                    init = url
                    continue
                if type(duration) not in (float, int) or not math.isfinite(duration) or duration <= 0:
                    return None
                segments.append(dict(url=url, start=seconds, duration=duration, init=init, discontinuity=False))
                seconds += duration
            kind = 'dash'
        chosen = [s for s in segments if s['start'] < end and s['start'] + s['duration'] > start]
        if (not chosen or chosen[0]['start'] > start or
                chosen[-1]['start'] + chosen[-1]['duration'] < end - .25 or
                any(s['discontinuity'] for s in chosen[1:]) or len({s['init'] for s in chosen}) != 1):
            return None
        tracks.append(dict(kind=kind, segments=chosen, headers=headers, format=fmt))
    return tracks


def download_vod(metadata, tracks, start, end, directory, cancel, report, workers):
    from sources import _media_duration, download_progress, find_tool, run_command
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-', dir=directory) as temporary:
        work = Path(temporary)
        jobs, inputs, maps = [], [], []
        for index, track in enumerate(tracks):
            chosen = track['segments']
            extension = 'm4s' if chosen[0]['init'] else 'ts'
            names = [f'track{index}-{n}.{extension}' for n in range(len(chosen))]
            init = f'init{index}.mp4' if chosen[0]['init'] else None
            if init:
                jobs.append((chosen[0]['init'], work / init, track['headers']))
            jobs.extend((s['url'], work / name, track['headers']) for s, name in zip(chosen, names))
            if track['kind'] == 'hls':
                path = work / f'track{index}.m3u8'
                lines = ['#EXTM3U', '#EXT-X-VERSION:7', '#EXT-X-PLAYLIST-TYPE:VOD',
                         f"#EXT-X-TARGETDURATION:{math.ceil(max(s['duration'] for s in chosen))}"]
                if init:
                    lines.append(f'#EXT-X-MAP:URI="{init}"')
                for s, name in zip(chosen, names):
                    lines.extend([f"#EXTINF:{s['duration']},", name])
                path.write_text('\n'.join(lines + ['#EXT-X-ENDLIST']) + '\n', encoding='utf-8')
            else:
                path = work / f'track{index}.mp4'
            inputs.append(path)
            track['local_parts'] = ([init] if init else []) + names
            if track['format'].get('vcodec') not in (None, 'none'):
                maps += ['-map', f'{index}:v:0']
            if track['format'].get('acodec') not in (None, 'none'):
                maps += ['-map', f'{index}:a:0']
        download_files(jobs, workers, cancel, report)
        for track, path in zip(tracks, inputs):
            if track['kind'] == 'dash':
                with path.open('wb') as output:
                    for name in track['local_parts']:
                        with (work / name).open('rb') as fragment:
                            shutil.copyfileobj(fragment, output)
        report({'kind': 'download', 'step': 3, 'message': tr('STEP 3/4 · 원본 영상·음성 병합 및 길이 검증')})
        output = work / 'video.mkv'
        args = [find_tool('ffmpeg'), '-v', 'error', '-nostdin', '-n', '-copyts']
        for path in inputs:
            if path.suffix == '.m3u8':
                args += ['-protocol_whitelist', 'file,crypto,data']
            args += ['-i', str(path)]
        args += maps + ['-c', 'copy', '-avoid_negative_ts', 'make_zero', '-progress', 'pipe:2', '-nostats', str(output)]
        run_command(args, cancel, report, on_line=download_progress(end - start, report, step=3))
        duration = _media_duration(output)
        acquired_start = min(t['segments'][0]['start'] for t in tracks)
        acquired_end = max(t['segments'][-1]['start'] + t['segments'][-1]['duration'] for t in tracks)
        if not math.isfinite(duration) or duration <= 0 or abs(duration - (acquired_end - acquired_start)) > 1:
            raise ValueError(tr('다운로드한 영상 길이가 선택한 구간과 다릅니다.'))
        if cancel.is_set():
            raise InterruptedError(tr('다운로드를 취소했습니다.'))
        source = {k: v for k, v in metadata.items() if not k.startswith('_') and k != 'window'}
        source.update(requested_start=start, requested_end=end, acquired_start=acquired_start,
                      acquired_end=acquired_end, local_duration=duration, alignment_verified=False)
        (work / 'source.json').write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf-8')
        for path in work.iterdir():
            if path.name not in {'video.mkv', 'source.json'}:
                path.unlink()
        destination = directory / ('youtube-' + uuid.uuid4().hex)
        work.rename(destination)
        report({'kind': 'download', 'step': 4, 'percent': 100, 'message': tr('STEP 4/4 · 완료 · {value1}', value1=destination / 'video.mkv')})
        return destination / 'video.mkv'
