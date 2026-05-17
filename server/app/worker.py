from celery import Celery

from app.config import REDIS_URL

celery_app = Celery(
    "gpx_video_overlay",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

celery_app.conf.include = ["app.tasks"]
