"""Experimental decoder/Y-plane comparison; never changes production defaults."""
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

import av
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import jacket_recognition as jackets
import media
import video_sampling
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
    if args.repeat < 1 or not 0 <= args.start < args.end:
        parser.error("Expected repeat >= 1 and 0 <= start < end")
    profile = load_profile(args.profile)
    original_open, original_image = av.open, media.oriented_image
    original_scan = jackets.scan_jackets

    def y_image(frame, *, region=None, reference_size=None):
        # Deliberately restrict this experiment to untransformed 8-bit planar YUV.
        assert frame.format.name == "yuv420p" and frame.rotation == 0
        assert not any(s.type.name == "DISPLAYMATRIX" for s in frame.side_data)
        assert region is not None
        if reference_size:
            assert abs(frame.width / frame.height - reference_size[0] / reference_size[1]) <= .01
        x0, y0, x1, y1 = (round(v * n) for v, n in zip(region, (frame.width, frame.height) * 2))
        plane = frame.planes[0]
        pixels = np.frombuffer(plane, dtype=np.uint8).reshape(plane.height, plane.line_size)
        return Image.fromarray(pixels[y0:y1, x0:x1].copy())

    cases = [(mode, repeat) for mode in ("baseline", "y-only", "skip-loop", "y-skip-loop")
             for repeat in range(args.repeat)]
    random.Random(42).shuffle(cases)
    reference = None
    with args.output.open("x", encoding="utf-8") as output:
        for mode, repeat in [("baseline", -1), *cases]:
            detail, observations = {}, []
            def opened(*a, **kw):
                if "skip-loop" in mode:
                    kw["options"] = {**kw.get("options", {}), "skip_loop_filter": "all"}
                return original_open(*a, **kw)
            def converted(*a, **kw):
                began = perf_counter()
                image = (y_image if mode.startswith("y-") else original_image)(*a, **kw)
                detail["convert_s"] = detail.get("convert_s", 0.) + perf_counter() - began
                return image
            def scanned(*a, **kw):
                found = original_scan(*a, **kw)
                observations.extend(deepcopy(found))
                return found
            with ExitStack() as stack:
                # Preserve this experiment's original re-decode baseline.
                stack.enter_context(patch.object(jackets, "BoundaryBuffer", lambda *a: None))
                stack.enter_context(patch.object(av, "open", opened))
                for module in (media, video_sampling):
                    stack.enter_context(patch.object(module, "oriented_image", converted))
                stack.enter_context(patch.object(jackets, "scan_jackets", scanned))
                stack.enter_context(patch.object(jackets, "write_json", lambda *a: None))
                began = perf_counter()
                result = jackets.detect(args.video, args.start, args.end, deepcopy(profile),
                    args.directory, threading.Event(), lambda _: None, decoder_threads=32)
                elapsed = perf_counter() - began
            if reference is None:
                reference = result["candidates"], observations
            candidates, expected = reference
            signature = lambda rows: [(r["song_id"], r["start"], r["end"]) for r in rows]
            assert len(observations) == len(expected)
            mismatches = sum(a[0] != b[0] or a[1]["song_id"] != b[1]["song_id"]
                             for a, b in zip(observations, expected))
            score_changes = sum(a[1] != b[1] for a, b in zip(observations, expected))
            row = dict(mode=mode, repeat=repeat, start=args.start, end=args.end, seconds=elapsed,
                detail=detail, metrics=result["scan_metrics"], candidates=result["candidates"],
                same_boundaries=signature(result["candidates"]) == signature(candidates),
                identical_candidates=result["candidates"] == candidates,
                observation_mismatches=mismatches, score_changes=score_changes,
                warnings=result["warnings"])
            line = json.dumps(row, ensure_ascii=False)
            output.write(line + "\n")
            output.flush()
            print(line, flush=True)


if __name__ == "__main__":
    main()
