from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from playwright.async_api import async_playwright

from app.config import STATIC_DIR


async def render_overlay(
    job_dir: Path,
    timeline: list[dict],
    width: int,
    height: int,
    duration: float,
) -> Path:
    overlay_html = (STATIC_DIR / "overlay.html").resolve()
    overlay_url = overlay_html.as_uri()

    payload = {
        "timeline": timeline,
        "width": width,
        "height": height,
        "duration": duration,
        "fps": 30,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(overlay_url, wait_until="load")
        await page.evaluate(
            "(data) => { window.overlayData = data; }",
            payload,
        )

        for _ in range(600):
            state = await page.evaluate(
                """() => ({
                  done: Boolean(window.overlayDone),
                  error: window.overlayError || null,
                })"""
            )
            if state.get("error"):
                await browser.close()
                raise RuntimeError(state["error"])
            if state.get("done"):
                break
            await asyncio.sleep(0.5)
        else:
            await browser.close()
            raise RuntimeError("Таймаут рендера оверлея")

        b64 = await page.evaluate("() => window.overlayBlobBase64")
        await browser.close()

    if not b64:
        raise RuntimeError("overlayBlobBase64 пуст")

    out_path = job_dir / "overlay.webm"
    out_path.write_bytes(base64.b64decode(b64))
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
