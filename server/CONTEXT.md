# GPX Video Overlay — Server

## Назначение

Сервер принимает видео (MOV/MP4) и GPX, синхронизирует метрики по времени, рендерит прозрачный WebM-оверлей в headless Chrome (Playwright) и склеивает результат через ffmpeg.

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
  1. video_start_time: QuickTime creationdate → mvhd → start_time из формы
  2. parse_gpx (скорость Haversine 10с, мощность 3с, каденс 3с)
  3. offset = video_start - gpx_first_point
  4. timeline (1 сэмпл/сек) → Playwright → overlay.webm
  5. ffmpeg overlay + libx265 → output.MOV
```

## API

- `POST /process` — multipart: `video`, `gpx`, опционально `start_time` (ISO 8601)
- `GET /status/{job_id}` — `{ job_id, status, progress, error }`
- `GET /result/{job_id}` — `output.MOV` при `status == "done"`
- `DELETE /job/{job_id}` — удаление файлов и записи в Redis

Статусы: `queued` → `processing` → `done` | `error`

Прогресс: 10% GPX, 30% overlay, 60–99% ffmpeg, 100% готово.

## Модули

- `app/gpx.py` — парсинг и сглаживание метрик (из `export.py`)
- `app/sync.py` — время видео, timeline, ffprobe
- `app/renderer.py` — Playwright + `static/overlay.html`
- `static/overlay.html` — `renderFrame` идентичен `src/overlay.js`

## Запуск

```bash
# Шрифт: скачать Oxanium-Bold.ttf в server/fonts/
docker compose up --build
```

## Railway (два сервиса)

| Сервис | Config file | Команда |
|--------|-------------|---------|
| API | `railway.json` | `uvicorn ... --port $PORT` |
| Worker | `worker.railway.json` | `celery -A app.worker worker --loglevel=info --concurrency=1` |

Оба сервиса — один образ (`server/Dockerfile`), общий `REDIS_URL`.

### Worker (шаг 4)

1. **+ New** → **GitHub Repo** → тот же репозиторий, ветка `main`.
2. Переименовать сервис, например `worker`.
3. **Settings → Config-as-code** → `worker.railway.json`.
4. **Variables** → `REDIS_URL` = reference на Redis-сервис (не копировать URL вручную).
5. **Networking** → публичный домен **не нужен** (можно выключить Public Networking).
6. Deploy → в логах: `celery@... ready`.

План Hobby: для worker лучше **512 MB+** RAM (Playwright + ffmpeg).

## Ограничения

- Время начала видео должно быть в метаданных файла или передано как `start_time`
- Максимальный размер видео: `MAX_VIDEO_SIZE_MB` (500 по умолчанию)
- Рендер оверлея требует Chromium (Playwright) в образе worker
