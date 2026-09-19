"""Download and validate the official maimai jacket catalog."""
from i18n import tr

import io
import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from threading import Event

from PIL import Image

MANIFEST_URL = "https://maimai.sega.jp/data/maimai_songs.json"
IMAGE_BASE = "https://maimaidx.jp/maimai-mobile/img/Music/"
REQUEST_INTERVAL = 0.5
TIMEOUT = 20
DOWNLOAD_WORKERS = 3
OTOGE_MANIFEST = "https://otoge-db.net/maimai/data/maimai_songs.json"
OTOGE_IMAGES = "https://otoge-db.net/maimai/jacket/"
# Offered in the settings; the first is the official one and needs no certificate check.
PRESETS = (("maimai.sega.jp", MANIFEST_URL, IMAGE_BASE),
           ("otoge-db.net", OTOGE_MANIFEST, OTOGE_IMAGES))
OFFICIAL_SOURCE = urllib.parse.urlsplit(IMAGE_BASE).hostname
ALL_SOURCES = ""
SELECTION = "selected.json"


def relaxed_context():
    """The official artwork server sends an incomplete chain, and pinning a bundle of our
    own would break again whenever it is renewed. Song artwork is public, and every file
    is checked as an image before it is kept, so fetch it without verifying the chain."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def normalize_source(source) -> dict:
    """Fill in the official addresses and settle the certificate choice."""
    source = dict(source or {})
    addresses = {}
    for key, fallback in (("manifest", MANIFEST_URL), ("images", IMAGE_BASE)):
        value = str(source.get(key) or "").strip() or fallback
        if urllib.parse.urlsplit(value).scheme not in ("http", "https"):
            raise ValueError(tr('자켓 서버 주소는 http:// 또는 https://로 시작해야 합니다.'))
        addresses[key] = value
    if not addresses["images"].endswith("/"):
        addresses["images"] += "/"
    official = addresses["manifest"] == MANIFEST_URL and addresses["images"] == IMAGE_BASE
    verify = source.get("verify")
    # The official servers are the known-broken case; another server is the caller's own.
    return {**addresses, "verify": (not official) if verify is None else bool(verify)}


def source_from(settings) -> dict:
    """Read the addresses saved in application settings."""
    stored = settings.value("jacket_verify")
    if isinstance(stored, str):
        stored = stored.lower() in ("true", "1")
    return normalize_source({"manifest": settings.value("jacket_manifest"),
                             "images": settings.value("jacket_images"),
                             "verify": stored})


def save_source(settings, source) -> dict:
    source = normalize_source(source)
    settings.setValue("jacket_manifest", source["manifest"])
    settings.setValue("jacket_images", source["images"])
    settings.setValue("jacket_verify", source["verify"])
    return source


def _check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise CancelledError()


def _fetch(url: str, cancel: Event, limit: int, context=None, attempts=3, headers=None) -> bytes:
    for attempt in range(attempts):
        _check_cancel(cancel)
        request = urllib.request.Request(url, headers={"User-Agent": "maimai-clip/0.1", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
                # A custom server may be plain http; a secure one must not be downgraded.
                if urllib.parse.urlsplit(response.geturl()).scheme not in ("https", urllib.parse.urlsplit(url).scheme):
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


def source_name(images: str) -> str:
    """The folder a server's jackets are kept in: its host, so two servers never mix."""
    host = urllib.parse.urlsplit(images).hostname
    if not host or any(part in ("", ".", "..") for part in (host,)) or "/" in host:
        raise ValueError(tr('자켓 서버 주소는 http:// 또는 https://로 시작해야 합니다.'))
    return host


def migrate_legacy(data_dir: Path) -> str | None:
    """Earlier versions kept one flat folder, which held the official jackets."""
    root = Path(data_dir) / "jackets"
    if not (root / "_manifest.json").is_file():
        return None
    target = root / OFFICIAL_SOURCE
    target.mkdir(parents=True, exist_ok=True)
    for path in list(root.iterdir()):
        if path.is_file() and (path.suffix.lower() == ".png" or path.name == "_manifest.json"):
            path.replace(target / path.name)
    return OFFICIAL_SOURCE


def installed_sources(data_dir: Path) -> list[str]:
    """Folder names that hold a usable catalog, newest servers and all."""
    root = Path(data_dir) / "jackets"
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir()
                  if path.is_dir() and (path / "_manifest.json").is_file())


def selected_source(data_dir: Path) -> str:
    """Which stored data to recognize with: a folder name, or "" for all of them.

    Kept beside the data rather than in application settings so a worker process can
    read it. With nothing chosen, one installed server is used on its own and the
    official one wins when there are several.
    """
    root = Path(data_dir) / "jackets"
    installed = installed_sources(data_dir)
    try:
        chosen = json.loads((root / SELECTION).read_bytes()).get("use")
    except (OSError, ValueError, AttributeError):
        chosen = None
    if chosen == ALL_SOURCES or (isinstance(chosen, str) and chosen in installed):
        return chosen
    if len(installed) == 1:
        return installed[0]
    return OFFICIAL_SOURCE if OFFICIAL_SOURCE in installed else ALL_SOURCES


def select_source(data_dir: Path, use: str) -> None:
    root = Path(data_dir) / "jackets"
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write(root / SELECTION, json.dumps({"use": use}).encode())


def catalog_folders(data_dir: Path) -> list[Path]:
    """The folders a catalog is built from, following the current selection."""
    root = Path(data_dir) / "jackets"
    use = selected_source(data_dir)
    names = installed_sources(data_dir) if use == ALL_SOURCES else [use]
    folders = [root / name for name in names if (root / name / "_manifest.json").is_file()]
    # A folder that predates per-server storage still works, migrated or not.
    return folders or ([root] if (root / "_manifest.json").is_file() else [])


def _folder_status(directory: Path) -> bool:
    try:
        songs = _manifest((directory / "_manifest.json").read_bytes())
    except (OSError, ValueError):
        return False
    return all(_valid_image(directory / name) for name in {song["image_url"] for song in songs})


def catalog_status(data_dir: Path) -> bool:
    """Return whether every jacket the selected data refers to is valid."""
    folders = catalog_folders(data_dir)
    return bool(folders) and all(_folder_status(directory) for directory in folders)


def source_status(data_dir: Path, source=None) -> bool:
    """Return whether one server's own data is complete, whatever is selected for use."""
    source = normalize_source(source)
    return _folder_status(Path(data_dir) / "jackets" / source_name(source["images"]))


def catalog_summary(data_dir, cancel, report):
    """Count the selected song data in a worker, without decoding every jacket."""
    titles, jackets, size = set(), 0, 0
    for directory in catalog_folders(data_dir):
        manifest = directory / "_manifest.json"
        songs = _manifest(manifest.read_bytes()) if manifest.exists() else []
        # Servers list the same songs, so count each song once across the chosen folders.
        titles.update((song["image_url"], song["title"]) for song in songs)
        for path in directory.rglob("*"):
            _check_cancel(cancel)
            if path.is_file():
                size += path.stat().st_size
                jackets += path.suffix.lower() == ".png"
    return len(titles), jackets, size


def download_jackets(data_dir: Path, cancel: Event, report: Callable[[str], None],
                     source=None, workers: int = DOWNLOAD_WORKERS) -> dict:
    """Run in a worker; cancellation preserves completed files for the next run.

    The caller starts at most one download for a data directory at a time.
    A few jackets are fetched at once, each thread pausing between its own requests.
    Socket operations use a timeout; cancellation is checked between reads.
    """
    result = dict(total=0, downloaded=0, skipped=0, failed=[], cancelled=False, complete=False)
    source = normalize_source(source)
    migrate_legacy(data_dir)
    result["source"] = source_name(source["images"])
    directory = Path(data_dir) / "jackets" / result["source"]
    try:
        _check_cancel(cancel)
        directory.mkdir(parents=True, exist_ok=True)
        report(tr('공식 곡 목록을 확인하고 있습니다…'))
        context = None if source["verify"] else relaxed_context()
        body = _fetch(source["manifest"], cancel, 8 * 1024 * 1024, context)
        songs = _manifest(body)
        names = sorted({song["image_url"] for song in songs})
        result["total"] = len(names)
        _check_cancel(cancel)
        _atomic_write(directory / "_manifest.json", body)
        lock = threading.Lock()
        done = 0

        def record(kind, failure=None):
            nonlocal done
            with lock:
                done += 1
                index = done
                if failure is not None:
                    result["failed"].append(failure)
                else:
                    result[kind] += 1
                pending = len(result["failed"])
            if kind == "skipped":
                return report(tr('자켓 확인 {index:,} / {value2:,}', index=index, value2=len(names)))
            if pending:
                return report(tr(
                    '자켓 다운로드 {index:,} / {value2:,} · 재시도 필요 {value3:,}',
                    index=index,
                    value2=len(names),
                    value3=pending,
                ))
            report(tr('자켓 다운로드 {index:,} / {value2:,}', index=index, value2=len(names)))

        def collect(name):
            _check_cancel(cancel)
            path = directory / name
            if _valid_image(path):
                return record("skipped")
            try:
                image = _fetch(source["images"] + name, cancel, 4 * 1024 * 1024, context, attempts=1)
                _verify_image(io.BytesIO(image))
                _check_cancel(cancel)
                _atomic_write(path, image)
                record("downloaded")
            except (urllib.error.URLError, TimeoutError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
                record("failed", {"name": name, "error": str(error)})
            if cancel.wait(REQUEST_INTERVAL):
                raise CancelledError()

        count = max(1, min(int(workers), 8))
        pool = ThreadPoolExecutor(max_workers=count, thread_name_prefix="jacket")
        try:
            for future in [pool.submit(collect, name) for name in names]:
                future.result()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        _check_cancel(cancel)
        # The download is judged on its own folder, whatever the recognizer is set to use.
        result["complete"] = not result["failed"] and _folder_status(directory)
        report(tr('자켓 준비가 완료되었습니다.') if result["complete"] else tr('일부 자켓을 다시 받아 주세요.'))
    except CancelledError:
        result["cancelled"] = True
        report(tr('다운로드를 멈췄습니다.'))
    return result
