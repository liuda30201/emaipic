from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from libs.common.sample_data import ensure_mock_mailbox_assets


if __name__ == "__main__":
    outputs = ensure_mock_mailbox_assets()
    print("Seeded mock assets:")
    for output in outputs:
        print(output)
