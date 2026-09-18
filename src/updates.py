"""Release lookup for the public repository and a prefilled problem report link."""
from i18n import tr

import itertools
import json
import platform
import ssl
import urllib.parse
import urllib.request

VERSION = "0.1.0"
REPOSITORY = "151net/ArcadeClip"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
NEW_ISSUE = f"https://github.com/{REPOSITORY}/issues/new"
TIMEOUT = 10
LIMIT = 1 << 20


def _numbers(tag):
    # Compare leading numeric parts only, so a suffix such as "-beta" cannot break the check.
    parts = []
    for piece in tag.strip().lstrip("vV").split("."):
        digits = "".join(itertools.takewhile(str.isdigit, piece))
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(tag, current=VERSION):
    return bool(_numbers(tag)) and _numbers(tag) > _numbers(current)


def latest_release(timeout=TIMEOUT):
    """Return the newest published release tag. Raises when the lookup does not succeed."""
    request = urllib.request.Request(RELEASES_API, headers={
        "User-Agent": f"ArcadeClip/{VERSION}",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        if not response.geturl().startswith("https://"):
            raise ValueError(tr('HTTPS 응답이 아닙니다.'))
        payload = json.loads(response.read(LIMIT))
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise ValueError(tr('공개된 버전을 찾지 못했습니다.'))
    return tag


def report_url():
    """A new-issue page carrying only the app version and the operating system."""
    body = "\n\n".join([
        tr('### 무엇을 하려고 했나요?'), "",
        tr('### 어떤 일이 일어났나요?'), "",
        tr('### 재현 방법'), "",
        f"---\nArcadeClip {VERSION} · {platform.platform()}"])
    return f"{NEW_ISSUE}?{urllib.parse.urlencode({'body': body})}"
