"""Gettext boundary. Korean is the source language; select language before UI imports."""
import gettext
import os
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
_translation = gettext.translation("arcadeclip", localedir=LOCALES,
    languages=[os.environ.get("ARCADECLIP_LANGUAGE", "ko")], fallback=True)


def initialize_language():
    """Apply the saved UI preference before importing UI modules or starting workers."""
    from PySide6.QtCore import QSettings
    language = QSettings("ArcadeClip", "ArcadeClip").value("language")
    if language not in ("ko", "en"):
        language = os.environ.get("ARCADECLIP_LANGUAGE", "ko")
    os.environ["ARCADECLIP_LANGUAGE"] = language
    global _translation
    _translation = gettext.translation("arcadeclip", localedir=LOCALES,
                                       languages=[language], fallback=True)


def tr(message, **values):
    translated = _translation.gettext(message)
    return translated.format(**values) if values else translated


def trn(singular, plural, count, **values):
    return _translation.ngettext(singular, plural, count).format(count=count, **values)


def install_qt_translation(application):
    """Keep Qt's standard dialog buttons in the same language as the app."""
    from PySide6.QtCore import QLibraryInfo, QTranslator
    if hasattr(application, '_arcadeclip_qt_translation'):
        return
    translator = QTranslator(application)
    language = os.environ.get('ARCADECLIP_LANGUAGE', 'ko').replace('-', '_')
    if translator.load('qtbase_' + language, QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        application.installTranslator(translator)
    application._arcadeclip_qt_translation = translator
