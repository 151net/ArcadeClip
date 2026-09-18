"""Analysis orchestration: song intervals -> text enrichment -> validated clip data.

A recognizer module supplies METHOD, detect(...) and recognize(...).
No window, Qt widget or hash-matching details belong in this layer.
"""
from i18n import tr
from concurrent.futures import CancelledError
import math
from pathlib import Path

from analysis_common import check_cancel, crop, progress
from clips import validate_clip
from media import frame_at
from profiles import validate_profile
from text_recognition import make_ocr, ocr_clips
from video_sampling import clamp_analysis_end


def analyze(path, start, end, profile, directory, cancel, report, *, workers=0,
            decoder_threads=0, ocr_workers=0, gpu=False, recognizer=None):
    if recognizer is None:
        import jacket_recognition as recognizer
    profile = validate_profile(profile)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
        raise ValueError(tr('분석할 구간의 시작과 끝을 확인해 주세요.'))
    path = Path(path).resolve()
    stat = path.stat()
    result = {"clips": [], "warnings": [], "cancelled": False}
    try:
        check_cancel(cancel)
        end = clamp_analysis_end(path, start, end)
        detected = recognizer.detect(path, start, end, profile, directory, cancel, report,
                                     workers=workers, decoder_threads=decoder_threads, gpu=gpu)
        profile = validate_profile(detected.get("profile", profile))
        result.update(profile=profile, warnings=list(detected.get("warnings", [])))
        if "scan_metrics" in detected:
            result["scan_metrics"] = detected["scan_metrics"]
        candidates = detected["candidates"]
        progress(report, 5, 0, max(1, len(candidates)), tr('곡 끝 문자 판독 준비'))
        rows, previous_end = [], start
        for index, candidate in enumerate(candidates):
            check_cancel(cancel)
            row = validate_clip({**candidate, "source": str(path), "player": "", "player2": "", "reviewed": False,
                "partial_start": index == 0, "partial_end": index == len(candidates) - 1,
                "analysis": {"version": 1, "method": recognizer.METHOD, "profile": profile,
                             "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "range": [start, end]}})
            if row["start"] < previous_end or row["end"] > end:
                raise ValueError(tr('인식 결과의 구간 순서와 범위를 확인해 주세요.'))
            previous_end = row["end"]
            rows.append(row)
        result["clips"] = rows
        ocr_clips(path, rows, profile, directory, cancel, report, result["warnings"], ocr_workers)
    except CancelledError:
        result["cancelled"] = True
    return result


def test_frame(path, seconds, profile, directory, cancel, report, *, recognizer=None):
    if recognizer is None:
        import jacket_recognition as recognizer
    profile = validate_profile(profile)
    image = frame_at(path, seconds)["image"]
    if abs(image.width / image.height - profile["size"][0] / profile["size"][1]) > .01:
        raise ValueError(tr('영상과 프로파일의 화면 비율이 다릅니다.'))
    result = recognizer.recognize(image, profile, directory, cancel, report)
    result["seconds"] = seconds
    if any(key != "jacket" for key in profile["regions"]):
        read = make_ocr(directory, cancel, report)
        result["ocr"] = {key: read(crop(image, box)) for key, box in profile["regions"].items() if key != "jacket"}
    return result
