from __future__ import annotations

import subprocess
from pathlib import Path

from app.sync import VideoColorInfo, ffprobe_video_color, is_hdr_color

# HEVC VUI: BT.2020 primaries, ARIB STD-B67 (HLG), BT.2020 NCL matrix
HEVC_HDR_METADATA_BSF = (
    "hevc_metadata=colour_primaries=9:transfer_characteristics=18:matrix_coefficients=9"
)


def pick_video_encoder(*, hdr: bool) -> str:
    preferred = ("libx265", "libx264") if hdr else ("libx264", "libx265")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    encoders = f"{result.stdout}\n{result.stderr}"
    for name in preferred:
        if f" {name}" in encoders or f"V.....{name}" in encoders:
            return name
    raise RuntimeError("ffmpeg без libx264/libx265 — проверьте пакет ffmpeg в образе")


def _setparams_suffix(color: VideoColorInfo) -> str:
    return (
        f"setparams=color_primaries={color.primaries}:"
        f"color_trc={color.transfer}:colorspace={color.space},format=yuv420p[out]"
    )


def _x265_params(color: VideoColorInfo) -> str:
    transfer_map = {
        "arib-std-b67": "hlg",
        "smpte2084": "smpte2084",
        "bt2020-10": "bt2020-10",
        "bt2020-12": "bt2020-12",
    }
    transfer = transfer_map.get(color.transfer, "hlg")
    matrix = color.space if color.space != "unknown" else "bt2020nc"
    primaries = color.primaries if color.primaries != "unknown" else "bt2020"
    return f"colourprim={primaries}:transfer={transfer}:colormatrix={matrix}"


def build_overlay_mux_command(
    video_path: Path,
    overlay_path: Path,
    output_path: Path,
    width: int,
    height: int,
    *,
    with_audio: bool,
    video_encoder: str | None = None,
    color: VideoColorInfo | None = None,
) -> list[str]:
    """Видео + WebM (VP9 с альфой). Декодер libvpx-vp9 обязателен для альфа-канала."""
    color = color or ffprobe_video_color(video_path)
    hdr = is_hdr_color(color)
    encoder = video_encoder or pick_video_encoder(hdr=hdr)

    filter_complex = (
        f"[1:v]format=yuva420p,scale={width}:{height}[ov];"
        f"[0:v][ov]overlay=0:0:format=auto,"
        f"{_setparams_suffix(color)}"
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
            # Цвет задаём явно с входа (setparams + VUI), без -map_metadata 0:
            # иначе H.264 наследует HLG/bt2020 и ломает галереи вроде Instagram.
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
            "-f",
            "mov",
        ]
    )
    if encoder == "libx265":
        cmd.extend(["-tag:v", "hvc1", "-x265-params", _x265_params(color)])
        if hdr:
            cmd.extend(["-bsf:v", HEVC_HDR_METADATA_BSF])
    cmd.append(str(output_path))
    return cmd
