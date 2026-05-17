from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import JOBS_DIR, MAX_VIDEO_SIZE_MB
from app.job_store import delete_job, get_job, init_job, save_job_meta
from app.tasks import process_video

app = FastAPI(title="GPX Video Overlay API")


@app.on_event("startup")
def ensure_jobs_dir() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


@app.post("/process")
async def process(
    video: UploadFile = File(...),
    gpx: UploadFile = File(...),
    start_time: str | None = Form(default=None),
):
    max_bytes = MAX_VIDEO_SIZE_MB * 1024 * 1024

    job_id = str(uuid.uuid4())
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    video_ext = Path(video.filename or "video.MOV").suffix or ".MOV"
    video_path = job_dir / f"video{video_ext}"
    gpx_path = job_dir / "track.gpx"

    video_bytes = 0
    async with aiofiles.open(video_path, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            video_bytes += len(chunk)
            if video_bytes > max_bytes:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"Видео больше {MAX_VIDEO_SIZE_MB} MB")
            await f.write(chunk)

    async with aiofiles.open(gpx_path, "wb") as f:
        while chunk := await gpx.read(64 * 1024):
            await f.write(chunk)

    save_job_meta(job_dir, {"start_time": start_time})
    init_job(job_id)
    process_video.delay(job_id)

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
async def status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return job


@app.get("/result/{job_id}")
async def result(job_id: str):
    job = get_job(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404, "Результат недоступен")

    output = _job_dir(job_id) / "output.MOV"
    if not output.is_file():
        raise HTTPException(404, "Файл output.MOV не найден")

    return FileResponse(
        output,
        media_type="video/quicktime",
        filename="output.MOV",
    )


@app.delete("/job/{job_id}")
async def remove_job(job_id: str):
    job_dir = _job_dir(job_id)
    if job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)
    delete_job(job_id)
    return {"job_id": job_id, "deleted": True}
