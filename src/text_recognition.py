"""Text model loading, ending-frame consensus and bounded OCR workers."""
from i18n import tr
from collections import Counter
from concurrent.futures import CancelledError, ThreadPoolExecutor, wait, FIRST_COMPLETED
from difflib import SequenceMatcher
import hashlib
import logging
import os
from itertools import islice
from pathlib import Path
import re
import threading
from time import perf_counter
import numpy as np
from jackets import _atomic_write, _fetch, relaxed_context
from media import frame_at
from analysis_common import DEFAULTS, check_cancel, crop, progress

_PLAYER_WIDTH = str.maketrans({**{chr(i): chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}, "\u3000": " "})


def normalize_player(value):
    # Full-width Latin glyph spacing can be emitted as extra OCR word spaces.
    value = value.translate(_PLAYER_WIDTH).strip()
    return re.sub(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", "", value)


def same_player(value, other):
    """Two readings of one name: spacing is ignored and a few glyphs may differ."""
    left, right = (re.sub(r"\s+", "", text) for text in (value, other))
    return left == right or SequenceMatcher(None, left, right).ratio() >= .6


def make_ocr(directory, cancel, report):
    """Use pinned RapidOCR recognition only: the user already supplies text-line ROIs."""
    import rapidocr
    from rapidocr import LangRec, ModelType, OCRVersion
    from rapidocr.ch_ppocr_rec import TextRecInput, TextRecognizer
    from rapidocr.utils.parse_parameters import ParseParams
    # maimai names are Latin, digits and symbols, with kana on the Japanese servers, and the
    # Chinese model is the one whose dictionary covers all of them.
    language, checksum = "ch", "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5"
    model = Path(directory) / "models" / f"{language}_PP-OCRv5_rec_mobile.onnx"
    check_cancel(cancel)
    if not model.is_file() or hashlib.sha256(model.read_bytes()).hexdigest() != checksum:
        report(tr('OCR 모델을 준비하고 있습니다…'))
        url = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/" + model.name
        # The file is pinned by checksum below, so a chain this machine cannot build
        # must not stand between the user and a first analysis.
        body = _fetch(url, cancel, 64 * 1024 * 1024, relaxed_context())
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError(tr('OCR 모델 검증에 실패했습니다. 다시 시도해 주세요.'))
        model.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(model, body)
    cfg = ParseParams.load(Path(rapidocr.__file__).parent / "config.yaml")
    cfg.Rec.lang_type = LangRec(language)
    cfg.Rec.model_type = ModelType.MOBILE
    cfg.Rec.ocr_version = OCRVersion.PPOCRV5
    cfg.Rec.model_path = str(model)
    cfg.Rec.font_path = None
    cfg.Rec.engine_cfg = cfg.EngineConfig.onnxruntime
    cfg.Rec.engine_cfg.intra_op_num_threads = 2
    cfg.Rec.engine_cfg.inter_op_num_threads = 1
    check_cancel(cancel)
    engine = TextRecognizer(cfg.Rec)
    def read(image):
        check_cancel(cancel)
        result = engine(TextRecInput(img=np.asarray(image.convert("RGB"))[:, :, ::-1].copy()))
        check_cancel(cancel)
        return (result.txts[0], float(result.scores[0])) if result.txts else ("", 0.0)
    return read


def read_ending(path, row, profile, read, cancel):
    options = {**DEFAULTS, **profile.get("recognition", {})}
    keys = [key for key in profile["regions"] if key != "jacket"]
    evidence = []
    tail = min(options["ocr_tail_seconds"], row["end"] - row["start"])
    times = sorted({max(row["start"], row["end"] - tail * fraction) for fraction in (1, .5, .1)})
    seen = set()
    for seconds in times:
        check_cancel(cancel)
        frame = frame_at(path, seconds)
        if frame["seconds"] >= row["end"] or frame["seconds"] in seen:
            continue
        seen.add(frame["seconds"])
        for key in keys:
            text, score = read(crop(frame["image"], profile["regions"][key]))
            evidence.append({"seconds": frame["seconds"], "region": key, "text": text, "score": score})
    result = {"ocr_evidence": evidence}
    for key in keys:
        values = []
        for item in evidence:
            if item["region"] != key or item["score"] < .6:
                continue
            value = item["text"].strip()
            if key.startswith("player"):
                value = normalize_player(value)
            if key.startswith("achievement"):
                # A '%' inside the region reads as one stray glyph, often a digit, so drop a
                # single trailing character rather than making the region exclude the sign.
                match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,4})?)\s*.?", value)
                if not match or not 0 <= float(match[1]) <= 101:
                    continue
                value = float(match[1])
            if value != "":
                values.append(value)
        counts = Counter(values)
        if not counts:
            continue
        best, hits = counts.most_common(1)[0]
        if key.startswith("player"):
            # One player keeps playing, so readings a glyph apart are the same name and the
            # most frequent spelling wins. A name that is not close still blocks the result.
            hits = sum(count for value, count in counts.items() if same_player(value, best))
            if hits < len(values):
                continue
        # Conflicting names/scores remain in evidence for review, never silently merged.
        elif len(counts) > 1:
            continue
        if hits >= 2:
            result[key] = best
    return result


def ocr_clips(path, rows, profile, directory, cancel, report, warnings, workers=0):
    if type(workers) is not int or not 0 <= workers <= 32:
        raise ValueError(tr('OCR 작업자 수는 0~32로 지정해 주세요.'))
    if not rows or not any(key != "jacket" for key in profile["regions"]):
        progress(report, 5, len(rows), len(rows), tr('문자 영역 없음 · 클립 정리 완료'))
        return
    # Each ONNX engine uses two CPU threads; cap auto sessions to limit decoder/model memory.
    count = min(len(rows), workers or min(16, max(1, (os.cpu_count() or 1) // 2)))
    ocr_started = perf_counter()
    try:
        first_read = make_ocr(directory, cancel, report)
    except CancelledError:
        raise
    except Exception as error:
        warnings.append(tr('OCR 준비 실패: {error}', error=error))
        progress(report, 5, len(rows), len(rows), tr('OCR 준비 실패 · 원본 구간 보존'))
        return
    logging.info("[pipeline ocr] initial_engine_setup=%.2fs", perf_counter()-ocr_started)
    last_ocr_log = float("-inf")
    local = threading.local()
    local.read = first_read
    engines = iter([first_read])
    engine_lock = threading.Lock()
    def process(row):
        began = perf_counter()
        check_cancel(cancel)
        if not hasattr(local, "read"):
            with engine_lock:
                read = next(engines, None)
            local.read = read or make_ocr(directory, cancel, lambda _: None)
        setup_done = perf_counter()
        result = read_ending(path, row, profile, local.read, cancel)
        logging.debug("[pipeline ocr worker] thread=%s start=%.3f engine_setup=%.2fs read_ending=%.2fs",
                      threading.current_thread().name, row["start"], setup_done-began, perf_counter()-setup_done)
        return result
    def collect(row, operation):
        try:
            row.update(operation())
        except CancelledError:
            raise
        except Exception as error:
            row["ocr_error"] = str(error)
            warnings.append(tr('곡 끝 OCR 실패: {error}', error=error))
    def log_ocr(done, pending=()):
        nonlocal last_ocr_log
        now = perf_counter()
        if done != len(rows) and now-last_ocr_log < 5:
            return
        last_ocr_log = now
        elapsed = now-ocr_started
        logging.info("[pipeline ocr] completed=%d/%d workers=%d pending=%d ready=%d "
                     "elapsed=%.2fs rate=%.2f clips/s", done, len(rows), count, len(pending),
                     sum(future.done() for future in pending), elapsed, done/max(elapsed,.001))
    log_ocr(0)
    progress(report, 5, 0, len(rows), tr('곡 끝 OCR · CPU 작업자 {count}개 (각 2스레드)', count=count))
    if count == 1:
        for index, row in enumerate(rows):
            check_cancel(cancel)
            collect(row, lambda: process(row))
            progress(report, 5, index + 1, len(rows), tr('곡 끝 OCR · CPU 작업자 1개'))
            log_ocr(index+1)
        return
    pool = ThreadPoolExecutor(max_workers=count, thread_name_prefix="clip-ocr")
    pending = {}
    iterator = iter(rows)
    done = 0
    try:
        for row in islice(iterator, count):
            pending[pool.submit(process, row)] = row
        while pending:
            check_cancel(cancel)
            ready, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
            log_ocr(done, pending)
            for future in ready:
                collect(pending.pop(future), future.result)
                done += 1
                progress(report, 5, done, len(rows), tr('곡 끝 OCR · CPU 작업자 {count}개', count=count))
                check_cancel(cancel)
                row = next(iterator, None)
                if row is not None:
                    pending[pool.submit(process, row)] = row
        log_ocr(done, pending)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
