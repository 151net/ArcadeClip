"""Regression checks for source switches and portable profiles."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app import MainWindow
from profiles import load_profile, validate_profile, write_json
from setup_dialog import SetupDialog


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_download_eta_and_unsupported_notice_remain_visible(self):
        from i18n import tr
        with tempfile.TemporaryDirectory() as name, patch.object(MainWindow, 'refresh_catalog_summary'):
            window = MainWindow(Path(name))
            window.analysis_update({'kind': 'download', 'step': 2, 'mode': 'standard', 'message': 'start'})
            notice = window.download_mode.text()
            self.assertEqual(notice, tr('일반 다운로드 · 이 영상은 조각 병렬 다운로드를 지원하지 않습니다.'))
            self.assertEqual(window.download_eta.text(), tr('예상 남은 시간 계산 중…'))
            window.analysis_update({'kind': 'download', 'step': 2, 'percent': 25, 'eta_seconds': 90, 'message': 'progress'})
            self.assertEqual(window.download_mode.text(), notice)
            self.assertIn('00:01:30', window.download_eta.text())
            window.analysis_update({'kind': 'download', 'step': 3, 'message': 'merge'})
            self.assertEqual(window.download_eta.text(), tr('예상 남은 시간 계산 중…'))
            window.analysis_update({'kind': 'download', 'step': 4, 'message': 'done'})
            self.assertEqual(window.download_eta.text(), '')
            window.close()

    def test_project_volume_save_restore_and_legacy_files(self):
        import json
        with tempfile.TemporaryDirectory() as name, patch.object(MainWindow, 'refresh_catalog_summary'):
            directory = Path(name)
            window = MainWindow(directory)
            project = directory / 'project.json'
            for volume in (0, 37, 100):
                window.preview.volume_slider.setValue(volume)
                self.assertTrue(window.volume_save_timer.isActive())
                with patch('app.QFileDialog.getSaveFileName', return_value=(str(project), '')):
                    window.save_session()
                self.assertEqual(json.loads(project.read_text())['volume'], volume)
                window.preview.volume_slider.setValue(80)
                window.load_session(project)
                self.assertEqual(window.preview.volume_slider.value(), volume)
                self.assertAlmostEqual(window.preview.audio.volume(), volume / 100, places=5)
            write_json(project, {'version': 1, 'ranges': []})
            window.load_session(project)
            self.assertEqual(window.preview.volume_slider.value(), 100)
            for bad in (-1, 101, True, None, '30'):
                write_json(project, {'version': 1, 'ranges': [], 'volume': bad})
                with patch.object(window, 'fail') as fail:
                    window.load_session(project)
                    fail.assert_called_once()
                self.assertEqual(window.preview.volume_slider.value(), 100)
            window.close()

    def test_volume_autosave_waits_two_seconds_after_last_change(self):
        import json
        with tempfile.TemporaryDirectory() as name, patch.object(MainWindow, 'refresh_catalog_summary'):
            directory = Path(name)
            window = MainWindow(directory)
            recovery = directory / 'recovery.json'
            window.preview.volume_slider.setValue(20)
            QTest.qWait(1100)
            self.assertFalse(recovery.exists())
            window.preview.volume_slider.setValue(35)
            QTest.qWait(1100)
            self.assertFalse(recovery.exists())
            QTest.qWait(1100)
            self.assertEqual(json.loads(recovery.read_text())['volume'], 35)
            self.assertFalse(window.volume_save_timer.isActive())
            window.preview.volume_slider.setValue(50)
            window.close()
            self.assertEqual(json.loads(recovery.read_text())['volume'], 50)
            self.assertFalse(window.volume_save_timer.isActive())

    def test_download_workers_slider_persists_and_reaches_download(self):
        from PySide6.QtCore import QSettings
        with tempfile.TemporaryDirectory() as directory, patch.object(MainWindow, 'refresh_catalog_summary'):
            settings = QSettings(str(Path(directory) / 'settings.ini'), QSettings.Format.IniFormat)
            with patch('workspace_ui.QSettings', return_value=settings):
                window = MainWindow(Path(directory))
                self.assertEqual(window.download_workers.value(), 4)
                self.assertIn('#23834b', window.download_workers_status.styleSheet())
                window.download_workers.setValue(5)
                self.assertIn('#bd3434', window.download_workers_status.styleSheet())
                self.assertEqual(int(settings.value('download_workers')), 5)
                window.remote = dict(url='https://youtu.be/abcdefghijk', title='test', duration=10)
                window.youtube_start.setText('00:00:01')
                window.youtube_end.setText('00:00:05')
                with patch.object(window, 'run_task') as run, patch('app.download_section', return_value=Path(directory) / 'video.mkv') as download:
                    window.download_range()
                    run.assert_called_once()
                    import threading
                    run.call_args.args[0](threading.Event(), lambda _: None)
                    self.assertEqual(download.call_args.kwargs['workers'], 5)
                window.remote = None
                window.close()
                second = MainWindow(Path(directory))
                self.assertEqual(second.download_workers.value(), 5)
                second.close()

    def test_help_button_and_f1_open_the_user_guide(self):
        from PySide6.QtWidgets import QPushButton
        from i18n import tr
        with tempfile.TemporaryDirectory() as directory, patch.object(MainWindow, 'refresh_catalog_summary'), \
                patch('help_view.show_help') as show_help:
            window = MainWindow(Path(directory))
            button = next(b for b in window.findChildren(QPushButton) if b.text() == tr('도움말'))
            button.click()
            show_help.assert_called_once_with(window)
            show_help.reset_mock()
            QTest.keyClick(window, Qt.Key.Key_F1)
            show_help.assert_called_once_with(window)
            window.close()

    def test_language_setting_persists_and_applies_on_next_start(self):
        import i18n
        import gettext
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QComboBox, QPushButton
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {"ARCADECLIP_LANGUAGE": "ko"}), \
                patch.object(i18n, "_translation", gettext.NullTranslations()):
            path = str(Path(name) / "settings.ini")
            settings = QSettings(path, QSettings.Format.IniFormat)
            with patch("advanced_settings.QSettings", return_value=settings), \
                    patch("advanced_settings.QMessageBox.information") as notice, \
                    patch.object(MainWindow, "refresh_catalog_summary"):
                window = MainWindow(Path(name))
                window.show_advanced()
                language = window.advanced.findChild(QComboBox, "ui_language")
                self.assertEqual(window.advanced.windowTitle(), "Settings")
                self.assertEqual(language.accessibleName(), "Language")
                self.assertEqual(language.currentData(), "ko")
                notice.assert_not_called()
                language.setFocus()
                QTest.keyClick(language, Qt.Key.Key_Down)
                self.assertEqual(settings.value("language"), "en")
                notice.assert_called_once_with(window.advanced, "Language",
                                               "언어 변경은 앱을 다시 실행하면 적용됩니다.")
                self.assertEqual(i18n.tr("파일 저장"), "파일 저장")
                settings.sync()
                code = (
                    "import sys; from unittest.mock import patch; "
                    "from PySide6.QtCore import QSettings; import i18n; "
                    "settings = QSettings(sys.argv[1], QSettings.Format.IniFormat)\n"
                    "with patch('PySide6.QtCore.QSettings', return_value=settings): i18n.initialize_language()\n"
                    "from profiles import REGIONS\n"
                    "print(i18n.tr('파일 저장'), i18n.tr('Settings'), i18n.tr('Language'), REGIONS['jacket'], sep='|')"
                )
                result = subprocess.check_output([sys.executable, "-c", code, path], encoding="utf-8",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                self.assertEqual(result.strip(), "Export clips|Settings|언어|Jacket")
                with patch("PySide6.QtCore.QSettings", return_value=settings):
                    i18n.initialize_language()
                window.close()
                english = MainWindow(Path(name))
                english.show_advanced()
                language = english.advanced.findChild(QComboBox, "ui_language")
                self.assertEqual(english.advanced.windowTitle(), "Settings")
                self.assertEqual(language.accessibleName(), "언어")
                self.assertTrue(any(b.text() == "Settings" for b in english.findChildren(QPushButton)))
                self.assertEqual(language.currentData(), "en")
                notice.reset_mock()
                language.setCurrentIndex(0)
                notice.assert_called_once_with(english.advanced, i18n.tr('Language'),
                                               i18n.tr('언어 변경은 앱을 다시 실행하면 적용됩니다.'))
                self.assertEqual(settings.value("language"), "ko")
                with patch("PySide6.QtCore.QSettings", return_value=settings):
                    i18n.initialize_language()
                self.assertEqual(i18n.tr("파일 저장"), "파일 저장")
                english.close()

    def test_translated_ui_keeps_progress_and_song_identity_independent(self):
        import gettext
        class English(gettext.NullTranslations):
            def gettext(self, message):
                return {"시간 선택으로 →": "Select time →"}.get(message, message)
        with tempfile.TemporaryDirectory() as name, patch("i18n._translation", English()):
            window = MainWindow(Path(name))
            self.assertEqual(window.source_next.text(), "Select time →")
            self.assertEqual(window.export_mode.currentData(), "fast")
            self.assertTrue(window.song_visible({"song_id": "audio:123", "image_url": None}))
            self.assertFalse(window.song_visible({"song_id": None, "image_url": None}))
            window.analysis_update({"kind": "download", "step": 2, "message": "Downloading"})
            self.assertEqual(window.download_progress.maximum(), 0)
            window.analysis_update({"kind": "download", "step": 4, "percent": 100, "message": "Done"})
            self.assertEqual(window.download_progress.value(), 100)
            window.close()

    def test_data_directory_restart_and_failure(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            window = MainWindow(directory)
            window.show()
            with patch("app.SetupDialog") as setup, patch("app.QProcess.startDetached") as restart, patch("app.QSettings") as settings, patch("app.QMessageBox.warning") as warning:
                setup.return_value.exec.return_value = True
                setup.return_value.directory = directory / "new"
                restart.return_value = (False, 0)
                window.configure()
                self.assertTrue((directory / "recovery.json").is_file())
                self.assertEqual(window.directory, directory)
                self.assertTrue(window.isVisible())
                settings.return_value.setValue.assert_called_with("data_directory", str(directory))
                warning.assert_called_once()
                restart.return_value = (True, 123)
                window.configure()
                self.assertFalse(window.isVisible())
                self.assertEqual(restart.call_args.args[0], sys.executable)
                self.assertEqual(restart.call_args.args[1], sys.orig_argv[1:])
            window.close()

    def test_review_bounds_do_not_change_analysis_range(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.preview.set_regions({k: [.1,.1,.2,.2] for k in ("jacket", "achievement", "player")})
            window.path = Path(name) / "video.mp4"
            window.ranges = [dict(source=str(window.path), start=100, end=150)]
            with patch.object(window.preview.player, "duration", return_value=1000000), patch.object(window, "play_range"):
                window.start_time.setText("00:00:20")
                window.end_time.setText("00:15:00")
                window.refresh_ranges()
                window.set_stage(3)
                window.range_list.setCurrentRow(0)
                self.assertEqual(window.selected_times(), (100, 150))
                window.timeline_changed(95, 155)
                window.commit_timeline()
                self.assertEqual(window.selected_times(), (95, 155))
                self.assertEqual(window.selected_times(analysis=True), (20, 900))
                window.set_stage(1)
                self.assertEqual(window.full_timeline.selection, (20, 900))
                self.assertTrue(window.zoom_panel.isHidden())
                self.assertEqual(window.full_timeline.clips, [])
                self.assertIsNone(window.full_timeline.viewport)
                window.set_stage(3)
                self.assertEqual(window.full_timeline.selection, (95, 155))
                self.assertFalse(window.zoom_panel.isHidden())
                self.assertEqual(len(window.full_timeline.clips), 1)
                self.assertIsNotNone(window.full_timeline.viewport)
                window.set_stage(2)
                with patch.object(window, "existing_analysis_choice", return_value="append"), patch.object(window.profile, "profile_for_analysis", return_value={}), patch.object(window, "run_task") as run, patch("app.analyze") as analyze:
                    window.analyze_range()
                    run.call_args.args[0](None, None)
                    self.assertEqual(analyze.call_args.args[1:3], (20, 900))
            window.close()

    def test_reanalysis_cancel_append_and_replace(self):
        from copy import deepcopy
        from app import source_key
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.preview.set_regions({k: [.1,.1,.2,.2] for k in ("jacket", "achievement", "player")})
            window.path = Path(name) / "video.mp4"
            window.end_time.setText("00:01:40")
            source = str(window.path)
            window.ranges = [dict(source=source, start=10, end=20),
                             dict(source=source, start=30, end=40, custom=True)]
            window.refresh_ranges()
            baseline = deepcopy(window.detected_ranges)
            with patch.object(window, "selected_times", return_value=(0, 100)), patch.object(window, "existing_analysis_choice", return_value="cancel"), patch.object(window.profile, "profile_for_analysis") as profile, patch.object(window, "run_task") as run:
                window.analyze_range()
                profile.assert_not_called()
                run.assert_not_called()
            result = dict(clips=[dict(source=source, start=50, end=60)], warnings=[])
            for choice, expected in (("append", 3), ("replace", 2)):
                before = deepcopy(window.ranges)
                with patch.object(window, "existing_analysis_choice", return_value=choice) as ask, patch.object(window.profile, "profile_for_analysis", return_value={}), patch.object(window, "selected_times", return_value=(0, 100)), patch.object(window, "run_task") as run:
                    window.analyze_range()
                    ask.assert_called_once()
                    # Cancellation or worker failure must leave the existing data intact.
                    self.assertEqual(window.ranges, before)
                    run.call_args.args[1](result)
                self.assertEqual(len(window.ranges), expected)
                self.assertTrue(any(row.get("custom") for row in window.ranges))
            originals = window.detected_ranges[source_key(source)]
            self.assertEqual([(r["start"], r["end"]) for r in originals], [(50, 60)])
            self.assertNotEqual(window.detected_ranges, baseline)
            QTimer.singleShot(0, lambda: next(b for b in QApplication.activeModalWidget().buttons() if b.text() == "복원").click())
            window.reset_detected_clips()
            self.assertEqual(len(window.ranges), 2)
            self.assertEqual(window.ranges[0]["start"], 50)
            window.close()

    def test_new_profile_autosave_and_review_view_reset(self):
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QAbstractAnimation
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            panel = window.profile
            window.preview.canvas.set_image(QImage(100, 100, QImage.Format.Format_RGB32))
            regions = {key: [.1,.1,.5,.5] for key in ("jacket", "achievement", "player")}
            window.preview.set_regions(regions)
            with patch("profile_panel.QInputDialog.getText", return_value=("Arcade", True)) as ask:
                profile = panel.profile_for_analysis()
                ask.assert_called_once()
            self.assertEqual(load_profile(panel.profile_path), profile)
            window.show()
            window.set_stage(2)
            self.application.processEvents()
            self.assertEqual(window.stage_group.button(2).text(), "[3] 인식 영역")
            self.assertEqual(panel.kind_pulse.state(), QAbstractAnimation.State.Running)
            panel.kind.setCurrentIndex(panel.kind.findData("jacket"))
            self.assertEqual(panel.kind_pulse.state(), QAbstractAnimation.State.Stopped)
            self.assertEqual(panel.kind.styleSheet(), "")
            window.preview.canvas.zoom_scale = 3
            window.preview.canvas.pan = QPointF(50, 60)
            window.set_stage(3)
            self.assertEqual(window.preview.canvas.zoom_scale, 1)
            self.assertEqual(window.preview.canvas.pan, QPointF())
            self.assertEqual(window.preview.regions, regions)
            self.assertEqual(window.stage_group.button(2).text(), "3 인식 영역")
            self.assertEqual(window.stage_group.button(3).text(), "[4] 클립 확인")
            panel.profile_path = None
            panel.name.clear()
            with patch("profile_panel.QInputDialog.getText", return_value=("", False)):
                self.assertIsNone(panel.profile_for_analysis())
            panel.name.setText("Named")
            with patch("profile_panel.QInputDialog.getText") as ask:
                self.assertEqual(panel.profile_for_analysis()["name"], "Named")
                ask.assert_not_called()
            window.close()

    def test_youtube_temporary_tab_and_metadata_restore(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            metadata = dict(url="https://www.youtube.com/watch?v=abcdefghijk", title="리듬 게임 방송", duration=180,
                            uploader="채널", was_live=True, video_date="2026-09-15 22시")
            window.link_ready(metadata)
            self.assertEqual(window.stage, 5)
            self.assertFalse(window.youtube_tab.isHidden())
            self.assertTrue(window.time_controls.isHidden())
            window.youtube_start.setText("00:00:10")
            window.youtube_end.setText("00:00:20")
            video = Path(name) / "downloads" / "youtube-test" / "video.mkv"
            with patch.object(window, "run_task") as run, patch("app.download_section", return_value=video) as download:
                window.download_range()
                result = run.call_args.args[0](None, lambda _: None)
                self.assertEqual(download.call_args.args[1:4], (10, 20, Path(name) / "downloads"))
                self.assertIs(run.call_args.kwargs["log"], window.download_log)
            with patch.object(window.preview, "load"):
                window.youtube_downloaded(result)
                self.assertEqual(window.stage, 0)
                self.assertTrue(window.youtube_tab.isHidden())
                item = window.files.item(0)
                self.assertFalse(item.icon().isNull())
                self.assertIn("LIVE", item.text())
                self.assertIn("2026-09-15 22시", item.text())
                self.assertIn("리듬 게임 방송", item.text())
                window.load_session(Path(name) / "recovery.json")
                self.assertIn("2026-09-15 22시", window.files.item(0).text())
            window.close()

    def test_export_date_folder_toggle(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            target = Path(name) / "clips"
            window.output_directory.setText(str(target))
            window.pending_exports = [dict(source=str(Path(name) / "video.mp4"), start=0, end=1)]
            self.assertTrue(window.export_date_folder.isChecked())
            with patch("app.datetime") as clock, patch("app.export_clips") as export, patch.object(window, "run_task") as run:
                clock.now.return_value = datetime(2026, 9, 15, 13, 24, 56)
                window.execute_export()
                run.call_args.args[0](None, None)
                self.assertEqual(export.call_args.args[1], target / "2026-09-15_13-24-56")
                window.export_date_folder.setChecked(False)
                window.execute_export()
                run.call_args.args[0](None, None)
                self.assertEqual(export.call_args.args[1], target)
                self.assertEqual(window.output_directory.text(), str(target))
            window.close()

    def test_custom_export_settings_reach_encoder(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QComboBox, QDoubleSpinBox
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.pending_exports = [dict(source=str(Path(name) / "video.mp4"), start=0, end=10)]
            def configure():
                dialog = QApplication.activeModalWidget()
                mode = dialog.findChild(QComboBox, "save_mode")
                self.assertEqual(mode.currentData(), "fast")
                mode.setCurrentIndex(mode.findData("accurate"))
                for key, value in (("format", "webm"), ("height", 720), ("fps", 30), ("channels", 1), ("audio_kbps", 128)):
                    field = dialog.findChild(QComboBox, "save_" + key)
                    field.setCurrentIndex(field.findData(value))
                dialog.findChild(QDoubleSpinBox, "save_target_mb").setValue(20)
                dialog.findChild(QDoubleSpinBox, "save_gain").setValue(-6)
                self.assertFalse(dialog.findChild(QComboBox, "save_quality").isEnabled())
                dialog.accept()
            QTimer.singleShot(0, configure)
            window.show_export_settings()
            self.assertTrue(window.output_names.item(0).text().endswith(".webm"))
            with patch.object(window, "run_task") as run, patch("app.export_clips") as export:
                window.execute_export()
                run.call_args.args[0](None, None)
                self.assertEqual(export.call_args.kwargs["format"], "webm")
                self.assertEqual(export.call_args.kwargs["target_mb"], 20)
                self.assertEqual(export.call_args.kwargs["gain"], -6)
                window.export_mode.setCurrentIndex(1)
                window.execute_export()
                run.call_args.args[0](None, None)
                self.assertNotIn("format", export.call_args.kwargs)
            window.close()

    def test_x_compression_preset(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPushButton, QLabel
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.pending_exports = [dict(source="video.mp4", start=0, end=160)]
            window.export_options = dict(format="webm", target_mb=500, gain=12)
            def choose():
                dialog = QApplication.activeModalWidget()
                dialog.findChild(QPushButton, "save_x_preset").click()
                self.assertTrue(any("1개 클립이 140초" in label.text() for label in dialog.findChildren(QLabel)))
                dialog.accept()
            QTimer.singleShot(0, choose)
            window.show_export_settings()
            self.assertEqual(window.export_options, dict(height=720, fps=60, audio_kbps=128, channels=2))
            self.assertEqual(window.encoder_crf.value(), 23)
            self.assertEqual(window.encoder_preset.currentText(), "medium")
            self.assertEqual(window.pending_exports[0]["end"], 160)
            window.close()

    def test_share_presets_and_gpu_option(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPushButton, QComboBox, QCheckBox
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            for index, height in enumerate((720, 1080, 1440, 1080)):
                def choose():
                    dialog = QApplication.activeModalWidget()
                    dialog.findChild(QComboBox, "save_share_preset").setCurrentIndex(index)
                    dialog.findChild(QPushButton, "save_x_preset").click()
                    gpu = dialog.findChild(QCheckBox, "save_gpu")
                    gpu.setChecked(True)
                    self.assertFalse(dialog.findChild(QComboBox, "save_speed").isEnabled())
                    container = dialog.findChild(QComboBox, "save_format")
                    container.setCurrentIndex(container.findData("webm"))
                    self.assertFalse(gpu.isEnabled())
                    container.setCurrentIndex(container.findData("mp4"))
                    self.assertTrue(gpu.isEnabled())
                    dialog.accept()
                QTimer.singleShot(0, choose)
                window.show_export_settings()
                self.assertEqual(window.export_options["fps"], 60)
                self.assertEqual(window.export_options["height"], height)
                self.assertTrue(window.export_options["gpu"])
            window.close()

    def test_detection_offsets_and_grouped_advanced_settings(self):
        from app import source_key
        from analysis_common import DEFAULTS
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QGroupBox, QDoubleSpinBox, QDialog
        self.assertEqual(DEFAULTS["hash_distance"], 10)
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            existing = dict(source=str(window.path), start=30, end=40, custom=True, manual_bounds=True)
            window.ranges = [existing]
            window.refresh_ranges()
            with patch.object(window.preview.player, "duration", return_value=100000), patch.object(window, "play_range"):
                window.analysis_finished({"clips": [dict(source=str(window.path), start=5, end=95)], "warnings": []})
                self.assertEqual((window.ranges[1]["start"], window.ranges[1]["end"]), (0, 100))
                self.assertEqual((existing["start"], existing["end"]), (30, 40))
                original = window.detected_ranges[source_key(window.path)][0]
                self.assertEqual((original["start"], original["end"]), (5, 95))
                window.apply_clip_offsets()
                self.assertEqual((window.ranges[1]["start"], window.ranges[1]["end"]), (0, 100))
            window.show_advanced()
            self.assertEqual(window.advanced.windowTitle(), "Settings")
            self.assertEqual({g.title() for g in window.advanced.findChildren(QGroupBox)},
                             {"영역별 인식", "분석 성능", "화면 탐색", "곡 데이터", "데이터 저장 위치", "진단 로그", "앱 버전"})
            self.assertEqual(window.follow_clip.parentWidget().title(), "화면 탐색")
            observed = []
            def accept_settings():
                dialog = self.application.activeModalWidget()
                if isinstance(dialog, QDialog):
                    observed.extend(g.title() for g in dialog.findChildren(QGroupBox))
                    field = dialog.findChild(QDoubleSpinBox, "hash_distance")
                    observed.append(field.value())
                    field.setValue(11)
                    dialog.accept()
            QTimer.singleShot(0, accept_settings)
            window.profile.edit_recognition()
            self.assertEqual(observed, ["자켓 영역 · 곡 판독", "글자 영역 · 이름 및 달성률", 10])
            self.assertEqual(window.profile.recognition_options["hash_distance"], 11)
            window.close()

    def test_clip_merge_selection_originals_and_visible_context(self):
        from copy import deepcopy
        from app import source_key
        from PySide6.QtWidgets import QMenu
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            window.ranges = [dict(source=str(window.path), start=i * 10, end=i * 10 + 15,
                                  image_url="jacket.png", title="song", achievement=99 + i / 10)
                             for i in range(4)]
            with patch.object(window, "select_range"), patch.object(window.preview.player, "duration", return_value=100000):
                window.refresh_ranges()
                originals = deepcopy(window.detected_ranges)
                window.show()
                window.set_stage(3)
                self.application.processEvents()
                def click(index, modifier=Qt.KeyboardModifier.NoModifier):
                    rect = window.range_list.visualItemRect(window.range_list.item(index))
                    QTest.mouseClick(window.range_list.viewport(), Qt.MouseButton.LeftButton, modifier,
                                     QPoint(rect.right() - 20, rect.center().y()))
                click(0)
                click(2, Qt.KeyboardModifier.ShiftModifier)
                self.assertEqual(len(window.range_list.selectedItems()), 3)
                click(1, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(sorted(window.range_list.row(i) for i in window.range_list.selectedItems()), [0, 2])
                window.refresh_ranges()
                self.assertEqual(len(window.range_list.selectedItems()), 2)
                window.merge_clips()
                self.assertEqual(len(window.ranges), 3)
                self.assertEqual((window.ranges[0]["start"], window.ranges[0]["end"]), (0, 35))
                self.assertNotIn("achievement", window.ranges[0])
                self.assertTrue(window.ranges[0]["manual_bounds"])
                self.assertEqual(window.detected_ranges, originals)
                with patch.object(window, "offset_manual_choice", return_value="reset"):
                    window.apply_clip_offsets()
                self.assertEqual(window.ranges[0]["end"], 45)
                QTimer.singleShot(0, lambda: next(b for b in QApplication.activeModalWidget().buttons() if b.text() == "복원").click())
                window.reset_detected_clips()
                self.assertEqual(len(window.ranges), 4)
                self.assertEqual(window.detected_ranges, originals)
                window.range_list.item(1).setHidden(True)
                menu = QMenu()
                with patch("app.QMenu") as factory:
                    factory.return_value.addAction.side_effect = menu.addAction
                    factory.return_value.exec.side_effect = lambda *args: next(
                        a for a in menu.actions() if a.text() == "아래 클립과 합치기")
                    window.clip_context_menu(0, QPoint())
                self.assertEqual((window.ranges[0]["start"], window.ranges[0]["end"]), (0, 35))
                self.assertEqual(window.ranges[1]["start"], 10)
                window.undo_clip_edit()
                self.assertEqual(len(window.ranges), 4)
                window.ranges[1]["source"] = str(Path(name) / "other.mp4")
                window.merge_clips([0, 1])
                self.assertEqual(len(window.ranges), 4)
                self.assertEqual(window.detected_ranges, originals)
            window.close()

    def test_range_switch_and_remote_return(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            a, b = directory / "한글 A.mp4", directory / "B.mp4"
            a.touch()
            b.touch()
            window = MainWindow(directory)
            with patch.object(window.preview, "load"), patch.object(window, "play_range") as play:
                window.add_paths([a, b])
                window.ranges = [{"source": str(a), "start": 1, "end": 2}]
                window.refresh_ranges()
                window.select_range(window.range_list.item(0))
                self.assertEqual(window.path, a)
                self.assertIsNotNone(window.pending_range)
                window.media_loaded(3000)
                self.assertEqual(window.end_time.text(), "00:00:03.000")
                self.assertEqual(window.review_selection, (1, 2))
                window.media_loaded(3000)
                self.assertEqual(window.end_time.text(), "00:00:03.000")
                play.assert_called_once()
                window.link_ready({"title": "remote", "duration": 3})
                self.assertIsNone(window.files.currentItem())
                self.assertFalse(window.download.isHidden())
                self.assertTrue(all(c.isHidden() for c in window.local_time_controls))
                window.files.setCurrentRow(0)
                self.assertEqual(window.path, a)
                self.assertIsNone(window.remote)
                self.assertTrue(window.download.isHidden())
                self.assertTrue(all(not c.isHidden() for c in window.local_time_controls))
                self.assertNotIn(window.range_list, window.busy_controls)
            window.close()

    def test_setup_completion_keeps_main_window_alive(self):
        # Run native QObject destruction in a subprocess: the regression aborts Python.
        script = """
import tempfile
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app import MainWindow
from setup_dialog import SetupDialog
application = QApplication([])
with tempfile.TemporaryDirectory() as name:
    window = MainWindow(Path(name))
    window.show()
    original = SetupDialog.__init__
    def initialize(dialog, *args, **kwargs):
        original(dialog, *args, **kwargs)
        dialog.settings = type('Settings', (), {'setValue': lambda *args: None})()
        QTimer.singleShot(0, dialog.prepare)
    def run():
        with patch.object(SetupDialog, '__init__', initialize), patch('setup_dialog.catalog_status', return_value=True):
            for _ in range(20):
                window.configure()
                assert window.isVisible()
                application.processEvents()
        window.close()
        application.quit()
    QTimer.singleShot(0, run)
    application.exec()
print('setup cycles passed')
"""
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script],
                                env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("setup cycles passed", result.stdout)

    def test_clip_defaults_offsets_ranks_and_filtered_scroll(self):
        from clips import achievement_text, label, validate_clip
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "clip.mp4"
            window.ranges = [dict(source=str(window.path), start=i * 10, end=i * 10 + 8,
                                  title=f"Song {i}", player="A" if i % 2 else "B") for i in range(100)]
            window.refresh_ranges()
            self.assertTrue(all(window.range_list.item(i).checkState() == Qt.CheckState.Unchecked for i in range(100)))
            window.player_filter.setText("A")
            window.set_stage(3)
            window.show()
            QApplication.processEvents()
            item = window.range_list.item(51)
            window.range_list.scrollToItem(item)
            QApplication.processEvents()
            before = window.range_list.verticalScrollBar().value()
            with patch.object(window, "play_range"), patch.object(window.preview.player, "duration", return_value=1000000):
                QTest.mouseClick(window.range_list.viewport(), Qt.MouseButton.LeftButton,
                                 pos=window.range_list.visualItemRect(item).center())
                QApplication.processEvents()
                self.assertEqual(window.range_list.currentRow(), 51)
                self.assertEqual(window.range_list.verticalScrollBar().value(), before)
                window.refresh_ranges()
                QApplication.processEvents()
                self.assertEqual(window.range_list.verticalScrollBar().value(), before)
                window.clip_start_offset.setValue(-2)
                window.clip_end_offset.setValue(1)
                window.apply_clip_offsets()
                self.assertEqual((window.ranges[1]["start"], window.ranges[1]["end"]), (8, 19))
                first = [dict(r) for r in window.ranges]
                window.apply_clip_offsets()
                self.assertEqual(window.ranges, first)
                window.clip_start_offset.setValue(0)
                window.clip_end_offset.setValue(0)
                window.apply_clip_offsets()
                self.assertEqual((window.ranges[0]["start"], window.ranges[0]["end"]), (0, 8))
                window.clip_start_offset.setValue(600)
                window.apply_clip_offsets()
                self.assertEqual(window.ranges[1]["start"], 10)
            with patch.object(window, "start_export") as export:
                window.export_ranges(True)
                self.assertEqual(len(export.call_args.args[0]), 50)
            self.assertEqual(window.save_all_button.text(), "보이는 클립 50개 저장 설정 →")
            with self.assertRaises(ValueError):
                validate_clip(dict(window.ranges[0], boundary_offsets=[float("nan"), 0]))
            window.close()
        self.assertEqual(achievement_text(100), "100.0000")
        self.assertEqual(achievement_text(None), "미확인")
        self.assertTrue(label(dict(source="a.mp4", start=0, end=1, title="Song", player="A", achievement=100,
                                   player2="B", achievement2=99.5)).startswith("Song · A(100.0000)/B(99.5000)\n"))

    def test_detected_originals_manual_choices_layers_and_preview(self):
        from copy import deepcopy
        from app import source_key
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            source = str(window.path)
            window.ranges = [dict(source=source, start=20, end=40, title="A"),
                             dict(source=source, start=60, end=80, title="B")]
            window.refresh_ranges()
            original = deepcopy(window.detected_ranges)
            with patch.object(window.preview.player, "duration", return_value=100000), patch.object(window, "play_range"):
                window.set_stage(3)
                window.range_list.setCurrentRow(0)
                self.assertEqual((window.clip_start_offset.value(), window.clip_end_offset.value()), (-10, 10))
                window.apply_clip_offsets()
                self.assertEqual((window.ranges[0]["start"], window.ranges[0]["end"]), (10, 50))
                window.clip_fields["start"].setText("00:00:15.000")
                window.apply_clip_details()
                self.assertTrue(window.ranges[0]["manual_bounds"])
                edited = deepcopy(window.ranges)
                with patch.object(window, "offset_manual_choice", return_value="cancel"):
                    window.apply_clip_offsets()
                self.assertEqual(window.ranges, edited)
                window.clip_start_offset.setValue(-5)
                with patch.object(window, "offset_manual_choice", return_value="skip"):
                    window.apply_clip_offsets()
                self.assertEqual(window.ranges[0]["start"], 15)
                self.assertEqual(window.ranges[1]["start"], 55)
                with patch.object(window, "offset_manual_choice", return_value="reset"):
                    window.apply_clip_offsets()
                self.assertEqual(window.ranges[0]["start"], 15)
                self.assertFalse(window.ranges[0]["manual_bounds"])
                window.clip_start_offset.setValue(-10)
                window.apply_clip_offsets()
                self.assertEqual(window.ranges[0]["start"], 10)
                self.assertEqual(window.detected_ranges, original)
                window.sync_review_selection()
                bounds = (window.start_time.text(), window.end_time.text(), window.zoom_timeline.selection)
                with patch.object(window.preview, "seek") as seek, patch.object(window.preview.player, "play"):
                    window.check_edge(True)
                    seek.assert_called_with(47)
                    self.assertEqual(window.range_end, 50)
                    window.check_edge(False)
                    seek.assert_called_with(10)
                self.assertEqual((window.start_time.text(), window.end_time.text(), window.zoom_timeline.selection), bounds)
                with patch.object(window, "selected_times", return_value=(22, 30)):
                    window.add_range()
                self.assertEqual(window.zoom_timeline.custom_ids, {2})
                timeline = window.zoom_timeline
                timeline.view(0, 100)
                self.assertEqual(timeline.clip_at(timeline.x(25), 40), 0)
                self.assertEqual(timeline.clip_at(timeline.x(25), 60), 2)
                window.follow_clip.blockSignals(True)
                window.follow_clip.setChecked(False)
                window.select_timeline_clip(1)
                self.assertEqual(timeline.bounds, (0, 100))
                window.follow_clip.setChecked(True)
                window.select_timeline_clip(1)
                self.assertNotEqual(timeline.bounds, (0, 100))
                window.follow_clip.blockSignals(False)
                window.range_list.setCurrentRow(0)
                window.remove_range()
                self.assertEqual(len(window.ranges), 2)
                self.assertEqual(window.detected_ranges, original)
                window.save_recovery()
                with patch.object(window, "add_paths"):
                    window.load_session(Path(name) / "recovery.json")
                self.assertEqual(window.detected_ranges, original)
                self.assertEqual(len(window.ranges), 2)
                QTimer.singleShot(0, lambda: next(b for b in QApplication.activeModalWidget().buttons() if b.text() == "복원").click())
                window.reset_detected_clips()
                self.assertEqual([(r["start"], r["end"]) for r in window.ranges], [(20, 40), (60, 80), (22, 30)])
                self.assertTrue(window.ranges[2]["custom"])
                self.assertEqual(window.detected_ranges[source_key(source)], original[source_key(source)])
                window.undo_clip_edit()
                self.assertEqual(len(window.ranges), 2)
            window.close()

    def test_inline_guide_autoplay_and_hide(self):
        from PySide6.QtMultimedia import QMediaPlayer
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            window.show()
            window.set_stage(2)
            QApplication.processEvents()
            panel = window.profile
            self.assertTrue(panel.guide_prompt.isVisible())
            self.assertFalse(panel.guide_video.isVisible())
            panel.kind.setCurrentIndex(panel.kind.findData("jacket"))
            self.assertTrue(panel.guide_video.isVisible())
            self.assertTrue(panel.guide_player.source().toLocalFile().endswith("region-guide-jacket.mp4"))
            self.assertEqual(panel.guide_player.loops(), QMediaPlayer.Loops.Infinite)
            with patch.object(panel.guide_player, "play") as play:
                panel.kind.setCurrentIndex(panel.kind.findData("achievement"))
                play.assert_called_once()
            self.assertTrue(panel.guide_player.source().toLocalFile().endswith("region-guide-achievement.mp4"))
            with patch.object(panel.guide_player, "stop") as stop:
                window.set_stage(1)
                QApplication.processEvents()
                stop.assert_called()
            self.assertFalse(panel.guide_video.isVisible())
            window.close()

    def test_required_regions_gate_and_guide(self):
        from PySide6.QtGui import QImage
        import av
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            window.preview.canvas.set_image(QImage(1920, 1080, QImage.Format.Format_RGB32))
            window.profile.name.setText("Test")
            window.set_stage(2)
            window.ranges = [dict(source=str(window.path), start=1, end=10)]
            with patch("app.QMessageBox.warning") as warning, patch.object(window, "existing_analysis_choice") as choice, patch.object(window, "run_task") as run:
                window.analyze_range()
                self.assertIn("자켓", warning.call_args.args[2])
                run.assert_not_called()
                choice.assert_not_called()
                window.preview.set_regions({"jacket": [.1, .1, .2, .2]})
                window.analyze_range()
                self.assertIn("달성률", warning.call_args.args[2])
            window.set_stage(3)
            self.assertEqual(window.stage, 3)
            with self.assertRaises(ValueError):
                window.profile.current_profile(require_complete=True)
            window.preview.set_regions({k: [.1, .1, .2, .2] for k in ("jacket", "achievement", "player")})
            window.profile.current_profile(require_complete=True)
            window.set_stage(3)
            self.assertEqual(window.stage, 3)
            window.close()
        for kind in ("jacket", "achievement", "player"):
            with av.open(str(Path(__file__).parents[1] / "assets" / f"region-guide-{kind}.mp4")) as video:
                stream = video.streams.video[0]
                self.assertEqual((stream.width, stream.height), (960, 540))
                self.assertGreater(stream.frames, 150)
                self.assertGreater(len(list(video.decode(stream))), 150)

    def test_profile_library_and_analysis_save_choices(self):
        from copy import deepcopy
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            panel = window.profile
            panel.preview.canvas.set_image(QImage(100, 100, QImage.Format.Format_RGB32))
            profile = {"version": 1, "name": "Arcade", "size": [100, 100],
                       "regions": {k: [.1,.1,.5,.5] for k in ("jacket", "player", "achievement")}}
            panel.apply_profile(profile)
            panel.save_profile()
            original_path = panel.profile_path
            self.assertEqual(panel.profile_picker.currentData(), str(original_path))
            self.assertFalse(panel.is_modified())
            panel.preview.set_regions({**profile["regions"], "jacket": [.1,.1,.6,.6]})
            self.assertTrue(panel.is_modified())
            def click(text):
                return lambda dialog: next(button for button in dialog.buttons() if button.text() == text).click()
            with patch("profile_panel.QMessageBox.exec", click("저장 없이 곡 찾기")):
                unsaved = panel.profile_for_analysis()
            panel.accept_calibration(unsaved)
            self.assertEqual(load_profile(original_path), validate_profile({**profile, "recognition": {}}))
            self.assertTrue(panel.is_modified())
            with patch("profile_panel.QMessageBox.exec", click("선택한 프로필 덮어쓰기")):
                panel.profile_for_analysis()
            self.assertFalse(panel.is_modified())
            self.assertEqual(load_profile(original_path)["regions"]["jacket"], [.1,.1,.6,.6])
            panel.name.setText("Changed")
            with patch("profile_panel.QMessageBox.exec", click("새 프로필로 저장")), patch("profile_panel.QInputDialog.getText", return_value=("New", True)):
                panel.profile_for_analysis()
            self.assertNotEqual(panel.profile_path, original_path)
            self.assertEqual(panel.profile_picker.currentText(), "New")
            panel.select_profile(panel.profile_picker.findData(str(original_path)))
            self.assertEqual(panel.name.text(), "Arcade")
            panel.name.setText("Pending")
            with patch("profile_panel.QMessageBox.exec", click("취소")):
                self.assertIsNone(panel.profile_for_analysis())
            window.close()

    def test_profile_seek_preserves_playback_and_full_source_paths(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            with patch.object(window.preview, "load"):
                window.add_paths([Path(name)/"one"/"index.mp4", Path(name)/"two"/"index.mp4"])
            self.assertEqual(window.source_picker.itemText(0), str(Path(name)/"one"/"index.mp4"))
            self.assertEqual(window.source_picker.itemText(1), str(Path(name)/"two"/"index.mp4"))
            window.set_stage(2)
            window.show()
            window.preview.canvas.setFocus()
            QApplication.processEvents()
            with patch.object(window.preview.player, "pause") as pause, patch.object(window.preview.player, "play") as play, patch.object(window.preview, "seek") as seek:
                for stage in (1, 2, 3):
                    window.set_stage(stage)
                    for playing in (True, False):
                        with patch.object(window.preview.player, "isPlaying", return_value=playing):
                            window.preview._seek_slider(0)
                            window.scrub(2)
                pause.assert_not_called()
                play.assert_not_called()
                with patch.object(window.preview.player, "isPlaying", return_value=True), patch.object(window.preview.player, "position", return_value=5000), patch.object(window, "step_frame") as frame:
                    QTest.keyClick(window.preview.canvas, Qt.Key.Key_Right)
                    seek.assert_called_with(6)
                    QTest.keyClick(window.preview.canvas, Qt.Key.Key_Left)
                    seek.assert_called_with(4)
                    frame.assert_not_called()
                with patch.object(window.preview.player, "isPlaying", return_value=False), patch.object(window, "step_frame") as frame:
                    QTest.keyClick(window.preview.canvas, Qt.Key.Key_Right)
                    frame.assert_called_with(1)
            window.close()

    def test_export_selected_ranges_and_result(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.ranges = [{"source": str(Path(name) / "clip.mp4"), "start": i, "end": i + 1}
                             for i in range(3)]
            window.refresh_ranges()
            window.range_list.item(0).setCheckState(Qt.CheckState.Checked)
            window.range_list.item(2).setCheckState(Qt.CheckState.Checked)
            with patch.object(window, "start_export") as export:
                window.export_ranges(False)
                self.assertEqual(export.call_args.args[0], [window.ranges[0], window.ranges[2]])
                window.export_ranges(True)
                self.assertEqual(export.call_args.args[0], window.ranges)
            window.export_finished({"saved": ["done.mp4"], "failed": [{**window.ranges[1], "error": "failed"}],
                                    "cancelled": False})
            self.assertEqual([i for i, row in enumerate(window.ranges) if row.get("selected", True)], [1])
            self.assertIn("성공 1개", window.status.text())
            window.close()

    def test_review_filter_split_and_recovery_round_trip(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            source = directory / "video.mp4"
            source.touch()
            window = MainWindow(directory)
            window.ranges = [{"source": str(source), "start": 0, "end": 10, "title": "A", "player": "Alice",
                              "reviewed": True, "edited": True, "achievement": 100.0},
                             {"source": str(source), "start": 10, "end": 20, "title": "B", "player": "Bob"},
                             {"source": str(source), "start": 20, "end": 30, "title": "Unknown", "player": ""}]
            window.refresh_ranges()
            window.player_filter.setText("alice")
            self.assertFalse(window.range_list.item(0).isHidden())
            self.assertTrue(window.range_list.item(1).isHidden())
            self.assertFalse(window.range_list.item(2).isHidden())
            with patch.object(window, "start_export") as export:
                window.export_ranges(True)
                self.assertEqual([row["title"] for row in export.call_args.args[0]], ["A", "Unknown"])
            window.show_unknown.setChecked(False)
            self.assertTrue(window.range_list.item(2).isHidden())
            window.range_list.setCurrentRow(0)
            window.path = source
            with patch.object(window, "selected_times", return_value=(5, 9)):
                window.add_range()
            self.assertTrue(window.ranges[-1]["custom"])
            self.assertEqual((window.ranges[-1]["start"], window.ranges[-1]["end"]), (5, 9))
            saved = window.ranges.copy()
            with patch.object(window, "add_paths"):
                window.load_session(directory / "recovery.json")
            self.assertEqual(window.ranges, saved)
            # Reanalysis adds candidates without overwriting the user's edited rows.
            window.analysis_finished({"clips": [{"source": str(source), "start": 0, "end": 10}], "warnings": []})
            self.assertEqual(window.ranges[:len(saved)], saved)
            window.close()

    def test_inline_review_undo_and_save_preview(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.ranges = [{"source": str(Path(name) / "clip.mp4"), "start": 1, "end": 5,
                              "title": "Before", "achievement": 0}]
            window.refresh_ranges()
            window.set_stage(3)
            window.range_list.setCurrentRow(0)
            self.assertEqual(window.clip_fields["achievement"].text(), "0.0000")
            window.clip_fields["title"].setText("After")
            window.clip_fields["end"].setText("00:00:04.000")
            window.apply_clip_details()
            self.assertEqual(window.ranges[0]["title"], "After")
            self.assertEqual(window.ranges[0]["end"], 4)
            with patch.object(window, "apply_range"):
                window.undo_clip_edit()
            self.assertEqual(window.ranges[0]["title"], "Before")
            with patch.object(window, "run_task") as run:
                window.export_ranges(True)
                self.assertEqual(window.stage, 4)
                self.assertEqual(window.output_names.count(), 1)
                run.assert_not_called()
                window.execute_export()
                run.assert_called_once()
            window.check_clips(False)
            self.assertFalse(window.save_checked_button.isEnabled())
            self.assertTrue(window.save_all_button.isEnabled())
            window.close()

    def test_stage_scope_unknown_songs_and_network_paths(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.show()
            source = r"\\192.168.88.247\stream\data\video.mp4"
            with patch.object(window.preview, "load"), patch("pathlib.Path.resolve", side_effect=AssertionError("UI path resolution")), patch("pathlib.Path.is_file", side_effect=AssertionError("UI network stat")):
                window.add_paths([source])
                window.ranges = [{"source": source, "start": 0, "end": 1, "image_url": None},
                                 {"source": source, "start": 1, "end": 2, "image_url": "jacket.png", "title": "A"}]
                window.refresh_ranges()
                window.select_range(window.range_list.item(1))
                self.assertTrue(window.range_list.item(0).isHidden())
                self.assertEqual(window.full_timeline.clips, [])
                window.set_stage(3)
                self.assertEqual([r[0] for r in window.full_timeline.clips], [1])
                self.assertFalse(window.range_list.item(1).isHidden())
                window.show_unknown_songs.setChecked(True)
                self.assertFalse(window.range_list.item(0).isHidden())
                window.preview.set_regions({k: [.1, .1, .2, .2] for k in ("jacket", "achievement", "player")})
                for stage in range(5):
                    window.set_stage(stage)
                    self.assertEqual(window.source_actions.isVisible(), stage == 0)
                    self.assertEqual(window.timeline_panel.isVisible(), stage in (1, 3))
                    self.assertEqual(window.export_footer.isVisible(), stage == 3)
                    self.assertEqual(window.list_stack.isVisible(), stage in (0, 3))
                    self.assertEqual(window.time_controls.isVisible(), stage == 1)
                    self.assertEqual(window.detail_stack.isVisible(), stage != 1)
                    self.assertEqual(window.remove_clip_button.isVisible(), stage == 3)
                    self.assertEqual(window.preview.canvas.show_regions, stage == 2)
                with patch.object(window, "mark") as mark:
                    window.preview.canvas.setFocus()
                    QTest.keyClick(window.preview.canvas, Qt.Key.Key_S)
                    QTest.keyClick(window.preview.canvas, Qt.Key.Key_I)
                    mark.assert_not_called()
            window.close()

    def test_research_layout_filter_and_export_selection(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.show()
            self.assertFalse(window.preview_panel.isVisible())
            with patch.object(window.preview, "load"), patch.object(window, "play_range"):
                window.add_paths([Path(name) / "video.mp4"])
                window.ranges = [dict(source=str(window.path), start=i, end=i+1,
                                      player=player, selected=i == 0)
                                 for i, player in enumerate(("A", "B"))]
                window.refresh_ranges()
                window.player_filter.setText("A")
                self.assertEqual(window.full_timeline.clips, [])
                self.assertIn("숨김 1", window.selection_summary.text())
                window.set_stage(3)
                self.assertEqual([row[0] for row in window.full_timeline.clips], [0])
                window.range_list.setCurrentRow(0)
                self.assertIn("클립 1", window.current_clip_label.text())
                self.assertEqual(window.full_timeline.active_clip, 0)
                window.set_stage(4)
                self.assertEqual(len(window.pending_exports), 1)
                self.assertFalse(window.export_all)
                self.assertIn("1개", window.execute_export_button.text())
                window.set_stage(3)
                window.check_clips(False)
                window.set_stage(4)
                self.assertEqual(window.pending_exports, [])
                self.assertFalse(window.execute_export_button.isEnabled())
            window.close()


    def test_source_scoped_edits_exports_and_session(self):
        from app import source_key
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            a, b, c = [directory / folder / "index.mp4" for folder in ("a", "b", "c")]
            window = MainWindow(directory)
            with patch.object(window.preview, "load"), patch.object(window, "play_range"):
                window.add_paths([a])
                window.ranges = [{"source": str(a), "start": 0, "end": 2, "title": "A", "selected": False}]
                window.refresh_ranges()
                window.clip_undo = [{"source": str(a), "start": 0, "end": 1}]
                window.pending_exports = window.ranges.copy()
                window.add_paths([b])
                self.assertEqual(window.ranges, [])
                self.assertIsNone(window.clip_undo)
                self.assertEqual(window.pending_exports, [])
                self.assertFalse(window.save_all_button.isEnabled())
                window.ranges.append({"source": str(b), "start": 3, "end": 4, "title": "B"})
                window.refresh_ranges()
                window.export_ranges(True)
                self.assertEqual([r["source"] for r in window.pending_exports], [str(b)])
                window.source_picker.setCurrentIndex(0)
                self.assertEqual(window.path, a)
                self.assertEqual([r["title"] for r in window.ranges], ["A"])
                self.assertFalse(window.ranges[0]["selected"])
                window.add_paths([c])
                saved = window.session_data()
                self.assertEqual(len(saved["ranges"]), 2)
                self.assertEqual(saved["active_source"], str(c))
                write_json(directory / "scoped.json", saved)
                window.load_session(directory / "scoped.json")
                self.assertEqual(window.path, c)
                self.assertEqual(window.ranges, [])
                window.add_paths([a])
                window.ranges.clear()
                window.add_paths([b])
                self.assertEqual(window.ranges[0]["title"], "B")
                window.add_paths([a])
                self.assertEqual(window.ranges, [])
                self.assertEqual(len(window.session_data()["ranges"]), 1)
                window.analysis_finished({"clips": [{"source": str(b), "start": 5, "end": 6}], "warnings": []})
                self.assertEqual(window.ranges, [])
                window.add_paths([b])
                self.assertEqual(len(window.ranges), 2)
                write_json(directory / "empty.json", {"version": 1, "ranges": []})
                window.load_session(directory / "empty.json")
                self.assertIsNone(window.path)
                self.assertEqual(window.ranges, [])
                self.assertEqual(source_key(str(b).upper()), source_key(b))
            window.close()

    def test_legacy_mixed_project_is_partitioned_without_losing_clips(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            a, b = directory / "a.mp4", directory / "b.mp4"
            rows = [{"source": str(a), "start": 0, "end": 1}, {"source": str(b), "start": 1, "end": 2}]
            write_json(directory / "legacy.json", {"version": 1, "ranges": rows})
            window = MainWindow(directory)
            with patch.object(window.preview, "load"), patch.object(window, "play_range"):
                window.load_session(directory / "legacy.json")
                self.assertEqual([{k: v for k, v in r.items() if k != "clip_id"} for r in window.ranges], rows[:1])
                window.export_ranges(True)
                self.assertEqual([{k: v for k, v in r.items() if k != "clip_id"} for r in window.pending_exports], rows[:1])
                window.add_paths([b])
                self.assertEqual([{k: v for k, v in r.items() if k != "clip_id"} for r in window.ranges], rows[1:])
                self.assertEqual(len(window.session_data()["ranges"]), 2)
                window.link_ready({"title": "remote", "duration": 3})
                self.assertEqual(window.ranges, [])
                window.add_paths([a])
                self.assertEqual([{k: v for k, v in r.items() if k != "clip_id"} for r in window.ranges], rows[:1])
            window.close()


    def test_visible_checks_save_settings_and_folder_open(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QComboBox, QCheckBox
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            source = str(Path(name) / "a.mp4")
            window.ranges = [{"source": source, "start": 0, "end": 1, "player": "A", "selected": False},
                             {"source": source, "start": 1, "end": 2, "player": "B", "selected": False}]
            window.refresh_ranges()
            window.player_filter.setText("A")
            window.check_clips(True)
            self.assertEqual([r["selected"] for r in window.ranges], [True, False])
            window.player_filter.setText("B")
            window.check_clips(False)
            self.assertEqual([r["selected"] for r in window.ranges], [True, False])
            def settings():
                dialog = QApplication.activeModalWidget()
                dialog.findChild(QComboBox, "save_mode").setCurrentIndex(1)
                self.assertFalse(dialog.findChild(QComboBox, "save_quality").isEnabled())
                dialog.findChild(QComboBox, "save_mode").setCurrentIndex(0)
                dialog.findChild(QComboBox, "save_quality").setCurrentIndex(2)
                dialog.findChild(QComboBox, "save_speed").setCurrentIndex(2)
                dialog.findChild(QCheckBox, "save_audio").setChecked(False)
                dialog.accept()
            QTimer.singleShot(0, settings)
            window.show_export_settings()
            self.assertEqual(window.encoder_crf.value(), 28)
            self.assertEqual(window.encoder_preset.currentText(), "slow")
            self.assertFalse(window.export_audio)
            def reject():
                dialog = QApplication.activeModalWidget()
                dialog.findChild(QCheckBox, "save_audio").setChecked(True)
                dialog.reject()
            QTimer.singleShot(0, reject)
            window.show_export_settings()
            self.assertFalse(window.export_audio)
            with patch("app.QDesktopServices.openUrl", return_value=True) as open_url:
                window.output_directory.setText(name)
                window.open_output_folder()
                self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(Path(name)).replace("\\", "/"))
            with patch.object(window, "run_task") as run:
                window.export_ranges(False)
                run.assert_not_called()
                self.assertEqual(len(window.pending_exports), 1)
                window.execute_export()
                operation = run.call_args.args[0]
                with patch("app.export_clips") as export:
                    operation(None, None)
                    self.assertEqual(export.call_args.kwargs, {"mode": "accurate", "crf": 28, "preset": "slow", "audio": False})
            window.close()


    def test_playback_bar_click_seeks_in_profile_and_review(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / "video.mp4"
            window.ranges = [{"source": str(window.path), "start": 0, "end": 10}]
            window.refresh_ranges()
            window.show()
            with patch.object(window.preview.player, "duration", return_value=100000), patch.object(window.preview.player, "setPosition") as seek:
                window.preview._duration_changed(100000)
                for stage in (2, 3):
                    window.set_stage(stage)
                    self.application.processEvents()
                    window.range_end = 10
                    window.loop_clip.setChecked(True)
                    window.preview._position_changed(1000)
                    slider = window.preview.slider
                    QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                                     pos=QPoint(round(slider.width() * .7), slider.height() // 2))
                    self.assertIsNone(window.range_end)
                    self.assertGreater(seek.call_args.args[0], 65000)
                    self.assertLess(seek.call_args.args[0], 75000)
                    seek.reset_mock()
                    window.preview._position_changed(72000)
                    seek.assert_not_called()
            window.close()


    def test_timeline_drag_keeps_zoom_bounds(self):
        from timeline import RangeTimeline
        timeline = RangeTimeline()
        timeline.resize(500, 48)
        timeline.set_values(0, 100, 20, 80, 0)
        changes = []
        timeline.changed.connect(lambda start, end: changes.append((start, end)))
        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(round(timeline.x(20)), 18))
        QTest.mouseMove(timeline, QPoint(round(timeline.x(40)), 18))
        timeline.set_values(10, 90, *timeline.selection, 0)
        self.assertEqual(timeline.bounds, (0, 100))
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(changes[-1][0], 40, delta=.2)
        self.assertEqual(changes[-1][1], 80)
        timeline.close()

    def test_shortcuts_respect_text_fields_and_timeline_view(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.show()
            window.zoom_timeline.setFocus()
            with patch.object(window, "step_frame") as step:
                QTest.keyClick(window.zoom_timeline, Qt.Key.Key_Right)
                step.assert_called_once_with(1)
                window.player_filter.setFocus()
                QTest.keyClick(window.player_filter, Qt.Key.Key_I)
                self.assertEqual(window.player_filter.text(), "i")
                step.assert_called_once()
            from PIL import Image
            window.path = Path(name) / "video.mp4"
            with patch("app.adjacent_frame", return_value={"seconds": .5, "image": Image.new("RGB", (80, 40))}):
                window.step_frame(1)
                for _ in range(100):
                    QTest.qWait(10)
                    if window.frame_task is None:
                        break
                self.assertIsNone(window.frame_task)
                self.assertEqual(window.preview.frame_time, .5)
            timeline = window.zoom_timeline
            timeline.extent = 100
            timeline.set_values(0, 100, 20, 80, 50)
            timeline.zoom(2, 50)
            self.assertEqual(timeline.bounds, (25, 75))
            timeline.set_values(0, 100, 20, 80, 55)
            self.assertEqual(timeline.bounds, (25, 75))
            timeline.fit()
            self.assertEqual(timeline.bounds, (0, 100))
            window.close()

    def test_mouse_wheel_scrub_and_move_range(self):
        from timeline import RangeTimeline
        timeline = RangeTimeline()
        timeline.resize(500, 68)
        timeline.set_values(0, 100, 20, 80, 50)
        wheel = QWheelEvent(QPointF(250, 18), QPointF(250, 18), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(timeline, wheel)
        self.assertAlmostEqual(timeline.bounds[1] - timeline.bounds[0], 80)
        timeline.fit()
        commits, seeks = [], []
        timeline.committed.connect(lambda: commits.append(True))
        timeline.seek.connect(seeks.append)
        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                         QPoint(round(timeline.x(40)), 18))
        QTest.mouseMove(timeline, QPoint(round(timeline.x(50)), 18))
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(timeline.selection[0], 30, delta=.3)
        self.assertAlmostEqual(timeline.selection[1] - timeline.selection[0], 60)
        self.assertEqual(len(commits), 1)
        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(round(timeline.x(5)), 18))
        QTest.mouseMove(timeline, QPoint(round(timeline.x(10)), 18))
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton)
        self.assertGreaterEqual(len(seeks), 2)
        timeline.close()

    def test_zoom_scroll_and_step_progress(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            timeline = window.zoom_timeline
            timeline.extent = 100
            timeline.view(20, 40)
            self.assertEqual(window.full_timeline.viewport, (20, 40))
            self.assertEqual(window.zoom_scroll.pageStep(), 20000)
            self.assertEqual(window.zoom_scroll.maximum(), 80000)
            window.zoom_scroll.setValue(60000)
            self.assertEqual(timeline.bounds, (60, 80))
            self.assertEqual(window.full_timeline.viewport, (60, 80))
            window.analysis_update({"step": 3, "done": 25, "total": 100, "message": "CPU 작업자 32개"})
            self.assertEqual(window.analysis_progress.value(), 25)
            self.assertIn("25 / 100", window.analysis_detail.text())
            self.assertIn("STEP 3/5", window.analysis_progress.format())
            window.analysis_update({"step": 3, "done": 25, "total": 100, "message": "판독",
                "metrics": {"decoder_threads": 32, "decode_s": 1.2, "convert_s": .3,
                            "match_s": .04, "decoded_frames": 1500, "sample_frames": 25}})
            self.assertIn("디코더 32스레드", window.analysis_metrics.text())
            self.assertIn("디코딩 1.20s", window.analysis_metrics.text())
            window.close()

    def test_analysis_timeline_horizontal_scroll_preserves_selection(self):
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.path = Path(name) / 'video.mp4'
            window.set_stage(1)
            self.assertFalse(window.full_scroll.isHidden())
            timeline = window.full_timeline
            timeline.extent = 100
            timeline.set_values(0, 100, 10, 90, 30)
            self.assertEqual(window.full_scroll.maximum(), 0)
            timeline.view(20, 40)
            self.assertEqual(window.full_scroll.pageStep(), 20000)
            self.assertEqual(window.full_scroll.value(), 20000)
            self.assertEqual(window.full_scroll.maximum(), 80000)
            window.full_scroll.setValue(60000)
            self.assertEqual(timeline.bounds, (60, 80))
            self.assertEqual(timeline.selection, (10, 90))
            self.assertEqual(timeline.position, 30)
            timeline.fit()
            self.assertEqual(window.full_scroll.maximum(), 0)
            window.set_stage(3)
            self.assertFalse(window.full_scroll.isHidden())
            timeline.extent = 100
            timeline.view(20, 40)
            selection = timeline.selection
            window.full_scroll.setValue(60000)
            self.assertEqual(timeline.bounds, (60, 80))
            self.assertEqual(timeline.selection, selection)
            window.close()

    def test_offset_roundtrip_overlay_and_profile_file(self):
        from PIL import Image
        from PIL.ImageQt import ImageQt
        with tempfile.TemporaryDirectory() as name:
            window = MainWindow(Path(name))
            window.preview.show_frame(ImageQt(Image.new("RGB", (100,100))), 0)
            profile = {"version": 1, "name": "offset", "size": [100,100],
                       "regions": {"jacket": [.1,.1,.9,.9]},
                       "jacket_offset": {"version": 1, "roi": [.1,.1,.9,.9], "rect": [.25,.25,.75,.75], "distance": 2, "margin": 8}}
            path = Path(name) / "arcade.json"
            window.profile.profile_path = path
            window.profile.accept_calibration(profile)
            self.assertEqual(load_profile(path), profile)
            box = window.preview.canvas.offset_box
            for actual, expected in zip(box, [.3,.3,.7,.7]):
                self.assertAlmostEqual(actual, expected)
            self.assertEqual(window.profile.current_profile()["jacket_offset"], profile["jacket_offset"])
            window.preview.set_regions({"jacket": [0,0,1,1]})
            window.profile.update_crop()
            self.assertIsNone(window.preview.canvas.offset_box)
            window.close()

    def test_profile_atomic_save_and_validation(self):
        profile = {"version": 1, "name": "매장", "size": [1920, 1080],
                   "regions": {"jacket": [.1, .2, .3, .4]}}
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "profile.json"
            write_json(path, profile)
            self.assertEqual(load_profile(path), profile)
            with self.assertRaises(ValueError):
                write_json(path, {"bad": float("nan")})
            self.assertEqual(load_profile(path), profile)
            self.assertEqual(list(path.parent.iterdir()), [path])
        for box in ([.2, .2, .1, .3], [0, 0, float("nan"), 1], [0, 0, .0001, 1]):
            with self.assertRaises(ValueError):
                validate_profile({**profile, "regions": {"jacket": box}})

    def test_setup_checks_writing_before_catalog(self):
        with tempfile.TemporaryDirectory() as name:
            dialog = SetupDialog()
            dialog.directory = Path(name)
            with patch("setup_dialog.TemporaryFile", side_effect=PermissionError), patch("setup_dialog.catalog_status") as catalog:
                with self.assertRaises(PermissionError):
                    dialog._prepare(None, lambda _: None)
                catalog.assert_not_called()
            dialog.close()


if __name__ == "__main__":
    unittest.main()
