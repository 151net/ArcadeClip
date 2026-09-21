"""Release lookup and report link: version comparison, response handling, and what is sent."""
import json
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import updates


class FakeResponse:
    def __init__(self, payload, url='https://api.github.com/repos/x/y/releases/latest'):
        self.body = json.dumps(payload).encode()
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def geturl(self):
        return self.url

    def read(self, limit=None):
        return self.body


class VersionTests(unittest.TestCase):
    def test_version_matches_the_project_metadata(self):
        project = Path(updates.__file__).resolve().parents[1] / 'pyproject.toml'
        declared = tomllib.loads(project.read_text(encoding='utf-8'))['project']['version']
        self.assertEqual(updates.VERSION, declared)

    def test_newer_tags_are_recognised_regardless_of_prefix_or_suffix(self):
        for tag in ('0.1.1', 'v0.2.0', '1.0.0', '0.2.0-beta'):
            self.assertTrue(updates.is_newer(tag, '0.1.0'), tag)
        for tag in ('0.1.0', 'v0.1.0', '0.0.9', '0.1', ''):
            self.assertFalse(updates.is_newer(tag, '0.1.0'), tag)
        # A release without any leading number must not be read as an update.
        self.assertFalse(updates.is_newer('nightly', '0.1.0'))


class LookupTests(unittest.TestCase):
    def test_tag_is_returned_from_the_release_payload(self):
        with patch.object(updates.urllib.request, 'urlopen',
                          return_value=FakeResponse({'tag_name': 'v0.2.0'})):
            self.assertEqual(updates.latest_release(), 'v0.2.0')

    def test_plain_http_and_empty_tags_are_rejected(self):
        with patch.object(updates.urllib.request, 'urlopen',
                          return_value=FakeResponse({'tag_name': 'v9'}, url='http://example.invalid/')):
            with self.assertRaises(ValueError):
                updates.latest_release()
        with patch.object(updates.urllib.request, 'urlopen',
                          return_value=FakeResponse({'tag_name': '  '})):
            with self.assertRaises(ValueError):
                updates.latest_release()

    def test_lookup_targets_the_published_repository_over_https(self):
        self.assertTrue(updates.RELEASES_API.startswith('https://api.github.com/repos/'))
        for url in (updates.RELEASES_API, updates.RELEASES_PAGE, updates.NEW_ISSUE):
            self.assertIn(updates.REPOSITORY, url)
            self.assertTrue(url.startswith('https://'))


class MissingReleaseTests(unittest.TestCase):
    def test_a_draft_release_reads_as_nothing_published_not_a_failure(self):
        import urllib.error
        missing = urllib.error.HTTPError(updates.RELEASES_API, 404, 'Not Found', {}, None)
        with patch.object(updates.urllib.request, 'urlopen', side_effect=missing):
            self.assertIsNone(updates.latest_release())

    def test_other_http_errors_still_raise(self):
        import urllib.error
        refused = urllib.error.HTTPError(updates.RELEASES_API, 403, 'rate limited', {}, None)
        with patch.object(updates.urllib.request, 'urlopen', side_effect=refused):
            with self.assertRaises(urllib.error.HTTPError):
                updates.latest_release()


class ReportTests(unittest.TestCase):
    def test_report_link_opens_the_projects_own_form(self):
        url = updates.report_url()
        self.assertTrue(url.startswith(updates.NEW_ISSUE + '?'))
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['template'], [updates.TEMPLATE])
        # The form asks its own questions; nothing about this machine rides along.
        self.assertEqual(set(query), {'template'})
        for leaked in ('debug.log', 'crash.log', 'C:' + chr(92), '/Users/'):
            self.assertNotIn(leaked, url)

    def test_the_form_the_link_names_is_in_the_repository(self):
        template = Path(__file__).resolve().parents[2] / '.github/ISSUE_TEMPLATE' / updates.TEMPLATE
        self.assertTrue(template.is_file(), template)
