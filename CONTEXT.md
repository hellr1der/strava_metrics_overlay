# Strava Metrics Overlay — контекст и логика

## Идея

Наложение тренировочных метрик на видео. Источник данных — **GPX** (экспорт Strava/Garmin). Синхронизация — по **абсолютному времени UTC**: в момент `t` секунд от начала ролика показываем метрики GPX для момента `video_start + t`.

## Компоненты

| Часть | Роль |
|-------|------|
| **Веб** (`index.html`, `src/`) | Превью HUD поверх `<video>`, панель синхронизации, экспорт `sync.json` и WebM |
| **CLI** (`export.py`) | Локальная обработка: drawtext (ffmpeg) или mux готового WebM |
| **Сервер** (`server/`) | `POST /process` → Celery → Playwright → ffmpeg → `output.MOV` |

Веб и сервер используют одну модель синхронизации (`src/sync.js` ↔ `server/app/sync.py`).

---

## Синхронизация времени

### Отображаемый оффсет

```
offset_sec = video_start_time − gpx_first_point_time
```

Пример: GPX с 07:22:02, видео с 07:24:11 → **+2m 9s (129 с)**. Это значит: в `0:00` видео берутся данные GPX с 07:24:11, а не с начала трека.

### Как выбирается `video_start_time` (авто)

Цепочка (как во фронте, `server/app/sync.py`):

1. `sync.json` из формы/API (если передан)
2. `start_time` / `--start-time` (ручной ISO 8601)
3. **QuickTime** `com.apple.quicktime.creationdate` (скан 512 байт после ключа — важно для Meta)
4. **mvhd** в контейнере MOV
5. **ffprobe** — теги `creation_time` / `com.apple.quicktime.creationdate`

Если GPX не перекрывает интервал видео — ошибка с понятным текстом; нужен GPX той же поездки или ручной `start_time`.

### Таймлайн метрик

Для длительности видео `D` секунд строится массив из `ceil(D)` сэмплов (1 Гц):

```text
для t = 0 .. D-1:
  target = video_start + t секунд
  point = ближайшая точка GPX к target  // бинарный поиск, как getMetricAt() во фронте
```

В оверлее Playwright для кадра `frame` время `t = frame / 30`.

---

## Парсинг GPX (`server/app/gpx.py`)

- Точки с `<time>`, lat/lon, extensions
- **Strava namespace:** `trkpt.find("extensions")` не работает без `{http://www.topografix.com/GPX/1/1}extensions` — исправлено
- Теги: `hr`, `cad`, `power` (в т.ч. `gpxtpx:hr` через `local_name`)
- **Сглаживание** (как в `src/gpx.js`):
  - скорость — Haversine, окно 10 с
  - мощность — среднее, окно 3 с
  - каденс — 3 с; обнуляется, если нет мощности

---

## Пайплайн сервера (production)

```text
POST /process
  → сохранение video.* + track.gpx в JOBS_DIR/{job_id}/
  → Celery: process_video

process_video:
  1. resolve_video_start_time()
  2. assert_gpx_covers_video()
  3. build_metric_timeline() → meta.json (video_start, gpx_start, offset, time_source)
  4. Playwright: initRenderer → renderAt(t) → PNG (alpha) → ffmpeg → overlay.webm (VP9 yuva420p)
  5. mux: libvpx-vp9 decode overlay + overlay format=auto + -shortest → output.MOV
```

### Оверлей и альфа

| Этап | Детали |
|------|--------|
| Рендер | `static/overlay.html` — тот же `renderFrame`, что `src/overlay.js` |
| Кадры | PNG с прозрачным фоном (`omit_background=True`), 30 fps |
| Кодирование | `libvpx-vp9`, `yuva420p` |
| Mux | **Без `colorkey`** — тени полупрозрачные, не «чёрный фон» |
| Декод overlay | **`-c:v libvpx-vp9`** перед вторым `-i` — иначе альфа теряется |

Раньше использовали MediaRecorder + `await wait(33ms)` — таймлайн метрик «плыл» относительно видео; заменено на покадровый PNG.

### Модули

| Модуль | Назначение |
|--------|------------|
| `gpx.py` | Парсинг, сглаживание |
| `sync.py` | Время видео, timeline, ffprobe, валидация пересечения |
| `renderer.py` | Playwright → PNG → WebM |
| `mux.py` | `build_overlay_mux_command()` — общий с `export.py` |
| `tasks.py` | Celery-задача, прогресс ffmpeg |

---

## API

| Метод | Описание |
|-------|----------|
| `GET /health` | `{ status, redis }` |
| `POST /process` | `video`, `gpx`; опционально `start_time`, `sync` |
| `GET /status/{id}` | `queued` → `processing` → `done` \| `error`, `progress` 0–100 |
| `GET /result/{id}` | `output.MOV` |
| `DELETE /job/{id}` | Удаление job |

Прогресс: ~10% GPX/sync, ~30% overlay, 60–99% mux, 100% done.

---

## Деплой (Railway)

- Один сервис **strava_metrics_overlay**: `railway.json` → `server/start.sh` (uvicorn + Celery)
- Redis: `REDIS_URL`
- Volume: `JOBS_DIR=/tmp/jobs`
- **Не использовать** отдельный worker-сервис (нет общего диска с API)
- `start.sh` — только **LF** (CRLF ломает `set -e` в Linux)

Отдельный `worker.railway.json` удалён как артефакт.

---

## FIT vs GPX

Для тестовой пары `for_test.gpx` / `for_test.fit` (одна тренировка): **3355 точек**, те же HR/power/cadence. Strava-GPX уже полный.

Имеет смысл добавлять FIT позже, если появятся кейсы «пустой GPX, полный FIT» или нужны поля только из FIT. Сейчас **достаточно GPX**.

---

## История ключевых изменений

| Проблема | Решение |
|---------|---------|
| 502 / wrong port на Railway | Networking → порт `$PORT` / 8080 |
| Orphan worker без общего volume | Один контейнер API+Celery |
| Метрики 0 W / пустой таймлайн | `video_start + t` без лишнего `+ offset` в timeline |
| Неверный QuickTime на Meta | Скан 512 байт после `creationdate` |
| GPX без power/hr (Strava) | Парсинг extensions в GPX 1.1 namespace |
| `sync.json` вручную | Авто-синхронизация; sync опционален |
| MediaRecorder desync | PNG → VP9, кадр = `frame/fps` |
| Чёрный ореол у текста | Убран `colorkey`; mux по альфе VP9 |
| Чёрное видео после убора colorkey | Декод overlay: `-c:v libvpx-vp9` |
| `start.sh` crash loop | LF line endings + `.gitattributes` |
| Дублирование export/server | `export.py` импортирует `server/app`; `mux.py` общий |

---

## Дальше

- Telegram-бот поверх API
- Ручная подстройка оффсета в UI (слайдер)
- S3 + раздельные воркеры при масштабировании
- Опционально FIT как fallback
