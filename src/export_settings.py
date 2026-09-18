"""Settings view; values are applied only through its owning window."""
from i18n import tr
from PySide6.QtWidgets import QDialog, QVBoxLayout, QGroupBox, QFormLayout, QComboBox, QCheckBox, QDoubleSpinBox, QLabel, QPushButton, QHBoxLayout, QWidget, QScrollArea


def show_export_settings(window):
    dialog = QDialog(window)
    dialog.setWindowTitle(tr('저장 설정'))
    dialog.setMinimumWidth(460)
    dialog.resize(560, 720)
    outer = QVBoxLayout(dialog)
    content = QWidget()
    layout = QVBoxLayout(content)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(content)
    outer.addWidget(scroll)
    video_group = QGroupBox(tr('영상 · 파일 형식과 용량'))
    form = QFormLayout(video_group)
    def choice(name, items, value):
        field = QComboBox()
        field.setObjectName(name)
        for text, data in items:
            field.addItem(text, data)
        field.setCurrentIndex(max(0, field.findData(value)))
        return field
    mode = choice("save_mode", [(tr('정확한 구간 자르기'), "accurate"),
                                (tr('원본 빠른 복사'), "fast")], window.export_mode.currentData())
    quality = choice("save_quality", [(tr('선명하게 · 용량 큼'), 18), (tr('균형 있게 · 일반적인 공유용'), 23),
                                      (tr('작게 · 화질보다 용량 우선'), 28)], window.encoder_crf.value())
    speed = choice("save_speed", [(tr('빠르게 처리'), "fast"), (tr('시간과 압축률 균형'), "medium"),
                                  (tr('천천히 · 같은 화질에서 용량 절약'), "slow")], window.encoder_preset.currentText())
    sound = QCheckBox(tr('소리 포함'))
    sound.setObjectName("save_audio")
    sound.setChecked(window.export_audio)
    options = window.export_options
    gpu = QCheckBox(tr('GPU로 영상 압축'))
    gpu.setObjectName("save_gpu")
    gpu.setChecked(options.get("gpu", False))
    gpu.setToolTip(tr('MP4·MKV·MOV에서 사용 가능한 NVIDIA·Intel·AMD 인코더를 자동 선택합니다. 실패하면 CPU로 저장합니다.'))
    container = choice("save_format", [("MP4 · H.264 / AAC", "mp4"), ("MKV · H.264 / AAC", "mkv"),
                                        ("MOV · H.264 / AAC", "mov"), ("WebM · VP9 / Opus", "webm")], options.get("format", "mp4"))
    resolution = choice("save_height", [(tr(
        '원본 유지',
    ), 0)] + [(tr(
        '최대 {h}p',
        h=h,
    ), h) for h in (480, 720, 1080, 1440, 2160)], options.get("height", 0))
    framerate = choice("save_fps", [(tr('원본 유지'), 0)] + [(f"{f} FPS", f) for f in (24, 30, 60)], options.get("fps", 0))
    target = QDoubleSpinBox()
    target.setObjectName("save_target_mb")
    target.setRange(0, 100000)
    target.setSuffix(" MB")
    target.setSpecialValueText(tr('화질 우선 · 용량 자동'))
    target.setValue(options.get("target_mb", 0))
    gain = QDoubleSpinBox()
    gain.setObjectName("save_gain")
    gain.setRange(-60, 24)
    gain.setSuffix(" dB")
    gain.setValue(options.get("gain", 0))
    gain.setToolTip(tr('0: 원래 음량 · 음수: 작게 · 양수: 크게. 너무 높이면 소리가 찌그러질 수 있습니다.'))
    audio_rate = choice("save_audio_kbps", [(f"{v} kbps", v) for v in (64, 96, 128, 192, 256, 320)], options.get("audio_kbps", 192))
    channels = choice("save_channels", [(tr('원본 유지'), 0), (tr('모노'), 1), (tr('스테레오'), 2)], options.get("channels", 0))
    form.addRow(tr('자르는 방식'), mode)
    mode_hint = QLabel()
    mode_hint.setWordWrap(True)
    form.addRow(mode_hint)
    form.addRow(tr('파일 형식'), container)
    form.addRow(tr('클립당 목표 크기'), target)
    form.addRow(tr('화질과 용량'), quality)
    form.addRow(tr('해상도'), resolution)
    form.addRow(tr('프레임 속도'), framerate)
    form.addRow(tr('처리 속도'), speed)
    form.addRow(gpu)
    layout.addWidget(video_group)
    audio_group = QGroupBox(tr('오디오'))
    audio_form = QFormLayout(audio_group)
    audio_form.addRow(sound)
    audio_form.addRow(tr('음량 조절'), gain)
    audio_form.addRow(tr('오디오 품질'), audio_rate)
    audio_form.addRow(tr('채널'), channels)
    layout.addWidget(audio_group)
    hint = QLabel(tr('목표 크기는 클립마다 적용되며 실제 파일 크기는 달라질 수 있습니다.'))
    hint.setWordWrap(True)
    layout.addWidget(hint)
    def update():
        accurate = mode.currentData() == "accurate"
        mode_hint.setText(tr('선택한 시작·끝에 맞춰 영상을 변환합니다.') if accurate else
                         tr('MP4로 원본을 복사합니다. 시작·끝 위치가 조금 달라질 수 있습니다. 영상·음량 변환 설정은 정확한 구간 자르기에서 사용할 수 있습니다.'))
        for field in (container, target, resolution, framerate, speed):
            field.setEnabled(accurate)
        quality.setEnabled(accurate and target.value() == 0)
        gpu.setEnabled(accurate and container.currentData() != "webm")
        speed.setEnabled(accurate and not (gpu.isEnabled() and gpu.isChecked()))
        for field in (gain, audio_rate, channels):
            field.setEnabled(accurate and sound.isChecked())
    mode.currentIndexChanged.connect(update)
    target.valueChanged.connect(update)
    sound.toggled.connect(update)
    container.currentIndexChanged.connect(update)
    gpu.toggled.connect(update)
    update()
    presets = choice("save_share_preset", [(tr('X(트위터) · 720p 60fps'), (720, 23, 128)),
                     ("YouTube · 1080p 60fps", (1080, 18, 320)),
                     ("YouTube · 1440p 60fps", (1440, 18, 320)),
                     ("Vimeo · 1080p 60fps", (1080, 18, 320))], (720, 23, 128))
    social = QPushButton(tr('프리셋 적용'))
    social.setObjectName("save_x_preset")
    def apply_social():
        height_value, quality_value, audio_value = presets.currentData()
        for field, value in ((mode, "accurate"), (container, "mp4"), (quality, quality_value), (speed, "medium"),
                             (resolution, height_value), (framerate, 60), (audio_rate, audio_value), (channels, 2)):
            field.setCurrentIndex(field.findData(value))
        target.setValue(0)
        gain.setValue(0)
        sound.setChecked(True)
        preset_state.setText(tr('적용할 설정: {preset}', preset=presets.currentText()))
        social_hint.setVisible(presets.currentIndex() == 0)
    social.clicked.connect(apply_social)
    preset_row = QHBoxLayout()
    preset_row.addWidget(presets, 1)
    preset_row.addWidget(social)
    layout.insertLayout(0, preset_row)
    preset_caption = QLabel(tr('공유용 프리셋 · 선택 후 적용 버튼을 누르세요'))
    layout.insertWidget(0, preset_caption)
    preset_state = QLabel(tr('현재 설정: 원본 빠른 복사') if mode.currentData() == 'fast' else tr('현재 설정: 사용자 지정'))
    preset_state.setWordWrap(True)
    layout.insertWidget(2, preset_state)
    presets.currentIndexChanged.connect(lambda: preset_state.setText(tr('프리셋을 선택했습니다. 적용 버튼을 눌러 설정에 반영하세요.')))
    long_count = sum(row["end"] - row["start"] > 140 for row in window.pending_exports)
    social_hint = QLabel(tr('X 일반 계정: 140초·512MB 이하. 저장 후 파일 크기를 확인해 주세요.')
                         + (tr('\n현재 {long_count}개 클립이 140초를 넘습니다. 클립 확인에서 구간을 줄여 주세요.', long_count=long_count) if long_count else ""))
    social_hint.setWordWrap(True)
    social_hint.hide()
    layout.insertWidget(3, social_hint)
    def mark_custom():
        preset_state.setText(tr('적용할 설정: 사용자 지정'))
        social_hint.hide()
    for field in (mode, quality, speed, container, resolution, framerate, audio_rate, channels):
        field.currentIndexChanged.connect(mark_custom)
    for field in (target, gain):
        field.valueChanged.connect(mark_custom)
    sound.toggled.connect(mark_custom)
    gpu.toggled.connect(mark_custom)
    buttons = QHBoxLayout()
    cancel = QPushButton(tr('취소'))
    cancel.clicked.connect(dialog.reject)
    apply = QPushButton(tr('적용'))
    apply.setDefault(True)
    apply.clicked.connect(dialog.accept)
    buttons.addStretch()
    buttons.addWidget(cancel)
    buttons.addWidget(apply)
    outer.addLayout(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        window.export_mode.setCurrentIndex(window.export_mode.findData(mode.currentData()))
        window.encoder_crf.setValue(quality.currentData())
        window.encoder_preset.setCurrentText(speed.currentData())
        window.export_audio = sound.isChecked()
        defaults = dict(format="mp4", target_mb=0, height=0, fps=0, gain=0, audio_kbps=192, channels=0, gpu=False)
        values = dict(format=container.currentData(), target_mb=target.value(), height=resolution.currentData(),
                      fps=framerate.currentData(), gain=gain.value(), audio_kbps=audio_rate.currentData(), channels=channels.currentData(),
                      gpu=gpu.isEnabled() and gpu.isChecked())
        window.export_options = {key: value for key, value in values.items() if value != defaults[key]}
        text = tr('원본 빠른 복사') if mode.currentData() == "fast" else " · ".join([
            tr('정확한 구간'), quality.currentText().split(" · ")[0], speed.currentText().split(" · ")[0]])
        window.export_settings_summary.setText(text + (tr(' · 소리 포함') if window.export_audio else tr(' · 소리 제외')))
        if mode.currentData() == "accurate":
            extra = [container.currentData().upper(), resolution.currentText(), framerate.currentText()]
            if values["gpu"]:
                extra.append(tr('GPU 압축'))
            if target.value():
                extra.append(tr('클립당 약 {value1:g} MB', value1=target.value()))
            if window.export_audio:
                extra.append(tr(
                    '음량 {value1:+g} dB · {value2} · {value3}',
                    value1=gain.value(),
                    value2=audio_rate.currentText(),
                    value3=channels.currentText(),
                ))
            window.export_settings_summary.setText(window.export_settings_summary.text() + "\n" + " · ".join(extra))
        window.prepare_export(window.pending_exports)
