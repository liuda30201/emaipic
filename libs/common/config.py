from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.getenv("APP_DATA_ROOT", PROJECT_ROOT / "data")).resolve()
SAMPLES_ROOT = PROJECT_ROOT / "samples"
LOG_ROOT = DATA_ROOT / "logs"


def ensure_base_dirs() -> None:
    for path in (
        DATA_ROOT,
        DATA_ROOT / "raw",
        DATA_ROOT / "staging",
        DATA_ROOT / "processed",
        DATA_ROOT / "ai_raw",
        DATA_ROOT / "ai_clean",
        DATA_ROOT / "db",
        DATA_ROOT / "exports",
        LOG_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)
