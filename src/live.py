"""Frozen HLS windows: only selected media segments are fetched, never the full live stream."""
from i18n import tr
from datetime import datetime
import json
import math
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin, urlsplit
import uuid
import urllib.error

from jackets import _fetch
from sources import _media_duration, find_tool, run_command


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
    # Choose a muxed HLS rendition so the selected audio/video share one timeline.
    choices = [item for item in formats if item.get("protocol") in {"m3u8", "m3u8_native"}
               and item.get("vcodec") not in {None, "none"} and item.get("acodec") not in {None, "none"}
               and item.get("url", "").startswith("https://")]
    if not choices:
        raise ValueError(tr('이 라이브에서 접근 가능한 과거 구간을 확인할 수 없습니다.'))
    selected = max(choices, key=lambda item: (item.get("height") or 0, item.get("tbr") or 0))
    url = selected["url"]
    window = parse_window(_segment(url, cancel, 8 * 1024 * 1024, selected.get('http_headers')), url)
    window["height"] = selected.get("height")
    window['headers'] = selected.get('http_headers')
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
    chosen = [segment for segment in window["segments"]
              if segment["start"] < end and segment["start"] + segment["duration"] > start]
    if not chosen or any(segment["discontinuity"] for segment in chosen[1:]):
        raise ValueError(tr('방송 단절 경계를 포함한 구간입니다. 경계 앞뒤로 나눠 선택해 주세요.'))
    if len({segment["init"] for segment in chosen}) > 1:
        raise ValueError(tr('영상 형식이 바뀐 구간입니다. 앞뒤로 나눠 선택해 주세요.'))
    if cancel.is_set():
        raise InterruptedError(tr('다운로드를 취소했습니다.'))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_tool("ffmpeg")
    with tempfile.TemporaryDirectory(prefix=".live-", dir=directory) as temporary:
        work = Path(temporary)
        lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-PLAYLIST-TYPE:VOD",
                 f"#EXT-X-TARGETDURATION:{math.ceil(max(segment['duration'] for segment in chosen))}",
                 "#EXT-X-MEDIA-SEQUENCE:0"]
        if chosen[0]["init"]:
            (work / "init.mp4").write_bytes(_segment(chosen[0]["init"], cancel, 16 * 1024 * 1024, window.get('headers')))
            lines.append('#EXT-X-MAP:URI="init.mp4"')
        from fragments import download_files
        jobs = []
        for index, segment in enumerate(chosen):
            name = f"segment-{index}." + ("m4s" if segment['init'] else "ts")
            jobs.append((segment['url'], work / name, window.get('headers')))
            lines.extend([f"#EXTINF:{segment['duration']},", name])
        download_files(jobs, workers, cancel, report)
        lines.append("#EXT-X-ENDLIST")
        playlist = work / "selected.m3u8"
        playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = work / "video.mkv"
        report({"kind": "download", "step": 3, "message": tr('STEP 3/4 · 라이브 원본 병합 및 길이 검증')})
        from sources import download_progress
        run_command([ffmpeg, "-v", "error", "-nostdin", "-n", "-protocol_whitelist", "file,crypto,data",
                     "-i", str(playlist), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                     "-progress", "pipe:2", "-nostats", str(output)],
                    cancel, report, on_line=download_progress(end - start, report, step=3))
        duration = _media_duration(output)
        selected_duration = sum(segment["duration"] for segment in chosen)
        if not math.isfinite(duration) or duration <= 0 or abs(duration - selected_duration) > 1:
            raise ValueError(tr('확보한 라이브 구간 길이가 선택과 다릅니다.'))
        if cancel.is_set():
            raise InterruptedError(tr('다운로드를 취소했습니다.'))
        # Persist timing and public source identity, never expiring signed media URLs.
        source = {key: metadata.get(key) for key in ("url", "id", "title", "is_live", "live_status", "was_live", "uploader", "video_date")}
        source.update(requested_start=start, requested_end=end, local_duration=duration,
                      acquired_start=chosen[0]["start"], acquired_end=chosen[-1]["start"] + chosen[-1]["duration"],
                      time_basis="hls_snapshot", snapshot_start_utc=window["start_utc"],
                      first_sequence=chosen[0]["sequence"],
                      selected_start_utc=(chosen[0]["start_utc"] + start - chosen[0]["start"]
                                          if chosen[0]["start_utc"] is not None else None), alignment_verified=False)
        (work / "source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in work.iterdir():
            if path.name not in {"video.mkv", "source.json"}:
                path.unlink()
        destination = directory / ("youtube-" + uuid.uuid4().hex)
        work.rename(destination)
        report({"kind": "download", "step": 4, "percent": 100,
                "message": tr('STEP 4/4 · 라이브 다운로드 완료 · {value1}', value1=destination / 'video.mkv')})
        return destination / "video.mkv"
