"""Chapter marks for a whole recording, for YouTube's description and for the file itself.

Two places call a section of a video a chapter. YouTube reads timestamps from the
description; a media file carries them as metadata, which FFmpeg writes from its
own FFMETADATA format. Both are built from the clips chosen for saving.
"""
from i18n import tr

import re
from pathlib import Path

from clips import achievement_text
from sources import find_tool, run_command

# What YouTube needs before it turns a description into chapters.
LEAST_CHAPTERS = 3
LEAST_SECONDS = 10
# Shorter than this between two clips is rounding, not a pause.
LEAST_GAP = 0.5
FORMAT = "{title} · {player1_name} {player1_achievement}"
FIELDS = ("title", "player1_name", "player1_achievement",
          "player2_name", "player2_achievement", "index")
OPENING = tr('시작')
GAP = tr('쉬는 시간')


def timestamp(seconds):
    """A YouTube timestamp: M:SS, or H:MM:SS once the recording passes an hour."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, remainder = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{remainder:02d}" if hours else f"{minutes}:{remainder:02d}"


def fields(row, index=1):
    """What a chapter title can be written from."""
    def score(key):
        value = row.get(key)
        return achievement_text(value) if value is not None else ""
    return {"title": row.get("title") or tr('곡 미확인'),
            "player1_name": row.get("player") or "",
            "player1_achievement": score("achievement"),
            "player2_name": row.get("player2") or "",
            "player2_achievement": score("achievement2"),
            "index": index}


def tidy(text):
    """Close the holes a missing name or score leaves behind."""
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    return re.sub(r"\s*([·/,|-])\s*(?=\1|$)", "", text).strip(" ·/,|-").strip()


def title(row, template=FORMAT, index=1):
    """One chapter title. Raises ValueError when the template names something unknown."""
    try:
        return tidy(template.format(**fields(row, index)))
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise ValueError(tr('챕터 형식을 확인해 주세요: {error}', error=error)) from error


def check_format(template):
    """Say what is wrong with a template, or nothing when it is usable."""
    try:
        title({"title": "x", "player": "y", "achievement": 100.0}, template)
    except ValueError as error:
        return str(error)
    return ""


def marks(rows, template=FORMAT, gap=None):
    """The clips in order as (start, title) pairs.

    YouTube ignores a description whose first timestamp is not 0:00, and a file's
    first chapter has to start there, so a recording that opens before the first
    clip gets a mark of its own. With a gap label, every pause gets one too.
    """
    ordered = sorted((row for row in rows if row.get("end", 0) > row.get("start", 0)),
                     key=lambda row: row["start"])
    found, previous_end = [], 0.0
    for index, row in enumerate(ordered, 1):
        start = float(row["start"])
        if start > previous_end + LEAST_GAP and (gap is not None or not found):
            found.append((previous_end, gap if gap is not None else OPENING))
        found.append((start, title(row, template, index)))
        previous_end = float(row["end"])
    return found


def description(rows, template=FORMAT, gap=None):
    """The block of timestamps to paste into a YouTube description."""
    return "\n".join(f"{timestamp(start)} {name}" for start, name in marks(rows, template, gap))


def refused(rows, duration=None, template=FORMAT, gap=None):
    """Why YouTube would not show these as chapters. Empty when it would."""
    found = marks(rows, template, gap)
    if not found:
        return [tr('선택한 클립이 없습니다.')]
    reasons = []
    if len(found) < LEAST_CHAPTERS:
        reasons.append(tr('챕터가 {count}개입니다. YouTube는 {least}개부터 표시합니다.',
                          count=len(found), least=LEAST_CHAPTERS))
    ends = [start for start, _ in found[1:]] + [duration if duration else found[-1][0] + LEAST_SECONDS]
    if any(end - start < LEAST_SECONDS for (start, _), end in zip(found, ends)):
        reasons.append(tr('{seconds}초보다 짧은 챕터가 있습니다.', seconds=LEAST_SECONDS))
    return reasons


def ffmetadata(rows, duration, template=FORMAT, gap=None):
    """FFmpeg's own metadata format, in milliseconds, for writing chapters into a file."""
    found = marks(rows, template, gap)
    if not found:
        raise ValueError(tr('선택한 클립이 없습니다.'))
    if duration is None or duration <= found[-1][0]:
        raise ValueError(tr('영상 길이를 확인하지 못해 챕터를 넣을 수 없습니다.'))
    bounds = [start for start, _ in found[1:]] + [float(duration)]
    lines = [";FFMETADATA1"]
    for (start, name), end in zip(found, bounds):
        escaped = name
        for character in ("\\", "=", ";", "#"):
            escaped = escaped.replace(character, "\\" + character)
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={round(start * 1000)}",
                  f"END={round(end * 1000)}", "title=" + escaped]
    return "\n".join(lines) + "\n"


def write_chapters(source, destination, rows, duration, cancel, report,
                   template=FORMAT, gap=None, tool=None):
    """Copy the video with chapter marks added. Streams are copied, not re-encoded."""
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError(tr('원본과 다른 파일 이름을 지정해 주세요.'))
    metadata = destination.with_name(destination.name + ".ffmeta")
    metadata.write_text(ffmetadata(rows, duration, template, gap), encoding="utf-8")
    try:
        report(tr('챕터를 넣는 중입니다…'))
        run_command([tool or find_tool("ffmpeg"), "-y", "-i", str(source), "-i", str(metadata),
                     "-map_metadata", "1", "-map_chapters", "1", "-codec", "copy", str(destination)],
                    cancel, report)
    finally:
        metadata.unlink(missing_ok=True)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError(tr('챕터를 넣은 파일을 만들지 못했습니다.'))
    return destination
