"""Frame inspection uses presentation timestamps, not assumed frame rates."""
from i18n import tr
import math
import re
import struct
from pathlib import Path
from fractions import Fraction

import av
from av.filter import Graph


def parse_time(text: str) -> float:
    if not re.fullmatch(r"\d+:[0-5]\d:[0-5]\d(?:\.\d{1,3})?", text.strip()):
        raise ValueError(tr('시간을 HH:MM:SS 형식으로 입력해 주세요.'))
    hours, minutes, seconds = text.strip().split(":")
    value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if not math.isfinite(value):
        raise ValueError(tr('시간을 확인해 주세요.'))
    return value


def format_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    return f"{hours:02}:{minutes:02}:{milliseconds / 1000:06.3f}"


def oriented_image(frame, *, region=None, reference_size=None):
    for side in frame.side_data:
        if side.type.name != "DISPLAYMATRIX":
            continue
        matrix = struct.unpack("=9i", bytes(side))
        a, b, u, c, d, v, x, y, w = matrix
        if (a * d - b * c <= 0 or u or v or x or y or w != 1 << 30
                or sorted(map(abs, (a, b, c, d))) != [0, 0, 65536, 65536]
                or a * c + b * d):
            raise ValueError(tr('이 영상의 화면 변환을 처리할 수 없습니다. 회전과 반전을 영상에 적용한 파일을 사용해 주세요.'))
    angle = frame.rotation
    if angle % 90:
        raise ValueError(tr('이 영상의 회전 각도를 처리할 수 없습니다.'))
    width, height = (frame.height, frame.width) if angle % 180 else (frame.width, frame.height)
    if reference_size and abs(width / height - reference_size[0] / reference_size[1]) > .01:
        raise ValueError(tr('영상과 프로파일의 화면 비율이 다릅니다. 영역을 다시 지정해 주세요.'))
    box = tuple(round(v * size) for v, size in zip(region, (width, height) * 2)) if region is not None else None
    if box == (0, 0, width, height):
        box = None
    if box is not None and not angle and frame.format.name == "yuv420p" and width % 32 == 0 and height % 2 == 0:
        x0, y0, x1, y1 = box
        if 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height:
            # Keep chroma neighbours and SIMD alignment, then trim to the exact user ROI.
            left, top = max(0, (x0 - 16) // 32 * 32), max(0, (y0 - 2) // 2 * 2)
            right, bottom = min(width, (x1 + 47) // 32 * 32), min(height, (y1 + 3) // 2 * 2)
            graph = Graph()
            graph.threads = 1
            source = graph.add_buffer(template=frame, time_base=frame.time_base or Fraction(1, 1))
            cropped = graph.add("crop", f"{right-left}:{bottom-top}:{left}:{top}:exact=1")
            sink = graph.add("buffersink")
            source.link_to(cropped)
            cropped.link_to(sink)
            graph.configure()
            graph.push(frame)
            image = graph.pull().to_image()
            return image.crop((x0-left, y0-top, x1-left, y1-top))
    # Preserve display transforms and unusual pixel formats through the original path.
    image = frame.to_image()
    image = image.rotate(angle, expand=True) if angle else image
    return image.crop(box) if box is not None else image


def frame_at(path: Path, seconds: float, *, region=None, reference_size=None):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(tr('프레임 시각을 확인해 주세요.'))
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(tr('영상 스트림을 찾을 수 없습니다.'))
        stream = container.streams.video[0]
        origin = stream.start_time or 0
        target = origin + int(seconds / stream.time_base)
        container.seek(target, stream=stream, backward=True)
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            actual = float((frame.pts - origin) * stream.time_base)
            if actual + 1e-7 >= seconds:
                return {"pts": frame.pts, "time_base": str(frame.time_base),
                        "seconds": actual, "requested": seconds,
                        "image": oriented_image(frame, region=region, reference_size=reference_size)}
    raise ValueError(tr('해당 시각의 프레임을 찾을 수 없습니다.'))


def adjacent_frame(path, seconds, direction, cancel):
    """Move by decoded PTS, including VFR and nonzero stream origins."""
    from concurrent.futures import CancelledError
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        origin = stream.start_time or 0
        target = max(0, seconds - 1) if direction < 0 else seconds
        container.seek(origin + int(target / stream.time_base), stream=stream, backward=True)
        previous = None
        for frame in container.decode(stream):
            if cancel.is_set():
                raise CancelledError()
            if frame.pts is None:
                continue
            actual = float((frame.pts - origin) * stream.time_base)
            if direction < 0 and actual >= seconds - 1e-6:
                frame, actual = previous if previous is not None else (frame, actual)
                return {"seconds": actual, "image": oriented_image(frame)}
            if direction > 0 and actual > seconds + 1e-6:
                return {"seconds": actual, "image": oriented_image(frame)}
            previous = frame, actual
        if previous:
            frame, actual = previous
            return {"seconds": actual, "image": oriented_image(frame)}
    raise ValueError(tr('프레임을 찾을 수 없습니다.'))
