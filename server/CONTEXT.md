# GPX Video Overlay — Server

## Назначение

Сервер принимает видео (MOV/MP4) и GPX, синхронизирует метрики по времени, рендерит прозрачный оверлей (Playwright → PNG → VP9) и склеивает результат через ffmpeg.

## Компоненты

| Сервис | Роль |
|--------|------|
| **api** | FastAPI: загрузка файлов, постановка задач, статус, выдача результата |
| **worker** | Celery: парсинг GPX, рендер оверлея, ffmpeg |
| **redis** | Брокер Celery + хранение статуса задач (`job:{id}`) |

## Поток обработки

```
POST /process
  → /tmp/jobs/{job_id}/video.* + track.gpx
  → Celery: process_video(job_id)

process_video:
  1. video_start: sync.json / start_time → QuickTime → mvhd → ffprobe
  2. parse_gpx (Strava namespace, сглаживание скорости/мощности/каденса)
  3. timeline (1 сэмпл/сек) → Playwright PNG → overlay.webm (VP9 + alpha)
  4. ffmpeg mux (libvpx-vp9 decode, overlay format=auto) → output.MOV
```

## API

- `POST /process` — multipart: `video`, `gpx`; опционально `start_time`, `sync`
- `GET /status/{job_id}` — `{ job_id, status, progress, error }`
- `GET /result/{job_id}` — `output.MOV` при `status == "done"`
- `DELETE /job/{job_id}` — удаление файлов и записи в Redis

Статусы: `queued` → `processing` → `done` | `error`

Прогресс: 10% GPX, 30% overlay, 60–99% ffmpeg, 100% готово.

## Модули

- `app/gpx.py` — парсинг и сглаживание метрик
- `app/sync.py` — время видео, timeline, ffprobe
- `app/mux.py` — ffmpeg: видео + WebM с альфой
- `app/renderer.py` — Playwright + `static/overlay.html`
- `export.py` (корень) — локальный CLI, импортирует `app.*`
- `static/overlay.html` — `renderFrame` идентичен `src/overlay.js`

## Запуск

```bash
# Шрифт: скачать Oxanium-Bold.ttf в server/fonts/
docker compose up --build
```

## Railway (рекомендуется: один сервис)

| Сервис | Config | Старт |
|--------|--------|-------|
| **strava_metrics_overlay** | `railway.json` | `sh start.sh` (uvicorn + celery в одном контейнере) |
| Redis | managed | `REDIS_URL` |
| Volume | `/tmp/jobs` | `JOBS_DIR=/tmp/jobs` |

`server/start.sh` поднимает Celery в фоне и uvicorn на `$PORT` — общий диск для загрузки и обработки.

План Hobby: **1 GB+** RAM на API-сервисе (Playwright + ffmpeg + celery).

### Когда переходить на S3 (вариант B)

- Несколько реплик API или worker на разных машинах
- Видео > нескольких GB или долгое хранение результатов
- Нужна отказоустойчивость: контейнер упал — файлы в бакете остались

## Ограничения

- Время начала видео должно быть в метаданных файла или передано как `start_time`
- Максимальный размер видео: `MAX_VIDEO_SIZE_MB` (500 по умолчанию)
- Рендер оверлея требует Chromium (Playwright) в образе worker
