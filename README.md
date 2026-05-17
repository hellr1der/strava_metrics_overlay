# Strava Metrics Overlay

Наложение метрик из GPX (Strava/Garmin) на видео тренировки: скорость, мощность, пульс, каденс. Синхронизация по реальному времени — **достаточно загрузить видео и GPX с той же поездки**.

**Production API:** https://stravametricsoverlay-production.up.railway.app

Подробная логика и история доработок — в [CONTEXT.md](CONTEXT.md). Сервер и деплой — в [server/CONTEXT.md](server/CONTEXT.md).

## Как это работает (кратко)

1. **Время начала видео** берётся из метаданных файла (QuickTime `creationdate` → `mvhd` → ffprobe). Вручную — только если метаданные битые.
2. **GPX** парсится с учётом Strava namespace (`gpxtpx:hr`, `<power>` в extensions).
3. Для каждой секунды ролика: `target_time = video_start + t` → ближайшая точка GPX → метрики на экран.
4. **Оверлей** рисуется в headless Chrome (как в веб-превью), покадрово в PNG с альфой, кодируется в VP9 WebM, накладывается на видео через ffmpeg **без** вырезания чёрного фона (`colorkey`).

Оффсет `video_start − gpx_start` (например +2m 9s) — это не «сдвиг HUD», а пояснение в UI: видео началось позже старта трека.

## Структура проекта

```
├── index.html, src/       — веб-превью и экспорт WebM из браузера
├── export.py              — локальный CLI (drawtext или WebM-оверлей)
├── server/                — API + Celery + Playwright + ffmpeg (Docker)
│   └── app/               — gpx, sync, renderer, mux, tasks
├── sample/                — тестовые GPX, пример sync.json
├── for_test.gpx           — реальный Strava GPX для test.MOV
├── fonts/                 — Oxanium для CLI (локально)
└── CONTEXT.md             — логика, API, changelog
```

## Быстрый старт (веб-превью)

```bash
npx serve .
```

Откройте в **Chrome**. Загрузите GPX и видео (MOV/MP4 с метаданными времени записи).

Шрифт для локального CLI: [Oxanium](https://fonts.google.com/specimen/Oxanium) → `fonts/Oxanium-Bold.ttf` (см. `fonts/README.md`). В Docker-образе шрифт подтягивается автоматически.

## API (production / локально)

```bash
# Минимум: только видео + GPX
curl -X POST https://stravametricsoverlay-production.up.railway.app/process \
  -F "video=@ride.MOV" \
  -F "gpx=@ride.gpx"

# Статус
curl https://stravametricsoverlay-production.up.railway.app/status/{job_id}

# Скачать результат
curl -o output.MOV https://stravametricsoverlay-production.up.railway.app/result/{job_id}
```

Опционально:

- `start_time` — ISO 8601, если метаданные видео неверны
- `sync` — JSON из кнопки «Экспорт sync.json» в превью (переопределение)

## CLI (`export.py`)

Общая логика GPX/sync с сервером (`server/app`).

```bash
python export.py \
  --video tmp/uploads/ride.MOV \
  --gpx tmp/uploads/ride.gpx \
  --output tmp/output/ride.MOV
```

С WebM-оверлеем из браузера (тот же mux, что на сервере):

```bash
python export.py --video ride.MOV --gpx ride.gpx \
  --overlay tmp/output/overlay.webm --output ride_out.MOV
```

Нужны: Python 3.11+, ffmpeg, шрифт в `fonts/` (режим drawtext без overlay).

## Сервер (локально)

```bash
cd server
docker compose up --build
```

API: http://localhost:8000 — см. [server/CONTEXT.md](server/CONTEXT.md).

## Тестовая связка

| Файл | Назначение |
|------|------------|
| `test.MOV` | Короткое видео (~5 с), локально, не в git |
| `for_test.gpx` | Реальный Strava GPX той же тренировки (Ušće) |
| `sample/test.gpx` | Синтетический GPX для smoke-тестов API |

Проверка E2E: `POST /process` с `test.MOV` + `for_test.gpx` без `sync.json`. На `0:00` ожидаются ~585 W, ~149 BPM, ~42 km/h (зависит от сглаживания).

## Требования к файлам

| Файл | Требование |
|------|------------|
| **GPX** | `<time>` в каждой `<trkpt>`; extensions с `hr`, `power`, `cad` (Strava/Garmin) |
| **Видео** | Метаданные времени записи (Meta/GoPro/iPhone QuickTime). Не пересжатое мессенджером без метаданных |
| **GPX и видео** | Одна и та же тренировка, пересечение по времени |

Формат **FIT** пока не поддерживается; для Strava-экспорта полный GPX обычно достаточен (см. [CONTEXT.md](CONTEXT.md)#fit-vs-gpx).

## Временные файлы

| Путь | Назначение |
|------|------------|
| `tmp/uploads/` | Исходники для локальных тестов |
| `tmp/output/` | Результаты `export.py` |
| `tmp/jobs/` | Задачи API (локально) |

В git не коммитятся (`*.MOV`, `sample/e2e-*.MOV`).
