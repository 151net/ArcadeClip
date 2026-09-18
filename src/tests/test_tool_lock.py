import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('verify_tools', Path(__file__).parents[2] / 'run/verify_tools.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ToolLockTest(unittest.TestCase):
    def test_exact_bytes_required(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            binary = directory / 'ffmpeg.exe'
            binary.write_bytes(b'pinned build')
            expected = {'ffmpeg.exe': hashlib.sha256(binary.read_bytes()).hexdigest()}
            module.verify(directory, expected)
            binary.write_bytes(b'another build with the same version')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                module.verify(directory, expected)
