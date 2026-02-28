from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import fcntl


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        with tmp_path.open("w", encoding="utf-8") as tmp_handle:
            json.dump(payload, tmp_handle, ensure_ascii=False, indent=2)
            tmp_handle.flush()
            os.fsync(tmp_handle.fileno())
        os.replace(tmp_path, path)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
