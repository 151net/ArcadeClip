"""Portable normalized recognition regions, independent of the desktop UI."""
from i18n import tr
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

REGIONS = {"jacket": tr('자켓'), "achievement": tr('1P 달성률'), "player": tr('1P 이름'), "player2": tr('2P 이름'), "achievement2": tr('2P 달성률')}


def require_regions(regions):
    missing = [REGIONS[key] for key in ("jacket", "achievement", "player") if key not in regions]
    if missing:
        raise ValueError(tr('필수 영역을 지정해 주세요: ') + ", ".join(missing))


def validate_profile(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError(tr('프로파일 형식을 확인해 주세요.'))
    name = value.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError(tr('프로파일 이름을 입력해 주세요.'))
    size = value.get("size")
    if (not isinstance(size, list) or len(size) != 2
            or any(type(n) is not int or n <= 0 for n in size)):
        raise ValueError(tr('기준 영상 크기를 확인해 주세요.'))
    regions = value.get("regions")
    if not isinstance(regions, dict) or "jacket" not in regions:
        raise ValueError(tr('자켓 영역을 선택해 주세요.'))
    for key, rect in regions.items():
        if key not in REGIONS or not isinstance(rect, list) or len(rect) != 4:
            raise ValueError(tr('인식 영역 형식을 확인해 주세요.'))
        if any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1
               for n in rect):
            raise ValueError(tr('인식 영역 좌표를 확인해 주세요.'))
        if (rect[2] - rect[0]) * size[0] < 2 or (rect[3] - rect[1]) * size[1] < 2:
            raise ValueError(tr('인식 영역을 조금 더 크게 선택해 주세요.'))
    result = {"version": 1, "name": name.strip(), "size": list(size),
              "regions": {key: list(rect) for key, rect in regions.items()}}
    if "recognition" in value:
        options = value["recognition"]
        limits = {"sample_seconds": (.1, 5), "stable_seconds": (.1, 10),
                  "hash_distance": (0, 64), "hash_margin": (0, 64), "ocr_tail_seconds": (.1, 10), "location_seconds": (1, 120)}
        if not isinstance(options, dict) or set(options) - (set(limits) | {"ocr_language"}):
            raise ValueError(tr('인식 설정 형식을 확인해 주세요.'))
        for key, (low, high) in limits.items():
            if key in options and (type(options[key]) not in (int, float)
                                   or not math.isfinite(options[key]) or not low <= options[key] <= high):
                raise ValueError(tr('인식 설정 범위를 확인해 주세요.'))
        # Profiles shared before the reader was fixed to one model may carry a language.
        result["recognition"] = {key: value for key, value in options.items() if key != "ocr_language"}
    if "jacket_offset" in value:
        offset = value["jacket_offset"]
        if not isinstance(offset, dict) or offset.get("version") != 1:
            raise ValueError(tr('자켓 보정 캐시 형식을 확인해 주세요.'))
        rect = offset.get("rect")
        if (not isinstance(rect, list) or len(rect) != 4 or
                any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in rect)
                or not rect[0] < rect[2] or not rect[1] < rect[3]
                or any(type(offset.get(k)) is not int or not 0 <= offset[k] <= 64 for k in ("distance", "margin"))):
            raise ValueError(tr('자켓 보정 캐시 좌표·점수를 확인해 주세요.'))
        if offset.get("roi") == regions["jacket"]:
            result["jacket_offset"] = dict(offset)
    return result


def offset_path(directory, profile):
    identity = {key: profile[key] for key in ("name", "size")}
    identity["jacket"] = profile["regions"]["jacket"]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    return Path(directory) / "profiles" / f"offset-{key}.json"


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def load_profile(path: Path):
    return validate_profile(json.loads(path.read_text(encoding="utf-8")))


def read_profile_import(path):
    """Validate before writing; archive names never become output paths."""
    import zipfile
    from pathlib import PurePosixPath
    limit = 2 * 1024 * 1024
    try:
        if path.suffix.lower() != ".zip":
            if path.stat().st_size > limit:
                raise ValueError(tr('프로필 파일이 너무 큽니다.'))
            return [load_profile(path)]
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 200 or sum(item.file_size for item in entries) > 20 * limit:
                raise ValueError(tr('압축 파일의 크기 또는 파일 수가 너무 큽니다.'))
            profiles = []
            for item in entries:
                name = PurePosixPath(item.filename.replace("\\", "/"))
                if (name.is_absolute() or ".." in name.parts or ":" in item.filename
                        or (item.external_attr >> 16) & 0o170000 == 0o120000 or item.flag_bits & 1):
                    raise ValueError(tr('안전하지 않은 경로 또는 암호화된 파일이 있습니다.'))
                if item.is_dir() or name.suffix.lower() != ".json":
                    continue
                if item.file_size > limit:
                    raise ValueError(tr('압축 안의 프로필 파일이 너무 큽니다.'))
                profiles.append(validate_profile(json.loads(archive.read(item).decode("utf-8-sig"))))
            if not profiles:
                raise ValueError(tr('압축 파일에 유효한 프로필 JSON이 없습니다.'))
            return profiles
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, RecursionError, NotImplementedError) as error:
        raise ValueError(tr('프로필 압축 파일을 읽을 수 없습니다.')) from error
