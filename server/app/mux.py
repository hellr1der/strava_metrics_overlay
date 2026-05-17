from __future__ import annotations

import subprocess
from pathlib import Path


def pick_video_encoder() -> str:
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


def build_overlay_mux_command(
    video_path: Path,
    overlay_path: Path,
    output_path: Path,
    width: int,
    height: int,
    *,
    with_audio: bool,
    video_encoder: str | None = None,
) -> list[str]:
    """Видео + WebM (VP9 с альфой). Декодер libvpx-vp9 обязателен для альфа-канала."""
    encoder = video_encoder or pick_video_encoder()
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
        "-c:v",
        "libvpx-vp9",
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
            encoder,
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
