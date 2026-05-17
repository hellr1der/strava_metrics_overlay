from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

import redis

from app.config import REDIS_URL

_redis: redis.Redis | None = None

T = TypeVar("T")


class RedisUnavailableError(RuntimeError):
    """Redis недоступен."""


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis


def _redis_call(fn: Callable[[redis.Redis], T]) -> T:
    try:
        return fn(get_redis())
    except redis.RedisError as exc:
        raise RedisUnavailableError(str(exc)) from exc


def _key(job_id: str) -> str:
    return f"job:{job_id}"


def init_job(job_id: str) -> None:
    def _init(client: redis.Redis) -> None:
        client.hset(
            _key(job_id),
            mapping={
                "job_id": job_id,
                "status": "queued",
                "progress": "0",
                "error": "",
            },
        )

    _redis_call(_init)


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
    if not mapping:
        return

    def _update(client: redis.Redis) -> None:
        client.hset(_key(job_id), mapping=mapping)

    _redis_call(_update)


def get_job(job_id: str) -> dict[str, Any] | None:
    def _get(client: redis.Redis) -> dict[str, Any] | None:
        data = client.hgetall(_key(job_id))
        if not data:
            return None
        return {
            "job_id": data.get("job_id", job_id),
            "status": data.get("status", "queued"),
            "progress": int(data.get("progress", 0)),
            "error": data.get("error") or None,
        }

    return _redis_call(_get)


def delete_job(job_id: str) -> None:
    def _delete(client: redis.Redis) -> None:
        client.delete(_key(job_id))

    _redis_call(_delete)


def ping_redis() -> bool:
    try:
        _redis_call(lambda client: client.ping())
        return True
    except RedisUnavailableError:
        return False


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
