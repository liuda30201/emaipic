from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.ids import new_id
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.models import MODEL_SELECTION_LIMIT
from libs.common.paths import ai_raw_dir, processed_dir


PROMPT_V1 = """识别附件内容，按 JSON 数组返回。若非发票，is_invoice 设为 false 且其余字段留空。禁止输出解释，仅输出 JSON。

字段定义：
is_invoice: (Boolean) 是否为发票
num: 发票号码
date: 日期 (YYYY-MM-DD)
item: 货物/劳务
buyer: 购买方
seller: 销售方
amt: 不含税金额
tax: 税额
total: 价税合计

示例格式：
[{"is_invoice":true,"num":"123","date":"2026-03-01","item":"服务","buyer":"A","seller":"B","amt":"100","tax":"6","total":"106"},{"is_invoice":false,"num":"","date":"","item":"","buyer":"","seller":"","amt":"","tax":"","total":""}]"""

PROMPT_V2 = """识别附件内容，按 JSON 数组返回。若非发票，is_invoice 设为 false，其他字段留空字符串，items 返回空数组。禁止输出解释，仅输出 JSON。

字段定义：
is_invoice: (Boolean) 是否为发票
num: 发票号码
date: 日期 (YYYY-MM-DD)
buyer: 购买方
seller: 销售方
items: 明细数组，元素结构为 {name,spec,unit,qty,price,amount,tax}
amt: 不含税金额
tax: 税额
total: 价税合计

示例格式：
[{"is_invoice":true,"num":"123","date":"2026-03-01","buyer":"A","seller":"B","items":[{"name":"服务","spec":"","unit":"项","qty":"1","price":"100","amount":"100","tax":"6"}],"amt":"100","tax":"6","total":"106"},{"is_invoice":false,"num":"","date":"","buyer":"","seller":"","items":[],"amt":"","tax":"","total":""}]"""


class DispatchBody(BaseModel):
    models: list[str] = Field(default_factory=lambda: ["mock"])
    prompt_version: str = "v1"


app = create_app("ai_dispatcher")


def processed_manifest_path(batch_id: str) -> Path:
    return processed_dir(batch_id) / "manifest.json"


def status_manifest_path(batch_id: str) -> Path:
    return ai_raw_dir(batch_id) / "status.json"


def model_hub_url() -> str:
    return os.getenv("MODEL_HUB_URL", "http://localhost:3008").rstrip("/")


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


def normalize_models(items: list[str]) -> list[str]:
    models: list[str] = []
    for item in items:
        key = str(item).strip()
        if key and key not in models:
            models.append(key)
    if not models:
        models = ["mock"]
    if len(models) > MODEL_SELECTION_LIMIT:
        fail(f"models supports at most {MODEL_SELECTION_LIMIT} entries", code="too_many_models")
    return models


def prompt_for(version: str) -> str:
    prompts = {
        "v1": PROMPT_V1,
        "v2": PROMPT_V2,
    }
    if version not in prompts:
        fail("Only prompt_version=v1|v2 is supported", code="invalid_prompt_version")
    return prompts[version]


def infer_with_model_hub(
    model_key: str,
    prompt: str,
    page: dict[str, Any],
    run_id: str,
    prompt_version: str,
) -> tuple[int, dict[str, Any]]:
    request_body = {
        "model_key": model_key,
        "prompt": prompt,
        "image": {"type": "path", "value": page["image_relpath"]},
        "meta": {
            "batch_id": page["batch_id"],
            "page_id": page["page_id"],
            "run_id": run_id,
            "doc_id": page["doc_id"],
            "file_id": page["file_id"],
            "prompt_version": prompt_version,
        },
    }
    with httpx.Client(timeout=120.0) as client:
        response = client.post(f"{model_hub_url()}/api/infer", json=request_body)
    payload: dict[str, Any]
    try:
        payload = response.json()
    except ValueError:
        payload = {"success": False, "data": None, "error": {"message": response.text}}
    return response.status_code, {"request": request_body, "payload": payload}


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "ai_dispatcher", "status": "ok"})


@app.post("/api/batches/{batch_id}/dispatch")
def dispatch_batch(batch_id: str, body: DispatchBody | None = None) -> dict[str, Any]:
    payload = body or DispatchBody()
    selected_models = normalize_models(payload.models)
    prompt = prompt_for(payload.prompt_version)
    processed_manifest = get_processed_manifest(batch_id)

    page_records: list[dict[str, Any]] = []
    total_runs = 0
    failed_runs = 0

    for page in processed_manifest.get("pages", []):
        model_runs: list[dict[str, Any]] = []
        for model_key in selected_models:
            run_id = new_id("run")
            base_dir = ai_raw_dir(batch_id) / page["page_id"] / model_key / run_id
            base_dir.mkdir(parents=True, exist_ok=True)
            request_payload = {
                "batch_id": batch_id,
                "doc_id": page["doc_id"],
                "file_id": page["file_id"],
                "page_id": page["page_id"],
                "model_key": model_key,
                "run_id": run_id,
                "prompt_version": payload.prompt_version,
                "prompt": prompt,
                "image": {"type": "path", "value": page.get("image_relpath")},
                "page": page,
            }

            if page.get("status") == "failed" or not page.get("image_relpath"):
                response_payload = {
                    "success": False,
                    "data": None,
                    "error": {"code": "page_unavailable", "message": "page has no processed image"},
                }
                run_status = "failed"
                latency_ms = None
                error_message = "page has no processed image"
            else:
                status_code, infer_payload = infer_with_model_hub(model_key, prompt, page, run_id, payload.prompt_version)
                upstream = infer_payload["payload"]
                response_payload = {
                    "http_status": status_code,
                    "upstream": upstream,
                }
                if status_code >= 400 or not upstream.get("success", False):
                    run_status = "failed"
                    error_message = (upstream.get("error") or {}).get("message", f"Model call failed: {model_key}")
                    latency_ms = None
                else:
                    run_status = "done"
                    error_message = None
                    latency_ms = (upstream.get("data") or {}).get("latency_ms")

            write_json_atomic(base_dir / "request.json", request_payload)
            write_json_atomic(base_dir / "response.json", response_payload)
            total_runs += 1
            if run_status == "failed":
                failed_runs += 1

            model_runs.append(
                {
                    "model_key": model_key,
                    "run_id": run_id,
                    "status": run_status,
                    "latency_ms": latency_ms,
                    "error": error_message,
                    "prompt_version": payload.prompt_version,
                    "request_relpath": str((base_dir / "request.json").relative_to(DATA_ROOT)),
                    "response_relpath": str((base_dir / "response.json").relative_to(DATA_ROOT)),
                }
            )

        page_status = "done" if any(item["status"] == "done" for item in model_runs) else "failed"
        if any(item["status"] == "failed" for item in model_runs) and page_status == "done":
            page_status = "partial"
        page_records.append(
            {
                "page_id": page["page_id"],
                "doc_id": page["doc_id"],
                "file_id": page["file_id"],
                "status": page_status,
                "models": model_runs,
            }
        )

    manifest = {
        "batch_id": batch_id,
        "status": "done",
        "prompt_version": payload.prompt_version,
        "selected_models": selected_models,
        "summary": {
            "page_count": len(page_records),
            "total_runs": total_runs,
            "failed_runs": failed_runs,
        },
        "pages": page_records,
    }
    write_json_atomic(status_manifest_path(batch_id), manifest)
    append_batch_log(
        batch_id,
        "ai_dispatcher",
        f"Dispatched {len(page_records)} pages across {len(selected_models)} models (failed_runs={failed_runs}).",
    )
    return success_response(
        {
            "batch_id": batch_id,
            "status": "done",
            "page_count": len(page_records),
            "selected_models": selected_models,
            "failed_runs": failed_runs,
        }
    )


@app.get("/api/batches/{batch_id}/status")
def batch_status(batch_id: str) -> dict[str, Any]:
    return success_response(get_status(batch_id))


@app.get("/api/batches/{batch_id}/raw/{page_id}/{model_key}/{run_id}")
def get_raw_payloads(batch_id: str, page_id: str, model_key: str, run_id: str) -> dict[str, Any]:
    base_dir = ai_raw_dir(batch_id) / page_id / model_key / run_id
    request_payload = read_json(base_dir / "request.json")
    response_payload = read_json(base_dir / "response.json")
    if request_payload is None or response_payload is None:
        fail("raw payload not found", status_code=404, code="not_found")
    return success_response({"request": request_payload, "response": response_payload})
