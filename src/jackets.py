"""Download and validate the official maimai jacket catalog."""
from i18n import tr

import io
import json
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event

from PIL import Image

MANIFEST_URL = "https://maimai.sega.jp/data/maimai_songs.json"
IMAGE_BASE = "https://maimaidx.jp/maimai-mobile/img/Music/"
REQUEST_INTERVAL = 0.5
TIMEOUT = 20


def _check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise CancelledError()


def _fetch(url: str, cancel: Event, limit: int, context=None, attempts=3, headers=None) -> bytes:
    for attempt in range(attempts):
        _check_cancel(cancel)
        request = urllib.request.Request(url, headers={"User-Agent": "maimai-clip/0.1", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
                if not response.geturl().startswith("https://"):
                    raise ValueError(tr('HTTPS 응답이 아닙니다.'))
                body = bytearray()
                while True:
                    _check_cancel(cancel)
                    chunk = response.read(min(65536, limit + 1 - len(body)))
                    if not chunk:
                        return bytes(body)
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ValueError(tr('다운로드 파일이 허용 크기를 초과했습니다.'))
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
            if isinstance(error, urllib.error.HTTPError) and error.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise
        except TimeoutError:
            if attempt == attempts - 1:
                raise
        if cancel.wait(attempt + 1):
            raise CancelledError()
    raise RuntimeError(tr('다운로드를 완료하지 못했습니다.'))


def _manifest(body: bytes) -> list[dict]:
    songs = json.loads(body)
    if not isinstance(songs, list) or not songs:
        raise ValueError(tr('공식 곡 목록이 비어 있거나 올바르지 않습니다.'))
    for song in songs:
        if not isinstance(song, dict):
            raise ValueError(tr('곡 정보가 올바르지 않습니다.'))
        name = song.get("image_url")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.png", name):
            raise ValueError(tr('자켓 파일명이 올바르지 않습니다.'))
        # The official catalog includes a song whose title is an ideographic space.
        if not isinstance(song.get("title"), str):
            raise ValueError(tr('곡 제목 형식이 올바르지 않습니다.'))
    return songs


def _verify_image(source) -> None:
    try:
        with Image.open(source) as image:
            if image.format != "PNG" or not (0 < image.width <= 2048 and 0 < image.height <= 2048):
                raise ValueError(tr('올바른 자켓 PNG 파일이 아닙니다.'))
            image.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError(tr('자켓 이미지가 손상되었습니다.')) from error


def _valid_image(path: Path) -> bool:
    try:
        _verify_image(path)
        return True
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False


def _atomic_write(path: Path, body: bytes) -> None:
    temporary = path.with_name(path.name + ".part")
    try:
        temporary.write_bytes(body)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def catalog_status(data_dir: Path) -> bool:
    """Return whether every jacket referenced by the saved catalog is valid."""
    directory = Path(data_dir) / "jackets"
    try:
        songs = _manifest((directory / "_manifest.json").read_bytes())
    except (OSError, ValueError):
        return False
    return all(_valid_image(directory / name) for name in {song["image_url"] for song in songs})


def catalog_summary(data_dir, cancel, report):
    """Count local song data in a worker, without decoding every jacket."""
    directory = Path(data_dir) / "jackets"
    manifest = directory / "_manifest.json"
    songs = _manifest(manifest.read_bytes()) if manifest.exists() else []
    jackets = size = 0
    for path in directory.rglob("*"):
        _check_cancel(cancel)
        if path.is_file():
            size += path.stat().st_size
            jackets += path.suffix.lower() == ".png"
    return len(songs), jackets, size


def download_jackets(data_dir: Path, cancel: Event, report: Callable[[str], None]) -> dict:
    """Run in a worker; cancellation preserves completed files for the next run.

    The caller starts at most one download for a data directory at a time.
    Socket operations use a timeout; cancellation is checked between reads.
    """
    result = dict(total=0, downloaded=0, skipped=0, failed=[], cancelled=False, complete=False)
    directory = Path(data_dir) / "jackets"
    try:
        _check_cancel(cancel)
        directory.mkdir(parents=True, exist_ok=True)
        report(tr('공식 곡 목록을 확인하고 있습니다…'))
        body = _fetch(MANIFEST_URL, cancel, 8 * 1024 * 1024)
        songs = _manifest(body)
        names = sorted({song["image_url"] for song in songs})
        result["total"] = len(names)
        _check_cancel(cancel)
        _atomic_write(directory / "_manifest.json", body)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        for index, name in enumerate(names, 1):
            _check_cancel(cancel)
            path = directory / name
            if _valid_image(path):
                result["skipped"] += 1
                report(tr('자켓 확인 {index:,} / {value2:,}', index=index, value2=len(names)))
                continue
            try:
                image = _fetch(IMAGE_BASE + name, cancel, 4 * 1024 * 1024, context, attempts=1)
                _verify_image(io.BytesIO(image))
                _check_cancel(cancel)
                _atomic_write(path, image)
                result["downloaded"] += 1
                report(tr('자켓 다운로드 {index:,} / {value2:,}', index=index, value2=len(names)))
            except (urllib.error.URLError, TimeoutError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
                result["failed"].append({"name": name, "error": str(error)})
                report(tr(
                    '자켓 다운로드 {index:,} / {value2:,} · 재시도 필요 {value3:,}',
                    index=index,
                    value2=len(names),
                    value3=len(result['failed']),
                ))
            if cancel.wait(REQUEST_INTERVAL):
                raise CancelledError()
        _check_cancel(cancel)
        result["complete"] = not result["failed"] and catalog_status(data_dir)
        report(tr('자켓 준비가 완료되었습니다.') if result["complete"] else tr('일부 자켓을 다시 받아 주세요.'))
    except CancelledError:
        result["cancelled"] = True
        report(tr('다운로드를 멈췄습니다.'))
    return result
