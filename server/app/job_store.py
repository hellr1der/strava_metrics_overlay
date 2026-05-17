from __future__ import annotations

import json
from typing import Any

import redis

from app.config import REDIS_URL

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def _key(job_id: str) -> str:
    return f"job:{job_id}"


def init_job(job_id: str) -> None:
    get_redis().hset(
        _key(job_id),
        mapping={"job_id": job_id, "status": "queued", "progress": "0", "error": ""},
    )


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
) -> None:
    mapping: dict[str, Any] = {}
    if status is not None:
        mapping["status"] = status
    if progress is not None:
        mapping["progress"] = str(progress)
    if error is not None:
        mapping["error"] = error
    if mapping:
        get_redis().hset(_key(job_id), mapping=mapping)


def get_job(job_id: str) -> dict[str, Any] | None:
    data = get_redis().hgetall(_key(job_id))
    if not data:
        return None
    return {
        "job_id": data.get("job_id", job_id),
        "status": data.get("status", "queued"),
        "progress": int(data.get("progress", 0)),
        "error": data.get("error") or None,
    }


def delete_job(job_id: str) -> None:
    get_redis().delete(_key(job_id))


def save_job_meta(job_dir, meta: dict) -> None:
    from pathlib import Path

    path = Path(job_dir) / "meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")


def load_job_meta(job_dir) -> dict:
    from pathlib import Path

    path = Path(job_dir) / "meta.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
