"""YouTube metadata and explicitly bounded downloads, independent of the UI."""
from i18n import tr
import json
from datetime import datetime
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Callable
from urllib.parse import parse_qs, urlsplit
import uuid


def source_key(path):
    # Lexical only: never stat/resolve a network file on the UI thread.
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def youtube_url(value: str) -> str:
    """Accept individual YouTube videos and discard tracking/auth query fields."""
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"https", "http"} or parsed.username
                or parsed.password or parsed.port not in {None, 80, 443}):
            raise ValueError
        host = (parsed.hostname or "").lower()
        if host == "youtu.be":
            video_id = parsed.path.strip("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            else:
                parts = parsed.path.strip("/").split("/")
                video_id = parts[1] if len(parts) == 2 and parts[0] in {"live", "shorts", "embed"} else ""
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError(tr('YouTube 영상 링크를 입력해 주세요.')) from None
    return f"https://www.youtube.com/watch?v={video_id}"


def find_tool(name: str) -> str:
    filename = name + (".exe" if os.name == "nt" else "")
    for root in (Path(sys.executable).parent, Path(__file__).resolve().parent,
                 Path(__file__).resolve().parent.parent):
        candidate = root / "tools" / filename
        if candidate.is_file():
            return str(candidate)
    candidate = Path(sys.executable).parent / filename
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(tr('{name} 실행 파일을 찾을 수 없습니다.', name=name))
    return found


def _stop(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_command(args: list[str], cancel: threading.Event,
                report: Callable[[str], None], *, cwd=None, on_line=None) -> str:
    """Run in an isolated process tree so cancellation includes FFmpeg/EJS."""
    if cancel.is_set():
        raise InterruptedError(tr('작업을 취소했습니다.'))
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, cwd=cwd, text=True, encoding="utf-8",
                          errors="replace", **options) as process:
        readers = []
        chunks = [[], []]
        if on_line:
            def read(stream, parts, callback):
                for line in stream:
                    parts.append(line)
                    if callback:
                        callback(line.strip())
            for stream, parts, callback in ((process.stdout, chunks[0], lambda line: on_line(line) if not line.startswith("{") else None),
                                             (process.stderr, chunks[1], on_line)):
                reader = threading.Thread(target=read, args=(stream, parts, callback), daemon=True)
                reader.start()
                readers.append(reader)
        try:
            while True:
                if cancel.is_set():
                    raise InterruptedError(tr('작업을 취소했습니다.'))
                try:
                    if on_line:
                        process.wait(timeout=0.2)
                        for reader in readers:
                            reader.join()
                        output, error = ("".join(part) for part in chunks)
                    else:
                        output, error = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _stop(process)
            for reader in readers:
                reader.join()
            raise
        if process.returncode:
            # URLs may contain signed tokens; keep them out of UI logs.
            detail = re.sub(r"https?://\S+", "[URL]", error.strip())[-1200:]
            raise RuntimeError(detail or tr('영상 처리 작업이 실패했습니다.'))
    return output


def remaining_time(total):
    """Estimate from the last 10 seconds, excluding startup and waiting for samples."""
    from yt_dlp.utils.progress import ProgressCalculator
    calculator, initial, previous = None, 0, 0

    def update(completed):
        nonlocal calculator, initial, previous
        if completed >= total > 0:
            return 0
        if total <= 0 or completed <= 0:
            calculator = None
            return None
        if calculator is None or completed < previous:
            initial = completed
            calculator = ProgressCalculator(initial)
            calculator.SAMPLING_WINDOW = 10
            calculator.GRACE_PERIOD = 3
            calculator.total = total
        previous = completed
        calculator.update(completed - initial)
        return calculator.eta.value

    return update


def download_progress(seconds, report, step=2):
    """Read FFmpeg's machine-readable elapsed output time, never its signed URLs."""
    estimate = remaining_time(seconds)
    def receive(line):
        if line:
            report(re.sub(r"https?://\S+", "[URL]", line))
        if line.startswith("out_time_us="):
            try:
                elapsed = max(0, int(line.partition("=")[2]) / 1000000)
            except ValueError:
                return
            percent = min(99, round(elapsed / seconds * 100)) if seconds > 0 else 0
            eta = estimate(elapsed)
            report({"kind": "download", "step": step, "percent": percent, "eta_seconds": eta,
                    "message": tr('영상 처리 {percent}% · {elapsed:.1f} / {seconds:.1f}초', percent=percent, elapsed=elapsed, seconds=seconds)})
    return receive


def _command() -> list[str]:
    command = ([sys.executable, "--yt-dlp"] if getattr(sys, "frozen", False) or "__compiled__" in globals()
               else [sys.executable, "-m", "yt_dlp"])
    return command + ["--ignore-config", "--no-playlist", "--no-warnings",
                      "--socket-timeout", "20", "--retries", "2",
                      "--js-runtimes", "deno:" + find_tool("deno")]


def inspect_source(url: str, cancel: threading.Event,
                   report: Callable[[str], None]) -> dict:
    url = youtube_url(url)
    report(tr('영상 정보를 확인하고 있습니다.'))
    output = run_command(_command() + ["--skip-download", "--format", "bv*+ba/b", "--dump-single-json", "--", url], cancel, report)
    data = json.loads(output)
    if not isinstance(data, dict) or data.get("_type") in {"playlist", "multi_video"}:
        raise ValueError(tr('개별 영상 링크를 선택해 주세요.'))
    result = {"url": url, "id": data.get("id"), "title": data.get("title") or "YouTube",
            "uploader": data.get("uploader") or data.get("channel") or "",
            "duration": data.get("duration"), "live_status": data.get("live_status"),
            "is_live": bool(data.get("is_live")), "was_live": bool(data.get("was_live"))}
    stamp = (data.get("release_timestamp") or data.get("timestamp")) if result["is_live"] or result["was_live"] else data.get("timestamp")
    try:
        result["video_date"] = (datetime.fromtimestamp(stamp).astimezone().strftime(tr('%Y-%m-%d %H시')) if stamp else
                                datetime.strptime(data.get("upload_date") or "", "%Y%m%d").strftime("%Y-%m-%d"))
    except (ValueError, TypeError, OverflowError, OSError):
        result["video_date"] = tr('날짜 미제공')
    if result["is_live"]:
        from live import inspect_window
        report(tr('접근 가능한 라이브 구간을 확인하고 있습니다.'))
        result["window"] = inspect_window(data.get("formats", []), cancel)
        result["duration"] = result["window"]["duration"]
    else:
        # Signed media URLs stay in memory, never in logs, saved sessions or source.json.
        keys = ('url', 'protocol', 'vcodec', 'acodec', 'http_headers', 'fragments', 'fragment_base_url', 'has_drm')
        result['_download_formats'] = [{key: fmt[key] for key in keys if key in fmt}
                                       for fmt in (data.get('requested_formats') or [data])]
    return result


def _media_duration(path: Path) -> float:
    import av

    with av.open(str(path)) as container:
        if not container.streams.video or container.duration is None:
            raise RuntimeError(tr('다운로드한 영상의 길이를 확인할 수 없습니다.'))
        return container.duration / av.time_base


def download_section(url: str, start: float, end: float, directory: Path,
                     cancel: threading.Event, report: Callable[[str], None], snapshot=None, workers=4) -> Path:
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError(tr('동시 다운로드 수는 1–16개로 지정해 주세요.'))
    if (isinstance(start, bool) or isinstance(end, bool)
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end))
            or not 0 <= start < end):
        raise ValueError(tr('시작과 끝 시각을 확인해 주세요.'))
    metadata = snapshot if snapshot is not None else inspect_source(url, cancel, report)
    report({"kind": "download", "step": 1, "message": tr('STEP 1/4 · 영상 정보와 선택 구간 확인')})
    if metadata["url"] != youtube_url(url):
        raise ValueError(tr('조회한 영상과 다운로드 대상이 다릅니다.'))
    if metadata.get("is_live") and metadata.get("window"):
        from live import download_window
        return download_window(metadata, start, end, directory, cancel, report, workers=workers)
    duration = metadata["duration"]
    if metadata["is_live"] or metadata["live_status"] in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError(tr('방송 종료 후 처리된 영상 또는 로컬 녹화를 선택해 주세요.'))
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration <= 0):
        raise ValueError(tr('영상 길이를 확인할 수 없습니다. 영상 정보를 다시 확인해 주세요.'))
    if end > duration:
        raise ValueError(tr('선택한 끝 시각이 영상 길이를 넘습니다.'))
    from fragments import selected_tracks, download_vod
    tracks = selected_tracks(metadata.get('_download_formats'), start, end, cancel)
    if tracks:
        return download_vod(metadata, tracks, start, end, directory, cancel, report, workers)
    report({'kind': 'download', 'step': 2, 'mode': 'standard',
            'message': tr('일반 다운로드 · 이 영상은 조각 병렬 다운로드를 지원하지 않습니다.')})
    ffmpeg = find_tool("ffmpeg")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
        work = Path(temporary)
        report({"kind": "download", "step": 2, "message": tr('STEP 2/4 · 선택 구간 원본 다운로드 · {start:g}–{end:g}초', start=start, end=end)})
        args = _command() + [
            "--ffmpeg-location", str(Path(ffmpeg).parent),
            "--download-sections", f"*{start}-{end}",
            "--no-force-keyframes-at-cuts", "--no-simulate", "--no-progress",
            "--downloader-args", "ffmpeg:-c copy -progress pipe:2 -nostats",
            "--downloader-args", "ffmpeg_i:-request_size 8388608 -initial_request_size 8388608 -short_seek_size 8388608 -multiple_requests 1",
            "--no-overwrites", "--format", "bv*+ba/b", "--merge-output-format", "mkv",
            "--output", str(work / "video.%(ext)s"),
            "--print", "after_move:%()j", "--", metadata["url"],
        ]
        output = run_command(args, cancel, report, on_line=download_progress(end - start, report))
        report({"kind": "download", "step": 3, "message": tr('STEP 3/4 · 다운로드 파일과 영상 길이 검증')})
        rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        if len(rows) != 1:
            raise RuntimeError(tr('다운로드한 구간 정보를 확인할 수 없습니다.'))
        result = rows[0]
        if (result.get("section_start") != start or result.get("section_end") != end):
            raise RuntimeError(tr('다운로드한 구간이 선택한 시각과 일치하지 않습니다.'))
        path = Path(result.get("filepath", "")).resolve()
        if path.parent != work.resolve() or not path.is_file() or not path.stat().st_size:
            raise RuntimeError(tr('다운로드 결과 파일을 확인할 수 없습니다.'))
        # Stream-copy cuts follow source keyframes; requested and actual lengths can differ.
        actual_duration = _media_duration(path)
        if not math.isfinite(actual_duration) or actual_duration <= 0 or actual_duration > duration + 2:
            raise RuntimeError(tr('다운로드한 영상 길이가 선택한 구간과 다릅니다.'))
        if cancel.is_set():
            raise InterruptedError(tr('작업을 취소했습니다.'))
        metadata["local_duration"] = actual_duration
        # Keep the requested offset separate: source PTS alignment still needs media verification.
        metadata.update(requested_start=start, requested_end=end, alignment_verified=False)
        public = {key: value for key, value in metadata.items() if not key.startswith('_') and key != 'window'}
        (work / "source.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        destination = directory / ("youtube-" + uuid.uuid4().hex)
        work.rename(destination)
        report({"kind": "download", "step": 4, "percent": 100, "message": tr('STEP 4/4 · 완료 · {value1}', value1=destination / path.name)})
        return destination / path.name
