from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import File, Request, UploadFile
from fastapi.responses import Response

from libs.common.api import create_app, fail, success_response
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.paths import db_dir


app = create_app("orchestrator", enable_cors=True)
STATUS_DIR = db_dir() / "orchestrator"
STATUS_DIR.mkdir(parents=True, exist_ok=True)

STEP_ORDER = [
    "ingest",
    "organize",
    "process",
    "dispatch",
    "clean",
    "index",
]

job_queue: queue.Queue[dict[str, Any]] = queue.Queue()
worker_started = False
worker_lock = threading.Lock()


def service_url(name: str) -> str:
    defaults = {
        "mail_ingestor": "http://localhost:3002",
        "attachment_organizer": "http://localhost:3003",
        "image_preprocessor": "http://localhost:3004",
        "ai_dispatcher": "http://localhost:3005",
        "ai_cleaner": "http://localhost:3006",
        "index_export": "http://localhost:3007",
    }
    env_name = f"{name.upper()}_URL"
    return os.getenv(env_name, defaults[name]).rstrip("/")


def status_path(batch_id: str) -> Path:
    return STATUS_DIR / f"{batch_id}.json"


def default_steps() -> list[dict[str, Any]]:
    return [{"name": step, "status": "pending", "detail": None} for step in STEP_ORDER]


def read_status(batch_id: str) -> dict[str, Any]:
    payload = read_json(status_path(batch_id))
    if not payload:
        fail(f"batch {batch_id} not found", status_code=404, code="not_found")
    return payload


def write_status(payload: dict[str, Any]) -> None:
    write_json_atomic(status_path(payload["batch_id"]), payload)


def set_step(batch_id: str, step_name: str, status: str, detail: str | None = None) -> dict[str, Any]:
    payload = read_status(batch_id)
    for step in payload["steps"]:
        if step["name"] == step_name:
            step["status"] = status
            step["detail"] = detail
            break
    if status == "failed":
        payload["status"] = "failed"
        payload["error"] = {"step": step_name, "message": detail}
    elif all(step["status"] == "done" for step in payload["steps"]):
        payload["status"] = "done"
        payload["error"] = None
    else:
        payload["status"] = "processing"
    write_status(payload)
    return payload


def client() -> httpx.Client:
    return httpx.Client(timeout=120.0)


def extract_data(response: httpx.Response) -> Any:
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success", False):
        raise RuntimeError(payload.get("error", {}).get("message", "unknown service error"))
    return payload.get("data")


def call_json(method: str, url: str, **kwargs: Any) -> Any:
    with client() as session:
        response = session.request(method, url, **kwargs)
    return extract_data(response)


def init_batch_status(batch_id: str, source: str, profile: str, mode: str) -> dict[str, Any]:
    payload = {
        "batch_id": batch_id,
        "source": source,
        "profile": profile,
        "mode": mode,
        "status": "pending",
        "steps": default_steps(),
        "error": None,
    }
    write_status(payload)
    return payload


def run_pipeline_job(job: dict[str, Any]) -> None:
    batch_id = job["batch_id"]
    source = job["source"]
    profile = job["profile"]
    mode = job["mode"]
    prompt_version = job.get("prompt_version", "v1")
    try:
        if source in {"mock", "imap"}:
            set_step(batch_id, "ingest", "processing", f"Pulling from {source}")
            call_json("POST", f"{service_url('mail_ingestor')}/api/batches/{batch_id}/pull")
            set_step(batch_id, "ingest", "done", "Attachments pulled")
        else:
            set_step(batch_id, "ingest", "done", "Local upload stored")

        set_step(batch_id, "organize", "processing", "Organizing attachments")
        call_json("POST", f"{service_url('attachment_organizer')}/api/batches/{batch_id}/organize")
        set_step(batch_id, "organize", "done", "Staging ready")

        set_step(batch_id, "process", "processing", f"Processing with {profile}")
        call_json("POST", f"{service_url('image_preprocessor')}/api/batches/{batch_id}/process", params={"profile": profile})
        set_step(batch_id, "process", "done", "Pages generated")

        set_step(batch_id, "dispatch", "processing", f"Dispatching via {mode}")
        call_json(
            "POST",
            f"{service_url('ai_dispatcher')}/api/batches/{batch_id}/dispatch",
            params={"mode": mode, "prompt_version": prompt_version},
        )
        set_step(batch_id, "dispatch", "done", "AI raw responses stored")

        set_step(batch_id, "clean", "processing", "Normalizing AI output")
        call_json("POST", f"{service_url('ai_cleaner')}/api/batches/{batch_id}/clean")
        set_step(batch_id, "clean", "done", "Normalized results ready")

        set_step(batch_id, "index", "processing", "Indexing into SQLite")
        call_json("POST", f"{service_url('index_export')}/api/batches/{batch_id}/index")
        set_step(batch_id, "index", "done", "Indexed and searchable")
        append_batch_log(batch_id, "orchestrator", "Full pipeline finished.")
    except Exception as exc:
        append_batch_log(batch_id, "orchestrator", f"Pipeline failed: {exc}")
        current = read_status(batch_id)
        if current["status"] != "failed":
            for step in current["steps"]:
                if step["status"] == "processing":
                    set_step(batch_id, step["name"], "failed", str(exc))
                    break


def worker_loop() -> None:
    while True:
        job = job_queue.get()
        try:
            run_pipeline_job(job)
        finally:
            job_queue.task_done()


def ensure_worker() -> None:
    global worker_started
    with worker_lock:
        if worker_started:
            return
        thread = threading.Thread(target=worker_loop, daemon=True)
        thread.start()
        worker_started = True


@app.on_event("startup")
def on_startup() -> None:
    ensure_worker()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "orchestrator", "status": "ok", "queue_size": job_queue.qsize()})


@app.post("/api/run/full")
def run_full(source: str = "mock", profile: str = "prod_default", mode: str = "mock") -> dict[str, Any]:
    if source not in {"mock", "imap"}:
        fail("source must be mock or imap")
    batch = call_json("POST", f"{service_url('mail_ingestor')}/api/batches/create", params={"source": source})
    batch_id = batch["batch_id"]
    payload = init_batch_status(batch_id, source, profile, mode)
    job_queue.put({"batch_id": batch_id, "source": source, "profile": profile, "mode": mode, "prompt_version": "v1"})
    append_batch_log(batch_id, "orchestrator", f"Enqueued full pipeline source={source}, profile={profile}, mode={mode}.")
    return success_response(payload)


@app.post("/api/run/upload")
async def run_upload(profile: str = "prod_default", mode: str = "mock", files: list[UploadFile] = File(...)) -> dict[str, Any]:
    batch = call_json("POST", f"{service_url('mail_ingestor')}/api/batches/create", params={"source": "upload"})
    batch_id = batch["batch_id"]

    multipart_files = []
    try:
        for upload in files:
            content = await upload.read()
            multipart_files.append(("files", (upload.filename or "upload.bin", content, upload.content_type or "application/octet-stream")))
        with client() as session:
            response = session.post(f"{service_url('mail_ingestor')}/api/batches/{batch_id}/upload", files=multipart_files)
        extract_data(response)
    finally:
        for upload in files:
            await upload.close()

    payload = init_batch_status(batch_id, "upload", profile, mode)
    set_step(batch_id, "ingest", "done", "Uploaded files stored")
    job_queue.put({"batch_id": batch_id, "source": "upload", "profile": profile, "mode": mode, "prompt_version": "v1"})
    append_batch_log(batch_id, "orchestrator", f"Enqueued upload pipeline with {len(multipart_files)} files.")
    return success_response(read_status(batch_id))


@app.get("/api/batches/{batch_id}/status")
def batch_status(batch_id: str) -> dict[str, Any]:
    payload = read_status(batch_id)
    extras: dict[str, Any] = {}
    try:
        extras["pages"] = call_json("GET", f"{service_url('image_preprocessor')}/api/batches/{batch_id}/pages")["pages"]
    except Exception:
        extras["pages"] = []
    try:
        extras["results"] = call_json("GET", f"{service_url('ai_cleaner')}/api/batches/{batch_id}/results")["pages"]
    except Exception:
        extras["results"] = []
    payload["pages"] = extras["pages"]
    payload["results"] = extras["results"]
    return success_response(payload)


@app.get("/api/invoices/search")
def search_invoices(
    start_date: str | None = None,
    end_date: str | None = None,
    buyer: str | None = None,
    seller: str | None = None,
    invoice_no: str | None = None,
    min_total: str | None = None,
    max_total: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "buyer": buyer,
        "seller": seller,
        "invoice_no": invoice_no,
        "min_total": min_total,
        "max_total": max_total,
        "batch_id": batch_id,
    }
    data = call_json("GET", f"{service_url('index_export')}/api/invoices/search", params={k: v for k, v in params.items() if v not in (None, "")})
    for item in data.get("items", []):
        item["thumb_url"] = f"/api/assets/thumb/{item['batch_id']}/{item['page_id']}"
        item["image_url"] = f"/api/assets/image/{item['batch_id']}/{item['page_id']}"
    return success_response(data)


@app.post("/api/exports")
async def create_export(request: Request) -> dict[str, Any]:
    body = await request.json()
    data = call_json("POST", f"{service_url('index_export')}/api/exports", json=body)
    data["csv_url"] = f"/api/exports/{data['export_id']}/csv"
    data["zip_url"] = f"/api/exports/{data['export_id']}/zip"
    return success_response(data)


def proxy_binary(url: str) -> Response:
    with client() as session:
        upstream = session.get(url)
    upstream.raise_for_status()
    headers = {}
    content_type = upstream.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    content_disposition = upstream.headers.get("content-disposition")
    if content_disposition:
        headers["content-disposition"] = content_disposition
    return Response(content=upstream.content, media_type=content_type, headers=headers)


@app.get("/api/exports/{export_id}/csv")
def export_csv(export_id: str) -> Response:
    return proxy_binary(f"{service_url('index_export')}/api/exports/{export_id}/csv")


@app.get("/api/exports/{export_id}/zip")
def export_zip(export_id: str) -> Response:
    return proxy_binary(f"{service_url('index_export')}/api/exports/{export_id}/zip")


@app.get("/api/assets/thumb/{batch_id}/{page_id}")
def proxy_thumb(batch_id: str, page_id: str) -> Response:
    return proxy_binary(f"{service_url('index_export')}/api/assets/thumb/{batch_id}/{page_id}")


@app.get("/api/assets/image/{batch_id}/{page_id}")
def proxy_image(batch_id: str, page_id: str) -> Response:
    return proxy_binary(f"{service_url('index_export')}/api/assets/image/{batch_id}/{page_id}")
