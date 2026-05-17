from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.gpx import parse_gpx
from app.job_store import load_job_meta, save_job_meta, update_job
from app.renderer import render_overlay_sync
from app.sync import (
    assert_gpx_covers_video,
    build_metric_timeline,
    build_sync_payload,
    ffprobe_video,
    resolve_video_start_time,
    video_has_audio,
)
from app.worker import celery_app

FFMPEG_TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")


def _pick_video_encoder() -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    encoders = f"{result.stdout}\n{result.stderr}"
    for name in ("libx264", "libx265"):
        if f" {name}" in encoders or f"V.....{name}" in encoders:
            return name
    raise RuntimeError("ffmpeg без libx264/libx265 — проверьте пакет ffmpeg в образе")


def _build_mux_command(
    video_path: Path,
    overlay_path: Path,
    output_path: Path,
    width: int,
    height: int,
    *,
    with_audio: bool,
) -> list[str]:
    """Сборка видео + WebM-оверлей (PNG→VP9 с альфой, без colorkey)."""
    filter_complex = (
        f"[1:v]format=yuva420p,scale={width}:{height}[ov];"
        f"[0:v][ov]overlay=0:0:format=auto[out]"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
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
    if with_audio:
        cmd.extend(["-map", "0:a", "-c:a", "copy"])
    cmd.extend(
        [
            "-shortest",
            "-map_metadata",
            "0",
            "-c:v",
            _pick_video_encoder(),
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-f",
            "mov",
            str(output_path),
        ]
    )
    return cmd


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
        if len(stderr_tail) > 80:
            stderr_tail.pop(0)
        match = FFMPEG_TIME_RE.search(line)
        if not match or duration <= 0:
            continue
        current = _parse_ffmpeg_time(match)
        pct = min(99, 60 + int(current / duration * 39))
        if pct != last_pct:
            update_job(job_id, progress=pct)
            last_pct = pct
    remainder = proc.stderr.read()
    if remainder:
        stderr_tail.append(remainder)
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
        sync_path = job_dir / "sync.json"
        if not sync_path.is_file():
            sync_path = None

        points = parse_gpx(gpx_path)
        width, height, duration = ffprobe_video(video_path)
        video_start, time_source = resolve_video_start_time(
            video_path, start_time, sync_path
        )
        assert_gpx_covers_video(points, video_start, duration)
        save_job_meta(
            job_dir,
            {
                **meta,
                **build_sync_payload(video_start, points, time_source),
            },
        )
        update_job(job_id, progress=10)

        timeline = build_metric_timeline(points, video_start, duration)

        render_overlay_sync(job_dir, timeline, width, height, duration)
        update_job(job_id, progress=30)

        overlay_path = job_dir / "overlay.webm"
        if not overlay_path.is_file() or overlay_path.stat().st_size == 0:
            raise RuntimeError("overlay.webm не создан или пуст")

        output_path = job_dir / "output.MOV"
        has_audio = video_has_audio(video_path)
        cmd = _build_mux_command(
            video_path,
            overlay_path,
            output_path,
            width,
            height,
            with_audio=has_audio,
        )
        _run_ffmpeg_with_progress(
            cmd, duration, job_id, log_path=job_dir / "ffmpeg.log"
        )
        update_job(job_id, status="done", progress=100)
    except Exception as exc:
        update_job(job_id, status="error", error=str(exc))
