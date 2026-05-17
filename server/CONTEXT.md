# GPX Video Overlay — Server

## Назначение

HTTP API: принимает видео (MOV/MP4) и GPX, в фоне накладывает метрики и отдаёт `output.MOV`.

**Production:** https://stravametricsoverlay-production.up.railway.app

## Архитектура (один контейнер)

```text
┌─────────────────────────────────────────┐
│  start.sh (LF!)                         │
│  ├── celery worker (фон)                │
│  └── uvicorn app.main:app → :PORT       │
└─────────────────────────────────────────┘
         │                    │
         ▼                    ▼
    Redis (Celery +        JOBS_DIR/
     job status)           {job_id}/
```

| Компонент | Роль |
|-----------|------|
| **FastAPI** | `POST /process`, статус, выдача файла |
| **Celery** | `app.tasks.process_video` |
| **Redis** | Брокер + `job:{id}` в `job_store` |
| **Volume** | `/tmp/jobs` — видео, GPX, overlay, результат |

Отдельный Railway-сервис worker **не используется** (нет shared disk с загрузками).

## Поток `process_video`

```text
1. load_job_meta, find video.* + *.gpx
2. resolve_video_start_time(video, start_time?, sync.json?)
3. assert_gpx_covers_video(points, video_start, duration)
4. save_job_meta(+ video_start, gpx_start, offset_sec, time_source)
5. build_metric_timeline() — 1 sample/sec
6. render_overlay_sync() — Playwright PNG 30fps → overlay.webm
7. build_overlay_mux_command() — ffmpeg → output.MOV
```

### Рендер оверлея (`renderer.py`)

- Открывает `static/overlay.html` (file://)
- `initRenderer({ timeline, width, height, duration, fps: 30 })`
- Для каждого кадра: `renderAt(t)` → screenshot PNG (`omit_background=True`)
- ffmpeg: `-framerate 30 -i %05d.png -t {duration} -c:v libvpx-vp9 -pix_fmt yuva420p`

### Mux (`mux.py`)

```text
ffmpeg -i video \
  -c:v libvpx-vp9 -i overlay.webm \
  -filter_complex "[1:v]format=yuva420p,scale=WxH[ov];[0:v][ov]overlay=0:0:format=auto[out]" \
  -map [out] [-map 0:a] -shortest -c:v libx264|libx265 ...
```

- **`libvpx-vp9` на входе overlay обязателен** — иначе альфа не читается, видео чёрное/пустое
- **`colorkey` не используется** — ломает полупрозрачные тени

## API

### `POST /process`

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `video` | file | да | MOV/MP4 |
| `gpx` | file | да | GPX той же тренировки |
| `start_time` | form | нет | ISO 8601 override |
| `sync` | file | нет | JSON с `video_start_time` |

Ответ: `{ "job_id": "...", "status": "queued" }`

### `GET /status/{job_id}`

```json
{ "job_id": "...", "status": "processing", "progress": 30, "error": null }
```

### `GET /result/{job_id}`

Файл `output.MOV` при `status == "done"`.

### `GET /health`

`{ "status": "ok", "redis": "ok" }`

## Модули `app/`

| Файл | Содержание |
|------|------------|
| `main.py` | FastAPI routes |
| `tasks.py` | Celery task, ffmpeg progress |
| `gpx.py` | parse_gpx, Strava namespace, smoothing |
| `sync.py` | video start chain, timeline, ffprobe |
| `mux.py` | pick_video_encoder, build_overlay_mux_command |
| `renderer.py` | Playwright PNG pipeline |
| `job_store.py` | Redis + meta.json |
| `config.py` | JOBS_DIR, MAX_VIDEO_SIZE_MB, REDIS_URL |

`static/overlay.html` — кадровый рендер (`initRenderer` / `renderAt`), без MediaRecorder.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `REDIS_URL` | — | Celery + статусы |
| `JOBS_DIR` | `/tmp/jobs` | Хранилище задач |
| `PORT` | `8000` | uvicorn (Railway подставляет) |
| `MAX_VIDEO_SIZE_MB` | `500` | Лимит загрузки |
| `CELERY_CONCURRENCY` | `1` | Воркеры Celery |

## Локальный запуск

```bash
cd server
# fonts/Oxanium.ttf — в Docker качается в Dockerfile
docker compose up --build
```

## Railway

| Параметр | Значение |
|----------|----------|
| Config | `railway.json` (корень репо) |
| Dockerfile | `server/Dockerfile` |
| Start | `sh start.sh` |
| Health | `GET /health` |
| Redis | managed plugin |
| Volume | mount на `JOBS_DIR` |

**Важно:** Networking → target port = `$PORT` (часто 8080).

### Checklist после деплоя

```bash
curl https://stravametricsoverlay-production.up.railway.app/health

curl -X POST .../process -F video=@test.MOV -F gpx=@for_test.gpx
curl .../status/{job_id}
curl -o out.MOV .../result/{job_id}
```

Ожидание: ~5 с видео, ~9 MB, метрики на 0:00 ~585 W / 149 BPM (для `for_test.gpx`).

## Ограничения

- Playwright + Chromium в образе (~1 GB+ RAM на Hobby)
- Время записи в метаданных видео или `start_time` / `sync`
- Один Celery concurrency = 1 длинная задача блокирует очередь
- FIT не поддерживается

## Масштабирование (позже)

Переход на S3 для входов/выходов, если:

- несколько реплик worker на разных машинах;
- очень большие файлы или долгое хранение;
- нужна отказоустойчивость при падении контейнера.
