# Тестовые данные

## Рекомендуемая связка (E2E)

| Файл | Описание |
|------|----------|
| `../test.MOV` | Короткое видео съёмки (~5 с), в корне репо, **не в git** |
| `../for_test.gpx` | Реальный Strava GPX той же тренировки (Ušće, 2026-05-15) |

Проверка без `sync.json`:

```bash
curl -X POST https://stravametricsoverlay-production.up.railway.app/process \
  -F "video=@test.MOV" -F "gpx=@for_test.gpx"
```

Ожидаемая синхронизация: QuickTime **07:24:11 UTC**, GPX старт **07:22:02**, оффсет **+129 с**.

## Прочие файлы

| Файл | Описание |
|------|----------|
| `test.gpx` | Синтетический GPX (smoke API; **не** привязан к реальному видео) |
| `test-alt.gpx` | Дополнительный трек |
| `test-sync.json` | Пример override для `export.py --sync` |
| `../for_test.fit` | Тот же трек в FIT (для сравнения; сервер FIT не читает) |

Результаты E2E: `e2e-*.MOV` в gitignore.

Видео для локальных тестов: `tmp/uploads/`.
