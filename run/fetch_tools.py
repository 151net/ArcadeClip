"""Place the pinned FFmpeg build in the tools folder, checking every hash on the way."""
import argparse
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

LOCK = Path(__file__).with_name("tools.lock.json")
# Deno comes from its Python package, so the build script fetches it separately.
PACKAGE_SUPPLIED = {"deno.exe"}
# Notices the licence collector reads out of the tools folder, renamed to say where they came from.
NOTICES = {"LICENSE": "FFMPEG-LICENSE.txt", "README.txt": "FFMPEG-README.txt"}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _up_to_date(directory, expected):
    return all((directory / name).is_file() and _digest((directory / name).read_bytes()) == digest
               for name, digest in expected.items())


def fetch(directory):
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    expected = {name: digest for name, digest in lock["files"].items() if name not in PACKAGE_SUPPLIED}
    directory.mkdir(parents=True, exist_ok=True)
    if _up_to_date(directory, expected) and all((directory / name).is_file() for name in NOTICES.values()):
        print(f"Pinned FFmpeg {lock['ffmpeg_release']} already in {directory}")
        return

    url = lock["ffmpeg_archive_url"]
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "arcadeclip-build"})
    with urllib.request.urlopen(request, timeout=300) as response:
        archive = response.read()
    if _digest(archive) != lock["ffmpeg_archive_sha256"]:
        raise ValueError("Archive checksum does not match the lock file.")

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        members = {Path(item.filename).name: item for item in bundle.infolist() if not item.is_dir()}
        for name, digest in expected.items():
            if name not in members:
                raise ValueError(f"{name}: missing from the archive")
            payload = bundle.read(members[name])
            if _digest(payload) != digest:
                raise ValueError(f"{name}: checksum does not match the lock file")
            (directory / name).write_bytes(payload)
        for source, target in NOTICES.items():
            if source in members:
                (directory / target).write_bytes(bundle.read(members[source]))

    print(f"Fetched FFmpeg {lock['ffmpeg_release']}: {len(expected)} files in {directory}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "tools")
    fetch(parser.parse_args().directory)
