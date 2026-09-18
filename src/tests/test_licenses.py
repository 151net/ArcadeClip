import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QTextBrowser
from license_dialog import show_licenses


class LicenseDialogTest(unittest.TestCase):
    def test_bundled_notices_and_selection(self):
        application = QApplication.instance() or QApplication([])
        entries = json.loads((Path(__file__).parents[1] / 'licenses.json').read_text(encoding='utf-8'))
        names = {entry['name'].lower() for entry in entries}
        self.assertTrue({'pyside6', 'av', 'pillow', 'yt-dlp', 'rapidocr', 'onnxruntime', 'numpy', 'deno'} <= names)
        self.assertNotIn('nuitka', names)
        self.assertTrue({'ffmpeg (executable)', 'ffprobe (executable)', 'deno (executable)',
                         'python runtime', 'korean_pp-ocrv5_rec_mobile.onnx',
                         'ch_pp-ocrv5_rec_mobile.onnx', 'en_pp-ocrv5_rec_mobile.onnx'} <= names)
        for name in ('ffmpeg', 'ffprobe'):
            entry = next(entry for entry in entries if entry['name'] == name + ' (executable)')
            self.assertTrue(any(notice['file'] == name + ' -L' for notice in entry['notices']))
            self.assertTrue(any('COPYING.GPLv3' in notice['file'] for notice in entry['notices']))

        def inspect(dialog):
            listing = dialog.findChild(QListWidget)
            detail = dialog.findChild(QTextBrowser)
            self.assertEqual(listing.count(), len(entries))
            for index, entry in enumerate(entries):
                listing.setCurrentRow(index)
                text = detail.toPlainText()
                self.assertIn(entry['name'] + ' ' + entry['version'], text)
                for notice in entry['notices']:
                    self.assertIn(notice['text'].strip(), text)
            return 0

        with patch.object(QDialog, 'exec', inspect):
            show_licenses(None)
