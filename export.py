#!/usr/bin/env python3
"""Наложение метрик GPX на видео через ffmpeg."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

EARTH_RADIUS_KM = 6371.0
SPEED_WINDOW_SEC = 10
POWER_WINDOW_SEC = 3
CADENCE_WINDOW_SEC = 3
EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc)
ISO_DATE_RE = re.compile(
    rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}(?::?\d{2})?)?"
)
FFMPEG_TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")

SCRIPT_DIR = Path(__file__).resolve().parent
FONT_PATH = SCRIPT_DIR / "fonts" / "Oxanium-Bold.ttf"


@dataclass
class GpxPoint:
    time: datetime
    lat: float
    lon: float
    hr: float | None
    power: float | None
    cadence: float | None
    speed: float = 0.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    to_rad = math.radians
    d_lat = to_rad(lat2 - lat1)
    d_lon = to_rad(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def parse_iso_datetime(text: str) -> datetime:
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_optional_number(text: str | None) -> float | None:
    if text is None or not str(text).strip():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_extensions(trkpt) -> tuple[float | None, float | None, float | None]:
    hr = power = cadence = None
    ext = trkpt.find("extensions")
    if ext is None:
        return hr, power, cadence
    for el in ext.iter():
        if el is ext:
            continue
        name = local_name(el.tag).lower()
        value = parse_optional_number(el.text)
        if value is None:
            continue
        if name == "hr":
            hr = value
        elif name == "cad":
            cadence = value
        elif name == "power":
            power = value
    return hr, power, cadence


def index_at_or_after(points: list[GpxPoint], time_ms: float) -> int:
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid].time.timestamp() * 1000 < time_ms:
            lo = mid + 1
        else:
            hi = mid
    return lo


def index_at_or_before(points: list[GpxPoint], time_ms: float) -> int:
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if points[mid].time.timestamp() * 1000 > time_ms:
            hi = mid - 1
        else:
            lo = mid
    return lo


def get_window_range(points: list[GpxPoint], i: int, window_sec: int) -> tuple[int, int]:
    half_ms = window_sec * 500
    track_start = points[0].time.timestamp() * 1000
    track_end = points[-1].time.timestamp() * 1000
    center_ms = points[i].time.timestamp() * 1000
    start_ms = max(track_start, center_ms - half_ms)
    end_ms = min(track_end, center_ms + half_ms)
    j = index_at_or_after(points, start_ms)
    k = index_at_or_before(points, end_ms)
    if j > k:
        j, k = k, j
    return j, k


def apply_cadence_zero_when_no_power(points: list[GpxPoint]) -> None:
    for pt in points:
        if pt.power == 0 or pt.power is None:
            pt.cadence = 0


def compute_smoothed_speeds(points: list[GpxPoint]) -> None:
    n = len(points)
    if n == 0:
        return
    if n == 1:
        points[0].speed = 0
        return
    for i, pt in enumerate(points):
        j, k = get_window_range(points, i, SPEED_WINDOW_SEC)
        if j == k:
            j = max(0, i - 1)
            k = min(n - 1, i + 1)
        dt_ms = (points[k].time - points[j].time).total_seconds() * 1000
        if dt_ms <= 0:
            pt.speed = 0
            continue
        dist = haversine_km(points[j].lat, points[j].lon, points[k].lat, points[k].lon)
        pt.speed = (dist / dt_ms) * 3_600_000


def compute_window_average(
    points: list[GpxPoint], window_sec: int, getter
) -> list[float | None]:
    values: list[float | None] = []
    for i in range(len(points)):
        j, k = get_window_range(points, i, window_sec)
        total = 0.0
        count = 0
        for idx in range(j, k + 1):
            value = getter(points[idx])
            if value is None:
                continue
            total += value
            count += 1
        values.append(total / count if count else None)
    return values


def compute_smoothed_cadence(points: list[GpxPoint]) -> list[float | None]:
    window_ms = CADENCE_WINDOW_SEC * 1000
    track_start_ms = points[0].time.timestamp() * 1000
    values: list[float | None] = []
    for i, pt in enumerate(points):
        if pt.cadence == 0:
            values.append(0)
            continue
        center_ms = pt.time.timestamp() * 1000
        start_ms = max(track_start_ms, center_ms - window_ms)
        j = index_at_or_after(points, start_ms)
        if j > i:
            j = i
        total = 0.0
        count = 0
        for idx in range(j, i + 1):
            value = points[idx].cadence
            if value is None:
                continue
            total += value
            count += 1
        values.append(total / count if count else None)
    return values


def parse_gpx(path: Path) -> list[GpxPoint]:
    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    raw: list[GpxPoint] = []

    for trkpt in root.iter():
        if local_name(trkpt.tag) != "trkpt":
            continue
        time_el = None
        for child in trkpt:
            if local_name(child.tag) == "time":
                time_el = child
                break
        if time_el is None or not (time_el.text or "").strip():
            continue
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        hr, power, cadence = parse_extensions(trkpt)
        raw.append(
            GpxPoint(
                time=parse_iso_datetime(time_el.text),
                lat=lat,
                lon=lon,
                hr=hr,
                power=power,
                cadence=cadence,
            )
        )

    if not raw:
        raise SystemExit("GPX не содержит точек с тегом <time>")

    apply_cadence_zero_when_no_power(raw)
    compute_smoothed_speeds(raw)
    powers = compute_window_average(raw, POWER_WINDOW_SEC, lambda p: p.power)
    cadences = compute_smoothed_cadence(raw)

    for i, pt in enumerate(raw):
        pt.power = powers[i]
        pt.cadence = cadences[i]

    return raw


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
        dt = parse_iso_datetime(data["video_start_time"])
        print(f"[time source] sync.json: {dt.isoformat().replace('+00:00', 'Z')}")
        return dt

    if manual:
        dt = parse_iso_datetime(manual)
        print(f"[time source] --start-time: {dt}")
        return dt

    data = video_path.read_bytes()
    qt = read_quicktime_creation_date(data)
    if qt:
        print(f"[time source] QuickTime creationdate: {qt}")
        return qt
    mvhd = read_mvhd_creation_date(data)
    if mvhd:
        if mvhd.tzinfo is None:
            mvhd = mvhd.replace(tzinfo=timezone.utc)
        mvhd = mvhd.astimezone(timezone.utc)
        print(f"[time source] mvhd fallback: {mvhd}")
        return mvhd

    raise SystemExit(
        "Не удалось определить время начала записи видео. "
        'Укажите вручную: --start-time "2026-05-15T07:24:11Z"'
    )


def nearest_point(points: list[GpxPoint], target: datetime) -> GpxPoint | None:
    if not points:
        return None
    target_ms = target.timestamp() * 1000
    first_ms = points[0].time.timestamp() * 1000
    last_ms = points[-1].time.timestamp() * 1000
    if target_ms < first_ms or target_ms > last_ms:
        return None

    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid].time.timestamp() * 1000 < target_ms:
            lo = mid + 1
        else:
            hi = mid

    if lo > 0:
        prev = points[lo - 1]
        curr = points[lo]
        prev_diff = abs(prev.time.timestamp() * 1000 - target_ms)
        curr_diff = abs(curr.time.timestamp() * 1000 - target_ms)
        return prev if prev_diff <= curr_diff else curr
    return points[lo]


def merge_segments(values: list) -> list[tuple[float, float, object]]:
    if not values:
        return []
    segments: list[tuple[float, float, object]] = []
    start = 0
    current = values[0]
    for i in range(1, len(values)):
        if values[i] != current:
            segments.append((float(start), float(i), current))
            start = i
            current = values[i]
    segments.append((float(start), float(len(values)), current))
    return segments


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def escape_font_path(path: Path) -> str:
    resolved = str(path.resolve())
    if len(resolved) >= 2 and resolved[1] == ":":
        # Windows: C:\path -> C\\\:/path
        drive = resolved[0]
        rest = resolved[2:].replace("\\", "/")
        return f"{drive}\\\\\\:/{rest}"
    # Unix: просто слэши
    return resolved.replace("\\", "/")


def write_filter_script(vf: str) -> Path:
    """Записать цепочку фильтров во временный файл для -filter_script:v."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".fffilter",
        delete=False,
        encoding="utf-8",
        newline="\n",
    )
    try:
        tmp.write(vf)
        tmp.write("\n")
    finally:
        tmp.close()
    return Path(tmp.name)


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
) -> list[dict]:
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


def add_text_filter(
    filters: list[str],
    *,
    text: str,
    x: int,
    y: int,
    fontsize: int,
    fontcolor: str,
    fontfile: str,
    start: float,
    end: float,
    shadow: str | None = "1",
) -> None:
    enable = f"between(t\\,{start:.3f}\\,{max(start, end - 0.001):.3f})"
    shadow_part = ""
    if shadow == "2":
        shadow_part = ":shadowx=2:shadowy=2:shadowcolor=black@0.9"
    elif shadow == "1":
        shadow_part = ":shadowx=1:shadowy=1:shadowcolor=black@0.9"
    filters.append(
        "drawtext="
        f"fontfile={fontfile}"
        f":text={escape_drawtext(text)}"
        f":x={x}:y={y}"
        f":fontsize={fontsize}"
        f":fontcolor={fontcolor}"
        f"{shadow_part}"
        f":enable={enable}"
    )


def build_video_filter(
    timeline: list[dict],
    width: int,
    height: int,
    fontfile: str,
) -> str:
    scale = width / 1080
    px = lambda n: round(n * scale)

    speed_y = height - round(0.13 * height) - px(170)
    speed_x = px(43)
    speed_fs = px(120)
    unit_fs = px(36)
    sec_fs = px(52)
    sec_unit_fs = px(26)
    sec_y = speed_y + px(140)

    x_w = px(57)
    x_bpm = px(57) + px(130)
    x_rpm = px(57) + px(260)

    filters: list[str] = []

    box_x = px(43)
    box_y = speed_y + px(130)
    box_w = px(3)
    box_h = px(70)
    duration = len(timeline)
    filters.append(
        f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:color=white@0.25:t=fill:"
        f"enable=between(t\\,0\\,{max(0, duration - 0.001):.3f})"
    )

    speeds = [row["speed"] for row in timeline]
    powers = [row["power"] for row in timeline]
    hrs = [row["hr"] for row in timeline]
    cadences = [row["cadence"] for row in timeline]

    for start, end, value in merge_segments(speeds):
        if value is None:
            continue
        add_text_filter(
            filters,
            text=str(int(round(value))),
            x=speed_x,
            y=speed_y,
            fontsize=speed_fs,
            fontcolor="white",
            fontfile=fontfile,
            start=start,
            end=end,
            shadow="2",
        )

    for start, end, value in merge_segments(speeds):
        if value is None:
            continue
        add_text_filter(
            filters,
            text="km/h",
            x=speed_x + px(90),
            y=speed_y + px(80),
            fontsize=unit_fs,
            fontcolor="white@0.5",
            fontfile=fontfile,
            start=start,
            end=end,
            shadow=None,
        )

    metric_specs = [
        (powers, x_w, lambda v: str(int(round(v)))),
        (hrs, x_bpm, lambda v: str(int(round(v)))),
        (cadences, x_rpm, lambda v: str(int(round(v)))),
    ]

    for values, x_pos, formatter in metric_specs:
        for start, end, value in merge_segments(values):
            if value is None:
                continue
            add_text_filter(
                filters,
                text=formatter(value),
                x=x_pos,
                y=sec_y,
                fontsize=sec_fs,
                fontcolor="white",
                fontfile=fontfile,
                start=start,
                end=end,
                shadow="1",
            )

    unit_labels = [("W", x_w), ("BPM", x_bpm), ("RPM", x_rpm)]
    for label, x_pos in unit_labels:
        for start, end, value in merge_segments(
            powers if label == "W" else hrs if label == "BPM" else cadences
        ):
            if value is None:
                continue
            add_text_filter(
                filters,
                text=label,
                x=x_pos + px(65),
                y=sec_y,
                fontsize=sec_unit_fs,
                fontcolor="white@0.5",
                fontfile=fontfile,
                start=start,
                end=end,
                shadow=None,
            )

    return ",".join(filters)


def parse_ffmpeg_time(match: re.Match[str]) -> float:
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def format_hms(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def run_ffmpeg(cmd: list[str], duration: float) -> None:
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stderr is not None
    stderr_lines: list[str] = []
    last_pct = -1
    for line in proc.stderr:
        stderr_lines.append(line)
        match = FFMPEG_TIME_RE.search(line)
        if not match or duration <= 0:
            continue
        current = parse_ffmpeg_time(match)
        pct = min(100, int(current / duration * 100))
        if pct != last_pct:
            print(
                f"\rРендеринг: {pct}% ({format_hms(current)} / {format_hms(duration)})",
                end="",
                flush=True,
            )
            last_pct = pct
    proc.wait()
    print()
    print("FFmpeg stderr:")
    print("".join(stderr_lines))
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg завершился с кодом {proc.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Наложение GPX-метрик на видео")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--gpx", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--start-time",
        default=None,
        help='Время начала записи видео (ISO 8601), напр. "2026-05-15T07:24:11Z"',
    )
    parser.add_argument(
        "--sync",
        default=None,
        type=Path,
        help="JSON с параметрами синхронизации из веб-приложения (video_start_time)",
    )
    parser.add_argument(
        "--overlay",
        default=None,
        type=Path,
        help="WebM-оверлей с прозрачным фоном (из веб-приложения)",
    )
    args = parser.parse_args()

    for path, label in ((args.video, "видео"), (args.gpx, "GPX")):
        if not path.is_file():
            raise SystemExit(f"Файл {label} не найден: {path}")

    if args.sync and not args.sync.is_file():
        raise SystemExit(f"Файл sync не найден: {args.sync}")

    if args.overlay and not args.overlay.is_file():
        raise SystemExit(f"Файл overlay не найден: {args.overlay}")

    points = parse_gpx(args.gpx)
    video_start = read_video_start_time(args.video, args.start_time, args.sync)
    offset_sec = video_start.timestamp() - points[0].time.timestamp()

    width, height, duration = ffprobe_video(args.video)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Видео: {width}x{height}, {duration:.1f} с")
    print(f"Смещение GPX: {offset_sec:.1f} с")

    if args.overlay:
        print(f"Видео: {args.video.stat().st_size / 1024:.0f} KB")
        print(f"Overlay: {args.overlay.stat().st_size / 1024:.0f} KB")
        filter_complex = (
            f"[1:v]format=yuva420p,scale={width}:{height}[ov];"
            f"[0:v][ov]overlay=0:0[out]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(args.video),
            "-vcodec",
            "libvpx-vp9",
            "-i",
            str(args.overlay),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-map",
            "0:a",
            "-c:v",
            "libx265",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            str(args.output),
        ]
        print("Режим: WebM overlay")
        print("FFmpeg cmd:", " ".join(cmd))
        print("Запуск ffmpeg…")
        run_ffmpeg(cmd, duration)
    else:
        if not FONT_PATH.is_file():
            raise SystemExit(
                f"Шрифт не найден: {FONT_PATH}\n"
                "Скачайте Oxanium-Bold.ttf и положите в папку fonts/ (см. fonts/README.md)"
            )

        timeline = build_metric_timeline(points, video_start, duration)
        fontfile = escape_font_path(FONT_PATH)
        vf = build_video_filter(timeline, width, height, fontfile)
        filter_script_path = write_filter_script(vf)
        filter_script_arg = str(filter_script_path.resolve()).replace("\\", "/")

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(args.video),
            "-filter_script:v",
            filter_script_arg,
            "-c:v",
            "libx265",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            str(args.output),
        ]

        if "-vf" in cmd:
            raise SystemExit("Ошибка: в команде ffmpeg остался -vf, ожидается -filter_script:v")
        assert "-filter_script:v" in cmd
        print("Режим: drawtext")
        print("FFmpeg cmd:", " ".join(cmd))
        print("Filter script path:", filter_script_path)
        print("Filter script first 200 chars:")
        print(filter_script_path.read_text(encoding="utf-8")[:200])
        print("Запуск ffmpeg…")
        try:
            run_ffmpeg(cmd, duration)
        finally:
            filter_script_path.unlink(missing_ok=True)

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Готово: {args.output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
