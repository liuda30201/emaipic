from __future__ import annotations

import csv
import io
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body
from fastapi.responses import FileResponse

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.ids import new_id
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.models import SearchFilters
from libs.common.paths import ai_clean_dir, db_dir, exports_dir, processed_dir, raw_dir, staging_dir
from services.index_export.app.search import search_invoices


app = create_app("index_export")
DB_PATH = db_dir() / "app.db"
INDEX_STATUS_DIR = db_dir() / "batches"


def ensure_db() -> None:
    INDEX_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                page_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                image_relpath TEXT,
                thumb_relpath TEXT,
                original_relpath TEXT,
                invoice_no TEXT,
                invoice_date TEXT,
                buyer_name TEXT,
                seller_name TEXT,
                service_name TEXT,
                amount TEXT,
                tax TEXT,
                total TEXT,
                extraction_id TEXT,
                indexed_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def batch_status_path(batch_id: str) -> Path:
    return INDEX_STATUS_DIR / f"{batch_id}.json"


def processed_manifest_path(batch_id: str) -> Path:
    return processed_dir(batch_id) / "manifest.json"


def clean_results_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "results.json"


def get_processed_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(processed_manifest_path(batch_id))
    if not manifest:
        fail(f"processed manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_clean_results(batch_id: str) -> dict[str, Any]:
    manifest = read_json(clean_results_path(batch_id))
    if not manifest:
        fail(f"clean results missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_batch_status(batch_id: str) -> dict[str, Any]:
    payload = read_json(batch_status_path(batch_id))
    if not payload:
        fail(f"index status missing for batch {batch_id}", status_code=404, code="not_found")
    return payload


def open_connection() -> sqlite3.Connection:
    ensure_db()
    return sqlite3.connect(DB_PATH)


def export_csv(records: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "batch_id",
            "page_id",
            "file_id",
            "doc_id",
            "page_no",
            "invoice_no",
            "invoice_date",
            "buyer_name",
            "seller_name",
            "service_name",
            "amount",
            "tax",
            "total",
        ],
    )
    writer.writeheader()
    for record in records:
        writer.writerow({key: record.get(key) for key in writer.fieldnames})
    return output.getvalue()


def file_for_page(batch_id: str, page_id: str, key: str) -> Path:
    manifest = get_processed_manifest(batch_id)
    for page in manifest.get("pages", []):
        if page["page_id"] == page_id:
            relpath = page.get(key)
            if not relpath:
                fail("asset missing", status_code=404, code="not_found")
            target = DATA_ROOT / relpath
            if not target.exists():
                fail("asset file missing", status_code=404, code="not_found")
            return target
    fail("page not found", status_code=404, code="not_found")


def create_export_bundle(export_id: str, records: list[dict[str, Any]], filters: SearchFilters) -> dict[str, str]:
    export_dir = exports_dir(export_id)
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    csv_path = export_dir / "invoices.csv"
    zip_path = export_dir / "bundle.zip"
    csv_path.write_text(export_csv(records), encoding="utf-8")

    added_files: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(csv_path, "invoices.csv")
        for record in records:
            image_path = DATA_ROOT / record["image_relpath"] if record.get("image_relpath") else None
            if image_path and image_path.exists():
                archive_name = f"images/{record['batch_id']}_{Path(record['image_relpath']).name}"
                if archive_name not in added_files:
                    archive.write(image_path, archive_name)
                    added_files.add(archive_name)
            original_path = DATA_ROOT / record["original_relpath"] if record.get("original_relpath") else None
            if original_path and original_path.exists():
                archive_name = f"originals/{record['batch_id']}_{Path(record['original_relpath']).name}"
                if archive_name not in added_files:
                    archive.write(original_path, archive_name)
                    added_files.add(archive_name)

        for batch_id in sorted({record["batch_id"] for record in records}):
            candidate_manifests = [
                raw_dir(batch_id) / "ingest_manifest.json",
                staging_dir(batch_id) / "organize_manifest.json",
                processed_dir(batch_id) / "manifest.json",
                ai_clean_dir(batch_id) / "results.json",
            ]
            for manifest in candidate_manifests:
                if manifest.exists():
                    archive.write(manifest, f"manifests/{batch_id}/{manifest.name}")

    meta = {
        "export_id": export_id,
        "record_count": len(records),
        "created_at": datetime.utcnow().isoformat(),
        "filters": filters.model_dump(),
        "csv_relpath": str(csv_path.relative_to(DATA_ROOT)),
        "zip_relpath": str(zip_path.relative_to(DATA_ROOT)),
    }
    write_json_atomic(export_dir / "meta.json", meta)
    return {"csv": str(csv_path), "zip": str(zip_path)}


ensure_db()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "index_export", "status": "ok"})


@app.post("/api/batches/{batch_id}/index")
def index_batch(batch_id: str) -> dict[str, Any]:
    processed_manifest = get_processed_manifest(batch_id)
    clean_results = get_clean_results(batch_id)
    results_by_page = {item["page_id"]: item for item in clean_results.get("pages", [])}
    indexed_at = datetime.utcnow().isoformat()

    with open_connection() as connection:
        for page in processed_manifest.get("pages", []):
            result = results_by_page.get(page["page_id"], {})
            normalized = result.get("normalized", {})
            connection.execute(
                """
                INSERT INTO invoices (
                    page_id, batch_id, file_id, doc_id, page_no, image_relpath, thumb_relpath,
                    original_relpath, invoice_no, invoice_date, buyer_name, seller_name,
                    service_name, amount, tax, total, extraction_id, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    batch_id=excluded.batch_id,
                    file_id=excluded.file_id,
                    doc_id=excluded.doc_id,
                    page_no=excluded.page_no,
                    image_relpath=excluded.image_relpath,
                    thumb_relpath=excluded.thumb_relpath,
                    original_relpath=excluded.original_relpath,
                    invoice_no=excluded.invoice_no,
                    invoice_date=excluded.invoice_date,
                    buyer_name=excluded.buyer_name,
                    seller_name=excluded.seller_name,
                    service_name=excluded.service_name,
                    amount=excluded.amount,
                    tax=excluded.tax,
                    total=excluded.total,
                    extraction_id=excluded.extraction_id,
                    indexed_at=excluded.indexed_at
                """,
                (
                    page["page_id"],
                    batch_id,
                    page["file_id"],
                    page["doc_id"],
                    page["page_no"],
                    page.get("image_relpath"),
                    page.get("thumb_relpath"),
                    page.get("original_relpath"),
                    normalized.get("invoice_no"),
                    normalized.get("invoice_date"),
                    normalized.get("buyer_name"),
                    normalized.get("seller_name"),
                    normalized.get("service_name"),
                    normalized.get("amount"),
                    normalized.get("tax"),
                    normalized.get("total"),
                    result.get("extraction_id"),
                    indexed_at,
                ),
            )
        connection.commit()

    indexed_records = len(processed_manifest.get("pages", []))
    status = {
        "batch_id": batch_id,
        "status": "done",
        "record_count": indexed_records,
        "indexed_at": indexed_at,
    }
    write_json_atomic(batch_status_path(batch_id), status)
    append_batch_log(batch_id, "index_export", f"Indexed {indexed_records} records into SQLite.")
    return success_response(status)


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    return success_response(get_batch_status(batch_id))


@app.get("/api/invoices/search")
def search_endpoint(
    start_date: str | None = None,
    end_date: str | None = None,
    buyer: str | None = None,
    seller: str | None = None,
    invoice_no: str | None = None,
    min_total: str | None = None,
    max_total: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    filters = SearchFilters(
        start_date=start_date,
        end_date=end_date,
        buyer=buyer,
        seller=seller,
        invoice_no=invoice_no,
        min_total=min_total,
        max_total=max_total,
        batch_id=batch_id,
    )
    with open_connection() as connection:
        records = search_invoices(connection, filters)
    return success_response({"items": records, "count": len(records), "filters": filters.model_dump()})


@app.post("/api/exports")
def create_export(filters: SearchFilters = Body(default=SearchFilters())) -> dict[str, Any]:
    with open_connection() as connection:
        records = search_invoices(connection, filters)
    export_id = new_id("export")
    bundle = create_export_bundle(export_id, records, filters)
    return success_response(
        {
            "export_id": export_id,
            "record_count": len(records),
            "csv_url": f"/api/exports/{export_id}/csv",
            "zip_url": f"/api/exports/{export_id}/zip",
            "files": bundle,
        }
    )


@app.get("/api/exports/{export_id}/csv")
def download_csv(export_id: str) -> FileResponse:
    csv_path = exports_dir(export_id) / "invoices.csv"
    if not csv_path.exists():
        fail("export csv not found", status_code=404, code="not_found")
    return FileResponse(csv_path, filename=f"{export_id}.csv", media_type="text/csv")


@app.get("/api/exports/{export_id}/zip")
def download_zip(export_id: str) -> FileResponse:
    zip_path = exports_dir(export_id) / "bundle.zip"
    if not zip_path.exists():
        fail("export zip not found", status_code=404, code="not_found")
    return FileResponse(zip_path, filename=f"{export_id}.zip", media_type="application/zip")


@app.get("/api/assets/thumb/{batch_id}/{page_id}")
def asset_thumb(batch_id: str, page_id: str) -> FileResponse:
    return FileResponse(file_for_page(batch_id, page_id, "thumb_relpath"))


@app.get("/api/assets/image/{batch_id}/{page_id}")
def asset_image(batch_id: str, page_id: str) -> FileResponse:
    return FileResponse(file_for_page(batch_id, page_id, "image_relpath"))
