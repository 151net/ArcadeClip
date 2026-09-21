"""Release lookup for the public repository and a prefilled problem report link."""
from i18n import tr

import itertools
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
REPOSITORY = "151net/ArcadeClip"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
NEW_ISSUE = f"https://github.com/{REPOSITORY}/issues/new"
TEMPLATE = "bug_report.md"
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


def _read(request, timeout, context):
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        if not response.geturl().startswith("https://"):
            raise ValueError(tr('HTTPS 응답이 아닙니다.'))
        return response.read(LIMIT)


def latest_release(timeout=TIMEOUT):
    """The newest published release tag, or None when the project has published none.

    A draft is not a published release, so GitHub answers 404 for it. That is an
    answer, not a failure, and it must not read as a connection problem.
    """
    request = urllib.request.Request(RELEASES_API, headers={
        "User-Agent": f"ArcadeClip/{VERSION}",
        "Accept": "application/vnd.github+json"})

    def fetch(context):
        try:
            return _read(request, timeout, context)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    try:
        body = fetch(ssl.create_default_context())
    except urllib.error.URLError as error:
        # A machine that cannot build any chain would never see a new version at all,
        # and this lookup only reads a version number.
        if not isinstance(error.reason, ssl.SSLCertVerificationError):
            raise
        from jackets import relaxed_context
        body = fetch(relaxed_context())
    if body is None:
        return None
    tag = str(json.loads(body).get("tag_name") or "").strip()
    if not tag:
        raise ValueError(tr('공개된 버전을 찾지 못했습니다.'))
    return tag


def report_url():
    """A new issue on the project's own bug report form, which asks the questions."""
    return f"{NEW_ISSUE}?{urllib.parse.urlencode({'template': TEMPLATE})}"
