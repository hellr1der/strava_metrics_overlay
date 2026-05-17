from __future__ import annotations

import asyncio
import math
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

from app.config import STATIC_DIR

OVERLAY_FPS = 30


def _encode_overlay_from_frames(
    frames_dir: Path,
    output_path: Path,
    duration: float,
) -> None:
    """Собрать WebM с альфой: кадр N строго на t = N/fps."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(OVERLAY_FPS),
        "-i",
        str(frames_dir / "%05d.png"),
        "-t",
        f"{duration:.6f}",
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuva420p",
        "-auto-alt-ref",
        "0",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg overlay encode failed: "
            f"{(result.stderr or result.stdout)[-800:]}"
        )


async def render_overlay(
    job_dir: Path,
    timeline: list[dict],
    width: int,
    height: int,
    duration: float,
) -> Path:
    """
    Покадровый рендер PNG → ffmpeg.
    MediaRecorder писал в wall-clock и растягивал таймлайн метрик.
    """
    overlay_html = (STATIC_DIR / "overlay.html").resolve()
    overlay_url = overlay_html.as_uri()
    frames_dir = job_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    payload = {
        "timeline": timeline,
        "width": width,
        "height": height,
        "duration": duration,
        "fps": OVERLAY_FPS,
    }
    total_frames = max(1, math.ceil(duration * OVERLAY_FPS))

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        page = await browser.new_page()
        await page.add_init_script(
            "document.documentElement.style.background = 'transparent';"
        )
        await page.goto(overlay_url, wait_until="load")
        await page.evaluate("(data) => window.initRenderer(data)", payload)

        for _ in range(120):
            if await page.evaluate("() => Boolean(window.rendererReady)"):
                break
            await asyncio.sleep(0.1)
        else:
            await browser.close()
            raise RuntimeError("Таймаут инициализации overlay renderer")

        canvas = page.locator("#c")
        for frame in range(total_frames):
            t = frame / OVERLAY_FPS
            await page.evaluate("(time) => window.renderAt(time)", t)
            await canvas.screenshot(
                path=str(frames_dir / f"{frame:05d}.png"),
                omit_background=True,
            )

        await browser.close()

    out_path = job_dir / "overlay.webm"
    _encode_overlay_from_frames(frames_dir, out_path, duration)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return out_path


def render_overlay_sync(
    job_dir: Path,
    timeline: list[dict],
    width: int,
    height: int,
    duration: float,
) -> Path:
    return asyncio.run(
        render_overlay(job_dir, timeline, width, height, duration)
    )
