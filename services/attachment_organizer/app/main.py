from __future__ import annotations

from pathlib import Path
from typing import Any

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.hashing import sha256_file
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.paths import raw_dir, staging_dir
from libs.common.storage import LocalStorage


app = create_app("attachment_organizer")
storage = LocalStorage()


def ingest_manifest_path(batch_id: str) -> Path:
    return raw_dir(batch_id) / "ingest_manifest.json"


def staging_manifest_path(batch_id: str) -> Path:
    return staging_dir(batch_id) / "staging_manifest.json"


def get_ingest_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(ingest_manifest_path(batch_id))
    if not manifest:
        fail(f"ingest manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_staging_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(staging_manifest_path(batch_id))
    if not manifest:
        fail(f"staging manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "attachment_organizer", "status": "ok"})


@app.post("/api/batches/{batch_id}/organize")
def organize_batch(batch_id: str) -> dict[str, Any]:
    ingest_manifest = get_ingest_manifest(batch_id)
    files = []
    for file_entry in ingest_manifest.get("files", []):
        original_relpath = Path(file_entry["relpath"])
        source_path = DATA_ROOT / original_relpath
        if not source_path.exists():
            fail(f"raw file missing: {source_path}", status_code=500, code="missing_raw")
        extension = Path(file_entry["filename"]).suffix.lower() or ".bin"
        target_relpath = Path("staging") / batch_id / "files" / file_entry["doc_id"] / f"{file_entry['file_id']}{extension}"
        target_path = storage.copy_to(source_path, target_relpath)
        files.append(
            {
                "file_id": file_entry["file_id"],
                "doc_id": file_entry["doc_id"],
                "filename": file_entry["filename"],
                "content_type": file_entry["content_type"],
                "file_type": extension.lstrip(".") or "bin",
                "sha256": sha256_file(target_path),
                "size": file_entry.get("size", source_path.stat().st_size),
                "source_relpath": file_entry["relpath"],
                "relpath": str(target_relpath),
                "path": str(target_relpath),
            }
        )
    manifest = {
        "batch_id": batch_id,
        "status": "done",
        "documents": ingest_manifest.get("documents", []),
        "files": files,
        "notes": ["Attachments normalized into staging layout."],
    }
    write_json_atomic(staging_manifest_path(batch_id), manifest)
    append_batch_log(batch_id, "attachment_organizer", f"Organized {len(files)} files into staging.")
    return success_response({"batch_id": batch_id, "status": "done", "file_count": len(files)})


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    return success_response(get_staging_manifest(batch_id))
