"""Settings view; values are applied only through its owning window."""
from i18n import tr
import os
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QDialog, QVBoxLayout, QGroupBox, QFormLayout, QLineEdit, QHBoxLayout, QLabel, QDialogButtonBox, QMessageBox


def show_advanced(window):
    if window.advanced:
        window.advanced.show()
        window.advanced.raise_()
        return
    window.advanced = QDialog(window)
    window.advanced.setWindowTitle(tr('Settings'))
    window.advanced.resize(700, 650)
    layout = QVBoxLayout(window.advanced)
    settings = QSettings("ArcadeClip", "ArcadeClip")
    language = QComboBox()
    language.setObjectName("ui_language")
    language.setAccessibleName(tr('Language'))
    language.addItem("한국어", "ko")
    language.addItem("English", "en")
    selected = settings.value("language", os.environ.get("ARCADECLIP_LANGUAGE", "ko"))
    language.setCurrentIndex(1 if str(selected).startswith("en") else 0)
    def change_language():
        settings.setValue("language", language.currentData())
        QMessageBox.information(window.advanced, tr('Language'),
                                tr('언어 변경은 앱을 다시 실행하면 적용됩니다.'))

    language.currentIndexChanged.connect(change_language)
    language_form = QFormLayout()
    language_form.addRow(tr('Language'), language)
    language_hint = QLabel(tr('언어 변경은 앱을 다시 실행하면 적용됩니다.'))
    language_hint.setWordWrap(True)
    language_form.addRow(language_hint)
    layout.addLayout(language_form)
    layout.addWidget(QLabel(tr('분석 성능 설정은 다음 분석부터 적용됩니다.')))
    recognition = QGroupBox(tr('영역별 인식'))
    recognition_layout = QVBoxLayout(recognition)
    window.button(tr('자켓 · 글자 인식 설정…'), window.profile.edit_recognition, recognition_layout)
    layout.addWidget(recognition)
    performance = QGroupBox(tr('분석 성능'))
    form = QFormLayout(performance)
    form.addRow(tr('영상 디코더 스레드'), window.decoder_threads)
    window.decoder_threads.show()
    form.addRow(tr('자켓 판독 프로세스'), window.analysis_workers)
    window.analysis_workers.show()
    form.addRow(tr('글자 인식 작업자 (각 2 CPU 스레드)'), window.ocr_workers)
    window.ocr_workers.show()
    form.addRow(window.gpu_decode)
    window.gpu_decode.show()
    layout.addWidget(performance)
    navigation = QGroupBox(tr('화면 탐색'))
    navigation_layout = QVBoxLayout(navigation)
    navigation_layout.addWidget(window.follow_clip)
    window.follow_clip.show()
    layout.addWidget(navigation)
    layout.addWidget(window.catalog)
    window.catalog.show()
    storage = QGroupBox(tr('데이터 저장 위치'))
    storage_layout = QVBoxLayout(storage)
    path = QLineEdit(str(window.directory))
    path.setReadOnly(True)
    path.setAccessibleName(tr('현재 데이터 폴더'))
    storage_layout.addWidget(path)
    window.button(tr('데이터 폴더 변경…'), window.configure, storage_layout)
    layout.addWidget(storage)
    diagnostics = QGroupBox(tr('진단 로그'))
    diagnostic_layout = QVBoxLayout(diagnostics)
    diagnostic_layout.addWidget(window.log)
    row = QHBoxLayout()
    window.button(tr('FFmpeg·다운로드 도구 확인'), window.check_tools, row, busy=False)
    window.button(tr('문제 신고'), lambda: _report_problem(window), row, busy=False)
    diagnostic_layout.addLayout(row)
    report_hint = QLabel(tr('문제 신고는 브라우저에서 열립니다. 위 진단 로그의 내용을 함께 붙여 주세요.'))
    report_hint.setWordWrap(True)
    diagnostic_layout.addWidget(report_hint)
    layout.addWidget(diagnostics, 1)
    layout.addWidget(_version_group(window))
    buttons = QDialogButtonBox()
    from license_dialog import show_licenses
    licenses = buttons.addButton(tr('오픈소스 라이선스'), QDialogButtonBox.ButtonRole.ActionRole)
    licenses.clicked.connect(lambda: show_licenses(window.advanced))
    buttons.addButton(tr('닫기'), QDialogButtonBox.ButtonRole.RejectRole)
    buttons.rejected.connect(window.advanced.close)
    layout.addWidget(buttons)
    window.advanced.show()


def _version_group(window):
    from updates import VERSION
    group = QGroupBox(tr('앱 버전'))
    group_layout = QVBoxLayout(group)
    status = QLabel(tr('현재 버전 {name}', name=VERSION))
    status.setWordWrap(True)
    check = window.button(tr('새 버전 확인'), lambda: _check_updates(window, status, check), group_layout, busy=False)
    group_layout.addWidget(status)
    return group


def _check_updates(window, status, check):
    from tasks import Task
    from updates import VERSION, RELEASES_PAGE, is_newer, latest_release
    check.setEnabled(False)
    status.setText(tr('새 버전을 확인하는 중입니다…'))
    task = Task(lambda cancel, report: latest_release(), window.advanced)

    def finished():
        check.setEnabled(True)
        if task.error:
            status.setText(tr('새 버전을 확인하지 못했습니다. 인터넷 연결을 확인해 주세요.'))
        elif is_newer(task.result):
            status.setText(tr('새 버전 {name}이 있습니다.', name=task.result))
            answer = QMessageBox.question(window.advanced, tr('앱 버전'),
                                          tr('새 버전 {name}이 있습니다. 받는 곳을 열까요?', name=task.result))
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(RELEASES_PAGE))
        else:
            status.setText(tr('최신 버전입니다. 현재 버전 {name}', name=VERSION))

    task.finished.connect(finished)
    # Hold the thread on the dialog so Qt does not delete it while it runs.
    window.advanced.update_check = task
    task.start()


def _report_problem(window):
    from updates import report_url
    if not QDesktopServices.openUrl(QUrl(report_url())):
        QMessageBox.information(window.advanced, tr('문제 신고'),
                                tr('브라우저를 열지 못했습니다. 저장소의 Issues 페이지에서 신고해 주세요.'))
