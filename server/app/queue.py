from __future__ import annotations


class QueueUnavailableError(RuntimeError):
    """Celery broker недоступен."""


def enqueue_process_video(job_id: str) -> None:
    try:
        from app.tasks import process_video

        process_video.delay(job_id)
    except Exception as exc:
        raise QueueUnavailableError(str(exc)) from exc
