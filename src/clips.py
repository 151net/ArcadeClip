"""Clip review and session validation shared by manual and detected ranges."""
from i18n import tr
from copy import deepcopy
import math
import re

from media import format_time
from sources import source_key


def apply_offsets(rows, originals, start_offset, end_offset, duration, *, skip_manual=False):
    """Build edited copies from detection originals; never compound prior offsets."""
    result, count = deepcopy(rows), 0
    for row in result:
        if row.get("custom") or (skip_manual and row.get("manual_bounds")):
            continue
        bases = [originals[key] for key in row.get("merged_clip_ids", [row["clip_id"]]) if key in originals]
        if not bases:
            continue
        start, end = offset_bounds(min(base["start"] for base in bases), max(base["end"] for base in bases),
                                   start_offset, end_offset, duration)
        row.pop("boundary_offsets", None)
        row.update(start=start, end=end, manual_bounds=False, edited=True, reviewed=False)
        count += 1
    return result, count


def merge_rows(rows):
    """Merge one source's working clips without changing its detection originals."""
    if len(rows) < 2 or len({source_key(row["source"]) for row in rows}) != 1:
        raise ValueError(tr('같은 영상의 클립을 두 개 이상 선택해 주세요.'))
    base = next((row for row in rows if not row.get("custom")), rows[0])
    merged = deepcopy(base)
    known = next((row for row in rows if row.get("song_id") or row.get("image_url")), base)
    if "song_id" in known:
        merged["song_id"] = known["song_id"]
    merged.update(title=known.get("title", ""), image_url=known.get("image_url"),
                  start=min(row["start"] for row in rows), end=max(row["end"] for row in rows),
                  manual_bounds=True, edited=True, reviewed=False,
                  selected=any(row.get("selected", False) for row in rows))
    merged["merged_clip_ids"] = list(dict.fromkeys(clip_id for row in rows if not row.get("custom")
                                                  for clip_id in row.get("merged_clip_ids", [row["clip_id"]])))
    for key in ("player", "player2", "achievement", "achievement2"):
        values = {row[key] for row in rows if row.get(key) is not None}
        if len(values) == 1:
            merged[key] = values.pop()
        else:
            merged.pop(key, None)
    merged.pop("ocr_evidence", None)
    return validate_clip(merged)


def validate_clip(row):
    if (not isinstance(row, dict) or not isinstance(row.get("source"), str) or not row["source"]
            or any(type(row.get(key)) not in (int, float) or not math.isfinite(row[key])
                   for key in ("start", "end")) or not 0 <= row["start"] < row["end"]):
        raise ValueError(tr('저장된 구간 형식을 확인해 주세요.'))
    for key in ("title", "player", "player2"):
        if key in row and (not isinstance(row[key], str) or len(row[key]) > 300):
            raise ValueError(tr('곡명과 플레이어 이름을 확인해 주세요.'))
    if row.get("song_id") is not None and (not isinstance(row["song_id"], str) or not row["song_id"]):
        raise ValueError(tr('곡 식별자를 확인해 주세요.'))
    for key in ("achievement", "achievement2"):
        if row.get(key) is not None and (type(row[key]) not in (float, int)
                                         or not math.isfinite(row[key]) or not 0 <= row[key] <= 101):
            raise ValueError(tr('달성률을 확인해 주세요.'))
    if row.get("image_url") is not None and (not isinstance(row["image_url"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+\.png", row["image_url"])):
        raise ValueError(tr('자켓 파일명을 확인해 주세요.'))
    for key in ("selected", "reviewed", "partial_start", "partial_end", "edited", "custom", "manual_bounds"):
        if key in row and type(row[key]) is not bool:
            raise ValueError(tr('클립 검수 상태를 확인해 주세요.'))
    if "clip_id" in row and (not isinstance(row["clip_id"], str) or not row["clip_id"]):
        raise ValueError(tr('클립 식별자를 확인해 주세요.'))
    if "merged_clip_ids" in row and (not isinstance(row["merged_clip_ids"], list)
            or any(not isinstance(value, str) or not value for value in row["merged_clip_ids"])):
        raise ValueError(tr('합친 클립의 식별자를 확인해 주세요.'))
    offsets = row.get("boundary_offsets", [0, 0])
    if (not isinstance(offsets, list) or len(offsets) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in offsets)):
        raise ValueError(tr('클립 오프셋을 확인해 주세요.'))
    return deepcopy(row)


def achievement_text(value):
    return f"{value:.4f}" if value is not None else tr('미확인')


def offset_bounds(start, end, start_offset, end_offset, duration):
    limit = duration if duration and duration > 0 else math.inf
    start = max(0, min(limit, start + start_offset))
    end = max(0, min(limit, end + end_offset))
    if start >= end:
        raise ValueError(tr('오프셋 적용 후 길이가 0 이하인 클립이 있습니다.'))
    return start, end


def label(row):
    title = row.get("title") or tr('곡 미확인')
    names = "/".join(f"{row.get(player) or tr('이름 미확인')}({achievement_text(row.get(score))})"
                     for player, score in (("player", "achievement"), ("player2", "achievement2"))
                     if player == "player" or row.get(player) or row.get(score) is not None)
    return f"{title} · {names}\n{format_time(row['start'])} – {format_time(row['end'])}"
