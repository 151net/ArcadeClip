"""Shared cancellation, progress events and normalized image crops; no UI dependency."""
from concurrent.futures import CancelledError

DEFAULTS = {"sample_seconds": 1.0, "stable_seconds": 1.0, "hash_distance": 10,
            "hash_margin": 3, "ocr_tail_seconds": 3.0, "location_seconds": 10.0}

def check_cancel(cancel):
    if cancel.is_set():
        raise CancelledError()


def crop(image, box):
    return image.crop(tuple(round(value * dimension) for value, dimension in
                            zip(box, (image.width, image.height) * 2)))


def progress(report, step, done, total, message, **extra):
    report({"step": step, "done": done, "total": total, "message": message, **extra})
