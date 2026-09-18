import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

from media import format_time, frame_at, parse_time, adjacent_frame, oriented_image
from analysis_common import crop


class MediaTest(unittest.TestCase):
    def test_native_roi_matches_full_conversion_at_chroma_edges(self):
        rng = np.random.default_rng(91)
        for size in ((320, 192), (322, 194)):
            pixels = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
            for fmt in ("yuv420p", "rgb24", "nv12"):
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24").reformat(format=fmt)
                for color_range, colorspace in ((1, 1), (1, 5), (2, 1), (2, 5)):
                    frame.color_range = color_range
                    frame.colorspace = colorspace
                    full = oriented_image(frame)
                    for box in ([0, 0, 1, 1], [.103, .137, .507, .723],
                                [0, 0, .023, .031], [.977, .969, 1, 1]):
                        with self.subTest(size=size, fmt=fmt, color_range=color_range, colorspace=colorspace, box=box):
                            expected = crop(full, box)
                            actual = oriented_image(frame, region=box, reference_size=size)
                            self.assertEqual(actual.size, expected.size)
                            self.assertEqual(actual.tobytes(), expected.tobytes())

    def make_video(self, path, rotation=0, mirror=False, start=0):
        image = Image.new("RGB", (80, 40), "red")
        image.paste("blue", (40, 0, 80, 40))
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=5)
            stream.width, stream.height = image.size
            stream.pix_fmt = "yuv420p"
            stream.set_display_rotation(rotation, hflip=mirror)
            for index in range(10):
                frame = av.VideoFrame.from_image(image)
                frame.pts = start + index
                frame.time_base = Fraction(1, 5)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    def test_adjacent_frames_use_variable_pts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vfr.mp4"
            with av.open(str(path), "w") as container:
                stream = container.add_stream("libx264", rate=10)
                stream.width, stream.height, stream.pix_fmt = 80, 40, "yuv420p"
                for pts in (30, 31, 35, 37, 41):
                    frame = av.VideoFrame.from_image(Image.new("RGB", (80, 40), "red"))
                    frame.pts, frame.time_base = pts, Fraction(1, 10)
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            cancel = threading.Event()
            self.assertAlmostEqual(adjacent_frame(path, .1, 1, cancel)["seconds"], .5)
            self.assertAlmostEqual(adjacent_frame(path, .5, -1, cancel)["seconds"], .1)
            self.assertEqual(adjacent_frame(path, 0, -1, cancel)["seconds"], 0)
            self.assertAlmostEqual(adjacent_frame(path, 1.1, 1, cancel)["seconds"], 1.1)

    def test_time_input_and_format(self):
        self.assertEqual(parse_time(" 25:02:03.125 "), 90123.125)
        self.assertEqual(format_time(90123.125), "25:02:03.125")
        self.assertEqual(format_time(59.9996), "00:01:00.000")
        for value in ("1:60:00", "00:00:60", "-1:00:00", "00:00:00.1234", "nan"):
            with self.assertRaises(ValueError):
                parse_time(value)

    def test_rotated_frame_matches_display_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotated.mp4"
            self.make_video(path, rotation=90)
            image = frame_at(path, .2)["image"]
            box = [.1, .2, .8, .7]
            cropped = frame_at(path, .2, region=box, reference_size=[40, 80])["image"]
            self.assertEqual(cropped.tobytes(), crop(image, box).tobytes())
            with self.assertRaisesRegex(ValueError, "화면 비율"):
                frame_at(path, .2, region=box, reference_size=[80, 40])
            self.assertEqual(image.size, (40, 80))
            top, bottom = image.getpixel((20, 10)), image.getpixel((20, 70))
            self.assertGreater(top[2], top[0])
            self.assertGreater(bottom[0], bottom[2])

    def test_mirrored_frame_is_explicitly_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mirrored.mp4"
            self.make_video(path, mirror=True)
            with self.assertRaisesRegex(ValueError, "화면 변환"):
                frame_at(path, 0, region=[0, 0, .5, .5])

    def test_nonzero_pts_uses_relative_playback_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "offset.mp4"
            self.make_video(path, start=15)
            with av.open(str(path)) as container:
                self.assertGreater(container.streams.video[0].start_time, 0)
            result = frame_at(path, .35)
            self.assertAlmostEqual(result["seconds"], .4)
            self.assertGreater(result["pts"] * Fraction(result["time_base"]), 3)
            for seconds in (-1, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    frame_at(path, seconds)


if __name__ == "__main__":
    unittest.main()
