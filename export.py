#!/usr/bin/env python3
"""Локальный CLI: наложение GPX-метрик на видео (drawtext или WebM-оверлей)."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Общая логика с сервером (GPX, sync, mux)
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVER_DIR = _SCRIPT_DIR / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from app.gpx import parse_gpx  # noqa: E402
from app.mux import build_overlay_mux_command  # noqa: E402
from app.sync import (  # noqa: E402
    build_metric_timeline,
    compute_offset_sec,
    ffprobe_video,
    resolve_video_start_time,
    video_has_audio,
)

FFMPEG_TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")
FONT_PATH = _SCRIPT_DIR / "fonts" / "Oxanium-Bold.ttf"


def cli_resolve_video_start(
    video_path: Path,
    manual: str | None,
    sync_path: Path | None,
) -> datetime:
    path = sync_path if sync_path and sync_path.is_file() else None
    try:
        dt, source = resolve_video_start_time(video_path, manual, path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    label = dt.isoformat().replace("+00:00", "Z")
    print(f"[time source] {source}: {label}")
    return dt


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
        drive = resolved[0]
        rest = resolved[2:].replace("\\", "/")
        return f"{drive}\\\\\\:/{rest}"
    return resolved.replace("\\", "/")


def write_filter_script(vf: str) -> Path:
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
        help="JSON с video_start_time из веб-превью (опционально)",
    )
    parser.add_argument(
        "--overlay",
        default=None,
        type=Path,
        help="WebM-оверлей с альфой (из веб-приложения)",
    )
    args = parser.parse_args()

    for path, label in ((args.video, "видео"), (args.gpx, "GPX")):
        if not path.is_file():
            raise SystemExit(f"Файл {label} не найден: {path}")

    if args.sync and not args.sync.is_file():
        raise SystemExit(f"Файл sync не найден: {args.sync}")

    if args.overlay and not args.overlay.is_file():
        raise SystemExit(f"Файл overlay не найден: {args.overlay}")

    try:
        points = parse_gpx(args.gpx)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    video_start = cli_resolve_video_start(args.video, args.start_time, args.sync)
    offset_sec = compute_offset_sec(video_start, points)

    width, height, duration = ffprobe_video(args.video)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Видео: {width}x{height}, {duration:.1f} с")
    print(f"Смещение GPX: {offset_sec:.1f} с")

    if args.overlay:
        print(f"Overlay: {args.overlay.stat().st_size / 1024:.0f} KB")
        cmd = build_overlay_mux_command(
            args.video,
            args.overlay,
            args.output,
            width,
            height,
            with_audio=video_has_audio(args.video),
        )
        print("Режим: WebM overlay")
        print("FFmpeg cmd:", " ".join(cmd))
        print("Запуск ffmpeg…")
        run_ffmpeg(cmd, duration)
    else:
        if not FONT_PATH.is_file():
            raise SystemExit(
                f"Шрифт не найден: {FONT_PATH}\n"
                "Скачайте Oxanium в fonts/ (см. fonts/README.md)"
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

        print("Режим: drawtext")
        print("FFmpeg cmd:", " ".join(cmd))
        try:
            run_ffmpeg(cmd, duration)
        finally:
            filter_script_path.unlink(missing_ok=True)

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Готово: {args.output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
