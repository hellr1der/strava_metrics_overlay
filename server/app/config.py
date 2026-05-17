import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JOBS_DIR = Path(os.getenv("JOBS_DIR", str(REPO_ROOT / "tmp" / "jobs")))
STATIC_DIR = Path(__file__).parent.parent / "static"
FONTS_DIR = Path(__file__).parent.parent / "fonts"
MAX_VIDEO_SIZE_MB = 500
