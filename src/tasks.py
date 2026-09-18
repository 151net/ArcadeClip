"""One cancellable background operation at a time for desktop dialogs."""
import logging
import threading

from PySide6.QtCore import QThread, Signal


class Task(QThread):
    progress = Signal(str)
    details = Signal(object)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()
        self.result = None
        self.error = None

    def report(self, value):
        if isinstance(value, dict):
            logging.debug("Analysis progress: %s", value)
            self.details.emit(value)
            self.progress.emit(value["message"])
        else:
            self.progress.emit(value)

    def run(self):
        logging.debug("Background task started")
        try:
            self.result = self.operation(self.cancel, self.report)
        except Exception as error:
            logging.exception("Background task failed")
            self.error = error.with_traceback(None)
        finally:
            # Release callbacks that capture the owning dialog before Qt deletes this thread.
            self.operation = None
            logging.debug("Background task finished")
