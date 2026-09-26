"""Bounded YouTube DVR access using real fragment PTS, not sequence * duration."""
import io
import json
import math
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import av

from analysis_common import check_cancel
from i18n import tr
from live import _https, _segment


def fragment_url(fmt, sequence):
    parts = urlsplit(_https('', fmt['url']))
    query = dict(parse_qsl(parts.query))
    query['sq'] = str(sequence)
    return urlunsplit(parts._replace(query=urlencode(query)))


def refresh_urls(dvr, cancel):
    """Replace signed URLs atomically; keep the original DVR bounds and media identity."""
    from sources import _command, run_command, youtube_url
    check_cancel(cancel)
    url = youtube_url(dvr.get('source_url', ''))
    data = json.loads(run_command(_command() + ['--live-from-start', '--skip-download', '--format', 'bv*+ba/b',
                                              '--dump-single-json', '--', url], cancel, lambda _: None))
    if data.get('id') != urlsplit(url).query.partition('v=')[2]:
        raise ValueError(tr('조회한 영상과 다운로드 대상이 다릅니다.'))
    replacements = []
    for old in dvr['formats'] + [dvr['preview']]:
        fresh = next((fmt for fmt in data.get('formats', [])
                      if fmt.get('format_id') == old.get('format_id')
                      and fmt.get('protocol') == 'http_dash_segments_generator'
                      and (fmt.get('vcodec'), fmt.get('acodec')) == (old.get('vcodec'), old.get('acodec'))
                      and not fmt.get('has_drm') and fmt.get('url', '').startswith('https://')), None)
        old_id = dict(parse_qsl(urlsplit(old['url']).query)).get('id')
        if fresh is None or (old_id is not None and dict(parse_qsl(urlsplit(fresh['url']).query)).get('id') != old_id):
            raise ValueError(tr('영상 정보를 다시 확인해 주세요.'))
        replacements.append(fresh)
    check_cancel(cancel)
    dvr['formats'], dvr['preview'] = replacements[:-1], replacements[-1]
    dvr.pop('_sample', None)


def _head(fmt, cancel, sequence=None):
    check_cancel(cancel)
    url = _https('', fmt['url']) if sequence is None else fragment_url(fmt, sequence)
    try:
        with urlopen(Request(url, headers=fmt.get('http_headers') or {}, method='HEAD'), timeout=10) as response:
            check_cancel(cancel)
            return response.headers
    except HTTPError as error:
        if sequence is not None and error.code in (404, 410):
            return None
        if error.code == 429:
            raise ValueError(tr('요청이 너무 많아 다운로드를 중지했습니다. 잠시 후 동시 다운로드 수를 줄여 다시 시도해 주세요.')) from None
        raise ValueError(tr('다운로드 조각에 접근할 수 없습니다. 영상 정보를 다시 확인해 주세요.')) from None


def sample(dvr, sequence, cancel):
    cached = dvr.get('_sample')
    if cached and cached['sequence'] == sequence:
        check_cancel(cancel)
        return cached
    fmt = dvr['preview']
    url = fragment_url(fmt, sequence)
    body = _segment(url, cancel, 128 * 1024 * 1024, fmt.get('http_headers'))
    start, end = fragment_times(io.BytesIO(body), cancel)
    result = dict(sequence=sequence, start=start, end=end, url=url, body=body)
    dvr['_sample'] = result
    return result


def fragment_times(source, cancel, kind='video'):
    times = []
    with av.open(source) as container:
        stream = next((stream for stream in container.streams if stream.type == kind), None)
        if stream is None:
            raise ValueError(tr('라이브 조각의 시각을 확인할 수 없습니다.'))
        for packet in container.demux(stream):
            check_cancel(cancel)
            if packet.size and packet.pts is not None and packet.duration:
                times.append((float(packet.pts * packet.time_base), float((packet.pts + packet.duration) * packet.time_base)))
    if not times:
        raise ValueError(tr('라이브 조각의 시각을 확인할 수 없습니다.'))
    start, end = min(t[0] for t in times), max(t[1] for t in times)
    if not math.isfinite(start + end) or end <= start:
        raise ValueError(tr('라이브 조각 길이가 올바르지 않습니다.'))
    return start, end


def inspect_dvr(data, cancel):
    formats = [fmt for fmt in data.get('formats', []) if fmt.get('protocol') == 'http_dash_segments_generator'
               and not fmt.get('has_drm') and fmt.get('url', '').startswith('https://')]
    videos = [fmt for fmt in formats if fmt.get('vcodec') not in (None, 'none')]
    audios = [fmt for fmt in formats if fmt.get('vcodec') == 'none' and fmt.get('acodec') not in (None, 'none')]
    if not videos or not audios:
        return None
    selected = [max(videos, key=lambda f: (f.get('height') or 0, f.get('fps') or 0, f.get('tbr') or 0)), audios[-1]]
    preview = min(videos, key=lambda f: (f.get('height') or float('inf'), f.get('tbr') or float('inf')))
    formats = selected + [preview]
    last = min(int(_head(fmt, cancel).get('X-Head-Seqnum', 0)) - 1 for fmt in formats)
    if last < 0:
        return None
    first = 0
    for fmt in formats:
        if _head(fmt, cancel, first) is not None:
            continue
        # Availability is a rolling interval; locate its oldest retained fragment.
        low, high = first + 1, last
        while low < high:
            middle = (low + high) // 2
            if _head(fmt, cancel, middle) is None:
                low = middle + 1
            else:
                high = middle
        first = low
        if _head(fmt, cancel, first) is None:
            return None
    target_duration = preview.get('target_duration') or 5
    if type(target_duration) not in (int, float) or not math.isfinite(target_duration) or target_duration <= 0:
        raise ValueError(tr('라이브 조각 길이가 올바르지 않습니다.'))
    dvr = dict(formats=selected, preview=preview, first=first, last=last, target_duration=target_duration)
    begin = sample(dvr, first, cancel)['start']
    end = sample(dvr, last, cancel)['end']
    if end <= begin:
        raise ValueError(tr('현재 접근 가능한 라이브 구간이 없습니다.'))
    dvr.update(origin=begin, end=end)
    stamp = data.get('release_timestamp')
    return dict(dvr=dvr, duration=end - begin, start_utc=stamp + begin if stamp is not None else None)


def locate(dvr, seconds, cancel):
    try:
        return _locate(dvr, seconds, cancel)
    except ValueError as error:
        if not isinstance(error.__cause__, HTTPError) or error.__cause__.code != 403:
            raise
        refresh_urls(dvr, cancel)
        return _locate(dvr, seconds, cancel)


def _locate(dvr, seconds, cancel):
    target = dvr['origin'] + seconds
    if not dvr['origin'] <= target < dvr['end']:
        raise ValueError(tr('조회한 라이브 구간 안에서 시작과 끝을 선택해 주세요.'))
    low, high = dvr['first'], dvr['last']
    sequence = max(low, min(high, low + int(seconds / dvr['target_duration'])))
    adjusted = False
    # Sequence durations can vary (including the first fragment); verify actual PTS.
    while low <= high:
        item = sample(dvr, sequence, cancel)
        if item['start'] <= target < item['end']:
            return item
        if target < item['start']:
            high = sequence - 1
        else:
            low = sequence + 1
        estimate = sequence + math.floor((target - item['start']) / dvr['target_duration'])
        sequence = (low + high) // 2 if adjusted else max(low, min(high, estimate))
        adjusted = True
    raise ValueError(tr('방송 단절 경계를 포함한 구간입니다. 경계 앞뒤로 나눠 선택해 주세요.'))


def download_dvr(metadata, start, end, directory, cancel, report, workers):
    try:
        return _download_dvr(metadata, start, end, directory, cancel, report, workers)
    except ValueError as error:
        if not isinstance(error.__cause__, HTTPError) or error.__cause__.code != 403:
            raise
        report({'kind': 'download', 'step': 1, 'message': tr('다운로드 주소를 갱신하고 같은 구간을 다시 시도합니다.')})
        refresh_urls(metadata['window']['dvr'], cancel)
        return _download_dvr(metadata, start, end, directory, cancel, report, workers)


def _download_dvr(metadata, start, end, directory, cancel, report, workers):
    from fragments import download_vod
    dvr = metadata['window']['dvr']
    report({'kind': 'download', 'step': 1, 'message': tr('다운로드할 구간의 시작·끝 조각을 확인하고 있습니다.')})
    first = _locate(dvr, start, cancel)
    last = _locate(dvr, max(start, end - .000001), cancel)
    # Inspect timing from the downloaded originals instead of fetching every preview first.
    tracks = [dict(kind='dash', format=fmt, headers=fmt.get('http_headers'), time_origin=dvr['origin'],
                   segments=[dict(sequence=sequence, url=fragment_url(fmt, sequence), init=None)
                             for sequence in range(first['sequence'], last['sequence'] + 1)]) for fmt in dvr['formats']]
    origin = metadata['window']['start_utc']
    source = {**metadata, 'time_basis': 'youtube_dvr', 'snapshot_start_utc': origin,
              'selected_start_utc': origin + start if origin is not None else None, 'first_sequence': first['sequence']}
    return download_vod(source, tracks, start, end, directory, cancel, report, workers)
