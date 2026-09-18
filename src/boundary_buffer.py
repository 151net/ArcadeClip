"""Bounded, optional frame reuse for jacket boundary refinement."""
from collections import deque, OrderedDict
from analysis_common import check_cancel
from media import oriented_image


class BoundaryBuffer:
    def __init__(self, start, interval, region, reference_size, cancel):
        self.start, self.interval = start, interval
        self.region, self.reference_size, self.cancel = region, reference_size, cancel
        self.recent, self.windows = deque(), OrderedDict()
        self.native_bytes = self.roi_bytes = 0
        self.native_limit, self.roi_limit = 64 * 1024**2, 32 * 1024**2
        self.keep_target, self.previous = start, None
        self.metrics = dict(native_peak=0, roi_peak=0, hits=0, misses=0)

    def observe(self, seconds, frame):
        keep = seconds + 1e-7 >= self.keep_target
        if keep:
            self.keep_target = seconds + .1
        size = sum(p.buffer_size for p in frame.planes) if keep else 0
        self.recent.append((seconds, frame if keep else None, size))
        self.native_bytes += size
        while self.recent and (self.recent[0][0] < seconds-self.interval-.05
                               or self.native_bytes > self.native_limit or len(self.recent) > 8192):
            self.native_bytes -= self.recent.popleft()[2]
        self.metrics["native_peak"] = max(self.metrics["native_peak"], self.native_bytes)

    def matched(self, seconds, result):
        changed = self.previous is not None and self.previous[1] != result["image_url"]
        self.previous = seconds, result["image_url"]
        if not changed:
            return
        target = max(self.start, seconds-self.interval)
        if not self.recent or self.recent[0][0] > target + 1e-7:
            return
        selected = []
        for timestamp, frame, _ in self.recent:
            if timestamp + 1e-7 >= target:
                # VFR/sample phase mismatch: fall back instead of shifting the boundary.
                if frame is None:
                    return
                selected.append((timestamp, frame))
                target = timestamp + .1
        rows, size = [], 0
        for timestamp, frame in selected:
            check_cancel(self.cancel)
            image = oriented_image(frame, region=self.region, reference_size=self.reference_size)
            size += image.width * image.height * len(image.getbands())
            if size > self.roi_limit:
                return
            rows.append((timestamp, image))
        while self.windows and self.roi_bytes + size > self.roi_limit:
            self.roi_bytes -= self.windows.popitem(last=False)[1][1]
        self.windows[seconds] = rows, size
        self.roi_bytes += size
        self.metrics["roi_peak"] = max(self.metrics["roi_peak"], self.roi_bytes)

    def finish_scan(self):
        self.recent.clear()
        self.native_bytes = 0

    def get(self, boundary):
        found = self.windows.pop(boundary, None)
        self.metrics["hits" if found is not None else "misses"] += 1
        if found is None:
            return None
        self.roi_bytes -= found[1]
        return found[0]
