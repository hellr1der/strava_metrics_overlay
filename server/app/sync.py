from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.gpx import GpxPoint, nearest_point, parse_iso_datetime

EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc)
ISO_DATE_RE = re.compile(
    rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}(?::?\d{2})?)?"
)


def read_quicktime_creation_date(data: bytes) -> datetime | None:
    key = b"com.apple.quicktime.creationdate"
    idx = 0
    while True:
        idx = data.find(key, idx)
        if idx < 0:
            return None
        chunk = data[idx + len(key) : idx + len(key) + 30]
        match = ISO_DATE_RE.search(chunk)
        if match:
            try:
                return parse_iso_datetime(match.group(0).decode("utf-8"))
            except ValueError:
                pass
        idx += 1


def read_mvhd_creation_date(data: bytes) -> datetime | None:
    for i in range(4, len(data) - 28):
        if data[i : i + 4] != b"mvhd":
            continue
        version = data[i + 4]
        if version not in (0, 1):
            continue
        try:
            if version == 1:
                hi = int.from_bytes(data[i + 8 : i + 12], "big")
                lo = int.from_bytes(data[i + 12 : i + 16], "big")
                seconds = hi * 0x1_0000_0000 + lo
                return datetime.fromtimestamp(seconds, tz=timezone.utc)
            seconds = int.from_bytes(data[i + 8 : i + 12], "big")
            if seconds == 0:
                continue
            return EPOCH_1904 + timedelta(seconds=seconds)
        except (OverflowError, OSError, ValueError):
            continue
    return None


def read_video_start_time(
    video_path: Path,
    manual: str | None,
    sync_path: Path | None = None,
) -> datetime:
    if sync_path:
        data = json.loads(sync_path.read_text(encoding="utf-8"))
        return parse_iso_datetime(data["video_start_time"])

    if manual:
        return parse_iso_datetime(manual)

    data = video_path.read_bytes()
    qt = read_quicktime_creation_date(data)
    if qt:
        return qt
    mvhd = read_mvhd_creation_date(data)
    if mvhd:
        if mvhd.tzinfo is None:
            mvhd = mvhd.replace(tzinfo=timezone.utc)
        return mvhd.astimezone(timezone.utc)

    raise ValueError(
        "Не удалось определить время начала записи видео. "
        'Укажите вручную: start_time "2026-05-15T07:24:11Z"'
    )


def compute_offset_sec(video_start: datetime, points: list[GpxPoint]) -> float:
    return video_start.timestamp() - points[0].time.timestamp()


def ffprobe_video(path: Path) -> tuple[int, int, float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    duration = float(stream.get("duration") or data.get("format", {}).get("duration", 0))
    return width, height, duration


def build_metric_timeline(
    points: list[GpxPoint],
    video_start: datetime,
    duration_sec: float,
    offset_sec: float,
) -> list[dict]:
    seconds = max(1, int(math.ceil(duration_sec)))
    timeline = []
    for t in range(seconds):
        absolute = video_start.timestamp() + t
        gpx_time = datetime.fromtimestamp(absolute + offset_sec, tz=timezone.utc)
        pt = nearest_point(points, gpx_time)
        if pt is None:
            timeline.append(
                {"speed": None, "power": None, "hr": None, "cadence": None}
            )
        else:
            power = pt.power
            if power is None:
                power = 0
            timeline.append(
                {
                    "speed": pt.speed,
                    "power": power,
                    "hr": pt.hr,
                    "cadence": pt.cadence,
                }
            )
    return timeline
