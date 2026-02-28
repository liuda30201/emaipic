from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{stamp}_{uuid4().hex[:8]}"
