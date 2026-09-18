"""Repeatable CPU scan benchmark; uses synthetic crops and no network."""
import argparse
from pathlib import Path
import sys
import threading
import time
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from recognition import DEFAULTS, scan_jackets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--frames", type=int, default=96)
    args = parser.parse_args()
    rng = np.random.default_rng(42)
    catalog = [{"hash": int(h), "title": str(i), "image_url": str(i)} for i, h in
               enumerate(rng.integers(0, 2**64 - 1, 1500, dtype=np.uint64))]
    frames = [(i, Image.fromarray(rng.integers(0, 256, (140, 160, 3), dtype=np.uint8))) for i in range(args.frames)]
    baseline = None
    for workers in (1, args.workers):
        started = time.perf_counter()
        result = scan_jackets(frames, catalog, DEFAULTS, threading.Event(), workers, lambda _: None)
        elapsed = time.perf_counter() - started
        if baseline is None:
            baseline = result
        else:
            assert result == baseline, "Parallel scan differs from serial"
        print(f"workers={workers} frames={len(result)} seconds={elapsed:.3f}", flush=True)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
