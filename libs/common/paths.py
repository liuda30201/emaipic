from __future__ import annotations

from pathlib import Path

from libs.common.config import DATA_ROOT


def raw_dir(batch_id: str) -> Path:
    return DATA_ROOT / "raw" / batch_id


def staging_dir(batch_id: str) -> Path:
    return DATA_ROOT / "staging" / batch_id


def processed_dir(batch_id: str) -> Path:
    return DATA_ROOT / "processed" / batch_id


def ai_raw_dir(batch_id: str) -> Path:
    return DATA_ROOT / "ai_raw" / batch_id


def ai_clean_dir(batch_id: str) -> Path:
    return DATA_ROOT / "ai_clean" / batch_id


def exports_dir(export_id: str | None = None) -> Path:
    base = DATA_ROOT / "exports"
    return base / export_id if export_id else base


def db_dir() -> Path:
    return DATA_ROOT / "db"
