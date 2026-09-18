"""Default song recognizer: pHash catalog, jacket calibration and boundary refinement."""
from i18n import tr
from collections import deque
from concurrent.futures import ProcessPoolExecutor, TimeoutError
from bisect import bisect_right
import logging
import math
import multiprocessing
import os
from itertools import islice
from pathlib import Path
from time import perf_counter
import numpy as np
from PIL import Image
from jackets import _manifest, _verify_image
from media import frame_at
from profiles import load_profile, offset_path, write_json
from analysis_common import DEFAULTS, check_cancel, crop, progress
from video_sampling import samples, analysis_samples
from boundary_buffer import BoundaryBuffer

METHOD = "jacket-phash-v1"
# Unnormalized DCT-II, matching the conventional 32px/8x8 pHash definition.
_DCT = 2 * np.cos(np.pi * np.arange(8)[:, None] * (2 * np.arange(32) + 1) / 64)

def phash(image):
    pixels = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    coefficients = _DCT @ pixels @ _DCT.T
    return int.from_bytes(np.packbits(coefficients > np.median(coefficients)).tobytes(), "big")


def load_catalog(directory, cancel, report):
    directory = Path(directory) / "jackets"
    songs = _manifest((directory / "_manifest.json").read_bytes())
    entries, seen, damaged = [], set(), 0
    for number, song in enumerate(songs, 1):
        check_cancel(cancel)
        if number == 1 or number % 25 == 0 or number == len(songs):
            progress(report, 1, number, len(songs), tr('자켓 목록 준비'))
        name = song["image_url"]
        if name in seen:
            continue
        seen.add(name)
        try:
            path = directory / name
            _verify_image(path)
            with Image.open(path) as image:
                entries.append({"image_url": name, "title": song["title"], "hash": phash(image)})
        except (OSError, ValueError):
            damaged += 1
    if not entries:
        raise ValueError(tr('사용 가능한 자켓이 없습니다. 곡 데이터를 먼저 받아 주세요.'))
    if damaged:
        report(tr('손상되거나 누락된 자켓 {damaged:,}개를 제외했습니다.', damaged=damaged))
    return entries


def identify(image, catalog, options, previous=None, cancel=None, *, search=True, local_only=False):
    """Search square subwindows, then verify/track the prior location as in maipage."""
    if not catalog:
        raise ValueError(tr('사용 가능한 자켓이 없습니다.'))
    hashes = np.array([row["hash"] for row in catalog], dtype=np.uint64)
    width, height = image.size
    shortest = min(width, height)
    best = None
    seen = set()

    def evaluate(rect):
        nonlocal best
        if rect in seen:
            return None
        seen.add(rect)
        if cancel is not None:
            check_cancel(cancel)
        x0, y0, x1, y1 = rect
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            return None
        distances = np.bitwise_count(hashes ^ np.uint64(phash(image.crop(rect))))
        ranked = np.argsort(distances, kind="stable")[:2]
        index = int(ranked[0])
        distance = int(distances[index])
        margin = int(distances[ranked[1]]) - distance if len(ranked) > 1 else 64
        found = distance <= options["hash_distance"] and margin >= options["hash_margin"]
        result = {"image_url": catalog[index]["image_url"] if found else None,
                  "song_id": catalog[index]["image_url"] if found else None,
                  "title": catalog[index]["title"] if found else "", "distance": distance,
                  "margin": margin, "sub_rect": list(rect)}
        if best is None or (distance, -margin) < (best["distance"], -best["margin"]):
            best = result
        return result

    if not search:
        return evaluate((0, 0, width, height))

    # Always compare against the whole catalog so a changed song cannot remain locked.
    if previous and previous.get("sub_rect"):
        old = tuple(previous["sub_rect"])
        result = evaluate(old)
        if result and result["image_url"]:
            return result
        x0, y0, x1, y1 = old
        center_x, center_y, size = (x0 + x1) / 2, (y0 + y1) / 2, min(x1 - x0, y1 - y0)
        for scale in (.95, 1, 1.05):
            side = round(size * scale)
            for dx, dy in ((0, 0), (-5, 0), (5, 0), (0, -5), (0, 5)):
                x, y = round(center_x - side / 2 + dx), round(center_y - side / 2 + dy)
                evaluate((x, y, x + side, y + side))
        if best and best["image_url"]:
            return best
    center = ((width - shortest) // 2, (height - shortest) // 2)
    result = evaluate((*center, center[0] + shortest, center[1] + shortest))
    if result and result["image_url"]:
        return result
    if local_only:
        return best
    # maipage defaults: 70% of the short side, sizes every 6px, positions every 5px.
    # Small preview crops remain supported instead of imposing maipage's 80px floor.
    minimum = min(shortest, max(min(80, shortest), int(shortest * .7)))
    sizes = sorted(set(range(minimum, shortest + 1, 6)) | {shortest})
    for side in sizes:
        for y in sorted(set(range(0, height - side + 1, 5)) | {height - side}):
            for x in sorted(set(range(0, width - side + 1, 5)) | {width - side}):
                evaluate((x, y, x + side, y + side))
    return best


def _candidates(observations, start, end, stable):
    if not observations:
        raise ValueError(tr('선택한 구간에서 영상 프레임을 찾을 수 없습니다.'))
    current, pending = observations[0], None
    boundaries = [(start, current)]
    for sample in observations[1:]:
        if sample[1]["image_url"] == current[1]["image_url"]:
            pending = None
        elif pending is None or sample[1]["image_url"] != pending[1]["image_url"]:
            pending = sample
        if pending is not None and sample[0] - pending[0] + 1e-7 >= stable:
            boundaries.append((pending[0], pending))
            current, pending = pending, None
    return [{"start": left, "end": right, **sample[1]}
            for (left, sample), (right, _) in zip(boundaries, boundaries[1:] + [(end, None)])]


def _init_analysis_worker(catalog, options, stop):
    global _worker_catalog, _worker_options, _worker_stop
    _worker_catalog, _worker_options, _worker_stop = catalog, options, stop


def _identify_batch(batch, catalog, options, stop):
    previous, results = None, []
    for sample in batch:
        seconds, image = sample[:2]
        check_cancel(stop)
        previous = (identify(image, catalog, options, {"sub_rect": sample[2]}, stop, local_only=True)
                    if len(sample) == 3 else identify(image, catalog, options, cancel=stop, search=False))
        results.append((seconds, previous))
    return results


def _worker_batch(batch):
    began = perf_counter()
    result = _identify_batch(batch, _worker_catalog, _worker_options, _worker_stop)
    return os.getpid(), result, perf_counter() - began


def scan_jackets(frames, catalog, options, cancel, workers, report, *, total=None, metrics=None,
                 on_observation=None):
    """Bound pending crops; ordered results keep global transition detection intact."""
    metrics = metrics if metrics is not None else {}
    metrics.setdefault("match_s", 0.)
    metrics["result_wait_s"] = 0.
    scan_started, last_log = perf_counter(), float("-inf")
    pending = deque()
    def log_queue(done, force=False):
        nonlocal last_log
        now = perf_counter()
        if not force and now-last_log < 5:
            return
        last_log = now
        elapsed = now-scan_started
        ready = sum(future.done() for future in pending)
        logging.info("[pipeline jacket] completed=%s/%s elapsed=%.2fs rate=%.2f samples/s "
                     "pending=%d ready=%d dispatched=%d ordered_blocked=%d "
                     "decode=%.2fs convert_crop=%.2fs match_worker_sum=%.2fs result_wait=%.2fs",
                     done, total, elapsed, done/max(elapsed,.001), len(pending), ready,
                     sum(future.running() for future in pending),
                     ready if pending and not pending[0].done() else 0,
                     metrics.get("decode_s",0), metrics.get("convert_s",0), metrics["match_s"], metrics["result_wait_s"])
    def update(done, message):
        progress(report, 3, done, total, message, metrics=dict(metrics))
        log_queue(done, total is not None and done >= total)
    def match(batch):
        began = perf_counter()
        result = _identify_batch(batch, catalog, options, cancel)
        metrics["match_s"] += perf_counter() - began
        if on_observation is not None:
            for seconds, observation in result:
                on_observation(seconds, observation)
        return result
    frames = iter(frames)
    head = next(frames, None)
    if head is None:
        return []
    pixels = head[1].width * head[1].height * 3
    requested = workers
    workers = min(max(1, workers), 61, max(1, 128 * 1024 * 1024 // (pixels * 2)))
    batch_size = min(16, max(1, 128 * 1024 * 1024 // (pixels * workers * 2)))
    if on_observation is not None:
        assert workers == 1
        batch_size = 1
    first = [head, *islice(frames, batch_size - 1)]
    reason = tr(' · 이미지 메모리 상한 적용') if workers < min(requested, 61) else ""
    update(0, tr(
        '곡 판독 · 요청 {requested} / 최대 {workers}작업자 · 묶음 {batch_size}장{reason}',
        requested=requested,
        workers=workers,
        batch_size=batch_size,
        reason=reason,
    ))
    batches = iter(lambda: list(islice(frames, batch_size)), [])
    if workers == 1:
        results = match(first)
        update(len(results), tr('곡 판독 · 판독 작업자 1개'))
        for batch in batches:
            results.extend(match(batch))
            if on_observation is None or len(results) % 16 == 0:
                update(len(results), tr('곡 판독 · 판독 작업자 1개'))
        log_queue(len(results), True)
        return results
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=context,
                               initializer=_init_analysis_worker, initargs=(catalog, options, stop))
    pending, results, observed_pids = deque(), [], set()
    try:
        pending.append(pool.submit(_worker_batch, first))
        exhausted = False
        while pending:
            check_cancel(cancel)
            while not exhausted and len(pending) < workers * 2:
                check_cancel(cancel)
                batch = next(batches, None)
                if batch is None:
                    exhausted = True
                else:
                    pending.append(pool.submit(_worker_batch, batch))
            began = perf_counter()
            try:
                pid, result, match_time = pending[0].result(timeout=.1)
            except TimeoutError:
                log_queue(len(results))
                continue
            finally:
                metrics["result_wait_s"] += perf_counter()-began
            pending.popleft()
            results.extend(result)
            observed_pids.add(pid)
            metrics["match_s"] += match_time
            update(len(results),
                     tr(
                         '곡 판독 · 요청 {requested} / 최대 {workers} · 처리 확인 {value3}프로세스 · 미수집 {value4}묶음',
                         requested=requested,
                         workers=workers,
                         value3=len(observed_pids),
                         value4=len(pending),
                     ))
        log_queue(len(results), True)
        return results
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)


def calibrate_locations(path, start, end, profile, catalog, options, cancel, report):
    """Sparse location pass: locked square, nearby tracker, then dense fallback."""
    interval = options["location_seconds"]
    times = [start + i * interval for i in range(math.ceil((end - start) / interval))]
    if len(times) < 3:
        times = sorted(set(times + [start + (end - start) / 3, start + 2 * (end - start) / 3]))
    anchors, previous = [], None
    cached = profile.pop("jacket_offset", None)
    for index, seconds in enumerate(times):
        check_cancel(cancel)
        progress(report, 2, index, len(times), tr('자켓 위치 보정 · {value1:.1f}초 지점', value1=seconds - start))
        region = frame_at(path, seconds, region=profile["regions"]["jacket"],
                          reference_size=profile["size"])["image"]
        found = None
        if cached:
            rect = [round(v * size) for v, size in zip(cached["rect"], region.size * 2)]
            if rect[0] < rect[2] and rect[1] < rect[3]:
                found = identify(region.crop(rect), catalog, options, cancel=cancel, search=False)
                found["sub_rect"] = rect
            cached = None
        if not found or not found["image_url"] or found["distance"] > 4 or found["margin"] < 6:
            found = identify(region, catalog, options, previous, cancel)
        if found["image_url"] and found["distance"] <= 4 and found["margin"] >= 6:
            profile["jacket_offset"] = {"version": 1, "roi": profile["regions"]["jacket"][:],
                "rect": [v / size for v, size in zip(found["sub_rect"], region.size * 2)],
                "distance": found["distance"], "margin": found["margin"]}
            progress(report, 2, 1, 1, tr(
                '위치 확정 · 거리 {distance} / 후보 차이 {margin} · 나머지 보정 생략',
                distance=found['distance'],
                margin=found['margin'],
            ))
            return [(start, found["sub_rect"])]
        if found["image_url"]:
            previous = found
            anchors.append((seconds, found["sub_rect"]))
        progress(report, 2, index + 1, len(times), tr('자켓 위치 보정'))
    return anchors


def location_at(anchors, seconds):
    index = max(0, bisect_right([item[0] for item in anchors], seconds) - 1)
    return anchors[index][1]


def detect(path, start, end, profile, directory, cancel, report, *, workers=0, decoder_threads=0, gpu=False):
    options = {**DEFAULTS, **profile.get("recognition", {})}
    result = {"warnings": [], "profile": profile}
    progress(report, 1, 0, None, tr('자켓 목록 준비'))
    catalog = load_catalog(directory, cancel, report)
    if type(workers) is not int or workers < 0:
        raise ValueError(tr('CPU 작업자 수를 확인해 주세요.'))
    # Fixed-square matching is cheaper than spawn/IPC for the normal song catalog.
    # Explicit worker counts remain available for larger catalogs and comparison.
    count = workers or 1
    if type(decoder_threads) is not int or not 0 <= decoder_threads <= 64:
        raise ValueError(tr('디코더 스레드 수는 0~64로 지정해 주세요.'))
    threads = decoder_threads or min(32, os.cpu_count() or 1)
    metrics = {"decoder_threads": threads, "decode_s": 0., "convert_s": 0., "match_s": 0.,
               "decoded_frames": 0, "sample_frames": 0}
    result["scan_metrics"] = metrics
    cache_path = offset_path(directory, profile)
    if "jacket_offset" not in profile and cache_path.is_file():
        try:
            saved = load_profile(cache_path)
            if offset_path(directory, saved) == cache_path and "jacket_offset" in saved:
                profile["jacket_offset"] = saved["jacket_offset"]
        except (ValueError, OSError):
            pass
    anchors = calibrate_locations(path, start, end, profile, catalog, options, cancel, report)
    result["profile"] = profile
    if "jacket_offset" in profile:
        try:
            write_json(cache_path, profile)
        except OSError as error:
            result["warnings"].append(tr('보정 캐시 저장 실패: {error}', error=error))
    if not anchors:
        result["warnings"].append(tr('자켓 위치 보정에 실패했습니다. 영역과 테스트 인식을 확인해 주세요.'))
    # ponytail: synchronous CPU only; asynchronous workers need per-batch frame ownership.
    buffer = (BoundaryBuffer(start, options["sample_seconds"], profile["regions"]["jacket"],
                             profile["size"], cancel) if count == 1 and not gpu else None)
    def rectangle(region, seconds):
        if anchors:
            return location_at(anchors, seconds)
        side = min(region.size)
        x, y = (region.width - side) // 2, (region.height - side) // 2
        return [x, y, x + side, y + side]
    def frames():
        for seconds, image in analysis_samples(path, start, end, options["sample_seconds"], cancel,
                                                threads, metrics, gpu, report, result["warnings"],
                                                region=profile["regions"]["jacket"], reference_size=profile["size"],
                                                on_frame=buffer.observe if buffer else None):
            yield seconds, image, rectangle(image, seconds)
    observations = scan_jackets(frames(), catalog, options, cancel, count, report,
                                total=math.ceil((end - start) / options["sample_seconds"]), metrics=metrics,
                                on_observation=buffer.matched if buffer else None)
    if buffer:
        buffer.finish_scan()
        result["boundary_buffer"] = buffer.metrics
    progress(report, 3, len(observations), len(observations), tr('곡 판독 완료'), metrics=dict(metrics))
    candidates = _candidates(observations, start, end, options["stable_seconds"])
    progress(report, 4, 0, max(1, len(candidates) - 1), tr('곡 경계 정밀 확인'))
    # Refine only short windows around observed changes; do not repeatedly seek the full scan.
    for index in range(1, len(candidates)):
        boundary = candidates[index]["start"]
        target = candidates[index]["image_url"]
        frames = buffer.get(boundary) if buffer else None
        if frames is None:
            frames = samples(path, max(start, boundary - options["sample_seconds"]),
                             boundary + .001, .1, cancel, region=profile["regions"]["jacket"],
                             reference_size=profile["size"])
        for seconds, image in frames:
            if identify(image, catalog, options, {"sub_rect": rectangle(image, seconds)},
                        cancel, local_only=True)["image_url"] == target:
                candidates[index - 1]["end"] = candidates[index]["start"] = seconds
                break
        progress(report, 4, index, max(1, len(candidates) - 1), tr('곡 경계 정밀 확인'))
    progress(report, 4, 1, 1, tr('곡 경계 확인 완료'))
    return {**result, "candidates": candidates}


def recognize(image, profile, directory, cancel, report):
    options = {**DEFAULTS, **profile.get("recognition", {})}
    catalog = load_catalog(directory, cancel, report)
    result = identify(crop(image, profile["regions"]["jacket"]), catalog, options, cancel=cancel)
    return result
