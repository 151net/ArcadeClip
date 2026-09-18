"""Compare full-frame RGB cropping with native jacket ROI conversion."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import threading
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import jacket_recognition
import media
import video_sampling
from analysis_common import crop
from profiles import load_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = load_profile(args.profile)
    end = video_sampling.clamp_analysis_end(args.video, args.start, args.end)
    if args.repeat < 1 or not 0 <= args.start < end:
        parser.error("Expected repeat >= 1 and 0 <= start < end")
    original = media.oriented_image
    checked = 0

    def full_rgb(frame, *, region=None, reference_size=None):
        image = original(frame, reference_size=reference_size)
        return crop(image, region) if region is not None else image

    def verify(frame, **kwargs):
        nonlocal checked
        expected, actual = full_rgb(frame, **kwargs), original(frame, **kwargs)
        assert actual.size == expected.size and actual.tobytes() == expected.tobytes(), "ROI pixel mismatch"
        checked += 1
        return actual

    cases = [(mode, repeat) for mode in ("full-rgb", "roi-rgb") for repeat in range(args.repeat)]
    random.Random(42).shuffle(cases)
    reference = None
    with args.output.open("x", encoding="utf-8") as output:
        # Untimed parity pass: includes calibration, scan and boundary refinement frames.
        for mode, repeat in [("verify", 0), *cases]:
            detail = {}
            converter = {"verify": verify, "full-rgb": full_rgb, "roi-rgb": original}[mode]
            def timed(key, function):
                def call(*a, **kw):
                    began = perf_counter()
                    try:
                        return function(*a, **kw)
                    finally:
                        detail[key] = detail.get(key, 0.) + perf_counter() - began
                return call
            with ExitStack() as stack:
                # Isolate RGB conversion from the later boundary-buffer optimization.
                stack.enter_context(patch.object(jacket_recognition, "BoundaryBuffer", lambda *a: None))
                for module in (media, video_sampling):
                    stack.enter_context(patch.object(module, "oriented_image", timed("all_convert_s", converter)))
                for function, key in (("load_catalog", "catalog_s"), ("calibrate_locations", "calibration_s"), ("scan_jackets", "scan_s")):
                    stack.enter_context(patch.object(jacket_recognition, function, timed(key, getattr(jacket_recognition, function))))
                began = perf_counter()
                result = jacket_recognition.detect(args.video, args.start, end, deepcopy(profile),
                    args.directory, threading.Event(), lambda _: None, decoder_threads=32)
                elapsed = perf_counter() - began
            candidates = result["candidates"]
            if reference is None:
                reference = candidates
            assert candidates == reference, "Candidate/boundary/score changed"
            assert not result["warnings"], result["warnings"]
            row = dict(mode=mode, repeat=repeat, start=args.start, end=end, seconds=elapsed,
                verified_frames=checked, detail=detail, metrics=result["scan_metrics"], candidates=candidates)
            line = json.dumps(row, ensure_ascii=False)
            output.write(line + "\n")
            output.flush()
            print(line, flush=True)


if __name__ == "__main__":
    main()
