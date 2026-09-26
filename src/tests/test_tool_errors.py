"""External-tool failures and Windows trust fallback without external requests."""
from pathlib import Path
import shlex
import sys
import threading
import unittest
from unittest.mock import patch

import sources
from tool_errors import ToolError, error_kind, error_message


class ToolErrorTests(unittest.TestCase):
    def test_diagnostics_classify_specific_causes_not_generic_exit_codes(self):
        cases = {
            'Peer certificate failed verification\nffmpeg exited with code 4294967291': 'certificate',
            'CERTIFICATE_VERIFY_FAILED': 'certificate',
            'HTTP Error 429: Too Many Requests': 'rate_limit',
            'Server returned 403 Forbidden': 'forbidden',
            'HTTP Error 404: Not Found': 'remote_missing',
            'Sign in to confirm your age': 'login',
            'Video unavailable': 'unavailable',
            'getaddrinfo failed': 'network',
            'Connection timed out': 'network',
            'No space left on device': 'disk_full',
            'Cannot allocate memory': 'memory',
            'Permission denied': 'permission',
            'Cannot load nvcuda.dll': 'gpu',
            'Requested format is not available': 'format',
            'moov atom not found': 'media',
            'No such file or directory': 'file_missing',
            'I/O error': 'io',
            'ffmpeg exited with code 4294967291': 'unknown',
        }
        for detail, kind in cases.items():
            with self.subTest(detail=detail):
                self.assertEqual(error_kind(detail), kind)
                self.assertTrue(error_message(kind))
        self.assertEqual(ToolError('', 0xc0000135).kind, 'launch')
        self.assertEqual(ToolError('', -1073741701).kind, 'launch')

    def test_actual_child_failure_preserves_cause_and_redacts_urls(self):
        script = "import sys; sys.stderr.write('Peer certificate failed verification https://host.example/?token=secret\\n' + 'x'*5000); sys.exit(5)"
        with self.assertLogs(level='DEBUG'), self.assertRaises(ToolError) as caught:
            sources.run_command([sys.executable, '-c', script], threading.Event(), lambda _: None)
        self.assertEqual(caught.exception.kind, 'certificate')
        self.assertIn('Peer certificate failed verification', caught.exception.detail)
        self.assertNotIn('token=secret', caught.exception.detail)
        self.assertNotIn('token=secret', str(caught.exception))
        with patch('sources.subprocess.Popen', side_effect=FileNotFoundError('missing')):
            with self.assertRaises(ToolError) as caught:
                sources.run_command(['missing'], threading.Event(), lambda _: None)
            self.assertEqual(caught.exception.kind, 'tool_missing')

    def test_windows_roots_first_for_preview_and_each_downloader_input(self):
        for downloader in (False, True):
            tls = sources.ffmpeg_https_args()
            args = (['yt-dlp', '--downloader-args', 'ffmpeg_i:' + shlex.join(tls + ['-multiple_requests', '1'])]
                    if downloader else ['ffmpeg', *tls, '-i', 'https://media.example/video'])
            seen = []
            def run(command, *_args, **_kwargs):
                options = shlex.split(command[2].partition(':')[2]) if downloader else command
                ca = Path(options[options.index('-ca_file') + 1])
                self.assertEqual(options[options.index('-tls_verify') + 1], '1')
                seen.append(ca)
                if len(seen) == 1:
                    self.assertIn('BEGIN CERTIFICATE', ca.read_text())
                    raise ToolError('Peer certificate failed verification')
                self.assertEqual(command, args)
                return 'ok'
            with patch('sources.sys.platform', 'win32'), patch('sources.ssl.create_default_context') as context, patch('sources._run_command', side_effect=run):
                context.return_value.get_ca_certs.return_value = [b'example DER']
                self.assertEqual(sources.run_command(args, threading.Event(), lambda _: None, windows_ca=True), 'ok')
            self.assertEqual(len(seen), 2)
            self.assertFalse(seen[0].exists())

    def test_no_retry_for_other_failures_or_cancellation_or_success(self):
        args = ['ffmpeg', *sources.ffmpeg_https_args(), '-i', 'https://media.example/video']
        for failure in (None, ToolError('I/O error', 4294967291), ToolError('HTTP error 403'), InterruptedError()):
            with patch('sources.sys.platform', 'win32'), patch('sources.ssl.create_default_context') as context, patch('sources._run_command', side_effect=failure, return_value='ok') as run:
                context.return_value.get_ca_certs.return_value = [b'example DER']
                if failure:
                    with self.assertRaises(type(failure)):
                        sources.run_command(args, threading.Event(), lambda _: None, windows_ca=True)
                else:
                    self.assertEqual(sources.run_command(args, threading.Event(), lambda _: None, windows_ca=True), 'ok')
                self.assertEqual(run.call_count, 1)
        cancel = threading.Event()
        cancel.set()
        with patch('sources._run_command') as run:
            with self.assertRaises(InterruptedError):
                sources.run_command(args, cancel, lambda _: None, windows_ca=True)
            run.assert_not_called()

    def test_empty_windows_store_and_non_windows_use_bundled_roots(self):
        args = ['ffmpeg', *sources.ffmpeg_https_args(), '-i', 'https://media.example/video']
        for platform in ('win32', 'linux'):
            with patch('sources.sys.platform', platform), patch('sources.ssl.create_default_context') as context, patch('sources._run_command', return_value='ok') as run:
                context.return_value.get_ca_certs.return_value = []
                self.assertEqual(sources.run_command(args, threading.Event(), lambda _: None, windows_ca=True), 'ok')
                self.assertEqual(run.call_args.args[0], args)
                self.assertEqual(run.call_count, 1)

    def test_second_certificate_failure_is_final_and_cancel_prevents_retry(self):
        args = ['ffmpeg', *sources.ffmpeg_https_args(), '-i', 'https://media.example/video']
        for cancelled in (False, True):
            cancel = threading.Event()
            def fail(*_args, **_kwargs):
                if cancelled:
                    cancel.set()
                raise ToolError('Peer certificate failed verification')
            with patch('sources.sys.platform', 'win32'), patch('sources.ssl.create_default_context') as context, patch('sources._run_command', side_effect=fail) as run:
                context.return_value.get_ca_certs.return_value = [b'example DER']
                with self.assertRaises(InterruptedError if cancelled else ToolError):
                    sources.run_command(args, cancel, lambda _: None, windows_ca=True)
                self.assertEqual(run.call_count, 1 if cancelled else 2)


if __name__ == '__main__':
    unittest.main()
