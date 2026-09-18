"""Compare production boundary buffering with the original re-decode path."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import threading
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import jacket_recognition as jackets
from boundary_buffer import BoundaryBuffer
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
    verified = 0

    class VerifiedBuffer(BoundaryBuffer):
        def get(self, boundary):
            nonlocal verified
            rows = super().get(boundary)
            if rows is not None:
                expected = list(jackets.samples(args.video, max(args.start, boundary-self.interval),
                    boundary+.001, .1, self.cancel, region=self.region, reference_size=self.reference_size))
                assert len(rows) == len(expected), "Cached sample count differs"
                for (ta, ia), (tb, ib) in zip(rows, expected):
                    assert ta == tb and ia.size == ib.size and ia.tobytes() == ib.tobytes(), "Cached frame differs"
                    verified += 1
            return rows

    cases = [(mode, i) for mode in ("baseline", "buffer") for i in range(args.repeat)]
    random.Random(42).shuffle(cases)
    reference = None
    with args.output.open("x", encoding="utf-8") as output:
        for mode, repeat in [("baseline", -1), ("verify", -1), *cases]:
            factory = {"baseline": lambda *a: None, "buffer": BoundaryBuffer, "verify": VerifiedBuffer}[mode]
            with patch.object(jackets, "BoundaryBuffer", factory), patch.object(jackets, "write_json", lambda *a: None):
                began = perf_counter()
                result = jackets.detect(args.video, args.start, args.end, deepcopy(profile),
                    args.directory, threading.Event(), lambda _: None, decoder_threads=32)
                elapsed = perf_counter()-began
            if reference is None:
                reference = result["candidates"]
            assert result["candidates"] == reference, "Candidate/boundary/score changed"
            assert not result["warnings"], result["warnings"]
            row = dict(mode=mode, repeat=repeat, start=args.start, end=args.end, seconds=elapsed,
                       stats=result.get("boundary_buffer", {}), verified_frames=verified,
                       candidates=result["candidates"])
            line = json.dumps(row, ensure_ascii=False)
            output.write(line+"\n")
            output.flush()
            print(line, flush=True)


if __name__ == "__main__":
    main()
