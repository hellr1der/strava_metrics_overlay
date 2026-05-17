# Временные файлы

Сюда складывайте локальные тестовые данные — в git не попадают (см. `.gitignore`).

| Папка | Назначение |
|-------|------------|
| `uploads/` | Исходные видео и GPX для ручных тестов |
| `output/` | Результаты `export.py` и локальных прогонов |
| `jobs/` | Задачи API при локальном запуске сервера (`JOBS_DIR`) |

Пример:

```bash
python export.py --video tmp/uploads/ride.MOV --gpx tmp/uploads/ride.gpx --output tmp/output/ride.MOV
```
