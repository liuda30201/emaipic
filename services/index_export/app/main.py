from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body
from fastapi.responses import FileResponse
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

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

FILES_COLUMNS = [
    "file_id",
    "batch_id",
    "doc_id",
    "filename",
    "original_relpath",
    "staging_relpath",
    "content_type",
    "file_type",
    "sha256",
    "size",
    "created_at",
]
PAGES_COLUMNS = [
    "page_id",
    "batch_id",
    "file_id",
    "doc_id",
    "page_no",
    "profile",
    "image_relpath",
    "thumb_relpath",
    "original_relpath",
    "status",
    "created_at",
]
INVOICES_COLUMNS = [
    "page_id",
    "model_key",
    "run_id",
    "record_index",
    "is_invoice",
    "num",
    "date",
    "buyer",
    "seller",
    "item",
    "service_summary",
    "prompt_version",
    "normalized_payload",
    "amt",
    "tax",
    "total",
    "created_at",
    "batch_id",
    "file_id",
    "doc_id",
]
MODEL_RUNS_COLUMNS = [
    "page_id",
    "model_key",
    "run_id",
    "prompt_version",
    "status",
    "record_count",
    "is_invoice_detected",
    "error_reason",
    "error_message",
    "created_at",
    "batch_id",
    "file_id",
    "doc_id",
]


def table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row[1] for row in rows]


def ensure_schema() -> None:
    INDEX_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        expected = {
            "files": FILES_COLUMNS,
            "pages": PAGES_COLUMNS,
            "invoices": INVOICES_COLUMNS,
            "model_runs": MODEL_RUNS_COLUMNS,
        }
        for table_name, columns in expected.items():
            existing = table_columns(connection, table_name)
            if existing and existing != columns:
                connection.execute(f"DROP TABLE IF EXISTS {table_name}")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                file_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_relpath TEXT NOT NULL,
                staging_relpath TEXT NOT NULL,
                content_type TEXT,
                file_type TEXT,
                sha256 TEXT,
                size INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pages (
                page_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                profile TEXT NOT NULL,
                image_relpath TEXT,
                thumb_relpath TEXT,
                original_relpath TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                page_id TEXT NOT NULL,
                model_key TEXT NOT NULL,
                run_id TEXT NOT NULL,
                record_index INTEGER NOT NULL,
                is_invoice INTEGER NOT NULL,
                num TEXT,
                date TEXT,
                buyer TEXT,
                seller TEXT,
                item TEXT,
                service_summary TEXT,
                prompt_version TEXT NOT NULL,
                normalized_payload TEXT,
                amt TEXT,
                tax TEXT,
                total TEXT,
                created_at TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                PRIMARY KEY (page_id, model_key, run_id, record_index)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_runs (
                page_id TEXT NOT NULL,
                model_key TEXT NOT NULL,
                run_id TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                status TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                is_invoice_detected INTEGER,
                error_reason TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                PRIMARY KEY (page_id, model_key, run_id)
            )
            """
        )
        connection.commit()


def open_connection() -> sqlite3.Connection:
    ensure_schema()
    return sqlite3.connect(DB_PATH)


def batch_status_path(batch_id: str) -> Path:
    return INDEX_STATUS_DIR / f"{batch_id}.json"


def staging_manifest_path(batch_id: str) -> Path:
    return staging_dir(batch_id) / "staging_manifest.json"


def processed_manifest_path(batch_id: str) -> Path:
    return processed_dir(batch_id) / "manifest.json"


def clean_results_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "normalized_results.jsonl"


def clean_summary_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "results.json"


def issues_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "issues.jsonl"


def get_staging_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(staging_manifest_path(batch_id))
    if not manifest:
        fail(f"staging manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_processed_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(processed_manifest_path(batch_id))
    if not manifest:
        fail(f"processed manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_batch_status(batch_id: str) -> dict[str, Any]:
    payload = read_json(batch_status_path(batch_id))
    if not payload:
        fail(f"index status missing for batch {batch_id}", status_code=404, code="not_found")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        fail(f"jsonl file missing: {path.name}", status_code=404, code="not_found")
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        items.append(json.loads(stripped))
    return items


def export_csv(records: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "batch_id",
            "doc_id",
            "file_id",
            "page_id",
            "page_no",
            "result_status",
            "error_reason",
            "error_message",
            "model_key",
            "run_id",
            "record_index",
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


def export_items_csv(records: list[dict[str, Any]]) -> str | None:
    rows: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("normalized_payload") or {}
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "batch_id": record.get("batch_id"),
                    "doc_id": record.get("doc_id"),
                    "file_id": record.get("file_id"),
                    "page_id": record.get("page_id"),
                    "model_key": record.get("model_key"),
                    "run_id": record.get("run_id"),
                    "record_index": record.get("record_index"),
                    "item_index": index,
                    "name": item.get("name"),
                    "spec": item.get("spec"),
                    "unit": item.get("unit"),
                    "qty": item.get("qty"),
                    "price": item.get("price"),
                    "amount": item.get("amount"),
                    "tax": item.get("tax"),
                }
            )
    if not rows:
        return None
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "batch_id",
            "doc_id",
            "file_id",
            "page_id",
            "model_key",
            "run_id",
            "record_index",
            "item_index",
            "name",
            "spec",
            "unit",
            "qty",
            "price",
            "amount",
            "tax",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def build_combined_pdf(records: list[dict[str, Any]], target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    unique_pages: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda item: (item["batch_id"], item["page_no"], item["page_id"])):
        unique_pages.setdefault(record["page_id"], record)

    pdf = canvas.Canvas(str(target_path))
    if not unique_pages:
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 720, "No invoice pages selected.")
        pdf.showPage()
        pdf.save()
        return

    rendered = 0
    for record in unique_pages.values():
        image_path = DATA_ROOT / record["image_relpath"]
        if not image_path.exists():
            continue
        with Image.open(image_path) as image:
            width, height = image.size
        pdf.setPageSize((width, height))
        pdf.drawImage(ImageReader(str(image_path)), 0, 0, width=width, height=height)
        pdf.showPage()
        rendered += 1
    if rendered == 0:
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 720, "No rendered invoice pages available.")
        pdf.showPage()
    pdf.save()


def lookup_page_asset(batch_id: str, page_id: str, column_name: str) -> Path:
    with open_connection() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT {column_name} AS relpath FROM pages WHERE batch_id = ? AND page_id = ?",
            (batch_id, page_id),
        ).fetchone()
    if not row or not row["relpath"]:
        fail("asset not found", status_code=404, code="not_found")
    target = DATA_ROOT / row["relpath"]
    if not target.exists():
        fail("asset file missing", status_code=404, code="not_found")
    return target


def create_export_bundle(export_id: str, records: list[dict[str, Any]], filters: SearchFilters) -> dict[str, str]:
    export_dir = exports_dir(export_id)
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    csv_path = export_dir / "invoices.csv"
    items_csv_path = export_dir / "invoice_items.csv"
    combined_pdf_path = export_dir / "pdf" / "combined.pdf"
    zip_path = export_dir / "bundle.zip"

    csv_path.write_text(export_csv(records), encoding="utf-8")
    items_csv_content = export_items_csv(records)
    if items_csv_content:
        items_csv_path.write_text(items_csv_content, encoding="utf-8")
    build_combined_pdf(records, combined_pdf_path)

    added_files: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(csv_path, "invoices.csv")
        if items_csv_path.exists():
            archive.write(items_csv_path, "invoice_items.csv")
        if combined_pdf_path.exists():
            archive.write(combined_pdf_path, "pdf/combined.pdf")

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
                staging_dir(batch_id) / "staging_manifest.json",
                processed_dir(batch_id) / "manifest.json",
                ai_clean_dir(batch_id) / "normalized_results.jsonl",
                ai_clean_dir(batch_id) / "issues.jsonl",
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
        "combined_pdf_relpath": str(combined_pdf_path.relative_to(DATA_ROOT)),
    }
    write_json_atomic(export_dir / "meta.json", meta)
    return {"csv": str(csv_path), "zip": str(zip_path), "combined_pdf": str(combined_pdf_path)}


ensure_schema()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "index_export", "status": "ok"})


@app.post("/api/batches/{batch_id}/index")
def index_batch(batch_id: str) -> dict[str, Any]:
    staging_manifest = get_staging_manifest(batch_id)
    processed_manifest = get_processed_manifest(batch_id)
    normalized_records = read_jsonl(clean_results_path(batch_id))
    clean_summary = read_json(clean_summary_path(batch_id), default={}) or {}
    indexed_at = datetime.utcnow().isoformat()

    with open_connection() as connection:
        connection.execute("DELETE FROM invoices WHERE batch_id = ?", (batch_id,))
        connection.execute("DELETE FROM model_runs WHERE batch_id = ?", (batch_id,))
        connection.execute("DELETE FROM pages WHERE batch_id = ?", (batch_id,))
        connection.execute("DELETE FROM files WHERE batch_id = ?", (batch_id,))

        for file_entry in staging_manifest.get("files", []):
            connection.execute(
                """
                INSERT INTO files (
                    file_id, batch_id, doc_id, filename, original_relpath, staging_relpath,
                    content_type, file_type, sha256, size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_entry["file_id"],
                    batch_id,
                    file_entry["doc_id"],
                    file_entry["filename"],
                    file_entry["source_relpath"],
                    file_entry["relpath"],
                    file_entry.get("content_type"),
                    file_entry.get("file_type"),
                    file_entry.get("sha256"),
                    file_entry.get("size"),
                    indexed_at,
                ),
            )

        for page in processed_manifest.get("pages", []):
            connection.execute(
                """
                INSERT INTO pages (
                    page_id, batch_id, file_id, doc_id, page_no, profile, image_relpath,
                    thumb_relpath, original_relpath, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page["page_id"],
                    batch_id,
                    page["file_id"],
                    page["doc_id"],
                    page["page_no"],
                    page["profile"],
                    page.get("image_relpath"),
                    page.get("thumb_relpath"),
                    page.get("original_relpath"),
                    page.get("status", "done"),
                    indexed_at,
                ),
            )

        for record in normalized_records:
            connection.execute(
                """
                INSERT INTO invoices (
                    page_id, model_key, run_id, record_index, is_invoice, num, date, buyer,
                    seller, item, service_summary, prompt_version, normalized_payload,
                    amt, tax, total, created_at, batch_id, file_id, doc_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["page_id"],
                    record["model_key"],
                    record["run_id"],
                    record["record_index"],
                    1 if record.get("is_invoice") else 0,
                    record.get("num"),
                    record.get("date"),
                    record.get("buyer"),
                    record.get("seller"),
                    record.get("item"),
                    record.get("item"),
                    record.get("prompt_version", "v1"),
                    json.dumps(record.get("normalized_payload") or {}, ensure_ascii=False),
                    record.get("amt"),
                    record.get("tax"),
                    record.get("total"),
                    indexed_at,
                    batch_id,
                    record["file_id"],
                    record["doc_id"],
                ),
            )

        for page_entry in clean_summary.get("pages", []):
            doc_id = page_entry.get("doc_id")
            file_id = page_entry.get("file_id")
            for model_run in page_entry.get("models", []):
                records = model_run.get("records") or []
                issues = model_run.get("issues") or []
                primary_issue = issues[0] if issues else {}
                is_invoice_detected = None
                if records:
                    is_invoice_detected = 1 if any(item.get("is_invoice") for item in records) else 0
                connection.execute(
                    """
                    INSERT INTO model_runs (
                        page_id, model_key, run_id, prompt_version, status, record_count, is_invoice_detected,
                        error_reason, error_message, created_at, batch_id, file_id, doc_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        page_entry["page_id"],
                        model_run["model_key"],
                        model_run["run_id"],
                        model_run.get("prompt_version", clean_summary.get("prompt_version", "v1")),
                        model_run.get("status", "done"),
                        len(records),
                        is_invoice_detected,
                        primary_issue.get("reason") if model_run.get("status") != "done" else None,
                        primary_issue.get("message") if model_run.get("status") != "done" else None,
                        indexed_at,
                        batch_id,
                        file_id,
                        doc_id,
                    ),
                )
        connection.commit()

    status = {
        "batch_id": batch_id,
        "status": "done",
        "file_count": len(staging_manifest.get("files", [])),
        "page_count": len(processed_manifest.get("pages", [])),
        "record_count": len(normalized_records),
        "issue_count": clean_summary.get("issue_count", 0),
        "indexed_at": indexed_at,
    }
    write_json_atomic(batch_status_path(batch_id), status)
    append_batch_log(batch_id, "index_export", f"Indexed {len(normalized_records)} normalized records.")
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
    model_key: str | None = None,
    include_failed: bool = False,
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
        model_key=model_key,
        include_failed=include_failed,
    )
    with open_connection() as connection:
        records = search_invoices(connection, filters)
    return success_response({"items": records, "count": len(records), "filters": filters.model_dump()})


@app.post("/api/exports")
def create_export(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = payload or {}
    export_scope = str(payload.get("export_scope") or "success_only")
    if export_scope not in {"success_only", "all_results"}:
        fail("invalid export_scope", status_code=400, code="invalid_request")

    filters = SearchFilters(
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
        buyer=payload.get("buyer"),
        seller=payload.get("seller"),
        invoice_no=payload.get("invoice_no"),
        min_total=payload.get("min_total"),
        max_total=payload.get("max_total"),
        batch_id=payload.get("batch_id"),
        model_key=payload.get("model_key"),
        include_failed=export_scope == "all_results",
    )
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
    return FileResponse(lookup_page_asset(batch_id, page_id, "thumb_relpath"))


@app.get("/api/assets/image/{batch_id}/{page_id}")
def asset_image(batch_id: str, page_id: str) -> FileResponse:
    return FileResponse(lookup_page_asset(batch_id, page_id, "image_relpath"))
