# Strava Metrics Overlay

Наложение метрик из GPX (Strava) на видео тренировки: скорость, мощность, пульс, каденс.

## Структура проекта

```
├── index.html, src/     — веб-превью и экспорт WebM-оверлея
├── export.py            — CLI: GPX + видео → output (ffmpeg)
├── server/              — API и фоновая обработка (Docker)
├── sample/              — тестовые GPX и sync.json
├── tmp/                 — временные файлы (видео, результаты, jobs)
├── fonts/               — Oxanium-Bold.ttf (скачать вручную)
└── CONTEXT.md           — контекст и планы
```

## Быстрый старт (веб)

```bash
npx serve .
```

Откройте в Chrome. Загрузите GPX и видео (MP4/MOV с метаданными `creation_time`).

Шрифт: [Oxanium Bold](https://fonts.google.com/specimen/Oxanium) → `fonts/Oxanium-Bold.ttf`.

## CLI (export.py)

```bash
python export.py \
  --video tmp/uploads/ride.MOV \
  --gpx tmp/uploads/ride.gpx \
  --output tmp/output/ride.MOV
```

Нужны: Python 3.11+, ffmpeg, шрифт в `fonts/`.

С готовым WebM-оверлеем из браузера:

```bash
python export.py --video ... --gpx ... --overlay tmp/output/overlay.webm --output ...
```

## Сервер

```bash
cd server
# fonts/Oxanium-Bold.ttf
docker compose up --build
```

API: http://localhost:8000 — см. [server/CONTEXT.md](server/CONTEXT.md).

## Временные файлы

| Путь | Назначение |
|------|------------|
| `tmp/uploads/` | Исходники для локальных тестов |
| `tmp/output/` | Результаты export.py |
| `tmp/jobs/` | Задачи API (локально без Docker) |

Файлы в `tmp/` не коммитятся.

## Требования к входным файлам

| Файл | Требование |
|------|------------|
| GPX | `<time>` в каждой `<trkpt>` |
| Видео | Сохранённые метаданные времени записи (не пересжатое в мессенджере) |

Если метаданных нет — укажите `start_time` (веб, API) или `--start-time` (CLI).
