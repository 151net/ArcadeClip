"""Frozen HLS windows: only selected media segments are fetched, never the full live stream."""
from i18n import tr
from datetime import datetime
import math
import re
from urllib.parse import urljoin, urlsplit
import urllib.error

from jackets import _fetch


def _https(base, value):
    url = urljoin(base, value)
    if urlsplit(url).scheme != "https":
        raise ValueError(tr('HTTPS 라이브 조각만 지원합니다.'))
    return url


def parse_window(body, url):
    text = body.decode("utf-8-sig")
    if not text.startswith("#EXTM3U"):
        raise ValueError(tr('라이브 재생 목록을 확인할 수 없습니다.'))
    segments, duration, seconds, sequence, clock, first_clock = [], None, 0.0, 0, None, None
    discontinuity, init = False, None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            raise ValueError(tr('개별 화질의 라이브 재생 목록이 필요합니다.'))
        if line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line:
            raise ValueError(tr('암호화된 라이브 조각은 지원하지 않습니다.'))
        if line.startswith("#EXT-X-BYTERANGE"):
            raise ValueError(tr('이 라이브 조각의 범위 형식은 지원하지 않습니다.'))
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            stamp = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError(tr('라이브 시각의 시간대를 확인할 수 없습니다.'))
            next_clock = stamp.timestamp()
            if clock is not None and abs(next_clock - clock) > .5:
                discontinuity = True
            clock = next_clock
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            discontinuity = True
        elif line.startswith("#EXT-X-MAP:"):
            match = re.search(r'URI="([^"\r\n]+)"', line)
            if not match or "BYTERANGE=" in line:
                raise ValueError(tr('라이브 초기화 조각을 확인할 수 없습니다.'))
            init = _https(url, match[1])
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError(tr('라이브 조각 길이가 올바르지 않습니다.'))
        elif not line.startswith("#"):
            if duration is None:
                raise ValueError(tr('라이브 조각의 시각을 확인할 수 없습니다.'))
            if not segments:
                first_clock = clock
            segments.append({"url": _https(url, line), "start": seconds, "duration": duration,
                             "sequence": sequence + len(segments), "discontinuity": discontinuity, "init": init, "start_utc": clock})
            seconds += duration
            if clock is not None:
                clock += duration
            duration, discontinuity = None, False
    if duration is not None:
        raise ValueError(tr('라이브 재생 목록의 마지막 조각이 불완전합니다.'))
    if not segments:
        raise ValueError(tr('현재 접근 가능한 라이브 구간이 없습니다.'))
    return {"segments": segments, "duration": seconds, "start_utc": first_clock}


def inspect_window(formats, cancel):
    choices = [item for item in formats if item.get("protocol") in {"m3u8", "m3u8_native"}
               and not item.get("has_drm")
               and item.get("url", "").startswith("https://")]
    videos = [item for item in choices if item.get("vcodec") not in {None, "none"}]
    if not videos:
        raise ValueError(tr('이 라이브에서 접근 가능한 과거 구간을 확인할 수 없습니다.'))
    video = max(videos, key=lambda item: (item.get("height") or 0, item.get("fps") or 0, item.get("tbr") or 0))
    selected = [video]
    if video.get("acodec") in {None, "none"}:
        # yt-dlp orders formats by preference; audio-only HLS may omit its codec name.
        audio = next((item for item in reversed(choices)
                      if item.get("vcodec") == "none" and item.get("acodec") != "none"), None)
        if audio is None:
            raise ValueError(tr('이 라이브에서 접근 가능한 과거 구간을 확인할 수 없습니다.'))
        selected.append({**audio, "acodec": audio.get("acodec") or "unknown"})
    tracks = []
    for fmt in selected:
        url, headers = fmt["url"], fmt.get("http_headers")
        track = parse_window(_segment(url, cancel, 8 * 1024 * 1024, headers), url)
        tracks.append({**track, "kind": "hls", "format": fmt, "headers": headers})
    window = {**tracks[0], "height": video.get("height"), "tracks": tracks}
    if len(tracks) > 1:
        if any(track["start_utc"] is None for track in tracks):
            raise ValueError(tr('라이브 시각의 시간대를 확인할 수 없습니다.'))
        origin = max(track["start_utc"] for track in tracks)
        for track in tracks:
            for segment in track["segments"]:
                segment["start"] = segment["start_utc"] - origin
        duration = min(track["segments"][-1]["start"] + track["segments"][-1]["duration"] for track in tracks)
        if duration <= 0:
            raise ValueError(tr('현재 접근 가능한 라이브 구간이 없습니다.'))
        window.update(start_utc=origin, duration=duration)
    return window


def _segment(url, cancel, limit, headers=None):
    try:
        # Stop on rate limits, rather than multiplying retries across workers.
        return _fetch(url, cancel, limit, None, 1, headers)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise ValueError(tr('요청이 너무 많아 다운로드를 중지했습니다. 잠시 후 동시 다운로드 수를 줄여 다시 시도해 주세요.')) from error
        raise ValueError(tr('다운로드 조각에 접근할 수 없습니다. 영상 정보를 다시 확인해 주세요.')) from error
    except OSError as error:
        raise ValueError(tr('선택한 라이브 조각이 만료됐거나 연결이 끊겼습니다. 정보를 다시 조회해 주세요.')) from error


def download_window(metadata, start, end, directory, cancel, report, workers=4):
    window = metadata.get("window")
    if not window or not 0 <= start < end <= window["duration"]:
        raise ValueError(tr('조회한 라이브 구간 안에서 시작과 끝을 선택해 주세요.'))
    if 'dvr' in window:
        from live_dvr import download_dvr
        return download_dvr(metadata, start, end, directory, cancel, report, workers)
    tracks = []
    for track in window.get("tracks") or [{**window, "kind": "hls", "format": {"vcodec": "unknown", "acodec": "unknown"}}]:
        chosen = [segment for segment in track["segments"]
                  if segment["start"] < end and segment["start"] + segment["duration"] > start]
        if not chosen or any(segment["discontinuity"] for segment in chosen[1:]):
            raise ValueError(tr('방송 단절 경계를 포함한 구간입니다. 경계 앞뒤로 나눠 선택해 주세요.'))
        if chosen[0]["start"] > start or chosen[-1]["start"] + chosen[-1]["duration"] < end - .001:
            raise ValueError(tr('조회한 라이브 구간 안에서 시작과 끝을 선택해 주세요.'))
        if len({segment["init"] for segment in chosen}) > 1:
            raise ValueError(tr('영상 형식이 바뀐 구간입니다. 앞뒤로 나눠 선택해 주세요.'))
        tracks.append({**track, "segments": chosen, "headers": track.get("headers")})
    source = {key: metadata.get(key) for key in ("url", "id", "title", "is_live", "live_status", "was_live", "uploader", "video_date")}
    first = tracks[0]["segments"][0]
    source.update(time_basis="hls_snapshot", snapshot_start_utc=window["start_utc"],
                  first_sequence=first["sequence"],
                  selected_start_utc=first["start_utc"] + start - first["start"] if first["start_utc"] is not None else None)
    from fragments import download_vod
    return download_vod(source, tracks, start, end, directory, cancel, report, workers)
