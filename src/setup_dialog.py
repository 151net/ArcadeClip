"""First-run storage selection and resumable song artwork preparation."""
from i18n import tr
from pathlib import Path
from tempfile import TemporaryFile

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QProgressBar)

from jackets import download_jackets, migrate_legacy, source_status
from jacket_source_ui import download_controls
from tasks import Task


class SetupDialog(QDialog):
    def __init__(self, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('데이터 폴더') if current else tr('환영합니다'))
        self.resize(560, 260)
        self.task = None
        self.directory = None
        self.chosen = None
        self.settings = QSettings("ArcadeClip", "ArcadeClip")
        default = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "ArcadeClip"
        self.path = QLineEdit(str(current or self.settings.value("data_directory", str(default))))
        self.path.setAccessibleName(tr('데이터 저장 폴더'))
        browse = QPushButton(tr('폴더 선택'))
        browse.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.status = QLabel(tr('데이터를 저장할 폴더를 선택하세요.'))
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.hide()
        self.start = QPushButton(tr('적용') if current else tr('시작하기 →'))
        if not current:
            self.start.setStyleSheet("QPushButton { background:#176c92; color:white; font-weight:600; padding:7px 10px; }"
                                    "QPushButton:disabled { background:#777; color:#ddd; }")
        self.start.clicked.connect(self.prepare)
        self.cancel = QPushButton(tr('취소') if current else tr('닫기'))
        self.cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.start)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('데이터 저장 위치') if current else tr('ArcadeClip에 오신 것을 환영합니다')))
        layout.addLayout(row)
        self.source = download_controls(self.settings, self)
        self.source.hide()
        self.advanced = QPushButton(tr('고급'))
        self.advanced.setCheckable(True)
        self.advanced.toggled.connect(self.source.setVisible)
        advanced_row = QHBoxLayout()
        advanced_row.addWidget(self.advanced)
        advanced_row.addStretch()
        layout.addLayout(advanced_row)
        layout.addWidget(self.source)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addLayout(buttons)

    def browse(self):
        if self.task:
            return
        selected = QFileDialog.getExistingDirectory(self, tr('데이터 저장 폴더'), self.path.text())
        if selected:
            self.path.setText(selected)

    def prepare(self):
        if self.task:
            return
        if not self.path.text().strip():
            self.status.setText(tr('저장할 폴더를 선택하세요.'))
            return
        self.source.apply()
        # Settle the addresses on this thread; the worker must not touch widgets.
        self.chosen = self.source.current()
        self.directory = Path(self.path.text()).expanduser().resolve()
        self.path.setEnabled(False)
        self.source.setEnabled(False)
        self.start.setEnabled(False)
        self.cancel.setText(tr('취소'))
        self.progress.setRange(0, 0)
        self.progress.show()
        self.task = Task(lambda cancel, report: self._prepare(cancel, report), self)
        self.task.progress.connect(self.status.setText)
        self.task.finished.connect(self.complete)
        self.task.start()

    def _prepare(self, cancel, report):
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=self.directory) as probe:
            probe.write(b"1")
            probe.flush()
        report(tr('자켓 데이터를 확인하고 있습니다.'))
        migrate_legacy(self.directory)
        source = self.chosen
        if source_status(self.directory, source):
            return {"complete": True}
        return download_jackets(self.directory, cancel, report, source)

    def complete(self):
        self.task.wait()
        task, self.task = self.task, None
        self.path.setEnabled(True)
        self.source.setEnabled(True)
        self.start.setEnabled(True)
        self.cancel.setText(tr('닫기'))
        self.progress.hide()
        if task.cancel.is_set():
            self.status.setText(tr('준비를 중지했습니다. 시작하기를 눌러 이어서 받을 수 있습니다.'))
        elif task.error:
            self.status.setText(tr('데이터를 준비하지 못했습니다. 폴더와 연결을 확인해 주세요.'))
            message = QMessageBox(self)
            message.setWindowTitle(tr('데이터 준비'))
            message.setIcon(QMessageBox.Icon.Warning)
            message.setText(self.status.text())
            message.setDetailedText(f"{type(task.error).__name__}: {task.error}")
            message.exec()
        elif task.result.get("complete"):
            self.settings.setValue("data_directory", str(self.directory))
            self.accept()
        else:
            self.status.setText(tr('일부 자켓 데이터를 다시 받아야 합니다. 시작하기를 눌러 주세요.'))
        task.deleteLater()

    def reject(self):
        if self.task:
            self.task.cancel.set()
            self.status.setText(tr('준비를 중지하고 있습니다.'))
            return
        super().reject()

    def closeEvent(self, event):
        if self.task:
            self.reject()
            event.ignore()
        else:
            event.accept()
