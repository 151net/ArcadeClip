"""The bundled yt-dlp must resolve YouTube without loading the full extractor registry."""
from pathlib import Path
import subprocess
import sys
import unittest

import ytdlp_youtube

# Run in a subprocess: importing yt_dlp.extractor.extractors is a one-way change to sys.modules.
PROBE = """
import json, sys
from ytdlp_youtube import install
install()
import yt_dlp
ydl = yt_dlp.YoutubeDL({'quiet': True, 'ignoreconfig': True})
matches = {url: next((ie.IE_NAME for ie in ydl._ies.values() if ie.suitable(url)), None)
           for url in sys.argv[1:]}
print(json.dumps({
    'matches': matches,
    'registry': len(ydl._ies),
    'full_registry_loaded': 'yt_dlp.extractor._extractors' in sys.modules,
    'extractor_modules': sum(m.startswith('yt_dlp.extractor.') for m in sys.modules),
}))
"""


class YoutubeOnlyTests(unittest.TestCase):
    def probe(self, *urls):
        result = subprocess.run([sys.executable, '-c', PROBE, *urls], check=True, text=True,
                                capture_output=True, cwd=str(Path(ytdlp_youtube.__file__).parent))
        import json
        return json.loads(result.stdout)

    def test_youtube_urls_resolve_without_the_full_registry(self):
        report = self.probe('https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                            'https://youtu.be/dQw4w9WgXcQ',
                            'https://www.youtube.com/playlist?list=PLexample')
        self.assertFalse(report['full_registry_loaded'])
        self.assertEqual(report['matches']['https://www.youtube.com/watch?v=dQw4w9WgXcQ'], 'youtube')
        self.assertEqual(report['matches']['https://youtu.be/dQw4w9WgXcQ'], 'youtube')
        self.assertEqual(report['matches']['https://www.youtube.com/playlist?list=PLexample'], 'youtube:tab')
        # Roughly twenty YouTube extractors plus the generic fallback, not the full catalogue.
        self.assertLess(report['registry'], 60)
        self.assertLess(report['extractor_modules'], 100)

    def test_lookup_keeps_youtube_and_the_generic_fallback(self):
        lookup = ytdlp_youtube.class_lookup()
        self.assertIn('YoutubeIE', lookup)
        self.assertIn('YoutubeTabIE', lookup)
        self.assertIn('GenericIE', lookup)
        self.assertTrue(all(name.endswith('IE') for name in lookup))
