from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from libs.common.api import create_app, fail, success_response
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.models import CleanIssue, InvoiceFields
from libs.common.paths import ai_clean_dir, ai_raw_dir


app = create_app("ai_cleaner")


def status_path(batch_id: str) -> Path:
    return ai_raw_dir(batch_id) / "status.json"


def results_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "results.json"


def get_dispatch_status(batch_id: str) -> dict[str, Any]:
    manifest = read_json(status_path(batch_id))
    if not manifest:
        fail(f"dispatch status missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_results(batch_id: str) -> dict[str, Any]:
    manifest = read_json(results_path(batch_id))
    if not manifest:
        fail(f"clean results missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_date(value: Any, issues: list[dict[str, Any]]) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    candidates = [
        ("%Y-%m-%d", text),
        ("%Y/%m/%d", text),
        ("%Y.%m.%d", text),
        ("%Y%m%d", text),
    ]
    for fmt, raw in candidates:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    issues.append(CleanIssue(field="invoice_date", reason="invalid_date", raw_value=text).model_dump())
    return None


def normalize_decimal(field_name: str, value: Any, issues: list[dict[str, Any]]) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    normalized = text.replace(",", "")
    try:
        amount = Decimal(normalized).quantize(Decimal("0.01"))
        return f"{amount:.2f}"
    except InvalidOperation:
        issues.append(CleanIssue(field=field_name, reason="invalid_decimal", raw_value=text).model_dump())
        return None


def parse_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    response_text = payload.get("response_text")
    if isinstance(response_text, dict):
        return response_text
    if response_text is None:
        return {}
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return {}


def normalize_record(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    normalized = InvoiceFields(
        invoice_no=clean_text(raw.get("invoice_no")),
        invoice_date=normalize_date(raw.get("invoice_date"), issues),
        buyer_name=clean_text(raw.get("buyer_name")),
        seller_name=clean_text(raw.get("seller_name")),
        service_name=clean_text(raw.get("service_name")),
        amount=normalize_decimal("amount", raw.get("amount"), issues),
        tax=normalize_decimal("tax", raw.get("tax"), issues),
        total=normalize_decimal("total", raw.get("total"), issues),
    ).model_dump()
    return normalized, issues


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "ai_cleaner", "status": "ok"})


@app.post("/api/batches/{batch_id}/clean")
def clean_batch(batch_id: str) -> dict[str, Any]:
    dispatch_status = get_dispatch_status(batch_id)
    results: list[dict[str, Any]] = []
    for page_entry in dispatch_status.get("pages", []):
        base_dir = ai_clean_dir(batch_id) / page_entry["page_id"] / page_entry["extraction_id"]
        base_dir.mkdir(parents=True, exist_ok=True)
        raw_response = read_json(ai_raw_dir(batch_id) / page_entry["page_id"] / page_entry["extraction_id"] / "response.json", default={}) or {}
        parsed = parse_response_payload(raw_response)
        normalized, issues = normalize_record(parsed)
        write_json_atomic(base_dir / "normalized.json", normalized)
        write_json_atomic(base_dir / "issues.json", issues)
        results.append(
            {
                "page_id": page_entry["page_id"],
                "extraction_id": page_entry["extraction_id"],
                "status": "done" if page_entry.get("status") == "done" else "failed",
                "normalized": normalized,
                "issues": issues,
            }
        )

    manifest = {"batch_id": batch_id, "status": "done", "pages": results}
    write_json_atomic(results_path(batch_id), manifest)
    append_batch_log(batch_id, "ai_cleaner", f"Cleaned {len(results)} page responses.")
    return success_response({"batch_id": batch_id, "status": "done", "page_count": len(results)})


@app.get("/api/batches/{batch_id}/results")
def batch_results(batch_id: str) -> dict[str, Any]:
    return success_response(get_results(batch_id))
