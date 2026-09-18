"""Offline notices shipped with the runtime dependency snapshot."""
import json
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QSplitter, QTextBrowser, QVBoxLayout
from i18n import tr


def show_licenses(parent):
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr('오픈소스 라이선스'))
    dialog.resize(850, 650)
    layout = QVBoxLayout(dialog)
    description = QLabel(tr('라이브러리, 실행 도구 또는 모델을 선택하면 버전과 라이선스 정보를 볼 수 있습니다.'))
    description.setWordWrap(True)
    layout.addWidget(description)
    split = QSplitter()
    packages = QListWidget()
    packages.setAccessibleName(tr('오픈소스 구성 요소 목록'))
    details = QTextBrowser()
    details.setAccessibleName(tr('라이선스 및 고지문'))
    split.addWidget(packages)
    split.addWidget(details)
    split.setSizes([230, 620])
    layout.addWidget(split, 1)
    try:
        entries = json.loads(Path(__file__).with_name('licenses.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        entries = []
        details.setPlainText(tr('라이선스 정보를 불러올 수 없습니다. 앱 설치 파일을 확인해 주세요.'))

    def select(index):
        if index < 0:
            return
        entry = entries[index]
        sections = [entry['name'] + ' ' + entry['version'],
                    tr('라이선스: {name}', name=entry['license'] or tr('패키지에 라이선스 정보가 없습니다.'))]
        sections.extend(entry['urls'])
        if entry['homepage']:
            sections.append(entry['homepage'])
        sections.extend(notice['file'] + '\n\n' + notice['text'] for notice in entry['notices'])
        if not entry['notices']:
            sections.append(tr('이 패키지에는 라이선스 원문이 포함되어 있지 않습니다. 프로젝트에서 제공하는 라이선스를 확인하세요.'))
        details.setPlainText('\n\n'.join(sections))

    packages.currentRowChanged.connect(select)
    packages.addItems(entry['name'] for entry in entries)
    packages.setCurrentRow(0)
    buttons = QDialogButtonBox()
    buttons.addButton(tr('닫기'), QDialogButtonBox.ButtonRole.RejectRole)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
