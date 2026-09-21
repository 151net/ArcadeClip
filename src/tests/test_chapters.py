"""Chapter marks: what YouTube reads from a description and what a file carries."""
import subprocess
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import chapters


class TimestampTests(unittest.TestCase):
    def test_minutes_until_an_hour_then_hours(self):
        for seconds, text in ((0, "0:00"), (9, "0:09"), (75, "1:15"), (599, "9:59"),
                              (3600, "1:00:00"), (3725, "1:02:05"), (-5, "0:00")):
            with self.subTest(seconds=seconds):
                self.assertEqual(chapters.timestamp(seconds), text)


class DescriptionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            dict(start=30, end=150, title="Starry Colors", player="Tirr", achievement=100.7705),
            dict(start=150, end=280, title="GIGANTØMAKHIA", player="Tirr", achievement=100.8883),
            dict(start=280, end=400, title=None, player=None, achievement=None),
        ]

    def test_an_opening_mark_is_added_when_the_first_clip_starts_late(self):
        text = chapters.description(self.rows).splitlines()
        # YouTube ignores the whole description unless the first timestamp is 0:00.
        self.assertTrue(text[0].startswith("0:00 "))
        self.assertEqual(len(text), 4)
        self.assertIn("0:30 Starry Colors · Tirr 100.7705", text)
        self.assertIn("2:30 GIGANTØMAKHIA · Tirr 100.8883", text)

    def test_a_clip_at_zero_keeps_its_own_title(self):
        rows = [dict(start=0, end=100, title="First"), dict(start=100, end=200, title="Second")]
        self.assertEqual(chapters.description(rows).splitlines(),
                         ["0:00 First", "1:40 Second"])

    def test_clips_are_ordered_and_empty_ones_dropped(self):
        rows = [dict(start=200, end=300, title="Late"), dict(start=50, end=50, title="Empty"),
                dict(start=100, end=200, title="Early")]
        self.assertEqual(chapters.description(rows).splitlines(),
                         ["0:00 " + chapters.OPENING, "1:40 Early", "3:20 Late"])

    def test_youtube_rules_are_reported_rather_than_guessed_at(self):
        self.assertEqual(chapters.refused(self.rows, duration=400), [])
        short = [dict(start=0, end=5, title="A"), dict(start=5, end=9, title="B"),
                 dict(start=9, end=20, title="C")]
        self.assertEqual(len(chapters.refused(short, duration=20)), 1)
        self.assertEqual(len(chapters.refused([dict(start=0, end=600, title="Only")], duration=600)), 1)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(start=0, end=100, title="Song", player="P1", achievement=100.1,
                        player2="P2", achievement2=99.9)

    def test_a_template_names_the_parts_it_wants(self):
        self.assertEqual(chapters.title(self.row, "{index}. {title}", index=4), "4. Song")
        self.assertEqual(chapters.title(self.row, "{player1_name} vs {player2_name}"), "P1 vs P2")
        self.assertEqual(chapters.title(self.row, "{player2_achievement}"), "99.9000")

    def test_a_missing_part_leaves_no_hole(self):
        alone = dict(start=0, end=10, title="Song", player="P1", achievement=100.1)
        self.assertEqual(chapters.title(alone, chapters.FORMAT), "Song · P1 100.1000")
        # No second player: the separator that would have introduced them goes too.
        self.assertEqual(chapters.title(alone, "{title} · {player2_name}"), "Song")
        self.assertEqual(chapters.title(alone, "{player2_name}/{player1_name}"), "P1")

    def test_an_unknown_part_is_reported_not_raised_at_the_reader(self):
        self.assertIn("nothing", chapters.check_format("{nothing}"))
        self.assertEqual(chapters.check_format(chapters.FORMAT), "")
        self.assertEqual(chapters.check_format("{index}. {title}"), "")
        with self.assertRaises(ValueError):
            chapters.title(self.row, "{nothing}")


class GapTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(start=30, end=150, title="One"), dict(start=200, end=330, title="Two")]

    def test_pauses_become_chapters_only_when_asked(self):
        self.assertEqual(chapters.description(self.rows).splitlines(),
                         ["0:00 " + chapters.OPENING, "0:30 One", "3:20 Two"])
        self.assertEqual(chapters.description(self.rows, gap="쉬는 시간").splitlines(),
                         ["0:00 쉬는 시간", "0:30 One", "2:30 쉬는 시간", "3:20 Two"])

    def test_clips_that_run_together_need_no_gap(self):
        rows = [dict(start=0, end=100, title="One"), dict(start=100, end=200, title="Two")]
        self.assertEqual(chapters.description(rows, gap="pause").splitlines(),
                         ["0:00 One", "1:40 Two"])

    def test_nothing_chosen_is_said_plainly(self):
        self.assertEqual(chapters.description([]), "")
        self.assertEqual(len(chapters.refused([])), 1)
        with self.assertRaises(ValueError):
            chapters.ffmetadata([], 100)


class MetadataTests(unittest.TestCase):
    def test_times_are_milliseconds_and_cover_the_whole_video(self):
        rows = [dict(start=30, end=150, title="One"), dict(start=150, end=280, title="Two")]
        text = chapters.ffmetadata(rows, 400)
        self.assertTrue(text.startswith(";FFMETADATA1"))
        self.assertEqual(text.count("[CHAPTER]"), 3)
        self.assertIn("START=0", text)
        self.assertIn("END=30000", text)
        self.assertIn("START=150000", text)
        # The last chapter runs to the end of the recording, not to the last clip.
        self.assertIn("END=400000", text)

    def test_characters_the_format_reserves_are_escaped(self):
        rows = [dict(start=0, end=10, title="a=b;c#d")]
        line = next(l for l in chapters.ffmetadata(rows, 20).splitlines() if l.startswith("title="))
        self.assertEqual(line, "title=a" + chr(92) + "=b" + chr(92) + ";c" + chr(92) + "#d")

    def test_an_unknown_duration_is_refused(self):
        for duration in (None, 0, 10):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                chapters.ffmetadata([dict(start=20, end=30, title="x")], duration)


class WriteTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_a_real_file_carries_the_chapters_and_is_not_re_encoded(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "recording.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=10:duration=40", "-c:v", "mpeg4",
                            "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True, timeout=60)
            rows = [dict(start=10, end=25, title="First song"), dict(start=25, end=39, title="Second")]
            output = folder / "with-chapters.mp4"
            chapters.write_chapters(source, output, rows, 40, threading.Event(), lambda _: None,
                                    tool=shutil.which("ffmpeg"))
            probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-print_format", "json",
                                    "-show_chapters", "-show_streams", str(output)],
                                   check=True, capture_output=True, text=True, timeout=60)
            report = json.loads(probe.stdout)
            titles = [c["tags"]["title"] for c in report["chapters"]]
            self.assertEqual(titles, [chapters.OPENING, "First song", "Second"])
            starts = [round(float(c["start_time"])) for c in report["chapters"]]
            self.assertEqual(starts, [0, 10, 25])
            self.assertEqual(report["streams"][0]["codec_name"], "mpeg4")
            self.assertFalse(list(folder.glob("*.ffmeta")))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_writing_over_the_source_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "same.mp4"
            source.write_bytes(b"not really a video")
            with self.assertRaises(ValueError):
                chapters.write_chapters(source, source, [dict(start=0, end=5, title="x")], 10,
                                        threading.Event(), lambda _: None)


if __name__ == "__main__":
    unittest.main()
