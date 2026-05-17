# Strava Metrics Overlay — контекст

## Идея

Наложение тренировочных метрик из Strava/GPX на видео. Синхронизация по реальному времени: GPX — UTC в `<time>`, видео — `creation_time` в метаданных.

## Компоненты

| Часть | Описание |
|-------|----------|
| **Веб** (`index.html`, `src/`) | Превью в браузере, экспорт WebM-оверлея |
| **CLI** (`export.py`) | Локальная склейка через ffmpeg |
| **Сервер** (`server/`) | FastAPI + Celery + Playwright + ffmpeg |

## Синхронизация

- `offset = video_start_time − gpx_first_point_time`
- Метрики: скорость (Haversine, 10 с), мощность и каденс (окна 3 с)

## Временные файлы

`tmp/uploads/`, `tmp/output/`, `tmp/jobs/` — не в git (см. `tmp/README.md`).

## Дальше

- Telegram-бот, Strava API
- Ручная подстройка оффсета в UI
