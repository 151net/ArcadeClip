"""Translation loading, reordering and plural forms without Qt or extra dependencies."""
import gettext
import io
import os
from pathlib import Path
import struct
from string import Formatter
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import i18n


class TranslationTests(unittest.TestCase):
    def test_shipped_english_catalog_covers_source_and_preserves_formatting(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "run"))
        try:
            from compile_messages import compile_catalog
            from extract_messages import extract
        finally:
            sys.path.pop(0)
        path = i18n.LOCALES / "en/LC_MESSAGES/arcadeclip.po"
        compiled = compile_catalog(path)
        self.assertEqual(compiled, path.with_suffix(".mo").read_bytes())
        translation = gettext.GNUTranslations(io.BytesIO(compiled))
        messages = extract(Path(__file__).resolve().parents[2])
        fields = lambda text: sorted((name, spec, conversion) for _, name, spec, conversion
                                     in Formatter().parse(text) if name is not None)
        for texts in messages:
            for count in (0, 1, 2, 2000):
                translated = (translation.ngettext(*texts, count) if len(texts) == 2
                              else translation.gettext(texts[0]))
                with self.subTest(message=texts[0], count=count):
                    if texts[0] == "Settings":
                        self.assertEqual(translated, "Settings")
                    else:
                        self.assertNotEqual(translated, texts[0])
                    if texts[0] != "Language":
                        self.assertNotRegex(translated, "[가-힣]")
                    self.assertEqual(fields(texts[0]), fields(translated))
                    self.assertEqual(texts[0].count("\n"), translated.count("\n"))
        self.assertEqual(translation.ngettext("{count:,}곡", "{count:,}곡", 1).format(count=1), "1 song")
        self.assertEqual(translation.ngettext("{count:,}곡", "{count:,}곡", 2000).format(count=2000), "2,000 songs")
        self.assertEqual(translation.gettext("편집 작업 저장"), "Save session")
        self.assertEqual(translation.gettext("파일 저장"), "Export clips")
        for language, expected in (("en", "Export clips"), ("en_US", "Export clips"), ("ko", "파일 저장")):
            result = subprocess.check_output(
                [sys.executable, "-c", "from i18n import tr; print(tr('파일 저장'))"],
                env={**os.environ, "ARCADECLIP_LANGUAGE": language, "PYTHONIOENCODING": "utf-8",
                     "PYTHONPATH": str(i18n.LOCALES.parent)}, encoding="utf-8")
            self.assertEqual(result.strip(), expected)

    def test_real_gettext_catalog_fallback_placeholders_and_plurals(self):
        messages = {
            "": "Content-Type: text/plain; charset=UTF-8\nPlural-Forms: nplurals=2; plural=(n != 1);\n",
            "{title} · {count}개": "{count} clips: {title}",
            "{count:,}곡\0{count:,}곡": "{count:,} song\0{count:,} songs",
        }
        pairs = sorted((k.encode(), v.encode()) for k, v in messages.items())
        original = b"".join(k + b"\0" for k, _ in pairs)
        translated = b"".join(v + b"\0" for _, v in pairs)
        count = len(pairs)
        header = struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
        tables = b""
        for column, base in ((0, 28 + count * 16), (1, 28 + count * 16 + len(original))):
            for pair in pairs:
                tables += struct.pack("<2I", len(pair[column]), base)
                base += len(pair[column]) + 1
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "en/LC_MESSAGES/arcadeclip.mo"
            catalog.parent.mkdir(parents=True)
            catalog.write_bytes(header + tables + original + translated)
            translation = gettext.translation("arcadeclip", directory, languages=["en"])
            with patch.object(i18n, "_translation", translation):
                self.assertEqual(i18n.tr("{title} · {count}개", title="A", count=2), "2 clips: A")
                self.assertEqual(i18n.trn("{count:,}곡", "{count:,}곡", 1), "1 song")
                self.assertEqual(i18n.trn("{count:,}곡", "{count:,}곡", 2000), "2,000 songs")
                self.assertEqual(i18n.tr("번역 없는 문구"), "번역 없는 문구")
