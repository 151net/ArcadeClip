"""Accurate local clip export with cancellation and atomic, non-overwriting publication."""
from i18n import tr
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile

from media import format_time
from sources import find_tool, run_command


def _probe(path, cancel, report):
    data = json.loads(run_command([find_tool("ffprobe"), "-v", "error", "-show_streams",
                                  "-show_format", "-of", "json", str(path)], cancel, report))
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(tr('영상 스트림을 찾을 수 없습니다.'))
    container = data.get("format", {})
    origin = float(video.get("start_time", container.get("start_time", 0)))
    offset = origin - float(container.get("start_time", origin))
    duration = float(video.get("duration", float(container.get("duration", 0)) - max(0, offset)))
    if not all(math.isfinite(v) for v in (duration, offset)) or duration <= 0:
        raise ValueError(tr('영상 길이를 확인할 수 없습니다.'))
    return duration, offset, video


def _filename(row):
    title = "_".join(row.get(key, "") for key in ("player", "player2", "title") if row.get(key))
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (title or Path(row["source"]).stem)).strip(" .")[:100] or "clip"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", stem):
        stem = "_" + stem
    start, end = (format_time(row[key]).replace(":", "-") for key in ("start", "end"))
    return f"{stem}-{start}-{end}"


def _publish(source, directory, name):
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        destination = directory / f"{name}{suffix}{source.suffix}"
        try:
            # Windows rename and POSIX link both refuse to replace an existing file.
            if os.name == "nt":
                source.rename(destination)
            else:
                os.link(source, destination)
            return destination
        except FileExistsError:
            index += 1


def _gpu_encoder(ffmpeg, cancel, report):
    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        try:
            run_command([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=640x360:rate=60",
                         "-frames:v", "1", "-pix_fmt", "yuv420p", "-c:v", encoder, "-f", "null", "-"], cancel, report)
            logging.info("Export GPU encoder: %s", encoder)
            report(tr('GPU 저장 · {encoder}', encoder=encoder))
            return encoder
        except RuntimeError as error:
            logging.debug("GPU encoder unavailable: %s: %s", encoder, error)
    report(tr('사용 가능한 GPU 인코더가 없어 CPU로 저장합니다.'))
    logging.info("Export GPU unavailable; using CPU")
    return None


def export_clips(rows, directory, cancel, report, *, mode="accurate", crf=18, preset="fast", audio=True,
                 format="mp4", target_mb=0, height=0, fps=0, gain=0, audio_kbps=192, channels=0, gpu=False):
    """Export snapshots of local ranges; retain successful clips when later ones fail or cancel."""
    if type(gpu) is not bool or type(audio) is not bool or mode not in ("accurate", "fast") or type(crf) is not int or not 0 <= crf <= 51 or preset not in ("ultrafast", "fast", "medium", "slow"):
        raise ValueError(tr('저장 설정을 확인해 주세요.'))
    if not rows:
        raise ValueError(tr('저장할 구간을 선택해 주세요.'))
    if format not in ("mp4", "mkv", "mov", "webm") or height not in (0, 480, 720, 1080, 1440, 2160) or fps not in (0, 24, 30, 60) or channels not in (0, 1, 2):
        raise ValueError(tr('파일 형식·해상도·프레임·채널 설정을 확인해 주세요.'))
    for value, low, high in ((target_mb, 0, 100000), (gain, -60, 24), (audio_kbps, 32, 320)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(tr('용량 또는 오디오 설정을 확인해 주세요.'))
    if mode == "fast" and (format != "mp4" or target_mb or height or fps or gain or channels or audio_kbps != 192):
        raise ValueError(tr('변환 설정은 정확하게 자르기에서 사용할 수 있습니다.'))
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("source"), str)
                or any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k])
                       for k in ("start", "end")) or not 0 <= row["start"] < row["end"]):
            raise ValueError(tr('저장할 구간의 시작과 끝 시각을 확인해 주세요.'))
    result = {"saved": [], "failed": [], "cancelled": False}
    directory = Path(directory).expanduser().resolve()
    try:
        if cancel.is_set():
            raise InterruptedError()
        ffmpeg = find_tool("ffmpeg")
        encoder = _gpu_encoder(ffmpeg, cancel, report) if gpu and mode == "accurate" and format != "webm" else None
        if gpu and format == "webm":
            report(tr('WebM은 CPU로 저장합니다. GPU 저장은 MP4·MKV·MOV에서 지원합니다.'))
        directory.mkdir(parents=True, exist_ok=True)
        for index, row in enumerate(rows, 1):
            if cancel.is_set():
                raise InterruptedError()
            source = Path(row["source"]).expanduser().resolve()
            try:
                if not source.is_file():
                    raise ValueError(tr('원본 영상 파일을 찾을 수 없습니다.'))
                duration, offset, _ = _probe(source, cancel, report)
                start, end = row["start"], row["end"]
                if end > duration + .001:
                    raise ValueError(tr('선택한 끝 시각이 영상 길이를 넘습니다.'))
                report(tr('클립 저장 {index:,} / {value2:,} · {name}', index=index, value2=len(rows), name=source.name))
                with tempfile.TemporaryDirectory(prefix=".export-", dir=directory) as temporary:
                    output = Path(temporary) / f"clip.{format}"
                    filters = ["pad=ceil(iw/2)*2:ceil(ih/2)*2"]
                    if height:
                        filters.append(f"scale=-2:trunc(min(ih\\,{height})/2)*2")
                    if fps:
                        filters.append(f"fps={fps}")
                    rate = ["-crf", str(crf)]
                    if target_mb:
                        bitrate = int(target_mb * 1000000 * 8 * .98 / (end - start) - (audio_kbps * 1000 if audio else 0))
                        if bitrate < 32000:
                            raise ValueError(tr('클립 길이에 비해 목표 용량이 너무 작습니다. 용량을 늘려 주세요.'))
                        rate = ["-b:v", str(bitrate)]
                    codec = (["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", {"ultrafast": "8", "fast": "4", "medium": "2", "slow": "1"}[preset]]
                             if format == "webm" else ["-c:v", "libx264", "-preset", preset])
                    if format == "webm" and not target_mb:
                        rate += ["-b:v", "0"]
                    cpu_codec = codec + rate
                    if encoder:
                        codec = ["-c:v", encoder]
                        if not target_mb:
                            rate = {"h264_nvenc": ["-rc", "vbr", "-cq", str(crf), "-b:v", "0"],
                                    "h264_qsv": ["-global_quality", str(crf)],
                                    "h264_amf": ["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]}[encoder]
                    encoding = (["-c", "copy"] if mode == "fast" else [
                        "-vf", ",".join(filters), *codec, *rate, "-pix_fmt", "yuv420p",
                        "-fps_mode", "vfr", "-c:a", "libopus" if format == "webm" else "aac", "-b:a", f"{audio_kbps}k"])
                    if audio and mode == "accurate":
                        if gain:
                            encoding += ["-af", f"volume={gain}dB"]
                        if channels:
                            encoding += ["-ac", str(channels)]
                    if not audio:
                        encoding += ["-an"]
                    command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n",
                                 "-ss", str(start + offset), "-i", str(source), "-t", str(end - start),
                                 "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", *encoding,
                                 *(["-movflags", "+faststart"] if format in ("mp4", "mov") else []), str(output)]
                    try:
                        run_command(command, cancel, report)
                    except RuntimeError:
                        if not encoder:
                            raise
                        logging.exception("GPU export failed; retrying clip on CPU")
                        report(tr('GPU 저장에 실패해 이 클립부터 CPU로 다시 저장합니다.'))
                        output.unlink(missing_ok=True)
                        first = command.index("-c:v")
                        command[first:first + len(codec + rate)] = cpu_codec
                        encoder = None
                        run_command(command, cancel, report)
                    actual, _, video = _probe(output, cancel, report)
                    numerator, denominator = video.get("avg_frame_rate", "0/1").split("/")
                    actual_fps = float(numerator) / float(denominator) if float(denominator) else 0
                    tolerance = max(.15, 2 / actual_fps) if actual_fps > 0 else .5
                    if mode == "accurate" and abs(actual - (end - start)) > tolerance:
                        raise ValueError(tr('저장된 영상 길이가 선택 구간과 다릅니다.'))
                    if cancel.is_set():
                        raise InterruptedError()
                    if mode == "fast":
                        report(tr('빠른 저장 · 실제 길이 {value1} · 키프레임에 따라 경계 차이 가능', value1=format_time(actual)))
                    destination = _publish(output, directory, _filename(row))
                    result["saved"].append(str(destination))
            except InterruptedError:
                raise
            except (OSError, ValueError, RuntimeError) as error:
                result["failed"].append({**row, "error": str(error)})
                report(tr('클립 저장 실패 {index:,} / {value2:,} · {error}', index=index, value2=len(rows), error=error))
    except InterruptedError:
        result["cancelled"] = True
    return result
