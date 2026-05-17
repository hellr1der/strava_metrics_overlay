from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.gpx import GpxPoint, nearest_point, parse_iso_datetime

EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc)
MIN_CREATION = datetime(1990, 1, 1, tzinfo=timezone.utc)
MAX_CREATION = datetime(2035, 1, 1, tzinfo=timezone.utc)
ISO_DATE_RE = re.compile(
    rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}(?::?\d{2})?)?"
)
QUICKTIME_KEY = b"com.apple.quicktime.creationdate"


def _is_plausible(dt: datetime) -> bool:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    return MIN_CREATION.timestamp() <= ts <= MAX_CREATION.timestamp()


def read_quicktime_creation_date(data: bytes) -> datetime | None:
    """Как readCreationTimeFromQuickTime() в src/main.js."""
    idx = 0
    scan_window = 512
    while True:
        idx = data.find(QUICKTIME_KEY, idx)
        if idx < 0:
            return None
        chunk = data[idx + len(QUICKTIME_KEY) : idx + len(QUICKTIME_KEY) + scan_window]
        match = ISO_DATE_RE.search(chunk)
        if match:
            try:
                dt = parse_iso_datetime(match.group(0).decode("utf-8"))
                if _is_plausible(dt):
                    return dt
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
                dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            else:
                seconds = int.from_bytes(data[i + 8 : i + 12], "big")
                if seconds == 0:
                    continue
                dt = EPOCH_1904 + timedelta(seconds=seconds)
            if _is_plausible(dt):
                return dt.astimezone(timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
    return None


def read_ffprobe_creation_time(video_path: Path) -> datetime | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags=creation_time:com.apple.quicktime.creationdate",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    tags = data.get("format", {}).get("tags", {}) or {}
    for key in ("com.apple.quicktime.creationdate", "creation_time"):
        raw = tags.get(key)
        if not raw:
            continue
        try:
            dt = parse_iso_datetime(str(raw))
            if _is_plausible(dt):
                return dt
        except ValueError:
            continue
    return None


def resolve_video_start_time(
    video_path: Path,
    manual: str | None = None,
    sync_path: Path | None = None,
) -> tuple[datetime, str]:
    """
    Время начала записи видео — та же цепочка, что во фронте:
    sync.json / start_time (опционально) → QuickTime → mvhd → ffprobe.
    """
    if sync_path and sync_path.is_file():
        data = json.loads(sync_path.read_text(encoding="utf-8"))
        return parse_iso_datetime(data["video_start_time"]), "sync.json"

    if manual:
        return parse_iso_datetime(manual), "manual"

    data = video_path.read_bytes()
    qt = read_quicktime_creation_date(data)
    if qt:
        return qt, "quicktime"

    mvhd = read_mvhd_creation_date(data)
    if mvhd:
        return mvhd, "mvhd"

    ffprobe_dt = read_ffprobe_creation_time(video_path)
    if ffprobe_dt:
        return ffprobe_dt, "ffprobe"

    raise ValueError(
        "Не удалось определить время начала записи из метаданных видео. "
        "Убедитесь, что GPX и видео с одной поездки, или передайте start_time."
    )


def compute_offset_sec(video_start: datetime, points: list[GpxPoint]) -> float:
    return video_start.timestamp() - points[0].time.timestamp()


def video_has_audio(path: Path) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and "audio" in result.stdout


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


def assert_gpx_covers_video(
    points: list[GpxPoint],
    video_start: datetime,
    duration_sec: float,
) -> None:
    if not points:
        raise ValueError("GPX пуст")
    video_end_ts = video_start.timestamp() + duration_sec
    gpx_start_ts = points[0].time.timestamp()
    gpx_end_ts = points[-1].time.timestamp()
    if video_end_ts < gpx_start_ts or video_start.timestamp() > gpx_end_ts:
        raise ValueError(
            "GPX не перекрывает время видео по метаданным файла: "
            f"видео {video_start.isoformat()} (+{duration_sec:.0f} с), "
            f"GPX {points[0].time.isoformat()} … {points[-1].time.isoformat()}. "
            "Нужен GPX той же поездки или укажите start_time, если метаданные видео неверны."
        )


def build_metric_timeline(
    points: list[GpxPoint],
    video_start: datetime,
    duration_sec: float,
) -> list[dict]:
    """Как getMetricAt() во фронте: video_start + t сек → ближайшая точка GPX."""
    seconds = max(1, int(math.ceil(duration_sec)))
    timeline = []
    for t in range(seconds):
        target = datetime.fromtimestamp(video_start.timestamp() + t, tz=timezone.utc)
        pt = nearest_point(points, target)
        if pt is None:
            timeline.append(
                {"speed": None, "power": None, "hr": None, "cadence": None}
            )
        else:
            timeline.append(
                {
                    "speed": pt.speed,
                    "power": pt.power,
                    "hr": pt.hr,
                    "cadence": pt.cadence,
                }
            )
    return timeline


def build_sync_payload(
    video_start: datetime,
    points: list[GpxPoint],
    time_source: str,
) -> dict:
    """Тот же объект, что «Экспорт sync.json» в превью (для meta.json на сервере)."""
    return {
        "video_start_time": video_start.isoformat().replace("+00:00", "Z"),
        "gpx_start_time": points[0].time.isoformat().replace("+00:00", "Z"),
        "offset_sec": compute_offset_sec(video_start, points),
        "time_source": time_source,
    }
