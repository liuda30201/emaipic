from pathlib import Path

from libs.common.manifests import read_json, write_json_atomic


def test_write_json_atomic_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    payload = {"batch_id": "batch_x", "status": "done"}
    write_json_atomic(target, payload)

    assert read_json(target) == payload
    assert not target.with_suffix(".json.tmp").exists()
