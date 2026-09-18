"""Offline user guide, opened in the reader's default browser."""
import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from i18n import tr


def manual_url():
    # Nuitka ships manual beside the compiled modules; source runs use docs/manual.
    folder = Path(__file__).resolve().parent
    path = folder / 'manual/manual.html'
    if not path.is_file():
        path = folder.parent / 'docs/manual/manual.html'
    if not path.is_file():
        raise FileNotFoundError(tr('설명서 파일이 없습니다. 앱 설치 파일을 확인해 주세요.'))
    url = QUrl.fromLocalFile(str(path))
    lang = 'en' if os.environ.get('ARCADECLIP_LANGUAGE', 'ko').startswith('en') else 'ko'
    url.setFragment(f'{lang}/overview/1')
    return url


def show_help(parent):
    try:
        url = manual_url()
    except FileNotFoundError as error:
        parent.fail(error)
        return
    if not QDesktopServices.openUrl(url):
        parent.fail(ValueError(tr('설명서를 열지 못했습니다. manual 폴더의 manual.html을 브라우저에서 열어 주세요.')))
