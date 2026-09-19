"""Shared CLI startup and opt-in diagnostics for source and packaged launches."""
import os

# Before numpy is imported anywhere, including in the analysis workers that inherit
# this environment. Its linear algebra library reserves a thread buffer per core,
# which on a 32-core machine commits about 750 MB per process for work this app
# never asks of it: the heavy lifting is spread across processes, not BLAS threads.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from i18n import tr, initialize_language
import argparse
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

_crash_log = None


def launch():
    import multiprocessing
    multiprocessing.freeze_support()
    if sys.argv[1:2] == ["--yt-dlp"]:
        from ytdlp_youtube import install
        install()
        import yt_dlp
        return yt_dlp.main(sys.argv[2:])
    initialize_language()
    parser = argparse.ArgumentParser(description="ArcadeClip")
    parser.add_argument("--debug", action="store_true", help=tr('콘솔과 로그 파일에 진단 정보 기록'))
    options, qt_args = parser.parse_known_args()
    sys.argv = [sys.argv[0], *qt_args]
    if options.debug:
        from PySide6.QtCore import QCoreApplication, QStandardPaths, QtMsgType, qInstallMessageHandler
        QCoreApplication.setApplicationName("ArcadeClip")
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(), RotatingFileHandler(
                                folder / "debug.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")], force=True)
        global _crash_log
        _crash_log = (folder / "crash.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_log, all_threads=True)
        def qt_message(kind, context, message):
            level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
                     QtMsgType.QtWarningMsg: logging.WARNING}.get(kind, logging.ERROR)
            logging.log(level, "Qt: %s", message)
        qInstallMessageHandler(qt_message)
        def exception_hook(kind, value, traceback):
            logging.critical("Unhandled exception", exc_info=(kind, value, traceback))
        sys.excepthook = exception_hook
        logging.info("Debug enabled: %s (native crashes: crash.log)", folder / "debug.log")
        logging.info("Python %s on %s", sys.version.split()[0], sys.platform)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from app import main
    return main()
