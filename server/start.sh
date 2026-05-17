#!/bin/sh
set -e

CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-1}"
CELERY_LOGLEVEL="${CELERY_LOGLEVEL:-info}"

celery -A app.worker worker --loglevel="$CELERY_LOGLEVEL" --concurrency="$CELERY_CONCURRENCY" &
CELERY_PID=$!

cleanup() {
  kill "$CELERY_PID" 2>/dev/null || true
  wait "$CELERY_PID" 2>/dev/null || true
}
trap cleanup TERM INT

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
