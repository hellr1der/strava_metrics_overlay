from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.gpx import parse_gpx
from app.job_store import load_job_meta, update_job
from app.renderer import render_overlay_sync
from app.sync import (
    build_metric_timeline,
    compute_offset_sec,
    ffprobe_video,
    read_video_start_time,
    video_has_audio,
)
from app.worker import celery_app

FFMPEG_TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")


def _find_video(job_dir: Path) -> Path:
    for name in ("video.MOV", "video.MP4", "video.mov", "video.mp4"):
        p = job_dir / name
        if p.is_file():
            return p
    for p in sorted(job_dir.glob("video.*")):
        if p.is_file():
            return p
    raise FileNotFoundError("Видеофайл не найден в директории задачи")


def _find_gpx(job_dir: Path) -> Path:
    for name in ("track.gpx", "input.gpx"):
        p = job_dir / name
        if p.is_file():
            return p
    for p in job_dir.glob("*.gpx"):
        if p.is_file():
            return p
    raise FileNotFoundError("GPX не найден в директории задачи")


def _parse_ffmpeg_time(match: re.Match[str]) -> float:
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _run_ffmpeg_with_progress(
    cmd: list[str],
    duration: float,
    job_id: str,
    *,
    log_path: Path | None = None,
) -> None:
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stderr is not None
    last_pct = -1
    stderr_tail: list[str] = []
    for line in proc.stderr:
        stderr_tail.append(line)
        if len(stderr_tail) > 40:
            stderr_tail.pop(0)
        match = FFMPEG_TIME_RE.search(line)
        if not match or duration <= 0:
            continue
        current = _parse_ffmpeg_time(match)
        pct = min(99, 60 + int(current / duration * 39))
        if pct != last_pct:
            update_job(job_id, progress=pct)
            last_pct = pct
    proc.wait()
    if proc.returncode != 0:
        detail = "".join(stderr_tail).strip()
        if log_path is not None:
            log_path.write_text(
                f"CMD: {' '.join(cmd)}\n\nSTDERR:\n{detail}",
                encoding="utf-8",
            )
        msg = f"ffmpeg завершился с кодом {proc.returncode}"
        if detail:
            msg = f"{msg}: {detail[-800:]}"
        raise RuntimeError(msg)


@celery_app.task(name="app.tasks.process_video")
def process_video(job_id: str) -> None:
    from app.config import JOBS_DIR

    job_dir = JOBS_DIR / job_id
    update_job(job_id, status="processing", progress=0, error="")

    try:
        meta = load_job_meta(job_dir)
        video_path = _find_video(job_dir)
        gpx_path = _find_gpx(job_dir)
        start_time = meta.get("start_time")

        video_start = read_video_start_time(video_path, start_time)
        points = parse_gpx(gpx_path)
        update_job(job_id, progress=10)

        offset_sec = compute_offset_sec(video_start, points)
        width, height, duration = ffprobe_video(video_path)
        timeline = build_metric_timeline(
            points, video_start, duration, offset_sec
        )

        render_overlay_sync(job_dir, timeline, width, height, duration)
        update_job(job_id, progress=30)

        overlay_path = job_dir / "overlay.webm"
        output_path = job_dir / "output.MOV"
        filter_complex = (
            f"[1:v]scale={width}:{height}[ov];"
            f"[0:v][ov]overlay=0:0:shortest=1[out]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(overlay_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
        ]
        if video_has_audio(video_path):
            cmd.extend(["-map", "0:a", "-c:a", "copy"])
        cmd.extend(
            [
                "-map_metadata",
                "0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        _run_ffmpeg_with_progress(
            cmd, duration, job_id, log_path=job_dir / "ffmpeg.log"
        )
        update_job(job_id, status="done", progress=100)
    except Exception as exc:
        update_job(job_id, status="error", error=str(exc))
