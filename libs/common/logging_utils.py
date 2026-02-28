from __future__ import annotations

from datetime import datetime
from pathlib import Path

import fcntl

from libs.common.config import LOG_ROOT


def append_batch_log(batch_id: str, service_name: str, message: str) -> Path:
    batch_log_dir = LOG_ROOT / batch_id
    batch_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = batch_log_dir / "run.log"
    lock_path = batch_log_dir / "run.log.lock"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{service_name}] {message}\n"
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(line)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return log_path
