"""Where song artwork comes from, shared by the welcome and settings screens."""
from i18n import tr

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)

from jackets import (ALL_SOURCES, IMAGE_BASE, MANIFEST_URL, PRESETS, installed_sources,
                     normalize_source, save_source, select_source, selected_source,
                     source_from)

MANUAL = "manual"


def download_controls(settings, parent=None):
    """The server to fetch from: a preset, the two addresses, and the certificate choice."""
    panel = QWidget(parent)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    current = source_from(settings)
    preset = QComboBox()
    for label, _, _ in PRESETS:
        preset.addItem(label, label)
    preset.addItem(tr('직접 입력'), MANUAL)
    manifest = QLineEdit(current["manifest"])
    manifest.setAccessibleName(tr('곡 목록 주소'))
    images = QLineEdit(current["images"])
    images.setAccessibleName(tr('자켓 이미지 주소'))
    form = QFormLayout()
    form.addRow(tr('내려받을 서버'), preset)
    form.addRow(tr('곡 목록 주소'), manifest)
    form.addRow(tr('자켓 이미지 주소'), images)
    layout.addLayout(form)
    verify = QCheckBox(tr('인증서 검사'))
    verify.setChecked(current["verify"])
    reset = QPushButton(tr('기본값으로'))
    row = QHBoxLayout()
    row.addWidget(verify)
    row.addStretch()
    row.addWidget(reset)
    layout.addLayout(row)
    hint = QLabel(tr('공식 서버는 인증서가 확인되지 않아 검사 없이 받습니다. 주소를 바꾸면 검사를 켭니다.'))
    hint.setWordWrap(True)
    layout.addWidget(hint)

    def matching_preset():
        for label, list_url, image_url in PRESETS:
            if manifest.text().strip() == list_url and images.text().strip().rstrip("/") == image_url.rstrip("/"):
                return label
        return MANUAL

    def show_preset():
        preset.blockSignals(True)
        preset.setCurrentIndex(preset.findData(matching_preset()))
        preset.blockSignals(False)

    def follow_address():
        show_preset()
        verify.blockSignals(True)
        verify.setChecked(matching_preset() != PRESETS[0][0])
        verify.blockSignals(False)

    def apply():
        chosen = {"manifest": manifest.text(), "images": images.text(), "verify": verify.isChecked()}
        try:
            saved = normalize_source(chosen)
        except ValueError as error:
            hint.setText(str(error))
            return
        if saved != source_from(settings):
            save_source(settings, saved)
        for box, value in ((manifest, saved["manifest"]), (images, saved["images"])):
            if box.text() != value:
                box.setText(value)
        verify.setChecked(saved["verify"])
        show_preset()

    def choose_preset():
        label = preset.currentData()
        if label == MANUAL:
            return
        _, list_url, image_url = next(row for row in PRESETS if row[0] == label)
        manifest.setText(list_url)
        images.setText(image_url)
        follow_address()
        apply()

    for box in (manifest, images):
        box.textEdited.connect(follow_address)
        box.editingFinished.connect(apply)
    verify.toggled.connect(apply)
    preset.activated.connect(choose_preset)
    reset.clicked.connect(lambda: (manifest.setText(MANIFEST_URL), images.setText(IMAGE_BASE),
                                   follow_address(), apply()))
    show_preset()
    panel.apply = apply
    panel.current = lambda: source_from(settings)
    return panel


def fill_catalog_group(window, settings):
    """Complete the jacket data box: which data to use, where to fetch it, counts, update."""
    layout = window.catalog_body
    picker = QComboBox()
    picker.setAccessibleName(tr('다음 데이터 사용'))
    top = QFormLayout()
    top.addRow(tr('다음 데이터 사용'), picker)
    layout.insertLayout(0, top)
    controls = download_controls(settings, window.catalog)
    layout.insertWidget(1, controls)
    layout.addLayout(window.catalog_counts)
    row = QHBoxLayout()
    row.addStretch()
    window.button(tr('자켓 데이터 업데이트'), window.update_jackets, row)
    layout.addLayout(row)

    def load():
        picker.blockSignals(True)
        picker.clear()
        names = installed_sources(window.directory)
        if len(names) > 1:
            picker.addItem(tr('모든 데이터'), ALL_SOURCES)
        for name in names:
            picker.addItem(name, name)
        if not names:
            picker.addItem(tr('받은 데이터 없음'), ALL_SOURCES)
            picker.setEnabled(False)
        else:
            picker.setEnabled(True)
            picker.setCurrentIndex(max(0, picker.findData(selected_source(window.directory))))
        picker.blockSignals(False)

    def choose():
        select_source(window.directory, picker.currentData())
        window.refresh_catalog_summary()

    picker.activated.connect(choose)
    load()
    window.catalog_sources = load
    return controls
