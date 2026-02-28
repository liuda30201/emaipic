from __future__ import annotations

import shutil
from pathlib import Path

from libs.common.config import DATA_ROOT, ensure_base_dirs


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        ensure_base_dirs()
        self.root = (root or DATA_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        return (self.root / Path(relative_path)).resolve()

    def ensure_dir(self, relative_path: str | Path) -> Path:
        path = self.resolve(relative_path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def copy_to(self, source: Path, relative_path: str | Path) -> Path:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def write_bytes(self, relative_path: str | Path, payload: bytes) -> Path:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target
