"""Offline guide checks: python run/check_manual.py (stdlib only)."""
from html.parser import HTMLParser
from itertools import combinations
from pathlib import Path
import math
import re
import struct

ROOT = Path(__file__).resolve().parents[1] / "docs/manual"


class Guide(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.assets, self.markers, self.images, self.topics = [], [], {}, {"ko": [], "en": []}
        self.article = ""
        self.menu = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        assert tag not in ('details', 'summary'), 'Instructions must stay visible'
        if tag == 'b' and 'data-msgid' in attrs:
            self.menu = attrs['data-msgid']
        if "id" in attrs:
            assert attrs["id"] not in self.ids, attrs["id"]
            self.ids.add(attrs["id"])
        if tag == "article":
            self.article = attrs["id"]
            self.topics[self.article[:2]].append(attrs["data-topic"])
        if tag in ("img", "script", "link"):
            path = attrs.get("src", attrs.get("href"))
            assert path and ":" not in path and not path.startswith("/"), path
            assert (ROOT / path).is_file(), path
            self.assets.append(path)
        if tag == "img":
            assert attrs.get("alt"), self.article
            width, height = struct.unpack(">II", (ROOT / attrs["src"]).read_bytes()[16:24])
            assert (width, height) == (int(attrs["width"]), int(attrs["height"]))
            self.images[self.article] = (attrs["src"], width, height)
        if "hotspot" in attrs.get("class", "").split():
            assert attrs.get("aria-label"), self.article
            box = list(map(float, attrs["data-box"].split(",")))
            x, y, width, height = box
            assert 0 <= x < x + width <= 100 and 0 <= y < y + height <= 100, box
            center = list(map(float, re.findall(r"[\d.]+", attrs["style"])))
            self.markers.append((self.article, attrs["href"][1:], center))

    def handle_data(self, data):
        if self.menu:
            from build_manual import catalog, messages
            key = messages[self.menu]
            expected = self.menu if self.article.startswith('ko-') else (catalog.ngettext(*key, 2) if len(key) == 2 else catalog.gettext(self.menu))
            assert data == expected.format(count='…'), (self.menu, data)

    def handle_endtag(self, tag):
        if tag == 'b':
            self.menu = None


if __name__ == "__main__":
    html = (ROOT / "manual.html").read_text(encoding="utf-8")
    guide = Guide()
    guide.feed(html)
    assert guide.topics["ko"] == guide.topics["en"] and len(guide.topics["ko"]) == 13
    assert guide.topics['ko'][:2] == ['overview', 'welcome']
    assert 'id="all" checked' in html and '{{' not in html
    assert '사진의 번호를 누르면' not in html
    for lang in ('ko', 'en'):
        review = html.split(f'id="{lang}-review"', 1)[1].split('</article>', 1)[0]
        assert re.findall(r'class="point-select"[^>]*>\(([^)]+)\)', review) == ['1', '2', '3', 'A', 'B', 'C']
        assert 'data-msgid="클립 시작·끝 일괄 조정"' in review
    from build_manual import build
    build()
    assert html == (ROOT / 'manual.html').read_text(encoding='utf-8'), 'Rebuild the guide'
    for article, target, center in guide.markers:
        assert target in guide.ids, target
        assert all(0 <= value <= 100 for value in center), center
    for topic in guide.topics["ko"]:
        ko, en = f"ko-{topic}", f"en-{topic}"
        assert guide.images.get(ko) == guide.images.get(en), topic
        korean = [(t[3:], c) for a, t, c in guide.markers if a == ko]
        english = [(t[3:], c) for a, t, c in guide.markers if a == en]
        assert korean == english, topic
    # At a 360px viewport, the page has 328px of content. Targets must not overlap.
    for article, (_, width, height) in guide.images.items():
        for content_width in (328, 768):
            scale = min(content_width, width) / width
            centers = [c for a, _, c in guide.markers if a == article]
            for a, b in combinations(centers, 2):
                distance = math.hypot((a[0] - b[0]) * width * scale / 100,
                                      (a[1] - b[1]) * height * scale / 100)
                assert distance >= 36, (article, a, b, distance)
    assert not re.search(r"ADHD|주의력결핍", html, re.I)
    assert "fetch(" not in (ROOT / "guide.js").read_text(encoding="utf-8")
    print(f"OK: 2 languages, 13 topics each, {len(guide.markers)} markers, current UI labels, reproducible build, visible text, local assets, non-overlapping targets.")
