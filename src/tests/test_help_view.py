"""Guide wiring checks without opening a browser."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QWidget
import help_view


class HelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_source_and_packaged_paths_and_language(self):
        for language in ('ko', 'en', 'en_US'):
            with patch.dict(os.environ, ARCADECLIP_LANGUAGE=language):
                url = help_view.manual_url()
                self.assertTrue(Path(url.toLocalFile()).is_file())
                self.assertEqual(url.fragment(), f'{language[:2]}/overview/1')
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(help_view, '__file__', str(folder / 'help_view.py')):
                with self.assertRaises(FileNotFoundError):
                    help_view.manual_url()
                (folder / 'manual').mkdir()
                (folder / 'manual/manual.html').touch()
                self.assertEqual(Path(help_view.manual_url().toLocalFile()), folder / 'manual/manual.html')

    def test_help_opens_the_guide_in_the_default_browser(self):
        parent = QWidget()
        parent.fail = Mock()
        with patch.object(help_view.QDesktopServices, 'openUrl', return_value=True) as open_url:
            help_view.show_help(parent)
        open_url.assert_called_once()
        url = open_url.call_args.args[0]
        self.assertEqual(Path(url.toLocalFile()).name, 'manual.html')
        self.assertEqual(url.fragment(), 'ko/overview/1')
        parent.fail.assert_not_called()
        parent.close()

    def test_missing_guide_and_browser_failure_are_reported(self):
        parent = QWidget()
        parent.fail = Mock()
        with TemporaryDirectory() as directory:
            with patch.object(help_view, '__file__', str(Path(directory) / 'help_view.py')):
                with patch.object(help_view.QDesktopServices, 'openUrl') as open_url:
                    help_view.show_help(parent)
                open_url.assert_not_called()
        self.assertIsInstance(parent.fail.call_args.args[0], FileNotFoundError)

        parent.fail.reset_mock()
        with patch.object(help_view.QDesktopServices, 'openUrl', return_value=False):
            help_view.show_help(parent)
        self.assertIsInstance(parent.fail.call_args.args[0], ValueError)
        parent.close()
