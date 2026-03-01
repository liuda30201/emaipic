from __future__ import annotations

import base64
import json
import os
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.models import ModelAvailability, ModelDescriptor, ModelInferRequest


app = create_app("model_hub")
MODELS_FILE = Path(__file__).resolve().parents[1] / "models.yaml"


def load_catalog() -> list[ModelDescriptor]:
    payload = yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}
    models = payload.get("models", [])
    return [ModelDescriptor.model_validate(item) for item in models]


def descriptor_by_key(model_key: str) -> ModelDescriptor:
    for descriptor in load_catalog():
        if descriptor.key == model_key:
            return descriptor
    fail(f"Unknown model: {model_key}", status_code=404, code="unknown_model")


def availability_for(descriptor: ModelDescriptor) -> ModelAvailability:
    reason = None
    available = descriptor.enabled
    if not descriptor.enabled:
        available = False
        reason = "Disabled by config"
    elif descriptor.requires_key and descriptor.env_key_name and not os.getenv(descriptor.env_key_name):
        available = False
        reason = "Missing API key"
    return ModelAvailability(
        key=descriptor.key,
        label=descriptor.label,
        provider=descriptor.provider,
        purpose=descriptor.purpose,
        enabled=descriptor.enabled,
        requires_key=descriptor.requires_key,
        env_key_name=descriptor.env_key_name,
        available=available,
        reason=reason,
        default_params=descriptor.default_params,
    )


def resolve_image_value(image_type: str, value: str) -> str:
    if image_type == "url":
        return value
    if image_type != "path":
        fail("image.type must be path or url", code="invalid_image_type")
    candidate = Path(value)
    file_path = candidate if candidate.is_absolute() else (DATA_ROOT / candidate)
    if not file_path.exists():
        fail(f"Image path not found: {value}", status_code=404, code="missing_image")
    mime_type = "image/jpeg"
    if file_path.suffix.lower() == ".webp":
        mime_type = "image/webp"
    raw = file_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def mock_infer(request: ModelInferRequest) -> tuple[str, dict[str, Any]]:
    page_id = str(request.meta.get("page_id", request.image.value))
    prompt_version = str(request.meta.get("prompt_version", "v1"))
    seed = sum(ord(char) for char in page_id)
    sequence = 100000 + (seed % 899999)
    invoice_date = date(2026, 3, 1) - timedelta(days=seed % 17)
    amount = Decimal("88.00") + Decimal(seed % 250)
    tax = (amount * Decimal("0.06")).quantize(Decimal("0.01"))
    total = (amount + tax).quantize(Decimal("0.01"))
    if "non_invoice" in request.image.value.lower():
        if prompt_version == "v2":
            payload = [
                {
                    "is_invoice": False,
                    "num": "",
                    "date": "",
                    "buyer": "",
                    "seller": "",
                    "items": [],
                    "amt": "",
                    "tax": "",
                    "total": "",
                }
            ]
        else:
            payload = [
                {
                    "is_invoice": False,
                    "num": "",
                    "date": "",
                    "item": "",
                    "buyer": "",
                    "seller": "",
                    "amt": "",
                    "tax": "",
                    "total": "",
                }
            ]
    else:
        item_one_amount = (amount * Decimal("0.60")).quantize(Decimal("0.01"))
        item_two_amount = (amount - item_one_amount).quantize(Decimal("0.01"))
        item_one_tax = (item_one_amount * Decimal("0.06")).quantize(Decimal("0.01"))
        item_two_tax = (tax - item_one_tax).quantize(Decimal("0.01"))
        if prompt_version == "v2":
            payload = [
                {
                    "is_invoice": True,
                    "num": f"INV-{sequence}",
                    "date": invoice_date.isoformat(),
                    "buyer": f"测试购买方{seed % 9}",
                    "seller": f"测试销售方{seed % 7}",
                    "items": [
                        {
                            "name": f"服务项目{(seed % 3) + 1}",
                            "spec": "",
                            "unit": "项",
                            "qty": "1",
                            "price": f"{item_one_amount:.2f}",
                            "amount": f"{item_one_amount:.2f}",
                            "tax": f"{item_one_tax:.2f}",
                        },
                        {
                            "name": f"商品名称{(seed % 4) + 1}",
                            "spec": "",
                            "unit": "项",
                            "qty": "1",
                            "price": f"{item_two_amount:.2f}",
                            "amount": f"{item_two_amount:.2f}",
                            "tax": f"{item_two_tax:.2f}",
                        },
                    ],
                    "amt": f"{amount:.2f}",
                    "tax": f"{tax:.2f}",
                    "total": f"{total:.2f}",
                }
            ]
        else:
            payload = [
                {
                    "is_invoice": True,
                    "num": f"INV-{sequence}",
                    "date": invoice_date.isoformat(),
                    "item": f"服务项目{(seed % 3) + 1}",
                    "buyer": f"测试购买方{seed % 9}",
                    "seller": f"测试销售方{seed % 7}",
                    "amt": f"{amount:.2f}",
                    "tax": f"{tax:.2f}",
                    "total": f"{total:.2f}",
                }
            ]
    return json.dumps(payload, ensure_ascii=False), {"adapter": "mock", "items": len(payload), "prompt_version": prompt_version}


def provider_base_url(provider: str) -> str:
    if provider == "glm":
        return os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    if provider == "qwen":
        return os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    fail(f"Unsupported provider: {provider}", code="unsupported_provider")


def provider_api_key(descriptor: ModelDescriptor) -> str:
    if descriptor.env_key_name:
        value = os.getenv(descriptor.env_key_name)
        if value:
            return value
    fail(f"Missing API key for {descriptor.key}", status_code=400, code="missing_api_key")


def extract_response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return json.dumps(payload, ensure_ascii=False)
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                text_parts.append(str(item.get("text", "")))
            else:
                text_parts.append(str(item))
        return "".join(text_parts).strip()
    return str(content or "").strip()


def remote_infer(descriptor: ModelDescriptor, request: ModelInferRequest) -> tuple[str, dict[str, Any]]:
    availability = availability_for(descriptor)
    if not availability.available:
        fail(availability.reason or "Model unavailable", status_code=400, code="model_unavailable")

    image_url = resolve_image_value(request.image.type, request.image.value)
    body = {
        "model": descriptor.key,
        "temperature": descriptor.default_params.get("temperature", 0),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": request.prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }
    api_key = provider_api_key(descriptor)
    with httpx.Client(timeout=90.0) as client:
        response = client.post(
            f"{provider_base_url(descriptor.provider)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json()
        except ValueError:
            pass
        fail(
            f"{descriptor.provider} inference failed for {descriptor.key}",
            status_code=response.status_code,
            code="provider_error",
            details=detail,
        )
    payload = response.json()
    return extract_response_text(payload), {"adapter": "remote", "raw_response_keys": list(payload.keys())}


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "model_hub", "status": "ok"})


@app.get("/api/models")
def list_models() -> dict[str, Any]:
    items = [availability_for(descriptor).model_dump() for descriptor in load_catalog()]
    return success_response({"items": items, "count": len(items)})


@app.post("/api/infer")
def infer(request: ModelInferRequest) -> dict[str, Any]:
    descriptor = descriptor_by_key(request.model_key)
    started = time.perf_counter()
    if descriptor.provider == "mock":
        raw_text, provider_meta = mock_infer(request)
    else:
        raw_text, provider_meta = remote_infer(descriptor, request)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return success_response(
        {
            "model_key": descriptor.key,
            "raw_text": raw_text,
            "latency_ms": latency_ms,
            "provider_meta": provider_meta,
        }
    )
