"""Arcade profiles and recognition-region controls."""
from i18n import tr
from pathlib import Path
from copy import deepcopy
from uuid import uuid4
import json
import zipfile

from PySide6.QtCore import Qt, Signal, QUrl, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from preview import VideoCanvas
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QInputDialog, QMessageBox, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget, QDialogButtonBox,
)

from profiles import REGIONS, require_regions, load_profile, validate_profile, write_json, read_profile_import


class ProfilePanel(QWidget):
    error = Signal(object)
    message = Signal(str)

    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.preview = preview
        self.directory = Path.home()
        self.profile_size = None
        self.recognition_options = {}
        self.jacket_offset = None
        self.profile_path = None
        self.saved_profile = None
        self.persist_calibration = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(tr('오락실 프로필')))
        profile_row = QHBoxLayout()
        self.profile_picker = QComboBox()
        self.profile_picker.setAccessibleName(tr('오락실 프로필 선택'))
        self.profile_picker.activated.connect(self.select_profile)
        profile_row.addWidget(self.profile_picker, 1)
        import_button = QPushButton()
        import_button.setIcon(QIcon(str(Path(__file__).parent / "assets" / "import.svg")))
        import_button.setToolTip(tr('프로필 가져오기 (JSON / ZIP)'))
        import_button.setAccessibleName(tr('프로필 가져오기'))
        import_button.clicked.connect(self.import_profiles)
        profile_row.addWidget(import_button)
        layout.addLayout(profile_row)
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr('프로필 이름'))
        self.name.setAccessibleName(tr('프로필 이름'))
        layout.addWidget(self.name)
        self.kind = QComboBox()
        self.kind.setAccessibleName(tr('인식 영역 종류'))
        self.kind.addItem(tr('영역 선택'), "")
        for key, name in REGIONS.items():
            self.kind.addItem(name + (tr(' · 필수') if key in ("jacket", "achievement", "player") else tr(' · 선택 사항')), key)
        self.kind.currentIndexChanged.connect(self.change_region)
        layout.addWidget(self.kind)
        self.kind_pulse = QVariantAnimation(self)
        self.kind_pulse.setDuration(2400)
        self.kind_pulse.setStartValue(0.)
        self.kind_pulse.setKeyValueAt(.5, 1.)
        self.kind_pulse.setEndValue(0.)
        self.kind_pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.kind_pulse.setLoopCount(-1)
        self.kind_pulse.valueChanged.connect(lambda value: self.kind.setStyleSheet(
            "QComboBox { border: 2px solid qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 rgba(23,108,146,{int(70+185*value)}), stop:1 rgba(133,87,210,{int(70+185*value)}));"
            "border-radius:5px; padding:4px; }"))
        self.guide_title = QLabel()
        layout.addWidget(self.guide_title)
        self.guide_prompt = QLabel(tr('위 목록에서\n지정할 영역을 선택하세요.'))
        self.guide_prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guide_prompt.setWordWrap(True)
        self.guide_prompt.setMinimumHeight(135)
        self.guide_prompt.setStyleSheet("background:#18232b;color:white;border-radius:6px;font-size:15px;padding:12px;")
        layout.addWidget(self.guide_prompt)
        self.guide_video = VideoCanvas()
        self.guide_video.setEnabled(False)
        self.guide_video.setMinimumSize(240, 135)
        self.guide_video.setMaximumHeight(200)
        layout.addWidget(self.guide_video)
        self.guide_pause = QPushButton(tr('예시 일시정지'))
        self.guide_pause.clicked.connect(self.toggle_guide)
        layout.addWidget(self.guide_pause)
        self.guide_player = QMediaPlayer(self)
        self.guide_sink = QVideoSink(self)
        self.guide_sink.videoFrameChanged.connect(
            lambda frame: self.guide_video.set_image(frame.toImage()) if frame.isValid() else None)
        self.guide_player.setVideoSink(self.guide_sink)
        self.guide_player.setLoops(QMediaPlayer.Loops.Infinite)
        self.guide_player.errorOccurred.connect(lambda error, text: self.guide_title.setText(tr('가이드 재생 오류: ') + text))
        instruction = QLabel(tr('영상에서 드래그해 영역을 지정하세요.'))
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        layout.addWidget(QLabel(tr('현재 지정 영역')))
        self.crop = QLabel()
        self.crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.crop.setMinimumHeight(90)
        self.crop.setAccessibleName(tr('선택한 인식 영역 확대'))
        layout.addWidget(self.crop)
        for actions in (((tr('영역 지우기'), self.clear_region), (tr('좌표 입력'), self.edit_coordinates)),
                        ((tr('프로필 저장'), self.save_profile), (tr('프로필 내보내기'), self.export_profile))):
            row = QHBoxLayout()
            for text, callback in actions:
                button = QPushButton(text)
                button.clicked.connect(callback)
                row.addWidget(button)
            layout.addLayout(row)
        self.preview.regionChanged.connect(self.region_edited)
        self.preview.frameChanged.connect(self.update_crop)
        self.update_crop()

    def update_guide(self):
        kind = self.kind.currentData()
        self.guide_prompt.setVisible(not bool(kind))
        self.guide_video.setVisible(bool(kind))
        self.guide_pause.setVisible(bool(kind))
        self.guide_pause.setText(tr('예시 일시정지'))
        self.kind_pulse.stop()
        self.kind.setStyleSheet("")
        if not kind:
            self.guide_title.setText(tr('영역 지정 예시'))
            self.guide_player.stop()
            if self.isVisible():
                self.kind_pulse.start()
            return
        kind = {"player2": "player", "achievement2": "achievement"}.get(kind, kind)
        self.guide_title.setText(tr('{value1} 지정 예시', value1=REGIONS[kind]))
        source = QUrl.fromLocalFile(str(Path(__file__).parent / "assets" / f"region-guide-{kind}.mp4"))
        if self.guide_player.source() != source:
            self.guide_player.setSource(source)
        if self.isVisible():
            self.guide_player.play()

    def toggle_guide(self):
        if self.guide_player.isPlaying():
            self.guide_player.pause()
            self.guide_pause.setText(tr('예시 재생'))
        else:
            self.guide_player.play()
            self.guide_pause.setText(tr('예시 일시정지'))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_profiles()
        self.update_guide()

    def hideEvent(self, event):
        self.guide_player.stop()
        self.kind_pulse.stop()
        self.kind.setStyleSheet("")
        super().hideEvent(event)

    def change_region(self):
        self.update_guide()
        self.preview.set_region_kind(self.kind.currentData())
        self.update_crop()

    def region_edited(self, *args):
        image = self.preview.canvas.image
        if not image.isNull():
            self.profile_size = [image.width(), image.height()]
        self.update_crop()

    def update_crop(self):
        offset = self.jacket_offset
        roi = self.preview.regions.get("jacket")
        self.preview.canvas.offset_box = None
        if offset and offset["roi"] == roi:
            x0, y0, x1, y1 = roi
            a, b, c, d = offset["rect"]
            self.preview.canvas.offset_box = [x0 + a * (x1-x0), y0 + b * (y1-y0),
                                              x0 + c * (x1-x0), y0 + d * (y1-y0)]
        self.preview.canvas.update()
        image = self.preview.canvas.image
        if image.isNull():
            self.crop.clear()
            return
        size = [image.width(), image.height()]
        if self.profile_size and abs(size[0] / size[1] - self.profile_size[0] / self.profile_size[1]) > .01:
            self.preview.set_regions({})
            self.profile_size = None
            self.message.emit(tr('화면 비율이 바뀌었습니다. 인식 영역을 선택해 주세요.'))
        box = self.preview.regions.get(self.kind.currentData())
        if not box:
            self.crop.clear()
            return
        x0, y0, x1, y1 = box
        cropped = image.copy(round(x0 * size[0]), round(y0 * size[1]),
                             round((x1 - x0) * size[0]), round((y1 - y0) * size[1]))
        self.crop.setPixmap(QPixmap.fromImage(cropped).scaled(220, 100, Qt.AspectRatioMode.KeepAspectRatio))

    def clear_region(self):
        regions = self.preview.regions
        regions.pop(self.kind.currentData(), None)
        self.preview.set_regions(regions)
        self.update_crop()

    def restore_after_load(self, regions):
        self.preview.set_regions(regions)
        self.update_crop()

    def edit_coordinates(self):
        key = self.kind.currentData()
        image = self.preview.canvas.image
        if not key or image.isNull():
            return
        self.preview.player.pause()
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('{region} 영역 좌표', region=REGIONS[key]))
        layout = QFormLayout(dialog)
        box = self.preview.regions.get(key, [0, 0, 1, 1])
        dimensions = [image.width(), image.height()] * 2
        fields = []
        for label, value, maximum in zip((tr('왼쪽'), tr('위'), tr('오른쪽'), tr('아래')), box, dimensions):
            field = QSpinBox()
            field.setRange(0, maximum)
            field.setSuffix(tr(' 픽셀'))
            field.setValue(round(value * maximum))
            fields.append(field)
            layout.addRow(label, field)
        buttons = QDialogButtonBox()
        buttons.addButton(tr('적용'), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr('취소'), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            regions = self.preview.regions
            regions[key] = [field.value() / dimension for field, dimension in zip(fields, dimensions)]
            try:
                self.preview.set_regions(regions)
                self.region_edited()
            except ValueError as error:
                self.error.emit(error)

    def current_profile(self, require_complete=False):
        if require_complete:
            require_regions(self.preview.regions)
        image = self.preview.canvas.image
        size = self.profile_size if image.isNull() else [image.width(), image.height()]
        return validate_profile({**({"jacket_offset": self.jacket_offset} if self.jacket_offset else {}),
                                 "version": 1, "name": self.name.text(),
                                 "size": size, "regions": self.preview.regions,
                                 "recognition": self.recognition_options})

    def refresh_profiles(self):
        self.profile_picker.blockSignals(True)
        self.profile_picker.clear()
        self.profile_picker.addItem(tr('{name} · 저장 전', name=self.name.text()) if not self.profile_path and self.name.text().strip()
                                    else tr('프로필 선택'), None)
        directory = self.directory / "profiles"
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                if path.name.startswith("offset-"):
                    continue
                try:
                    self.profile_picker.addItem(load_profile(path)["name"], str(path))
                except (ValueError, OSError):
                    continue
        self.profile_picker.addItem(tr('새 프로필…'), "new")
        index = self.profile_picker.findData(str(self.profile_path)) if self.profile_path else 0
        self.profile_picker.setCurrentIndex(max(0, index))
        self.profile_picker.blockSignals(False)
        self.name.setVisible(self.profile_path is None)

    def is_modified(self):
        if self.saved_profile is None:
            return bool(self.preview.regions)
        try:
            current = self.current_profile()
        except ValueError:
            return True
        baseline = {**self.saved_profile, "recognition": self.saved_profile.get("recognition", {})}
        return {k: v for k, v in current.items() if k != "jacket_offset"} != {
            k: v for k, v in baseline.items() if k != "jacket_offset"}

    def select_profile(self, index):
        target = self.profile_picker.itemData(index)
        if not target:
            self.refresh_profiles()
            return
        if self.is_modified() and QMessageBox.question(self, tr(
            '프로필 변경',
        ), tr(
            '수정 중인 내용을 버리고 다른 프로필을 선택할까요?',
        )) != QMessageBox.StandardButton.Yes:
            self.refresh_profiles()
            return
        if target == "new":
            name, accepted = QInputDialog.getText(self, tr('새 프로필'), tr('이름'))
            if not accepted or not name.strip():
                self.refresh_profiles()
                return
            self.profile_path = self.saved_profile = None
            self.name.setText(name.strip())
            self.preview.set_regions({})
            self.jacket_offset = None
            self.recognition_options = {}
            self.profile_size = None
            self.update_crop()
        else:
            try:
                self.apply_profile(load_profile(Path(target)), Path(target))
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, tr('프로필 선택'), str(error))
        self.refresh_profiles()

    def store_profile(self, profile, path=None):
        path = path or self.directory / "profiles" / f"profile-{uuid4().hex}.json"
        write_json(path, profile)
        self.profile_path = path
        self.saved_profile = deepcopy(profile)
        self.name.setText(profile["name"])
        self.refresh_profiles()
        return profile

    def save_profile(self):
        try:
            self.store_profile(self.current_profile(), self.profile_path)
            self.message.emit(tr('프로필을 저장했습니다.'))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, tr('프로필 저장'), str(error))

    def import_profiles(self):
        name, _ = QFileDialog.getOpenFileName(self, tr('프로필 가져오기'), str(self.directory), tr('프로필 (*.json *.zip)'))
        if not name:
            return
        try:
            profiles = read_profile_import(Path(name))
            if self.is_modified() and QMessageBox.question(self, tr(
                '프로필 가져오기',
            ), tr(
                '수정 중인 내용을 버리고 가져온 프로필을 선택할까요?',
            )) != QMessageBox.StandardButton.Yes:
                return
            for profile in profiles:
                self.store_profile(profile)
            self.apply_profile(profiles[-1], self.profile_path)
            self.message.emit(tr('프로필 {value1}개를 가져왔습니다.', value1=len(profiles)))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, tr('프로필 가져오기'), str(error))

    def export_profile(self):
        try:
            profile = self.current_profile()
            name, _ = QFileDialog.getSaveFileName(self, tr('프로필 내보내기'), str(self.directory / "profile.json"), "JSON (*.json);;ZIP (*.zip)")
            if name:
                if Path(name).suffix.lower() == ".zip":
                    with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr("profile.json", json.dumps(profile, ensure_ascii=False, indent=2))
                else:
                    write_json(Path(name), profile)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, tr('프로필 내보내기'), str(error))

    def profile_for_analysis(self):
        require_regions(self.preview.regions)
        if not self.profile_path and not self.name.text().strip():
            name, accepted = QInputDialog.getText(self, tr('새 프로필 저장'), tr('프로필 이름'))
            if not accepted:
                return None
            self.name.setText(name.strip())
        profile = self.current_profile(require_complete=True)
        self.persist_calibration = True
        if not self.profile_path:
            self.store_profile(profile)
        elif self.is_modified():
            dialog = QMessageBox(self)
            dialog.setWindowTitle(tr('수정한 프로필'))
            dialog.setText(tr('프로필의 변경 내용을 어떻게 처리할까요?'))
            new = dialog.addButton(tr('새 프로필로 저장'), QMessageBox.ButtonRole.ActionRole)
            overwrite = dialog.addButton(tr('선택한 프로필 덮어쓰기'), QMessageBox.ButtonRole.AcceptRole)
            skip = dialog.addButton(tr('저장 없이 곡 찾기'), QMessageBox.ButtonRole.ActionRole)
            dialog.addButton(tr('취소'), QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() == new:
                name, accepted = QInputDialog.getText(self, tr('새 프로필로 저장'), tr('이름'), text=profile["name"])
                if not accepted:
                    return None
                profile = validate_profile({**profile, "name": name})
                self.store_profile(profile)
            elif dialog.clickedButton() == overwrite:
                self.store_profile(profile, self.profile_path)
            elif dialog.clickedButton() == skip:
                self.persist_calibration = False
            else:
                return None
        return profile

    def apply_profile(self, profile, path=None):
        profile = validate_profile(profile)
        self.profile_path = path
        self.saved_profile = deepcopy(profile) if path else None
        self.jacket_offset = profile.get("jacket_offset")
        self.recognition_options = dict(profile.get("recognition", {}))
        self.name.setText(profile["name"])
        self.profile_size = profile["size"][:]
        self.preview.set_regions(profile["regions"])
        self.update_crop()
        self.refresh_profiles()

    def accept_calibration(self, profile):
        path, baseline = self.profile_path, self.saved_profile
        self.apply_profile(profile, path)
        if path and self.persist_calibration:
            try:
                write_json(path, profile)
            except OSError as error:
                self.error.emit(error)
        else:
            self.saved_profile = baseline

    def edit_recognition(self):
        from analysis_common import DEFAULTS
        options = {**DEFAULTS, **self.recognition_options}
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('인식 설정'))
        layout = QVBoxLayout(dialog)
        jacket = QGroupBox(tr('자켓 영역 · 곡 판독'))
        jacket_form = QFormLayout(jacket)
        layout.addWidget(jacket)
        text = QGroupBox(tr('글자 영역 · 이름 및 달성률'))
        text_form = QFormLayout(text)
        layout.addWidget(text)
        fields = {}
        for key, label, low, high, step in (
            ("location_seconds", tr('자켓 위치 보정 간격 (초)'), 1, 120, 1),
            ("sample_seconds", tr('곡 확인 간격 (초)'), .1, 5, .1),
            ("stable_seconds", tr('곡 변경 확인 시간 (초)'), .1, 10, .1),
            ("hash_distance", tr('자켓 차이 허용값'), 0, 64, 1),
            ("hash_margin", tr('두 후보의 최소 점수 차이'), 0, 64, 1),
            ("ocr_tail_seconds", tr('곡 끝 글자 확인 범위 (초)'), .1, 10, .1),
        ):
            field = QDoubleSpinBox()
            field.setRange(low, high)
            field.setSingleStep(step)
            field.setDecimals(1 if step < 1 else 0)
            field.setValue(options[key])
            field.setObjectName(key)
            if key == "hash_distance":
                field.setToolTip(tr('높을수록 느슨하게 판독합니다. 비슷한 자켓이 잘못 인식될 수 있습니다.'))
            (text_form if key == "ocr_tail_seconds" else jacket_form).addRow(label, field)
            fields[key] = field
        jacket_hint = QLabel(tr('곡 확인 간격이 짧을수록 더 자주 확인합니다.\n자켓 차이 허용값을 높이면 더 느슨하게 인식합니다.\n두 후보의 최소 점수 차이를 높이면 비슷한 자켓을 더 엄격하게 구분합니다.'))
        jacket_hint.setWordWrap(True)
        jacket_form.addRow(jacket_hint)
        buttons = QDialogButtonBox()
        buttons.addButton(tr('적용'), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr('취소'), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.recognition_options = {key: field.value() for key, field in fields.items()}
