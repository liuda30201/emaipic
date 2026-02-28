from __future__ import annotations

import base64
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.ids import new_id
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.paths import ai_raw_dir, processed_dir


app = create_app("ai_dispatcher")


def processed_manifest_path(batch_id: str) -> Path:
    return processed_dir(batch_id) / "manifest.json"


def status_manifest_path(batch_id: str) -> Path:
    return ai_raw_dir(batch_id) / "status.json"


def get_processed_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(processed_manifest_path(batch_id))
    if not manifest:
        fail(f"processed manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_status(batch_id: str) -> dict[str, Any]:
    manifest = read_json(status_manifest_path(batch_id))
    if not manifest:
        fail(f"dispatch status missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def mock_extract(page: dict[str, Any]) -> str:
    seed = sum(ord(char) for char in page["page_id"])
    sequence = 10000 + (seed % 89999)
    invoice_date = date(2026, 1, 1) + timedelta(days=seed % 28)
    amount = Decimal("88.00") + Decimal(seed % 300)
    tax = (amount * Decimal("0.06")).quantize(Decimal("0.01"))
    total = (amount + tax).quantize(Decimal("0.01"))
    payload = {
        "invoice_no": f"INV-{sequence}",
        "invoice_date": invoice_date.isoformat(),
        "buyer_name": f"测试购买方{seed % 9}",
        "seller_name": f"测试销售方{seed % 7}",
        "service_name": f"服务项目{page['page_no']}",
        "amount": f"{amount:.2f}",
        "tax": f"{tax:.2f}",
        "total": f"{total:.2f}",
    }
    return json.dumps(payload, ensure_ascii=False)


def encode_image_as_data_url(image_path: Path) -> str:
    mime_type = "image/jpeg"
    if image_path.suffix.lower() == ".webp":
        mime_type = "image/webp"
    raw = image_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def real_extract(page: dict[str, Any], prompt: str) -> tuple[str, str]:
    provider = os.getenv("MODEL_PROVIDER", "").lower()
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("QWEN_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model_name = os.getenv("REAL_MODEL_NAME", "gpt-4o-mini")
    if provider not in {"openai", "qwen"} or not api_key:
        return mock_extract(page), "mock_fallback"

    image_path = DATA_ROOT / page["image_relpath"]
    data_url = encode_image_as_data_url(image_path)
    request_body = {
        "model": model_name,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Extract invoice fields and return strict JSON only.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        return str(content), provider
    except Exception:
        return mock_extract(page), "mock_fallback"


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "ai_dispatcher", "status": "ok"})


@app.post("/api/batches/{batch_id}/dispatch")
def dispatch_batch(batch_id: str, mode: str = "mock", prompt_version: str = "v1") -> dict[str, Any]:
    if mode not in {"mock", "real"}:
        fail("mode must be mock or real")
    processed_manifest = get_processed_manifest(batch_id)
    page_records: list[dict[str, Any]] = []
    for page in processed_manifest.get("pages", []):
        extraction_id = new_id("ext")
        base_dir = ai_raw_dir(batch_id) / page["page_id"] / extraction_id
        base_dir.mkdir(parents=True, exist_ok=True)
        prompt = (
            f"prompt_version={prompt_version}. "
            "Extract invoice_no, invoice_date, buyer_name, seller_name, "
            "service_name, amount, tax, total."
        )
        request_payload = {
            "batch_id": batch_id,
            "page_id": page["page_id"],
            "prompt_version": prompt_version,
            "mode_requested": mode,
            "prompt": prompt,
            "image_relpath": page.get("image_relpath"),
            "page": page,
        }

        if page.get("status") == "failed" or not page.get("image_relpath"):
            response_text = json.dumps({})
            effective_mode = "skipped"
            status = "failed"
            error = "page has no processed image"
        elif mode == "real":
            response_text, effective_mode = real_extract(page, prompt)
            status = "done"
            error = None
        else:
            response_text = mock_extract(page)
            effective_mode = "mock"
            status = "done"
            error = None

        response_payload = {
            "response_text": response_text,
            "mode_used": effective_mode,
            "error": error,
        }
        write_json_atomic(base_dir / "request.json", request_payload)
        write_json_atomic(base_dir / "response.json", response_payload)
        page_records.append(
            {
                "page_id": page["page_id"],
                "extraction_id": extraction_id,
                "status": status,
                "mode_used": effective_mode,
                "request_relpath": str((base_dir / "request.json").relative_to(DATA_ROOT)),
                "response_relpath": str((base_dir / "response.json").relative_to(DATA_ROOT)),
                "error": error,
            }
        )

    manifest = {
        "batch_id": batch_id,
        "status": "done",
        "mode_requested": mode,
        "prompt_version": prompt_version,
        "pages": page_records,
    }
    write_json_atomic(status_manifest_path(batch_id), manifest)
    append_batch_log(batch_id, "ai_dispatcher", f"Dispatched {len(page_records)} pages using mode={mode}.")
    return success_response({"batch_id": batch_id, "status": "done", "page_count": len(page_records)})


@app.get("/api/batches/{batch_id}/status")
def batch_status(batch_id: str) -> dict[str, Any]:
    return success_response(get_status(batch_id))


@app.get("/api/batches/{batch_id}/raw/{page_id}/{extraction_id}")
def get_raw_payloads(batch_id: str, page_id: str, extraction_id: str) -> dict[str, Any]:
    base_dir = ai_raw_dir(batch_id) / page_id / extraction_id
    request_payload = read_json(base_dir / "request.json")
    response_payload = read_json(base_dir / "response.json")
    if request_payload is None or response_payload is None:
        fail("raw payload not found", status_code=404, code="not_found")
    return success_response({"request": request_payload, "response": response_payload})
