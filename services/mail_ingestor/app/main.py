from __future__ import annotations

import email
import imaplib
import mimetypes
import os
from email.header import decode_header
from pathlib import Path
from typing import Any

from fastapi import File, UploadFile

from libs.common.api import create_app, fail, success_response
from libs.common.config import SAMPLES_ROOT
from libs.common.ids import new_id
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.paths import raw_dir
from libs.common.sample_data import ensure_mock_mailbox_assets
from libs.common.storage import LocalStorage


app = create_app("mail_ingestor")
storage = LocalStorage()


def manifest_path(batch_id: str) -> Path:
    return raw_dir(batch_id) / "ingest_manifest.json"


def decode_text(value: Any) -> str:
    if not value:
        return ""
    decoded = decode_header(str(value))
    chunks: list[str] = []
    for part, encoding in decoded:
        if isinstance(part, bytes):
            chunks.append(part.decode(encoding or "utf-8", errors="ignore"))
        else:
            chunks.append(str(part))
    return "".join(chunks)


def build_base_manifest(batch_id: str, source: str) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "source": source,
        "status": "pending",
        "notes": [],
        "documents": [],
        "files": [],
    }


def get_manifest_or_404(batch_id: str) -> dict[str, Any]:
    manifest = read_json(manifest_path(batch_id))
    if not manifest:
        fail(f"batch {batch_id} not found", status_code=404, code="not_found")
    return manifest


def save_manifest(batch_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    write_json_atomic(manifest_path(batch_id), manifest)
    return manifest


def store_attachment(batch_id: str, doc_id: str, source_file: Path, original_name: str) -> dict[str, Any]:
    file_id = new_id("file")
    target_name = f"{file_id}_{Path(original_name).name}"
    relative_path = Path("raw") / batch_id / "attachments" / target_name
    storage.copy_to(source_file, relative_path)
    mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    return {
        "file_id": file_id,
        "doc_id": doc_id,
        "filename": Path(original_name).name,
        "content_type": mime_type,
        "size": source_file.stat().st_size,
        "relpath": str(relative_path),
    }


def pull_mock(batch_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    ensure_mock_mailbox_assets()
    mailbox_root = SAMPLES_ROOT / "mailbox"
    manifest["documents"] = []
    manifest["files"] = []
    for mail_file in sorted(mailbox_root.glob("mail_*.json")):
        mail_payload = read_json(mail_file, default={}) or {}
        doc_id = new_id("doc")
        doc_record = {
            "doc_id": doc_id,
            "subject": mail_payload.get("subject"),
            "from": mail_payload.get("from"),
            "received_at": mail_payload.get("received_at"),
            "links": mail_payload.get("links", []),
            "source_mail": mail_file.name,
            "file_ids": [],
        }
        for attachment_name in mail_payload.get("attachments", []):
            source_file = mailbox_root / "attachments" / attachment_name
            if not source_file.exists():
                fail(f"mock attachment missing: {attachment_name}", status_code=500, code="missing_sample")
            file_record = store_attachment(batch_id, doc_id, source_file, attachment_name)
            manifest["files"].append(file_record)
            doc_record["file_ids"].append(file_record["file_id"])
        manifest["documents"].append(doc_record)
    manifest["status"] = "done"
    manifest["notes"] = ["Pulled from local mock mailbox samples."]
    append_batch_log(batch_id, "mail_ingestor", f"Pulled {len(manifest['files'])} files from mock mailbox.")
    return manifest


def pull_imap(batch_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    host = os.getenv("IMAP_HOST")
    port = int(os.getenv("IMAP_PORT", "993"))
    username = os.getenv("IMAP_USERNAME")
    password = os.getenv("IMAP_PASSWORD")
    folder = os.getenv("IMAP_FOLDER", "INBOX")
    if not all([host, username, password]):
        fail("IMAP is not configured. Set IMAP_HOST/IMAP_USERNAME/IMAP_PASSWORD.", status_code=400, code="imap_not_configured")

    batch_root = raw_dir(batch_id)
    temp_dir = batch_root / "_imap_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    manifest["documents"] = []
    manifest["files"] = []
    with imaplib.IMAP4_SSL(host, port) as client:
        client.login(username, password)
        client.select(folder)
        _, message_ids = client.search(None, "ALL")
        ids = [item for item in message_ids[0].split() if item][-10:]
        for message_id in ids:
            _, data = client.fetch(message_id, "(RFC822)")
            raw_message = data[0][1]
            message = email.message_from_bytes(raw_message)
            doc_id = new_id("doc")
            doc_record = {
                "doc_id": doc_id,
                "subject": decode_text(message.get("Subject")),
                "from": decode_text(message.get("From")),
                "received_at": decode_text(message.get("Date")),
                "links": [],
                "source_mail": f"imap:{message_id.decode()}",
                "file_ids": [],
            }
            for part in message.walk():
                content_disposition = part.get("Content-Disposition", "")
                if "attachment" not in content_disposition.lower():
                    continue
                filename = decode_text(part.get_filename()) or "attachment.bin"
                temp_file = temp_dir / filename
                temp_file.write_bytes(part.get_payload(decode=True) or b"")
                file_record = store_attachment(batch_id, doc_id, temp_file, filename)
                manifest["files"].append(file_record)
                doc_record["file_ids"].append(file_record["file_id"])
            manifest["documents"].append(doc_record)
    manifest["status"] = "done"
    manifest["notes"] = ["Pulled from IMAP inbox."]
    append_batch_log(batch_id, "mail_ingestor", f"Pulled {len(manifest['files'])} files from IMAP.")
    return manifest


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "mail_ingestor", "status": "ok"})


@app.post("/api/batches/create")
def create_batch(source: str = "mock") -> dict[str, Any]:
    if source not in {"mock", "imap", "upload"}:
        fail("source must be mock, imap, or upload")
    batch_id = new_id("batch")
    manifest = build_base_manifest(batch_id, source)
    save_manifest(batch_id, manifest)
    append_batch_log(batch_id, "mail_ingestor", f"Created batch with source={source}.")
    return success_response({"batch_id": batch_id})


@app.post("/api/batches/{batch_id}/pull")
def pull_batch(batch_id: str) -> dict[str, Any]:
    manifest = get_manifest_or_404(batch_id)
    source = manifest.get("source", "mock")
    manifest["status"] = "processing"
    save_manifest(batch_id, manifest)
    if source == "mock":
        manifest = pull_mock(batch_id, manifest)
    elif source == "imap":
        manifest = pull_imap(batch_id, manifest)
    else:
        fail("upload batches should use /upload instead of /pull", code="invalid_operation")
    save_manifest(batch_id, manifest)
    return success_response({"batch_id": batch_id, "status": manifest["status"], "file_count": len(manifest["files"])})


@app.post("/api/batches/{batch_id}/upload")
async def upload_files(batch_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    manifest = get_manifest_or_404(batch_id)
    if manifest.get("source") != "upload":
        fail("batch source must be upload", code="invalid_batch_source")
    manifest["status"] = "processing"
    manifest["documents"] = []
    manifest["files"] = []
    doc_id = new_id("doc")
    doc_record = {
        "doc_id": doc_id,
        "subject": "Local upload",
        "from": "local@upload",
        "received_at": None,
        "links": [],
        "source_mail": "local_upload",
        "file_ids": [],
    }
    for upload in files:
        file_id = new_id("file")
        target_name = f"{file_id}_{Path(upload.filename or 'upload.bin').name}"
        relative_path = Path("raw") / batch_id / "attachments" / target_name
        payload = await upload.read()
        storage.write_bytes(relative_path, payload)
        file_record = {
            "file_id": file_id,
            "doc_id": doc_id,
            "filename": Path(upload.filename or "upload.bin").name,
            "content_type": upload.content_type or "application/octet-stream",
            "size": len(payload),
            "relpath": str(relative_path),
        }
        manifest["files"].append(file_record)
        doc_record["file_ids"].append(file_id)
    manifest["documents"] = [doc_record]
    manifest["status"] = "done"
    manifest["notes"] = ["Pulled from local upload."]
    save_manifest(batch_id, manifest)
    append_batch_log(batch_id, "mail_ingestor", f"Stored {len(files)} uploaded files.")
    return success_response({"batch_id": batch_id, "status": "done", "file_count": len(files)})


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    manifest = get_manifest_or_404(batch_id)
    return success_response(manifest)
