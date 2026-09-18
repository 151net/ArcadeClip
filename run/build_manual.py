"""Build the offline bilingual guide from authored prose and current UI messages."""
import gettext
from html import escape
import io
import json
from pathlib import Path
import re
import struct

from compile_messages import compile_catalog
from extract_messages import extract

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / 'docs/manual'
catalog = gettext.GNUTranslations(io.BytesIO(compile_catalog(ROOT / 'src/locales/en/LC_MESSAGES/arcadeclip.po')))
messages = {key[0]: key for key in extract(ROOT)}


def prose(text, lang):
    def menu(match):
        msgid = match[1]
        assert msgid in messages, f'UI label no longer exists: {msgid}'
        key = messages[msgid]
        label = msgid if lang == 'ko' else (catalog.ngettext(*key, 2) if len(key) == 2 else catalog.gettext(msgid))
        return f'<b data-msgid="{escape(msgid)}">{escape(label.format(count="…"))}</b>'
    # Tokens can contain a single-braced formatting field, such as {count}.
    return re.sub(r'\{\{((?:[^{}]|\{[^{}]*\})+)\}\}', menu, escape(text))


groups = {
    'video': [(3, ('파일 또는 폴더로 열기 · 둘 중 하나', 'Open files or a folder · Choose one'), [1, 2]), (3, ('열린 영상 확인', 'Check the opened video'), [3, 4])],
    'range': [(3, ('시간 범위 지정 · 세 방법 중 하나', 'Set the range · Choose one method'), [1, 2, 3]), (3, ('다음 화면으로', 'Continue'), [4])],
    'regions': [(3, ('프로필 고르기 또는 새 프로필 만들기', 'Choose or create a profile'), [1]), (4, ('새 프로필 만들기', 'Create a new profile'), [2, 3]), (3, ('인식 확인과 곡 찾기', 'Test recognition and find songs'), [4, 5])],
    'review': [(3, ('저장할 클립 선택하기', 'Select clips to save'), [1, 2, 3]), (3, ('클립 수정하기 · 선택 사항', 'Edit clips · Optional'), [4, 5, 6])],
}
centers = {('range', 2): (25, 94), ('range', 3): (50, 75), ('regions', 2): (94, 32), ('regions', 5): (94, 94), ('youtube', 1): (94, 32), ('youtube', 2): (25, 44), ('youtube', 3): (75, 54), ('youtube', 4): (15, 64), ('session', 1): (84, 5), ('recognition', 2): (60, 25)}
faq = [
    ('곡 데이터가 없다고 나오는 경우 / 신곡이 나왔을 경우', 'Song data is missing / A new song was released', '{{Settings}} → {{곡 데이터 업데이트}}를 누르세요. 다운로드가 끝나면 다시 곡을 찾으세요.', 'Open {{Settings}} → {{곡 데이터 업데이트}}. When the download finishes, find songs again.'),
    ('클립이 안 보여요', 'No clips are visible', '검색어를 지우고 {{곡 미확인 구간 표시}}를 켜세요. {{현재 영상}}이 맞는지 확인하세요.', 'Clear the search and enable {{곡 미확인 구간 표시}}. Check {{현재 영상}}.'),
    ('저장 후 시작·끝이 달라요', 'Exported boundaries shifted', '{{저장 설정}}에서 {{정확한 구간 자르기}}를 고르세요.', 'Choose {{정확한 구간 자르기}} in {{저장 설정}}.'),
    ('이름이 틀리게 나와요', 'Player names are wrong', '{{1P 이름}} 영역이 글자를 정확히 감싸는지 확인하세요. 이름을 고친 뒤 {{수정 적용}}을 누를 수도 있습니다.', 'Check that the {{1P 이름}} region frames the text. You can also edit the name and click {{수정 적용}}.'),
    ('설명서 열기·공유', 'Open or share this guide', '앱의 {{도움말}} 또는 F1을 누르면 별도 창으로 열립니다. Windows·macOS·Linux의 브라우저에서 manual.html을 열어도 됩니다. 공유할 때는 manual 폴더 전체를 보내세요.', 'Click {{도움말}} or press F1 in the app to open this guide in a separate window. You can also open manual.html in a browser on Windows, macOS, or Linux. Share the whole manual folder.'),
]


REGION = '<rect x="8" y="8" width="92" height="92" fill="none" stroke="#075aa6" stroke-width="1.5" stroke-dasharray="5 4"/>'
JACKET_CASES = [
    ('fit', ('정사각형', 'Square'), ('인식 가능', 'Recognized')),
    ('skew', ('찌그러짐', 'Skewed'), ('인식 불가', 'Not recognized')),
    ('cut', ('잘림', 'Cut off'), ('인식 불가', 'Not recognized')),
]
JACKET_TITLES = {
    'fit': ('인식 영역 안에 정사각형으로 들어온 자켓', 'A square jacket inside the region'),
    'skew': ('가로세로 비율이 맞지 않아 찌그러진 자켓', 'A jacket squeezed out of square'),
    'cut': ('인식 영역 밖으로 나가 잘린 자켓', 'A jacket cut off by the region edge'),
}
MARK_OK = '<circle cx="54" cy="122" r="10" fill="none" stroke="#2e7d32" stroke-width="4"/>'
MARK_NO = ('<g stroke="#c62828" stroke-width="4" stroke-linecap="round">'
           '<line x1="46" y1="114" x2="62" y2="130"/><line x1="62" y1="114" x2="46" y2="130"/></g>')
JACKET_SHAPES = {
    'fit': REGION + '<image href="jacket-example.png" x="20" y="20" width="68" height="68"/>'
           '<rect x="20" y="20" width="68" height="68" fill="none" stroke="#171717" stroke-width="2"/>' + MARK_OK,
    'skew': REGION + '<g transform="matrix(0.96 0.15 -0.06 0.95 8 -6)">'
            '<image href="jacket-example.png" x="22" y="22" width="64" height="64" preserveAspectRatio="none"/>'
            '<rect x="22" y="22" width="64" height="64" fill="none" stroke="#171717" stroke-width="2"/></g>' + MARK_NO,
    'cut': '<defs><clipPath id="{lang}-region-clip"><rect x="8" y="8" width="92" height="92"/></clipPath></defs>'
           '<g clip-path="url(#{lang}-region-clip)">'
           '<image href="jacket-example.png" x="-12" y="20" width="68" height="68"/>'
           '<rect x="-12" y="20" width="68" height="68" fill="none" stroke="#171717" stroke-width="2"/></g>'
           + REGION + MARK_NO,
}


def build():
    chapters = json.loads((MANUAL / 'content.json').read_text(encoding='utf-8'))
    parts = ['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ArcadeClip 사용 설명서 / User guide</title><link rel="icon" href="icon.png"><link rel="stylesheet" href="guide.css"><script src="guide.js" defer></script></head><body>',
             '<header><h1 data-ko="ArcadeClip 사용 설명서" data-en="ArcadeClip user guide">ArcadeClip 사용 설명서</h1>',
             '<div id="tools" hidden><label>한국어 / English <select id="language"><option value="ko">한국어</option><option value="en">English</option></select></label> <label><span data-ko="목차" data-en="Contents">목차</span> <select id="topic"></select></label> <label><input type="checkbox" id="all" checked><span data-ko="전체 보기" data-en="Show all">전체 보기</span></label> <button id="print" data-ko="인쇄" data-en="Print">인쇄</button></div>',
             '<nav id="navigation" hidden><button id="prev" data-ko="이전" data-en="Previous">이전</button> <span id="progress" aria-live="polite"></span> <button id="next" data-ko="다음" data-en="Next">다음</button></nav></header>',
             '<noscript><p>한국어 설명 뒤에 English guide가 이어집니다. / The English guide follows the Korean guide.</p></noscript><main>']
    for idx, lang in enumerate(('ko', 'en')):
        def text(pair): return prose(pair[idx], lang)
        def extra(info):
            paragraphs = ''.join(f'<p>{prose(p, lang)}</p>' for p in info[idx+2].split('\n\n'))
            return f'<aside class="extra"><h4>{escape(info[idx])}</h4>{paragraphs}</aside>'
        parts.append(f'<section lang="{lang}" id="{lang}" aria-label="{text(("한국어 설명서", "English guide"))}">')
        parts.append(f'<article id="{lang}-overview" data-topic="overview"><h2 tabindex="-1">{text(("0. 어떻게 진행되나요?", "0. How does it work?"))}</h2><p>{text(("영상에서 곡을 찾고, 필요한 구간을 클립으로 저장합니다. 원본 영상은 그대로 유지됩니다.", "Find songs in a video, then save the sections you need as clips. The source video stays unchanged."))}</p><ol class="flow">')
        for topic, ko, en in [('video','영상 열기','Open video'),('range','시간 범위 지정','Set time range'),('regions','곡·글자 위치 지정 → 곡 찾기','Mark song/text regions → Find songs'),('review','클립 확인·저장 대상 확정','Review clips and confirm what to save'),('export','영상 파일 저장','Save video files')]:
            parts.append(f'<li><a data-topic-link="{topic}" href="#{lang}/{topic}/1">{text((ko,en))}</a></li>')
        parts.append(f'</ol><p>{text(("처음 실행한다면 다음 ‘환영합니다’부터 시작하세요. 이미 설정했다면 ‘영상 열기’로 이동하세요.", "On your first launch, start with Welcome below. If setup is complete, go to Open a video."))}</p>')
        parts.append(f'<h3>{text(("어떤 영상에서 곡을 찾을 수 있나요?", "Which videos does this work with?"))}</h3>')
        parts.append(f'<p>{text(("곡 인식은 화면의 자켓을 공식 자켓과 비교하는 방식입니다. 인식 영역 안에 자켓이 정사각형 그대로, 잘리지 않고 들어와야 인식됩니다.", "Songs are recognized by comparing the jacket on screen with the official art, so the jacket has to sit inside the region square and uncropped."))}</p>')
        parts.append('<ul class="cases">')
        for case, head, verdict in JACKET_CASES:
            parts.append(f'<li><figure><svg viewBox="0 0 108 136" role="img" aria-labelledby="{lang}-{case}-title">'
                         f'<title id="{lang}-{case}-title">{text(JACKET_TITLES[case])}</title>'
                         + JACKET_SHAPES[case].format(lang=lang) +
                         f'</svg><figcaption><b>{text(head)}</b>{text(verdict)}</figcaption></figure></li>')
        parts.append('</ul></article>')
        for topic, img, title, intro, points, info in chapters:
            root = f'{lang}-{topic}'
            labels = (['A', 'B', 'C', '1'] if topic == 'range' else
                      [str(n) if topic != 'review' or n <= 3 else chr(ord('A') + n - 4) for n in range(1, len(points)+1)])
            width, height = struct.unpack('>II', (MANUAL / 'images' / img).read_bytes()[16:24])
            parts.append(f'<article id="{root}" data-topic="{topic}"><h2 tabindex="-1">{text(title)}</h2><p>{text(intro)}</p><div class="walkthrough"><figure><div class="shot" style="max-width:{width}px"><img src="images/{img}" width="{width}" height="{height}" alt="{escape(title[idx])} — English UI"><div class="highlight" hidden aria-hidden="true"></div>')
            for n, (box, head, _) in enumerate(points, 1):
                x, y = centers.get((topic, n), (box[0]+2, box[1]+2))
                parts.append(f'<a class="hotspot" href="#{root}-{n}" data-point="{n}" data-box="{",".join(map(str,box))}" style="left:{x}%;top:{y}%" aria-label="{labels[n-1]}. {escape(head[idx])}">{labels[n-1]}</a>')
            parts.append(f'</div><figcaption><a href="images/{img}" target="_blank" rel="noopener">{text(("사진 크게 보기", "Open full-size image"))}</a></figcaption></figure><div class="instructions">')
            for level, heading, numbers in groups.get(topic, [(3, ('',''), list(range(1,len(points)+1)))]):
                if heading[idx]: parts.append(f'<h{level}>{text(heading)}</h{level}>')
                for n in numbers:
                    _, head, body = points[n-1]
                    paragraphs = ''.join(f'<p>{prose(p, lang)}</p>' for p in body[idx].split('\n\n'))
                    parts.append(f'<section class="point" id="{root}-{n}"><button class="point-select" data-point="{n}">({labels[n-1]}) {text(head)}</button>{paragraphs}</section>')
                if topic == 'regions' and numbers == [2,3]: parts.append(extra(info))
            if topic != 'regions' and info: parts.append(extra(info))
            if topic == 'video': parts.append(f'<a data-topic-link="youtube" href="#{lang}/youtube/1">{text(("YouTube 구간 가져오기 →", "Download a YouTube range →"))}</a>')
            parts.append('</div></div></article>')
        parts.append(f'<article id="{lang}-help" data-topic="help"><h2 tabindex="-1">{text(("문제 해결·단축키", "Troubleshooting and shortcuts"))}</h2>')
        for info in faq: parts.append(extra(info))
        parts.append('<table><caption>'+text(('앱에서 사용하는 키','Keys used in the app'))+'</caption><thead><tr><th>'+text(('키','Key'))+'</th><th>'+text(('동작','Action'))+'</th></tr></thead><tbody>')
        for key, ko, en in [
            ('F1','도움말 열기','Open Help'),
            ('Space / K','재생·일시정지 / 일시정지','Play/pause / pause'),
            ('← / →','재생 중 1초 이동 · 일시정지 중 한 프레임 이동','Seek 1 second while playing; step one frame while paused'),
            ('Shift + ← / →','1초 이동','Seek 1 second'),
            ('Ctrl + ← / →','5초 이동','Seek 5 seconds'),
            ('J / L','뒤로 탐색 / 앞으로 재생 · 반복하면 속도 증가','Seek backward / play forward; repeat to increase speed'),
            ('Home / End','영상 처음 / 끝으로','Go to the start / end of the video'),
            ('I / O','현재 위치를 구간 시작 / 끝으로','Set selection start / end to the playhead'),
            ('Alt + ← / →','구간 시작 / 끝으로 이동','Go to the selection start / end'),
            ('Ctrl + drag','시간축: 구간 길이를 유지하며 이동 · 영상: 화면 이동','Timeline: move the selection without changing its length; video: pan'),
            ('Alt + drag','다른 경계에 맞추지 않고 구간 조정','Adjust the selection without snapping to other boundaries'),
            ('Ctrl / Shift + click','클립 개별 / 연속 선택','Select individual clips / a range of clips'),
            ('A / Delete','클립 직접 추가 / 목록에서 제거','Add a clip manually / remove from the list'),
            ('+ / − / 0','시간축 확대 / 축소 / 전체 보기','Zoom the timeline in / out / fit'),
            ('wheel / Shift + wheel','시간축 가로 이동 / 빠르게 이동','Scroll the timeline / scroll faster'),
            ('Ctrl + wheel','앱 영상·시간축 확대·축소','Zoom the video or timeline in the app'),
            ('Ctrl + + / −','인식 영역 화면의 영상 확대·축소','Zoom the video in Recognition regions'),
            ('Ctrl + 0 / middle click','인식 영역 화면의 영상 맞춤','Fit the video in Recognition regions'),
            ('Ctrl + S','작업 JSON 저장','Save the session JSON'),
            ('Ctrl + E','체크한 클립의 저장 설정 열기','Open export settings for checked clips'),
            ('Ctrl + Z','직전 클립 편집 되돌리기','Undo the last clip edit'),
        ]:
            parts.append(f'<tr><td><kbd>{escape(key)}</kbd></td><td>{text((ko,en))}</td></tr>')
        parts.append('</tbody></table><p>'+text(('문자 입력 중에는 입력란의 편집 단축키가 우선합니다.', 'While typing, text-field editing shortcuts take priority.'))+'</p></article></section>')
    parts.append('</main><footer><small>ArcadeClip</small></footer></body></html>')
    (MANUAL / 'manual.html').write_text('\n'.join(parts)+'\n', encoding='utf-8')


if __name__ == '__main__':
    build()
    print('Built docs/manual/manual.html from authored text and current UI translations.')
