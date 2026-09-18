"""Arcade Clip desktop entry point."""
from i18n import tr, trn, install_qt_translation
import sys
import os

import json
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
import logging
import unicodedata
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QTimer, QSettings, QEvent, QUrl, QProcess
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QFileDialog, QLineEdit, QListWidgetItem, QMainWindow, QPlainTextEdit, QMenu, QMessageBox, QPushButton, QWidget

from clips import label as clip_label, validate_clip, achievement_text, offset_bounds, merge_rows, apply_offsets
from export import export_clips
from jackets import download_jackets, catalog_summary
from media import format_time, parse_time, adjacent_frame
from preview import Preview
from profiles import validate_profile, write_json, load_profile, require_regions
from recognition import analyze, test_frame
from setup_dialog import SetupDialog
from sources import download_section, find_tool, inspect_source, source_key, youtube_url
from tasks import Task

VIDEO_FILTER = tr('영상 (*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.ts);;모든 파일 (*)')


VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}


def list_video_files(folder, cancel, report):
    report(tr('폴더에서 영상을 찾고 있습니다…'))
    rows = []
    for path in Path(folder).iterdir():
        if cancel.is_set():
            return []
        if path.suffix.lower() in VIDEO_SUFFIXES and path.is_file():
            rows.append(path)
    return sorted(rows)


class MainWindow(QMainWindow):
    def __init__(self, directory: Path):
        super().__init__()
        install_qt_translation(QApplication.instance())
        self.directory = directory
        self.path = None
        self.remote = None
        self.source_info = {}
        self.task = None
        self.pending_close = False
        self.range_end = None
        self.ranges = []
        self.source_ranges = {}
        self.detected_ranges = {}
        self.active_source = None
        self.loading_session = False
        self.last_exports = None
        self.pending_range = None
        self.review_selection = (0, 0)
        self.setWindowTitle("ArcadeClip")
        self.resize(1280, 780)
        self.setAcceptDrops(True)
        self.preview = Preview()
        self.preview.player.durationChanged.connect(self.media_loaded)
        self.preview.player.positionChanged.connect(self.position_changed)
        self.preview.player.errorOccurred.connect(self.media_error)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(300)
        self.advanced = None
        self.busy_controls = []
        self._build_window()
        self.volume_save_timer = QTimer(self)
        self.volume_save_timer.setSingleShot(True)
        self.volume_save_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.volume_save_timer.setInterval(2000)
        self.volume_save_timer.timeout.connect(self.save_recovery)
        self.preview.volume_slider.valueChanged.connect(lambda _: self.volume_save_timer.start())
        self.frame_task = None
        self.frame_steps = 0
        self.seek_revision = 0
        self.reverse_rate = 0
        self.reverse_timer = QTimer(self)
        self.reverse_timer.setInterval(100)
        self.reverse_timer.timeout.connect(self.reverse_tick)
        self.preview.seekStarted.connect(self.begin_scrub)
        self.preview.play_button.clicked.connect(self.stop_reverse)
        QApplication.instance().installEventFilter(self)
        self.catalog_task = None
        self.refresh_catalog_summary()
        if (self.directory / "recovery.json").is_file():
            QTimer.singleShot(0, lambda: self.load_session(self.directory / "recovery.json"))

    def button(self, text, callback, layout, busy=True):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        if busy:
            self.busy_controls.append(button)
        return button

    def _build_window(self):
        from workspace_ui import build_window
        self.stage = 0
        self.pending_exports = []
        self.export_all = True
        self.clip_undo = None
        build_window(self)

    def set_stage(self, index):
        if index == 1 and self.remote:
            index = 5
        if ((index == 1 and not self.path) or (index == 2 and not self.path)
                or (index in (3, 4) and not self.ranges and not self.path) or (index == 5 and not self.remote)):
            self.status.setText(tr('먼저 영상을 열고 작업할 구간을 준비해 주세요.'))
            self.stage_group.button(self.stage).setChecked(True)
            return
        if index == 3 and self.stage != 3:
            self.preview.canvas.reset_view()
            if self.review_selection[0] >= self.review_selection[1]:
                try:
                    self.review_selection = (parse_time(self.start_time.text()), parse_time(self.end_time.text()))
                except ValueError:
                    self.review_selection = (0, self.preview.player.duration() / 1000)
        self.stage = index
        self.source_next.setEnabled(bool(self.path or self.remote) and not self.task)
        self.source_next.setText(tr('YouTube 다운로드로 →') if self.remote else tr('시간 선택으로 →'))
        for number, title in enumerate((tr('영상 선택'), tr('시간 선택'), tr('인식 영역'), tr('클립 확인'), tr('파일 저장'))):
            self.stage_group.button(number).setText(f"[{number + 1}] {title}" if number == index else f"{number + 1} {title}")
        self.source_actions.setVisible(index == 0)
        self.youtube_tab.setVisible(bool(self.remote))
        self.download.setVisible(bool(self.remote))
        for control in self.local_time_controls:
            control.setVisible(bool(self.path))
        self.timeline_panel.setVisible(index in (1, 3))
        self.zoom_panel.setVisible(index == 3)
        self.full_scroll.setVisible(index in (1, 3))
        self.full_timeline_label.setText(tr('전체 시간축 · 분석 구간') if index == 1 else tr('전체 시간축 · 점선 테두리: 확대해서 보는 범위'))
        self.time_controls.setVisible(index == 1)
        self.export_footer.setVisible(index == 3)
        self.list_stack.setVisible(index in (0, 3))
        self.preview_panel.setVisible(index != 5 and (bool(self.path) or index == 1))
        self.preview.setVisible(bool(self.path))
        self.detail_stack.setVisible(index != 1)
        self.current_clip_label.setVisible(index == 3)
        self.preview.slider.setVisible(index not in (1, 3))
        self.remove_clip_button.setVisible(index == 3)
        self.preview.canvas.show_regions = index == 2
        self.preview.canvas.controls_hint.setVisible(index == 2)
        self.preview.canvas.update()
        self.stage_group.button(index).setChecked(True)
        self.list_stack.setCurrentIndex(1 if index >= 3 else 0)
        self.detail_stack.setCurrentIndex(index)
        self.workspace_splitter.setSizes({0: [550, 650, 320], 1: [0, 1000, 0],
                                         2: [0, 850, 330], 3: [300, 700, 300],
                                         4: [0, 650, 520], 5: [0, 0, 1000]}[index])
        self.source_context.setText(str(self.path) if self.path else
                                    (self.remote or {}).get("title", tr('영상 선택 → 구간 지정 → 곡 확인 → 파일 저장')))
        self.stage_hint.setText((
            tr('편집할 영상을 선택하세요.'),
            tr('곡을 찾을 분석 구간의 시작과 끝을 지정하세요.'),
            tr('프로필을 선택하거나, 지정할 영역을 고르고 영상에서 드래그하세요.'),
            tr('클립을 선택해 시작과 끝을 확인하세요. 저장할 클립은 체크하세요.'),
            tr('저장할 파일 이름과 폴더, 설정을 확인하세요.'),
            tr('YouTube · 다운로드할 구간을 선택하세요. 완료하면 영상 선택으로 돌아갑니다.'),
        )[index])
        self.preview.set_region_kind(self.profile.kind.currentData() if index == 2 else "")
        self.analysis_range_summary.setText(tr('분석 구간: {start} – {end}', start=self.start_time.text(), end=self.end_time.text()))
        self.update_timelines()
        if index == 4 and not self.pending_exports:
            self.export_all = False
            self.prepare_export([row for row in self.ranges if row.get("selected", False)])

    def update_timelines(self, *_):
        try:
            start, end = self.review_selection if self.stage == 3 else (parse_time(self.start_time.text()), parse_time(self.end_time.text()))
        except ValueError:
            return
        duration = self.preview.player.duration() / 1000 if self.path else (self.remote or {}).get("duration", 0)
        position = self.preview.player.position() / 1000
        for timeline in (self.full_timeline, self.zoom_timeline):
            timeline.extent = duration or max(1, end)
            timeline.clips = [(i, row["start"], row["end"], row.get("title", "")) for i, row in enumerate(self.ranges)
                              if self.stage != 1 and self.path and Path(row["source"]) == self.path and self.song_visible(row)
                              and (i >= self.range_list.count() or not self.range_list.item(i).isHidden())]
            timeline.custom_ids = {i for i, row in enumerate(self.ranges) if row.get("custom")}
            timeline.active_clip = self.range_list.currentRow() if self.stage == 3 else -1
        self.full_timeline.set_values(0, duration or max(1, end), start, end, position)
        padding = max(2, (end - start) * .2)
        self.zoom_timeline.set_values(max(0, start - padding), min(duration or end + padding, end + padding),
                                      start, end, position)

    def sync_zoom_view(self, low, high):
        self.full_timeline.viewport = None if self.stage == 1 else (low, high)
        self.full_timeline.update()
        self.sync_timeline_scroll(self.zoom_scroll, self.zoom_timeline.extent, low, high)

    @staticmethod
    def sync_timeline_scroll(scroll, extent, low, high):
        scroll.blockSignals(True)
        scroll.setRange(0, max(0, round((extent - (high - low)) * 1000)))
        scroll.setPageStep(max(1, round((high - low) * 1000)))
        scroll.setSingleStep(max(1, round((high - low) * 100)))
        scroll.setValue(round(low * 1000))
        scroll.blockSignals(False)

    def scroll_full(self, milliseconds):
        low, high = self.full_timeline.bounds
        self.full_timeline.view(milliseconds / 1000, milliseconds / 1000 + high - low)

    def scroll_zoom(self, milliseconds):
        low, high = self.zoom_timeline.bounds
        self.zoom_timeline.view(milliseconds / 1000, milliseconds / 1000 + high - low)

    def analysis_update(self, value):
        if value.get("kind") == "download":
            percent = value.get("percent")
            self.download_progress.setRange(0, 100 if percent is not None else 0)
            if percent is not None:
                self.download_progress.setValue(percent)
            self.download_detail.setText(value["message"])
            if 'mode' in value:
                self.download_mode.setText(
                    tr('일반 다운로드 · 이 영상은 조각 병렬 다운로드를 지원하지 않습니다.')
                    if value['mode'] == 'standard' else tr('조각 병렬 다운로드'))
            eta = value.get('eta_seconds')
            if value.get('step') == 4:
                self.download_eta.clear()
            elif eta is None:
                self.download_eta.setText(tr('예상 남은 시간 계산 중…'))
            else:
                self.download_eta.setText(tr('현재 단계 예상 남은 시간: 약 {time}', time=format_time(eta).split('.')[0]))
            return
        self.analysis_panel.show()
        metrics = value.get("metrics", {})
        if "decoder_threads" in metrics:
            self.analysis_metrics.setText(
                tr(
                    '{value1} · 디코더 {decoder_threads}스레드 · 디코딩 {decode_s:.2f}s · 이미지 변환 {convert_s:.2f}s · 판독 누적 {match_s:.2f}s · 디코딩 {decoded_frames:,}장 / 샘플 {sample_frames:,}장',
                    value1=metrics.get('decoder', 'CPU'),
                    decoder_threads=metrics['decoder_threads'],
                    decode_s=metrics['decode_s'],
                    convert_s=metrics['convert_s'],
                    match_s=metrics['match_s'],
                    decoded_frames=metrics['decoded_frames'],
                    sample_frames=metrics['sample_frames'],
                ))
        step = value["step"]
        done, total = value["done"], value["total"]
        for index, label in enumerate(self.analysis_steps, 1):
            label.setStyleSheet("font-weight:600;color:#176c92" if index == step else "color:#666")
        self.analysis_detail.setText(value["message"] + (f" · {done:,} / {total:,}" if total else ""))
        self.analysis_progress.setRange(0, 100 if total else 0)
        if total:
            self.analysis_progress.setValue(min(100, round(100 * done / total)))
        self.analysis_progress.setFormat(f"STEP {step}/5 · %p%")

    def timeline_changed(self, start, end):
        if self.stage == 3:
            self.review_selection = (start, end)
        else:
            self.start_time.setText(format_time(start))
            self.end_time.setText(format_time(end))
        if self.stage == 3 and self.range_list.currentRow() >= 0:
            self.clip_fields["start"].setText(format_time(start))
            self.clip_fields["end"].setText(format_time(end))

    def select_whole(self):
        duration = self.preview.player.duration() / 1000 if self.path else (self.remote or {}).get("duration", 0)
        self.start_time.setText(format_time(0))
        self.end_time.setText(format_time(duration or 0))

    def select_timeline_clip(self, index):
        if self.range_list.currentRow() == index:
            self.activate_clip(index)
        else:
            self.range_list.setCurrentRow(index)

    def activate_clip(self, index):
        self.zoom_timeline.manual_view = not self.follow_clip.isChecked()
        self.show_clip_details(index)
        if index >= 0:
            self.select_range(self.range_list.item(index))
        self.update_timelines()

    def show_clip_details(self, index):
        if index < 0 or index >= len(self.ranges):
            self.current_clip_label.setText(tr('목록에서 확인할 클립을 선택하세요.'))
            for field in self.clip_fields.values():
                field.clear()
            self.clip_reviewed.setChecked(False)
            return
        row = self.ranges[index]
        self.current_clip_label.setText(tr('보고 있는 클립 {value1} · {value2}', value1=index + 1, value2=row.get('title') or tr('곡 미확인')))
        for key, field in self.clip_fields.items():
            value = format_time(row[key]) if key in ("start", "end") else str(row[key]) if row.get(key) is not None else ""
            if key.startswith("achievement") and row.get(key) is not None:
                value = achievement_text(row[key])
            field.setText(value)
        self.clip_reviewed.setChecked(row.get("reviewed", False))

    def apply_clip_details(self):
        index = self.range_list.currentRow()
        if index < 0:
            return
        try:
            row = deepcopy(self.ranges[index])
            for key, field in self.clip_fields.items():
                value = field.text().strip()
                if key in ("start", "end"):
                    row[key] = parse_time(value)
                elif key.startswith("achievement"):
                    row.pop(key, None)
                    if value:
                        row[key] = float(value.rstrip("%"))
                else:
                    row[key] = value
            if any(row[k] != self.ranges[index][k] for k in ("start", "end")):
                row["manual_bounds"] = True
            row.update(edited=True, reviewed=self.clip_reviewed.isChecked())
            validate_clip(row)
            if self.path == Path(row["source"]) and self.preview.player.duration() > 0 and row["end"] > self.preview.player.duration() / 1000 + .001:
                raise ValueError(tr('영상 길이 안에서 구간을 지정해 주세요.'))
            self.clip_undo = deepcopy(self.ranges)
            self.ranges[index] = row
            self.refresh_ranges()
            self.range_list.setCurrentRow(index)
            self.review_selection = (row["start"], row["end"])
            self.update_timelines()
            self.save_recovery()
        except ValueError as error:
            self.fail(error)

    def undo_clip_edit(self):
        if self.clip_undo is not None:
            self.ranges, self.clip_undo = self.clip_undo, None
            self.refresh_ranges()
            if self.ranges:
                self.range_list.setCurrentRow(0)
                self.apply_range(self.ranges[0])
            self.save_recovery()

    def check_edge(self, ending):
        index = self.range_list.currentRow()
        if index < 0:
            return
        row = self.ranges[index]
        sample = dict(row)
        if ending:
            sample["start"] = max(row["start"], row["end"] - 3)
        else:
            sample["end"] = min(row["end"], row["start"] + 3)
        self.stop_reverse()
        self.preview.seek(sample["start"])
        self.range_end = sample["end"]
        self.preview.player.play()

    def clip_checked(self, item):
        index = self.range_list.row(item)
        if 0 <= index < len(self.ranges):
            self.ranges[index]["selected"] = item.checkState() == Qt.CheckState.Checked
        self.update_selection_summary()
        if self.stage == 4:
            self.prepare_export(self.visible_ranges() if self.export_all else [row for row in self.ranges if row.get("selected", False)])
        self.save_recovery()

    def check_clips(self, checked):
        for index, row in enumerate(self.ranges):
            if not self.range_list.item(index).isHidden():
                row["selected"] = checked
        self.refresh_ranges()
        self.save_recovery()

    def update_selection_summary(self):
        checked = [row for row in self.ranges if row.get("selected", False)]
        seconds = sum(row["end"] - row["start"] for row in checked)
        hidden = sum(self.range_list.item(i).isHidden() for i in range(self.range_list.count()))
        hidden_checked = sum(self.range_list.item(i).isHidden() and row.get("selected", False)
                             for i, row in enumerate(self.ranges))
        self.selection_summary.setText(
            tr(
                '보이는 클립 {value1}개 / 전체 {value2}개 · 숨김 {hidden}개\n저장할 클립 {value4}개 · 총 {value5}',
                value1=len(self.ranges) - hidden,
                value2=len(self.ranges),
                hidden=hidden,
                value4=len(checked),
                value5=format_time(seconds),
            )
            + (tr(' · 숨긴 항목 {hidden_checked}개 포함', hidden_checked=hidden_checked) if hidden_checked else ""))
        self.save_all_button.setText(trn('보이는 클립 {count}개 저장 설정 →', '보이는 클립 {count}개 저장 설정 →', len(self.ranges) - hidden))
        self.save_checked_button.setText(trn('체크한 클립 {count}개 저장 설정 →', '체크한 클립 {count}개 저장 설정 →', len(checked)))
        self.save_all_button.setEnabled(len(self.ranges) > hidden and not self.task)
        self.save_checked_button.setEnabled(bool(checked) and not self.task)
        self.empty_clips.setText(tr('검색 조건에 맞는 클립이 없습니다. 검색어와 필터를 확인하세요.') if self.ranges
                                 else tr('클립이 없습니다. 인식 영역에서 곡을 찾거나 클립을 직접 추가하세요.'))
        self.empty_clips.setVisible(len(self.ranges) == hidden)
        if len(self.ranges) == hidden:
            self.current_clip_label.setText(tr('미리보기'))

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, tr('클립 저장 폴더'), self.output_directory.text())
        if path:
            self.output_directory.setText(path)

    def open_output_folder(self):
        path = self.output_directory.text().strip()
        if not path:
            self.status.setText(tr('먼저 대상 폴더를 선택해 주세요.'))
            return
        url = QUrl.fromLocalFile(os.path.abspath(os.path.expanduser(path)))
        if not QDesktopServices.openUrl(url):
            self.status.setText(tr('폴더를 열 수 없습니다. 대상 경로를 확인해 주세요.'))

    def prepare_export(self, rows):
        from export import _filename
        self.pending_exports = deepcopy(rows)
        self.export_summary.setText(tr(
            '클립 {value1}개 · 총 {value2}',
            value1=len(rows),
            value2=format_time(sum(row['end'] - row['start'] for row in rows)),
        ))
        hidden_count = sum(self.range_list.item(i).isHidden() and row in rows
                           for i, row in enumerate(self.ranges))
        if hidden_count:
            self.export_summary.setText(self.export_summary.text() + tr('\n필터로 숨긴 클립 {count}개 포함', count=hidden_count))
        self.output_names.clear()
        extension = self.export_options.get("format", "mp4") if self.export_mode.currentData() == "accurate" else "mp4"
        self.output_names.addItems([_filename(row) + "." + extension for row in rows])
        self.execute_export_button.setText(trn('파일 {count}개 저장 시작', '파일 {count}개 저장 시작', len(rows)))
        self.execute_export_button.setEnabled(bool(rows) and not self.task)

    def execute_export(self):
        if not self.pending_exports or self.task:
            return
        if not self.output_directory.text().strip():
            self.status.setText(tr('저장할 폴더를 지정해 주세요.'))
            return
        rows, directory = deepcopy(self.pending_exports), Path(self.output_directory.text()).expanduser()
        if self.export_date_folder.isChecked():
            directory /= datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        settings = {"mode": self.export_mode.currentData(), "crf": self.encoder_crf.value(),
                    "preset": self.encoder_preset.currentText(), "audio": self.export_audio}
        if settings["mode"] == "accurate":
            settings.update(self.export_options)
        self.preview.player.pause()
        self.run_task(lambda cancel, report: export_clips(rows, directory, cancel, report, **settings), self.export_finished)

    def add_files(self):
        names, _ = QFileDialog.getOpenFileNames(self, tr('영상 추가'), "", VIDEO_FILTER)
        self.add_paths(names)

    def add_folder(self):
        name = QFileDialog.getExistingDirectory(self, tr('영상 폴더'))
        if name:
            self.run_task(lambda cancel, report: list_video_files(name, cancel, report), self.add_paths)

    def add_paths(self, paths):
        existing = {source_key(self.files.item(i).data(Qt.ItemDataRole.UserRole)): self.files.item(i)
                    for i in range(self.files.count())}
        selected = None
        for path in paths:
            path = Path(os.path.abspath(os.path.expanduser(path)))
            selected = existing.get(source_key(path))
            if selected is None:
                selected = QListWidgetItem(path.name)
                info = self.source_info.get(source_key(path))
                if info:
                    selected.setText(f"YouTube · {info.get('kind', tr('일반 동영상'))} · {info.get('title', path.name)}\n"
                                     f"{info.get('date', tr('날짜 미제공'))} · {info.get('channel', '')}\n{info.get('range', '')}\n{path}")
                    selected.setIcon(QIcon(str(Path(__file__).parent / "assets" / "youtube.svg")))
                selected.setToolTip(str(path))
                selected.setData(Qt.ItemDataRole.UserRole, str(path))
                self.files.addItem(selected)
                self.source_picker.blockSignals(True)
                self.source_picker.addItem(str(path), str(path))
                self.source_picker.setItemIcon(self.source_picker.count() - 1, selected.icon())
                self.source_picker.setItemData(self.source_picker.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole)
                self.source_picker.blockSignals(False)
                existing[source_key(path)] = selected
        if selected is not None:
            self.files.setCurrentItem(selected)

    def remember_source(self):
        groups = {self.active_source: []} if self.active_source else {}
        for row in self.ranges:
            groups.setdefault(source_key(row["source"]), []).append(row)
        self.source_ranges.update(groups)

    def switch_source(self, path):
        self.remember_source()
        self.active_source = source_key(path) if path else None
        self.ranges = self.source_ranges.setdefault(self.active_source, []) if path else []
        self.clip_undo = None
        self.pending_exports = []
        self.last_exports = None
        self.output_names.clear()
        self.export_summary.setText(tr('현재 영상에서 저장할 클립을 선택하세요.'))
        self.loop_clip.setChecked(False)
        for field in self.clip_fields.values():
            field.clear()
        self.clip_reviewed.setChecked(False)

    def select_file(self, item, previous=None):
        if item is None:
            return
        self.pause_transport()
        for timeline in (self.full_timeline, self.zoom_timeline):
            timeline.manual_view = False
        self.switch_source(item.data(Qt.ItemDataRole.UserRole))
        self.path = Path(item.data(Qt.ItemDataRole.UserRole))
        self.remote = None
        self.range_end = None
        self.pending_range = None
        self.review_selection = (0, 0)
        regions = self.preview.regions
        self.start_time.setText(format_time(0))
        self.end_time.setText(format_time(0))
        self.preview.load(self.path)
        self.profile.restore_after_load(regions)
        self.refresh_ranges()
        self.source_picker.blockSignals(True)
        self.source_picker.setCurrentIndex(self.files.currentRow())
        self.source_picker.blockSignals(False)
        self.source_picker.setToolTip(str(self.path))
        self.status.setText(tr('{name} · 이 영상의 클립 {value2}개', name=self.path.name, value2=len(self.ranges)))
        self.set_stage(1 if self.stage in (0, 5) or (self.stage >= 3 and not self.ranges) else self.stage)
        if not self.loading_session:
            self.save_recovery()

    def media_loaded(self, milliseconds):
        if self.path and milliseconds > 0:
            if self.end_time.text() == format_time(0):
                self.end_time.setText(format_time(milliseconds / 1000))
            if self.pending_range:
                row, self.pending_range = self.pending_range, None
                self.apply_range(row)

    def media_error(self, error, message):
        self.log.appendPlainText(message)
        self.status.setText(tr('영상을 열 수 없습니다. 파일을 다시 선택해 주세요.'))

    def mark(self, field):
        if self.path:
            seconds = self.preview.frame_time if self.preview._exact_frame else self.preview.player.position() / 1000
            if self.stage == 3:
                start, end = self.review_selection
                self.review_selection = (seconds, end) if field is self.start_time else (start, seconds)
                self.clip_fields["start" if field is self.start_time else "end"].setText(format_time(seconds))
                self.commit_timeline()
                self.update_timelines()
            else:
                field.setText(format_time(seconds))

    def selected_times(self, analysis=False):
        start, end = self.review_selection if self.stage == 3 and not analysis else (parse_time(self.start_time.text()), parse_time(self.end_time.text()))
        if start >= end:
            raise ValueError(tr('끝 시각을 시작보다 뒤로 지정해 주세요.'))
        duration = self.preview.player.duration() / 1000 if self.path else (self.remote or {}).get("duration")
        if not duration:
            raise ValueError(tr('영상 길이 안에서 구간을 선택해 주세요.'))
        if analysis:
            end = min(end, duration)
        if start >= end or end > duration + .001:
            raise ValueError(tr('영상 길이 안에서 구간을 선택해 주세요.'))
        return start, end

    def play_range(self, *, bounds=None):
        self.stop_reverse()
        if not self.path:
            return
        try:
            start, self.range_end = bounds if bounds is not None else self.selected_times()
            self.preview.seek(start)
            self.preview.player.play()
        except ValueError as error:
            self.fail(error)

    def position_changed(self, milliseconds):
        self.update_timelines()
        if self.range_end is not None and milliseconds >= self.range_end * 1000:
            index = self.range_list.currentRow()
            if self.loop_clip.isChecked() and self.stage == 3 and index >= 0:
                self.preview.seek(self.ranges[index]["start"])
            else:
                self.preview.player.pause()
                self.range_end = None

    def add_range(self):
        if not self.path:
            return
        try:
            start, end = self.selected_times()
            self.clip_undo = deepcopy(self.ranges)
            self.ranges.append({"source": str(self.path), "start": start, "end": end,
                                "custom": True, "clip_id": uuid4().hex})
            self.refresh_ranges()
            self.save_recovery()
        except ValueError as error:
            self.fail(error)

    def refresh_ranges(self):
        self.capture_detected_ranges()
        self.pending_exports = []
        current = self.range_list.currentRow()
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.range_list.selectedItems()}
        scroll = self.range_list.verticalScrollBar().value()
        self.range_list.blockSignals(True)
        self.range_list.clear()
        for index, row in enumerate(self.ranges):
            item = QListWidgetItem(f"{index + 1}. {clip_label(row)}")
            item.setData(Qt.ItemDataRole.UserRole, row["clip_id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row.get("selected", False) else Qt.CheckState.Unchecked)
            if row.get("image_url"):
                item.setIcon(QIcon(str(self.directory / "jackets" / row["image_url"])))
            self.range_list.addItem(item)
        self.filter_ranges()
        if 0 <= current < len(self.ranges) and not self.range_list.item(current).isHidden():
            self.range_list.setCurrentRow(current)
        if selected_ids:
            for i in range(self.range_list.count()):
                item = self.range_list.item(i)
                item.setSelected(not item.isHidden() and item.data(Qt.ItemDataRole.UserRole) in selected_ids)
        self.range_list.doItemsLayout()
        self.range_list.verticalScrollBar().setValue(scroll)
        self.range_list.blockSignals(False)
        self.update_timelines()
        self.show_clip_details(self.range_list.currentRow())
        self.update_selection_summary()
        if self.stage == 4:
            self.prepare_export(self.visible_ranges() if self.export_all else [row for row in self.ranges if row.get("selected", False)])

    def visible_ranges(self):
        return [row for i, row in enumerate(self.ranges) if not self.range_list.item(i).isHidden()]

    def song_visible(self, row):
        if "song_id" in row:
            return self.show_unknown_songs.isChecked() or bool(row["song_id"])
        return self.show_unknown_songs.isChecked() or "image_url" not in row or bool(row["image_url"])

    def filter_ranges(self, *_):
        query = unicodedata.normalize("NFKC", self.player_filter.text().strip()).casefold()
        for index, row in enumerate(self.ranges):
            names = [row.get(key, "") for key in ("player", "player2") if row.get(key)]
            visible = (any(query in unicodedata.normalize("NFKC", name).casefold() for name in names) if names else self.show_unknown.isChecked())
            if not self.song_visible(row):
                visible = False
            self.range_list.item(index).setHidden(not visible)
            if not visible:
                self.range_list.item(index).setSelected(False)
                if self.range_list.currentRow() == index:
                    self.range_list.setCurrentRow(-1)
        self.update_timelines()
        self.update_selection_summary()

    def capture_detected_ranges(self):
        known = {r["clip_id"] for rows in self.detected_ranges.values() for r in rows}
        for row in self.ranges:
            if "clip_id" not in row:
                row["clip_id"] = uuid4().hex
            if row.get("custom"):
                continue
            originals = self.detected_ranges.setdefault(source_key(row["source"]), [])
            if row["clip_id"] not in known:
                original = deepcopy(row)
                # ponytail: legacy projects lack raw detections; reanalysis restores a true original.
                offsets = original.pop("boundary_offsets", [0, 0])
                original["start"] -= offsets[0]
                original["end"] -= offsets[1]
                validate_clip(original)
                originals.append(original)
                known.add(row["clip_id"])

    def offset_manual_choice(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr('수동 조정된 클립'))
        dialog.setText(tr('수동으로 시작·끝을 조정한 클립이 있습니다.'))
        dialog.setInformativeText(tr('자동 인식 원본을 기준으로 전역 오프셋을 적용합니다. 직접 추가한 클립은 유지됩니다.'))
        reset = dialog.addButton(tr('수동 조정 초기화'), QMessageBox.ButtonRole.AcceptRole)
        skip = dialog.addButton(tr('수동 조정은 제외하고 재설정'), QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton(tr('취소'), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        return "reset" if dialog.clickedButton() == reset else "skip" if dialog.clickedButton() == skip else "cancel"

    def apply_clip_offsets(self):
        if not self.ranges or not self.path:
            return
        duration = self.preview.player.duration() / 1000
        if duration <= 0:
            self.status.setText(tr('영상 길이를 확인한 후 적용해 주세요.'))
            return
        choice = self.offset_manual_choice() if any(r.get("manual_bounds") and not r.get("custom") for r in self.ranges) else "reset"
        if choice == "cancel":
            return
        self.capture_detected_ranges()
        originals = {r["clip_id"]: r for r in self.detected_ranges.get(source_key(self.path), [])}
        try:
            rows, count = apply_offsets(self.ranges, originals, self.clip_start_offset.value(),
                                        self.clip_end_offset.value(), duration, skip_manual=choice == "skip")
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.clip_undo = deepcopy(self.ranges)
        self.ranges = rows
        self.refresh_ranges()
        self.sync_review_selection()
        self.save_recovery()
        self.status.setText(tr('자동 인식 원본을 기준으로 {count}개 클립에 오프셋을 적용했습니다.', count=count))

    def sync_review_selection(self):
        index = self.range_list.currentRow()
        if 0 <= index < len(self.ranges):
            self.review_selection = (self.ranges[index]["start"], self.ranges[index]["end"])
            self.update_timelines()

    def reset_detected_clips(self):
        if not self.path:
            return
        originals = self.detected_ranges.get(source_key(self.path), [])
        if not originals:
            self.status.setText(tr('저장된 자동 인식 원본이 없습니다.'))
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr('자동 인식 결과로 복원'))
        dialog.setText(tr('현재 영상의 자동 인식 클립을 복원할까요?'))
        dialog.setInformativeText(tr('편집한 내용과 시작·끝 조정을 초기화하고 제거한 클립을 복원합니다. 자동 클립의 저장 체크도 해제합니다. 직접 추가한 클립은 유지합니다.'))
        restore = dialog.addButton(tr('복원'), QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(tr('취소'), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() != restore:
            return
        self.clip_undo = deepcopy(self.ranges)
        self.ranges = [dict(deepcopy(row), selected=False, manual_bounds=False)
                       for row in originals] + [row for row in self.ranges if row.get("custom")]
        self.refresh_ranges()
        self.sync_review_selection()
        self.save_recovery()
        self.status.setText(tr('자동 인식 원본으로 복원했습니다. 직접 추가한 클립은 유지됩니다.'))

    def clip_context_menu(self, index, position):
        if self.task or self.stage != 3:
            return
        menu = QMenu(self)
        if index >= 0 and not self.range_list.item(index).isSelected():
            self.range_list.setCurrentRow(index)
        selected = menu.addAction(tr('선택한 클립 합치기')) if len(self.range_list.selectedItems()) > 1 else None
        below = next((i for i in range(index + 1, len(self.ranges))
                      if not self.range_list.item(i).isHidden()), None) if index >= 0 else None
        merge = menu.addAction(tr('아래 클립과 합치기')) if below is not None else None
        remove = menu.addAction(tr('이 클립을 목록에서 제거')) if index >= 0 else None
        add = menu.addAction(tr('선택 구간으로 클립 추가'))
        action = menu.exec(position)
        if selected is not None and action == selected:
            self.merge_clips()
        elif merge is not None and action == merge:
            self.merge_clips([index, below])
        elif remove is not None and action == remove:
            self.range_list.clearSelection()
            self.range_list.setCurrentRow(index)
            self.remove_range()
        elif action == add:
            self.add_range()

    def merge_clips(self, indices=None):
        if self.task:
            return
        indices = sorted(set(indices if indices is not None else
                             [self.range_list.row(item) for item in self.range_list.selectedItems() if not item.isHidden()]))
        if len(indices) < 2:
            self.status.setText(tr('Shift 또는 Ctrl을 누르고 합칠 클립을 두 개 이상 선택해 주세요.'))
            return
        rows = [self.ranges[i] for i in indices]
        if len({source_key(row["source"]) for row in rows}) != 1:
            self.status.setText(tr('같은 영상의 클립만 합칠 수 있습니다.'))
            return
        self.capture_detected_ranges()
        merged = merge_rows(rows)
        self.clip_undo = deepcopy(self.ranges)
        for i in reversed(indices):
            del self.ranges[i]
        self.ranges.insert(indices[0], merged)
        self.refresh_ranges()
        self.range_list.setCurrentRow(indices[0])
        self.sync_review_selection()
        self.save_recovery()
        self.status.setText(tr('{value1}개 클립을 하나로 합쳤습니다. 되돌리기로 복구할 수 있습니다.', value1=len(indices)))

    def existing_analysis_choice(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr('기존 클립을 어떻게 할까요?'))
        dialog.setText(tr('이 영상에 기존 클립이 있습니다. 자동 인식 결과를 어떻게 저장할까요?'))
        dialog.setInformativeText(tr('덮어쓰면 이 영상의 자동 인식 클립과 그 편집 내용을 새 결과로 교체합니다. 직접 추가한 클립은 유지합니다.'))
        replace = dialog.addButton(tr('자동 인식 클립 교체'), QMessageBox.ButtonRole.AcceptRole)
        append = dialog.addButton(tr('기존 클립에 추가'), QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton(tr('취소'), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        return "replace" if dialog.clickedButton() == replace else "append" if dialog.clickedButton() == append else "cancel"

    def analyze_range(self):
        if self.task:
            return
        if not self.path:
            self.status.setText(tr('분석할 영상을 먼저 열어 주세요.'))
            return
        try:
            require_regions(self.preview.regions)
            start, end = self.selected_times(analysis=True)
            choice = self.existing_analysis_choice() if self.ranges or self.detected_ranges.get(source_key(self.path)) else "append"
            if choice == "cancel":
                return
            profile = self.profile.profile_for_analysis()
            if profile is None:
                return
            start, end = self.selected_times(analysis=True)
            path, directory = self.path, self.directory
            workers = self.analysis_workers.value()
            decoder_threads = self.decoder_threads.value()
            ocr_workers, gpu = self.ocr_workers.value(), self.gpu_decode.isChecked()
            self.preview.player.pause()
            self.run_task(lambda cancel, report: analyze(path, start, end, profile, directory, cancel, report, workers=workers, decoder_threads=decoder_threads, ocr_workers=ocr_workers, gpu=gpu),
                          lambda result: self.analysis_finished(result, source_key(path) if choice == "replace" else None))
        except (ValueError, OSError) as error:
            self.fail(error)
            QMessageBox.warning(self, tr('곡 찾기를 시작할 수 없습니다'), str(error))
            self.profile.kind.setFocus()

    def analysis_finished(self, result, replace_source=None):
        if result.get("profile"):
            self.profile.accept_calibration(result["profile"])
        current = source_key(self.path) if self.path else self.active_source
        if replace_source is not None:
            self.detected_ranges[replace_source] = []
            if replace_source == current:
                self.ranges = [row for row in self.ranges if row.get("custom")]
                self.source_ranges[replace_source] = self.ranges
                self.clip_undo = None
            else:
                self.source_ranges[replace_source] = [row for row in self.source_ranges.get(replace_source, []) if row.get("custom")]
        offset_skipped = 0
        for original in result["clips"]:
            row = deepcopy(original)
            row["clip_id"] = uuid4().hex
            key = source_key(row["source"])
            self.detected_ranges.setdefault(key, []).append(deepcopy(row))
            duration = self.preview.player.duration() / 1000 if key == current else None
            try:
                row["start"], row["end"] = offset_bounds(row["start"], row["end"],
                    self.clip_start_offset.value(), self.clip_end_offset.value(), duration)
            except ValueError:
                offset_skipped += 1
            if key == current:
                self.ranges.append(row)
            else:
                self.source_ranges.setdefault(key, []).append(row)
        self.pending_exports = []
        self.refresh_ranges()
        self.save_recovery()
        self.status.setText(tr('구간 후보 {value1:,}개를 추가했습니다. 미리보기에서 확인해 주세요.', value1=len(result['clips'])))
        if offset_skipped:
            self.status.setText(self.status.text() + tr(
                ' · {offset_skipped}개는 오프셋 적용 시 길이가 없어 원래 구간을 유지했습니다.',
                offset_skipped=offset_skipped,
            ))
        self.set_stage(3)
        if result["warnings"]:
            self.status.setText(self.status.text() + tr(' · 고급 로그의 안내 확인'))
            self.log.appendPlainText("\n".join(result["warnings"]))

    def recognize_frame(self):
        if not self.path:
            return
        try:
            profile = self.profile.current_profile(require_complete=True)
            path, seconds, directory = self.path, self.preview.player.position() / 1000, self.directory
            self.preview.player.pause()
            self.run_task(lambda cancel, report: test_frame(path, seconds, profile, directory, cancel, report),
                          self.recognition_finished)
        except ValueError as error:
            self.fail(error)

    def recognition_finished(self, result):
        self.log.appendPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        names = " / ".join(value[0] for key, value in result.get("ocr", {}).items() if key.startswith("player"))
        self.status.setText(f"{result.get('title') or tr('곡 미확인')}" + (f" · {names}" if names else ""))

    def remove_range(self):
        self.clip_undo = deepcopy(self.ranges)
        for index in sorted((self.range_list.row(item) for item in self.range_list.selectedItems()), reverse=True):
            del self.ranges[index]
        self.refresh_ranges()
        self.save_recovery()

    def export_current(self):
        if not self.path:
            self.status.setText(tr('저장할 영상을 먼저 열어 주세요.'))
            return
        try:
            start, end = self.selected_times()
            self.start_export([{"source": str(self.path), "start": start, "end": end}])
        except ValueError as error:
            self.fail(error)

    def export_ranges(self, all_rows):
        self.export_all = all_rows
        rows = self.visible_ranges() if all_rows else [row for row in self.ranges if row.get("selected", False)]
        self.start_export(rows)

    def start_export(self, rows):
        if self.task:
            return
        if not rows:
            self.status.setText(tr('저장할 구간을 선택해 주세요.'))
            return
        self.prepare_export(rows)
        self.set_stage(4)

    def export_finished(self, result):
        self.last_exports = result
        for path in result["saved"]:
            self.log.appendPlainText(tr('저장 완료: {path}', path=path))
        for row in result["failed"]:
            self.log.appendPlainText(tr('저장 실패: {source} · {error}', source=row['source'], error=row['error']))
        if result["failed"] and not result["cancelled"]:
            for row in self.ranges:
                row["selected"] = any(all(row[key] == failed[key] for key in ("source", "start", "end"))
                                      for failed in result["failed"])
            self.refresh_ranges()
        self.save_recovery()
        prefix = tr('저장 중지') if result["cancelled"] else tr('저장 완료')
        self.status.setText(tr(
            '{prefix} · 성공 {value2:,}개 · 실패 {value3:,}개',
            prefix=prefix,
            value2=len(result['saved']),
            value3=len(result['failed']),
        ))

    def select_range(self, item):
        row = self.ranges[self.range_list.row(item)]
        if self.path != Path(row["source"]):
            self.add_paths([row["source"]])
            index = next((i for i, candidate in enumerate(self.ranges) if candidate is row), -1)
            self.range_list.blockSignals(True)
            self.range_list.setCurrentRow(index)
            self.range_list.blockSignals(False)
            self.show_clip_details(index)
            self.pending_range = row
            if self.preview.player.duration():
                self.media_loaded(self.preview.player.duration())
            return
        if not self.preview.player.duration():
            self.pending_range = row
            return
        self.apply_range(row)

    def apply_range(self, row):
        self.review_selection = (row["start"], row["end"])
        self.update_timelines()
        self.play_range(bounds=self.review_selection)

    def inspect_link(self):
        try:
            url = youtube_url(self.url.text())
        except ValueError:
            self.url_error.setText(tr('YouTube 영상 링크를 확인하세요.'))
            self.url_error.show()
            self.url.setFocus()
            return
        self.url_error.hide()
        self.run_task(lambda cancel, report: inspect_source(url, cancel, report), self.link_ready)

    def link_ready(self, metadata):
        self.switch_source(None)
        self.path = None
        self.remote = metadata
        self.pending_range = None
        self.range_end = None
        self.files.setCurrentRow(-1)
        self.source_picker.setCurrentIndex(-1)
        self.refresh_ranges()
        regions = self.preview.regions
        self.preview.clear()
        self.profile.restore_after_load(regions)
        self.start_time.setText(format_time(0))
        self.end_time.setText(format_time(metadata.get("duration") or 0))
        self.youtube_start.setText(format_time(0))
        self.youtube_end.setText(format_time(metadata.get("duration") or 0))
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_detail.setText(tr('다운로드 대기'))
        self.download_mode.clear()
        self.download_eta.clear()
        kind = "LIVE" if metadata.get("is_live") or metadata.get("was_live") else tr('일반 동영상')
        self.youtube_info.setText(tr(
            '{kind} · {title}\n{value3} · {value4}\n길이 {value5}\n{value6}\n저장 위치: {value7}',
            kind=kind,
            title=metadata['title'],
            value3=metadata.get('video_date', tr('날짜 미제공')),
            value4=metadata.get('uploader') or metadata.get('channel') or '',
            value5=format_time(metadata.get('duration') or 0),
            value6=metadata.get('url', ''),
            value7=self.directory / 'downloads',
        ))
        self.download_log.clear()
        self.status.setText(metadata["title"])
        public = {key: value for key, value in metadata.items() if key != "window" and not key.startswith('_')}
        self.log.appendPlainText(json.dumps(public, ensure_ascii=False, indent=2))
        self.set_stage(5)
        if "window" in metadata:
            from datetime import datetime
            stamp = metadata["window"].get("start_utc")
            origin = datetime.fromtimestamp(stamp).astimezone().strftime("%m-%d %H:%M:%S") if stamp else tr('조회 구간 첫 장면')
            self.status.setText(tr('라이브 · 00:00:00 기준: {origin} · 접근 가능 {value2}', origin=origin, value2=format_time(metadata['duration'])))
            self.youtube_info.setText(self.youtube_info.text() + tr('\n라이브 00:00:00 기준: {origin}', origin=origin))

    def download_range(self):
        if not self.remote:
            self.status.setText(tr('YouTube 링크의 정보를 먼저 확인해 주세요.'))
            return
        try:
            start, end = parse_time(self.youtube_start.text()), parse_time(self.youtube_end.text())
            if not 0 <= start < end <= self.remote.get("duration", 0):
                raise ValueError(tr('다운로드할 시작·끝을 영상 길이 안에서 지정해 주세요.'))
            url = self.remote["url"]
            snapshot = deepcopy(self.remote)
            workers = self.download_workers.value()
            directory = self.directory / "downloads"
            self.download_log.clear()
            self.download_progress.setRange(0, 0)
            self.download_detail.setText(tr('다운로드 준비 중'))
            self.download_mode.clear()
            self.download_eta.setText(tr('예상 남은 시간 계산 중…'))
            self.download_log.appendPlainText(f"{snapshot['title']}\n{format_time(start)} – {format_time(end)} · {format_time(end-start)}\n{directory}")
            def download(cancel, report):
                path = download_section(url, start, end, directory, cancel, report, snapshot=snapshot, workers=workers)
                return {"path": path, "metadata": snapshot, "start": start, "end": end}
            self.run_task(download, self.youtube_downloaded, log=self.download_log)
        except ValueError as error:
            self.fail(error)

    def youtube_downloaded(self, result):
        path, metadata = result["path"], result["metadata"]
        self.source_info[source_key(path)] = {
            "title": metadata.get("title", "YouTube"),
            "channel": metadata.get("uploader") or metadata.get("channel") or "",
            "date": metadata.get("video_date", tr('날짜 미제공')),
            "kind": "LIVE" if metadata.get("is_live") or metadata.get("was_live") else tr('일반 동영상'),
            "range": f"{format_time(result['start'])} – {format_time(result['end'])}"}
        self.add_paths([path])
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(100)
        self.download_detail.setText(tr('다운로드 완료'))
        self.download_eta.clear()
        self.set_stage(0)
        self.save_recovery()

    def run_task(self, operation, success, log=None):
        if self.task:
            return
        self.task = Task(operation, self)
        self.analysis_panel.hide()
        self.analysis_metrics.clear()
        self.task.progress.connect(self.status.setText)
        if log is not None:
            self.task.progress.connect(log.appendPlainText)
        self.task.details.connect(self.analysis_update)
        self.task.finished.connect(lambda: self.task_finished(success))
        for control in self.busy_controls:
            control.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel.show()
        self.task.start()

    def task_finished(self, success):
        self.task.wait()
        task, self.task = self.task, None
        for control in self.busy_controls:
            control.setEnabled(True)
        self.progress.hide()
        self.cancel.hide()
        if self.remote and (task.cancel.is_set() or task.error):
            self.download_progress.setRange(0, 100)
            self.download_detail.setText(tr('다운로드 취소') if task.cancel.is_set() else tr('다운로드 실패 · 아래 로그 확인'))
            self.download_eta.clear()
        if task.cancel.is_set():
            if isinstance(task.result, dict) and "saved" in task.result:
                success(task.result)
            else:
                self.status.setText(tr('작업을 취소했습니다.'))
                if self.remote:
                    self.download_log.appendPlainText(tr('다운로드를 취소했습니다. 구간을 다시 선택할 수 있습니다.'))
        elif task.error:
            self.fail(task.error)
        else:
            try:
                success(task.result)
            except Exception as error:
                self.fail(error)
        if self.analysis_panel.isVisible():
            self.analysis_progress.setRange(0, 100)
            if task.cancel.is_set() or task.error:
                self.analysis_detail.setText(tr('분석 중지') if task.cancel.is_set() else tr('분석 실패 · 상세 로그 확인'))
            else:
                self.analysis_progress.setValue(100)
                self.analysis_detail.setText(tr('분석 완료 · 클립을 확인해 주세요.'))
        task.deleteLater()
        self.update_selection_summary()
        if self.pending_close:
            QTimer.singleShot(0, self.close)

    def cancel_task(self):
        if self.task:
            self.task.cancel.set()
            self.status.setText(tr('작업을 중지하고 있습니다.'))

    def fail(self, error):
        logging.error("%s: %s", type(error).__name__, error)
        self.log.appendPlainText(f"{type(error).__name__}: {error}")
        if self.remote:
            self.download_log.appendPlainText(tr('오류 · {error}', error=error))
        self.status.setText(str(error) if isinstance(error, ValueError) else tr('작업을 완료하지 못했습니다. 고급에서 상세 내용을 확인해 주세요.'))

    def configure(self):
        if self.task:
            return
        try:
            write_json(self.directory / "recovery.json", self.session_data())
        except (ValueError, OSError) as error:
            self.fail(error)
            return
        dialog = SetupDialog(self.directory, self)
        dialog.start.setText(tr('적용 후 재시작'))
        dialog.status.setText(tr('현재 작업은 기존 폴더에 보관됩니다. 새 폴더 준비가 끝나면 프로그램을 다시 시작합니다.'))
        if dialog.exec() and source_key(dialog.directory) != source_key(self.directory):
            settings = QSettings("ArcadeClip", "ArcadeClip")
            settings.sync()
            arguments = sys.argv[1:] if getattr(sys, "frozen", False) else sys.orig_argv[1:]
            started, _ = QProcess.startDetached(sys.executable, arguments, os.getcwd())
            if started:
                self.close()
            else:
                settings.setValue("data_directory", str(self.directory))
                settings.sync()
                QMessageBox.warning(self, tr('재시작 실패'), tr('프로그램을 다시 시작하지 못했습니다. 기존 데이터 폴더를 유지합니다.'))
        dialog.deleteLater()

    def show_export_settings(self):
        from export_settings import show_export_settings
        show_export_settings(self)

    def show_advanced(self):
        from advanced_settings import show_advanced
        show_advanced(self)

    def check_tools(self):
        for name in ("ffmpeg", "ffprobe", "deno"):
            try:
                self.log.appendPlainText(f"{name}: {find_tool(name)}")
            except RuntimeError as error:
                self.log.appendPlainText(str(error))

    def update_jackets(self):
        def complete(result):
            self.status.setText(tr('곡 데이터 업데이트 완료') if result["complete"] else tr('다시 받을 곡 데이터가 있습니다.'))
            self.refresh_catalog_summary()
        self.run_task(lambda cancel, report: download_jackets(self.directory, cancel, report),
                      complete)

    def refresh_catalog_summary(self):
        if self.catalog_task:
            return
        self.catalog_task = Task(lambda cancel, report: catalog_summary(self.directory, cancel, report), self)
        def complete():
            task, self.catalog_task = self.catalog_task, None
            task.wait()
            if task.error:
                for value in self.catalog_values:
                    value.setText(tr('확인 실패'))
                    value.setToolTip(str(task.error))
            elif not task.cancel.is_set():
                songs, jackets, size = task.result
                amount = f"{size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.1f} MB"
                for value, text in zip(self.catalog_values, (trn(
                    '{count:,}곡',
                    '{count:,}곡',
                    songs,
                ), trn(
                    '{count:,}개',
                    '{count:,}개',
                    jackets,
                ), amount)):
                    value.setText(text)
                    value.setToolTip(str(self.directory / "jackets"))
            task.deleteLater()
        self.catalog_task.finished.connect(complete)
        self.catalog_task.start()

    def session_data(self):
        try:
            profile = self.profile.current_profile() if self.preview.regions else None
        except ValueError:
            profile = None
        self.remember_source()
        sources = [self.files.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.files.count())]
        return {"version": 1, "ranges": [row for rows in self.source_ranges.values() for row in rows],
                "sources": sources, "active_source": str(self.path) if self.path else None,
                "profile": profile, "profile_file": self.profile.profile_path.name if self.profile.profile_path else None,
                "exports": self.last_exports,
                "volume": self.preview.volume_slider.value(),
                "source_info": self.source_info,
                "detected_ranges": [row for rows in self.detected_ranges.values() for row in rows]}

    def save_recovery(self):
        self.volume_save_timer.stop()
        try:
            write_json(self.directory / "recovery.json", self.session_data())
        except (ValueError, OSError) as error:
            self.fail(error)

    def save_session(self):
        name, _ = QFileDialog.getSaveFileName(self, tr('편집 작업 저장'), str(self.directory / "ArcadeClip-work.json"), tr('ArcadeClip 편집 작업 (*.json)'))
        if name:
            try:
                write_json(Path(name), self.session_data())
            except (ValueError, OSError) as error:
                self.fail(error)

    def open_session(self):
        name, _ = QFileDialog.getOpenFileName(self, tr('편집 작업 열기'), str(self.directory), tr('ArcadeClip 편집 작업 (*.json)'))
        if name:
            self.load_session(Path(name))

    def load_session(self, path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("ranges"), list):
                raise ValueError(tr('작업 파일 형식을 확인해 주세요.'))
            rows = [validate_clip(row) for row in value["ranges"]]
            volume = value.get("volume", self.preview.volume_slider.value())
            if type(volume) is not int or not 0 <= volume <= 100:
                raise ValueError(tr('작업 파일 형식을 확인해 주세요.'))
            detected = value.get("detected_ranges", [])
            if not isinstance(detected, list):
                raise ValueError(tr('자동 인식 원본 형식을 확인해 주세요.'))
            detected = [validate_clip(row) for row in detected]
            if any(not row.get("clip_id") for row in detected):
                raise ValueError(tr('자동 인식 원본 식별자를 확인해 주세요.'))
            profile = validate_profile(value["profile"]) if value.get("profile") else None
            profile_file = value.get("profile_file")
            if profile_file is not None and (not isinstance(profile_file, str) or Path(profile_file).name != profile_file
                                             or Path(profile_file).suffix.lower() != ".json"):
                raise ValueError(tr('프로필 파일 이름을 확인해 주세요.'))
            sources = value.get("sources", [])
            info = value.get("source_info", {})
            if (not isinstance(info, dict) or any(not isinstance(k, str) or not isinstance(v, dict)
                    or any(not isinstance(text, str) or len(text) > 2000 for text in v.values()) for k, v in info.items())):
                raise ValueError(tr('영상 메타데이터를 확인해 주세요.'))
            if not isinstance(sources, list) or any(not isinstance(p, str) or not p for p in sources):
                raise ValueError(tr('작업 파일의 영상 경로를 확인해 주세요.'))
            sources = list(dict.fromkeys([*sources, *(row["source"] for row in rows), *(row["source"] for row in detected)]))
            active = value.get("active_source") or (sources[0] if sources else None)
            if active is not None and (not isinstance(active, str) or source_key(active) not in {source_key(p) for p in sources}):
                raise ValueError(tr('작업 파일의 선택된 영상을 확인해 주세요.'))
            self.detected_ranges = {}
            for row in detected:
                self.detected_ranges.setdefault(source_key(row["source"]), []).append(row)
            self.active_source, self.ranges, self.source_ranges = None, [], {}
            self.source_info = {source_key(k): v for k, v in info.items()}
            for row in rows:
                self.source_ranges.setdefault(source_key(row["source"]), []).append(row)
            self.files.blockSignals(True)
            self.files.clear()
            self.files.blockSignals(False)
            self.source_picker.blockSignals(True)
            self.source_picker.clear()
            self.source_picker.blockSignals(False)
            # Add the saved active source last so add_paths selects it only once.
            self.loading_session = True
            try:
                self.add_paths([p for p in sources if source_key(p) != source_key(active)] + ([active] if active else []))
            finally:
                self.loading_session = False
            self.active_source = source_key(active) if active else None
            self.ranges = self.source_ranges.setdefault(self.active_source, []) if active else []
            self.clip_undo, self.pending_exports = None, []
            if not active:
                self.path = None
                self.remote = None
                self.preview.clear()
                self.set_stage(0)
            self.last_exports = value.get("exports")
            self.refresh_ranges()
            if profile:
                profile_path = self.directory / "profiles" / profile_file if profile_file else None
                if profile_path and not profile_path.is_file():
                    profile_path = None
                self.profile.apply_profile(profile, profile_path)
                if profile_path:
                    self.profile.saved_profile = load_profile(profile_path)
            if self.ranges:
                self.set_stage(3)
                self.range_list.setCurrentRow(next((i for i in range(self.range_list.count()) if not self.range_list.item(i).isHidden()), -1))
            self.preview.volume_slider.setValue(volume)
            self.status.setText(tr('작업 구간 {value1:,}개를 불러왔습니다.', value1=len(rows)))
        except (ValueError, OSError) as error:
            self.fail(error)

    def dragEnterEvent(self, event):
        if not self.task and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.task:
            self.add_paths(url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile())

    def stop_reverse(self):
        self.reverse_timer.stop()
        self.reverse_rate = 0
        self.frame_steps = 0
        self.seek_revision += 1

    def reverse_tick(self):
        seconds = max(0, self.preview.player.position() / 1000 - self.reverse_rate * .1)
        self.preview.seek(seconds)
        if seconds == 0:
            self.stop_reverse()

    def pause_transport(self):
        self.reverse_timer.stop()
        self.reverse_rate = 0
        self.frame_steps = 0
        self.seek_revision += 1
        self.preview.player.pause()

    def begin_scrub(self):
        self.stop_reverse()
        self.range_end = None

    def scrub(self, seconds):
        self.begin_scrub()
        self.preview.seek(seconds)
        low, high = self.zoom_timeline.bounds
        if self.follow_clip.isChecked() and not low <= seconds <= high:
            half = (high - low) / 2
            self.zoom_timeline.view(seconds - half, seconds + half)

    def commit_timeline(self):
        if self.stage == 3 and not self.task:
            self.apply_clip_details()

    def step_frame(self, direction):
        if not self.path:
            return
        self.reverse_timer.stop()
        self.preview.player.pause()
        self.frame_steps += direction
        self.start_frame_step()

    def start_frame_step(self):
        if self.frame_task or not self.frame_steps or not self.path:
            return
        direction = 1 if self.frame_steps > 0 else -1
        self.frame_steps -= direction
        path, revision = self.path, self.seek_revision
        seconds = self.preview.frame_time if self.preview.frame_time is not None else self.preview.player.position() / 1000
        self.frame_task = Task(lambda cancel, report: adjacent_frame(path, seconds, direction, cancel), self)
        self.frame_task.finished.connect(lambda: self.frame_step_finished(path, revision))
        self.frame_task.start()

    def frame_step_finished(self, path, revision):
        task, self.frame_task = self.frame_task, None
        task.wait()
        if revision == self.seek_revision and path == self.path and not task.cancel.is_set():
            if task.error:
                self.fail(task.error)
                self.frame_steps = 0
            else:
                self.preview.seek(task.result["seconds"])
                self.preview.show_frame(ImageQt(task.result["image"]), task.result["seconds"])
        task.deleteLater()
        self.start_frame_step()

    def shuttle(self, direction):
        self.frame_steps = 0
        self.seek_revision += 1
        if direction < 0:
            self.preview.player.pause()
            self.reverse_rate = min(8, self.reverse_rate * 2 if self.reverse_timer.isActive() else 1)
            self.reverse_timer.start()
        else:
            self.reverse_timer.stop()
            rate = self.preview.player.playbackRate()
            self.preview.player.setPlaybackRate(min(8, rate * 2) if self.preview.player.isPlaying() else 1)
            self.preview.player.play()

    def show_controls(self):
        from help_view import show_help
        show_help(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress or not isinstance(watched, QWidget) or watched.window() is not self:
            return super().eventFilter(watched, event)
        focus = self.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox)):
            return False
        key, mods = event.key(), event.modifiers()
        ctrl, shift, alt = (bool(mods & flag) for flag in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.AltModifier))
        if self.stage == 2 and ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus, Qt.Key.Key_0):
            if key == Qt.Key.Key_0:
                self.preview.canvas.reset_view()
            else:
                self.preview.canvas.zoom(.8 if key == Qt.Key.Key_Minus else 1.25)
        elif key == Qt.Key.Key_F1:
            self.show_controls()
        elif ctrl and key == Qt.Key.Key_S and not self.task:
            self.save_session()
        elif ctrl and key == Qt.Key.Key_E and not self.task:
            self.export_ranges(False)
        elif ctrl and key == Qt.Key.Key_Z and not self.task and self.stage == 3:
            self.undo_clip_edit()
        elif key == Qt.Key.Key_Space:
            if self.reverse_timer.isActive():
                self.pause_transport()
            else:
                self.frame_steps = 0
                self.seek_revision += 1
                self.preview.toggle_playback()
        elif key == Qt.Key.Key_K:
            self.pause_transport()
        elif key in (Qt.Key.Key_J, Qt.Key.Key_L):
            self.shuttle(-1 if key == Qt.Key.Key_J else 1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            direction = -1 if key == Qt.Key.Key_Left else 1
            if alt:
                try:
                    self.scrub(self.selected_times()[0 if direction < 0 else 1])
                except ValueError as error:
                    self.fail(error)
            elif ctrl or shift:
                self.scrub(self.preview.player.position() / 1000 + direction * (5 if ctrl else 1))
            elif self.preview.player.isPlaying():
                self.scrub(self.preview.player.position() / 1000 + direction)
            else:
                self.step_frame(direction)
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.scrub(self.preview.player.position() / 1000 + (-1 if key == Qt.Key.Key_PageUp else 1) * (10 if ctrl and shift else 5 if ctrl else 2 if shift else 1))
        elif key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self.scrub(0 if key == Qt.Key.Key_Home else max(0, self.preview.player.duration() / 1000 - .001))
        elif key in (Qt.Key.Key_I, Qt.Key.Key_O) and not self.task and self.stage in (1, 3):
            self.mark(self.start_time if key == Qt.Key.Key_I else self.end_time)
        elif self.stage == 3 and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus, Qt.Key.Key_0):
            self.zoom_timeline.fit() if key == Qt.Key.Key_0 else self.zoom_timeline.zoom(.8 if key == Qt.Key.Key_Minus else 1.25)
        elif not self.task and self.stage == 3 and key in (Qt.Key.Key_Delete, Qt.Key.Key_A):
            {Qt.Key.Key_A: self.add_range, Qt.Key.Key_Delete: self.remove_range}[key]()
        else:
            return False
        event.accept()
        return True

    def closeEvent(self, event):
        self.pause_transport()
        if self.catalog_task:
            self.catalog_task.cancel.set()
            self.catalog_task.wait()
        if self.frame_task:
            self.frame_task.cancel.set()
            self.frame_task.wait()
        if self.task:
            self.pending_close = True
            self.cancel_task()
            event.ignore()
        else:
            if self.volume_save_timer.isActive():
                self.save_recovery()
            self.preview.player.stop()
            if self.advanced:
                self.advanced.close()
            event.accept()


def main():
    application = QApplication(sys.argv)
    install_qt_translation(application)
    application.setApplicationName("ArcadeClip")
    application.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.ico")))
    directory = QSettings("ArcadeClip", "ArcadeClip").value("data_directory", "")
    if not directory or not (Path(directory) / "jackets" / "_manifest.json").is_file():
        setup = SetupDialog()
        if not setup.exec():
            return 0
        directory = setup.directory
    window = MainWindow(Path(directory))
    window.show()
    return application.exec()

