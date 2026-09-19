import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame, QtVideo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from preview import Preview, VideoCanvas
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))  # however the suite is started
from support import run_child


class PreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_letterbox_reverse_drag_clamping_and_small_region(self):
        canvas = VideoCanvas()
        canvas.resize(400, 400)
        canvas.kind = "jacket"
        canvas.set_image(QImage(400, 200, QImage.Format.Format_RGB32))
        canvas.show()
        self.app.processEvents()
        self.assertEqual(canvas.video_rect().y(), 100)
        self.assertEqual(canvas.normalized_point(QPointF(500, 0)), QPointF(1, 0))
        changes = []
        canvas.regionChanged.connect(lambda kind, box: changes.append((kind, box)))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(300, 250))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 150))
        self.assertEqual(changes, [("jacket", [.25, .25, .75, .75])])
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(350, 250))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 150))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(101, 151))
        self.assertEqual(len(changes), 1)
        with self.assertRaises(ValueError):
            canvas.set_regions({"jacket": [0, 0, float("nan"), 1]})
        canvas.close()

    def test_zoom_pan_and_region_coordinates(self):
        canvas = VideoCanvas()
        canvas.resize(400, 400)
        canvas.set_image(QImage(400, 200, QImage.Format.Format_RGB32))
        canvas.kind = "jacket"
        canvas.show()
        self.app.processEvents()
        anchor = QPointF(200, 200)
        before = canvas.normalized_point(anchor)
        canvas.zoom(2, anchor)
        self.assertEqual(canvas.normalized_point(anchor), before)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(200, 200))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(240, 220))
        self.assertEqual(canvas.pan, QPointF(40, 20))
        self.assertEqual(canvas.regions, {})
        rect = canvas.video_rect()
        start = QPoint(round(rect.x()+rect.width()*.4), round(rect.y()+rect.height()*.4))
        end = QPoint(round(rect.x()+rect.width()*.6), round(rect.y()+rect.height()*.6))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        for actual, expected in zip(canvas.regions["jacket"], [.4, .4, .6, .6]):
            self.assertAlmostEqual(actual, expected)
        QTest.keyClick(canvas, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(canvas.zoom_scale, 1)
        self.assertEqual(canvas.pan, QPointF())
        QTest.keyClick(canvas, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
        self.assertGreater(canvas.zoom_scale, 1)
        QTest.mouseClick(canvas, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(canvas.zoom_scale, 1)
        self.assertEqual(canvas.regions["jacket"], [.4, .4, .6, .6])
        canvas.close()

    def test_pan_gestures_cursor_and_reset(self):
        canvas = VideoCanvas()
        canvas.resize(400, 400)
        canvas.set_image(QImage(400, 400, QImage.Format.Format_RGB32))
        canvas.kind = 'jacket'
        canvas.show()
        self.app.processEvents()
        edits = []
        canvas.editStarted.connect(lambda: edits.append(True))
        for button, modifier in (
                (Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier),
                (Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)):
            canvas.reset_view()
            QTest.mousePress(canvas, button, modifier, QPoint(100, 100))
            self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
            QTest.mouseMove(canvas, QPoint(130, 120))
            self.assertEqual(canvas.pan, QPointF(30, 20))
            QTest.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(130, 120))
            self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
            QTest.mouseRelease(canvas, button, pos=QPoint(150, 130))
            self.assertEqual(canvas.pan, QPointF(50, 30))
            self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.CrossCursor)
        self.assertEqual(canvas.regions, {})
        self.assertEqual(edits, [])
        QTest.mousePress(canvas, Qt.MouseButton.RightButton, pos=QPoint(100, 100))
        canvas.reset_view()
        QTest.mouseRelease(canvas, Qt.MouseButton.RightButton, pos=QPoint(150, 130))
        self.assertEqual(canvas.pan, QPointF())
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.CrossCursor)
        canvas.close()

    def test_control_key_previews_pan_cursor_without_canvas_focus(self):
        preview = Preview()
        preview.set_region_kind('jacket')
        preview.show()
        preview.play_button.setFocus()
        self.app.processEvents()
        canvas = preview.canvas
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.CrossCursor)
        QTest.keyPress(preview.play_button, Qt.Key.Key_Control)
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(100, 100))
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(120, 120))
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        QTest.keyRelease(canvas, Qt.Key.Key_Control)
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.CrossCursor)
        preview.close()

    def test_frame_transforms_generation_and_exact_frame(self):
        preview = Preview()
        image = QImage(20, 10, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        frame = QVideoFrame(image)
        frame.setStartTime(1_250_000)
        frame.setRotation(QtVideo.Rotation.Clockwise90)
        preview._receive_frame(frame, 0)
        self.assertEqual(preview.canvas.image.size().width(), 10)
        self.assertEqual(preview.frame_time, 1.25)
        preview._generation = 1
        frame.setStartTime(2_000_000)
        preview._receive_frame(frame, 0)
        self.assertEqual(preview.frame_time, 1.25)
        preview.show_frame(image, 3.)
        preview._receive_frame(frame, 1)
        self.assertEqual(preview.frame_time, 3.)
        profile = {"player": [.1, .2, .3, .4]}
        preview.set_regions(profile)
        profile["player"][0] = 0
        self.assertEqual(preview.regions["player"][0], .1)
        preview.clear()
        self.assertTrue(preview.canvas.image.isNull())
        self.assertIsNone(preview.frame_time)
        self.assertEqual(preview.regions, {})
        self.assertTrue(preview.player.source().isEmpty())
        preview.close()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_switch_while_playing_audio_video_does_not_deadlock(self):
        # Use a subprocess: a GIL/native renderer deadlock also blocks Qt timers.
        script = r"""
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from preview import Preview
app = QApplication([])
preview = Preview()
preview.show()
state = {"switches": 0, "play": False}
def poll():
    if preview.canvas.image.isNull():
        return
    if not state["play"]:
        preview.player.play()
        state["play"] = True
    if preview.frame_time is None or preview.frame_time < .2:
        return
    preview.player.pause()
    state["switches"] += 1
    if state["switches"] == 4:
        preview.clear()
        preview.close()
        print("SWITCHES_OK", flush=True)
        app.quit()
        return
    state["play"] = False
    preview.load(sys.argv[1 + state["switches"] % 2])
timer = QTimer()
timer.timeout.connect(poll)
timer.start(20)
preview.load(sys.argv[1])
app.exec()
"""
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.mp4", Path(directory) / "b.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=320x180:rate=30:duration=3", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                            "-c:a", "aac", "-pix_fmt", "yuv420p", str(a)],
                           check=True, capture_output=True, timeout=30)
            shutil.copyfile(a, b)
            code, out, err = run_child([sys.executable, "-c", script, str(a), str(b)], 20)
            self.assertEqual(code, 0, err)
            self.assertIn("SWITCHES_OK", out)


    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_real_video_load_seek_and_playback(self):
        preview = Preview()

        def wait_for(predicate):
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if predicate() else None)
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            poll.start(20)
            deadline.start(5000)
            if not predicate():
                loop.exec()
            poll.stop()
            deadline.stop()
            self.assertTrue(predicate(), preview.player.errorString())

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=10:duration=3", "-c:v", "mpeg4",
                            "-pix_fmt", "yuv420p", str(path)], check=True,
                           capture_output=True, timeout=30)
            try:
                preview.load(path)
                wait_for(lambda: not preview.canvas.image.isNull())
                self.assertIsNotNone(preview.frame_time)
                preview.resize(800, 400)
                preview.show()
                self.app.processEvents()
                QTest.mouseClick(preview.slider, Qt.MouseButton.LeftButton,
                                 pos=QPoint(round(preview.slider.width() * .7), preview.slider.height() // 2))
                clicked = preview.player.position() / 1000
                self.assertGreater(clicked, 1.8)
                self.assertLess(clicked, 2.4)
                QTest.mouseClick(preview.play_button, Qt.MouseButton.LeftButton)
                wait_for(lambda: preview.frame_time is not None and preview.frame_time >= clicked + .1)
                self.assertGreaterEqual(preview.player.position() / 1000, clicked)
                QTest.mouseClick(preview.slider, Qt.MouseButton.LeftButton,
                                 pos=QPoint(round(preview.slider.width() * .2), preview.slider.height() // 2))
                wait_for(lambda: preview.frame_time is not None and .5 <= preview.frame_time < 1.5)
                self.assertTrue(preview.player.isPlaying())
                preview.player.pause()
                preview.seek(1.)
                wait_for(lambda: preview.frame_time is not None and .9 <= preview.frame_time <= 1.1)
                self.assertFalse(preview.player.isPlaying())
                preview.speed_combo.setCurrentIndex(3)
                self.assertEqual(preview.player.playbackRate(), 2.)
                preview.volume_slider.setValue(30)
                self.assertAlmostEqual(preview.audio.volume(), .3)
                preview.toggle_playback()
                wait_for(lambda: preview.frame_time is not None and preview.frame_time > 1.2)
                preview.player.pause()
                self.assertFalse(preview.canvas.image.isNull())
            finally:
                preview.clear()
                preview.close()


if __name__ == "__main__":
    unittest.main()
