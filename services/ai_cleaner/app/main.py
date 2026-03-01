from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from libs.common.api import create_app, fail, success_response
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.models import NormalizedInvoiceRecord
from libs.common.paths import ai_clean_dir, ai_raw_dir, processed_dir


app = create_app("ai_cleaner")


def dispatch_status_path(batch_id: str) -> Path:
    return ai_raw_dir(batch_id) / "status.json"


def processed_manifest_path(batch_id: str) -> Path:
    return processed_dir(batch_id) / "manifest.json"


def normalized_jsonl_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "normalized_results.jsonl"


def issues_jsonl_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "issues.jsonl"


def summary_path(batch_id: str) -> Path:
    return ai_clean_dir(batch_id) / "results.json"


def get_dispatch_status(batch_id: str) -> dict[str, Any]:
    manifest = read_json(dispatch_status_path(batch_id))
    if not manifest:
        fail(f"dispatch status missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_processed_manifest(batch_id: str) -> dict[str, Any]:
    manifest = read_json(processed_manifest_path(batch_id))
    if not manifest:
        fail(f"processed manifest missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def get_results(batch_id: str) -> dict[str, Any]:
    manifest = read_json(summary_path(batch_id))
    if not manifest:
        fail(f"clean results missing for batch {batch_id}", status_code=404, code="not_found")
    return manifest


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in items)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_bool(value: Any, issues: list[dict[str, Any]], field_name: str = "is_invoice") -> bool:
    if isinstance(value, bool):
        return value
    text = clean_text(value)
    if text is None:
        return False
    if text.lower() in {"true", "1", "yes", "y"}:
        return True
    if text.lower() in {"false", "0", "no", "n"}:
        return False
    issues.append({"field": field_name, "reason": "invalid_boolean", "raw_value": str(value)})
    return False


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
    issues.append({"field": "date", "reason": "invalid_date", "raw_value": text})
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
        issues.append({"field": field_name, "reason": "invalid_decimal", "raw_value": text})
        return None


def extract_json_array(raw_text: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if isinstance(raw_text, list):
        return [item if isinstance(item, dict) else {} for item in raw_text], issues
    text = clean_text(raw_text)
    if not text:
        raise ValueError("empty_response")

    try:
        payload = json.loads(text)
        if isinstance(payload, list):
            return [item if isinstance(item, dict) else {} for item in payload], issues
        if isinstance(payload, dict):
            issues.append({"field": "response", "reason": "wrapped_single_object", "raw_value": text})
            return [payload], issues
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    if start < 0:
        raise ValueError("json_array_not_found")

    depth = 0
    end = -1
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise ValueError("json_array_not_closed")

    candidate = text[start : end + 1]
    payload = json.loads(candidate)
    if not isinstance(payload, list):
        raise ValueError("json_array_not_found")
    issues.append({"field": "response", "reason": "repaired_outer_array", "raw_value": None})
    return [item if isinstance(item, dict) else {} for item in payload], issues


def normalize_item(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    normalized = {
        "is_invoice": normalize_bool(raw.get("is_invoice"), issues),
        "num": clean_text(raw.get("num")),
        "date": normalize_date(raw.get("date"), issues),
        "item": clean_text(raw.get("item")),
        "buyer": clean_text(raw.get("buyer")),
        "seller": clean_text(raw.get("seller")),
        "amt": normalize_decimal("amt", raw.get("amt"), issues),
        "tax": normalize_decimal("tax", raw.get("tax"), issues),
        "total": normalize_decimal("total", raw.get("total"), issues),
    }
    return normalized, issues


def response_text_from_payload(payload: dict[str, Any]) -> str | None:
    upstream = payload.get("upstream") or {}
    data = upstream.get("data") or {}
    return data.get("raw_text")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "ai_cleaner", "status": "ok"})


@app.post("/api/batches/{batch_id}/clean")
def clean_batch(batch_id: str) -> dict[str, Any]:
    dispatch_status = get_dispatch_status(batch_id)
    processed_manifest = get_processed_manifest(batch_id)
    pages_by_id = {item["page_id"]: item for item in processed_manifest.get("pages", [])}

    summary_pages: list[dict[str, Any]] = []
    normalized_lines: list[dict[str, Any]] = []
    issue_lines: list[dict[str, Any]] = []

    for page_entry in dispatch_status.get("pages", []):
        page = pages_by_id.get(page_entry["page_id"], {})
        page_summary = {
            "page_id": page_entry["page_id"],
            "doc_id": page_entry.get("doc_id") or page.get("doc_id"),
            "file_id": page_entry.get("file_id") or page.get("file_id"),
            "models": [],
        }
        for model_run in page_entry.get("models", []):
            run_dir = ai_clean_dir(batch_id) / page_entry["page_id"] / model_run["model_key"] / model_run["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)
            response_payload = read_json(
                ai_raw_dir(batch_id) / page_entry["page_id"] / model_run["model_key"] / model_run["run_id"] / "response.json",
                default={},
            ) or {}

            run_records: list[dict[str, Any]] = []
            run_issues: list[dict[str, Any]] = []
            run_status = "done"

            if model_run.get("status") != "done":
                run_status = "failed"
                run_issues.append(
                    {
                        "batch_id": batch_id,
                        "page_id": page_entry["page_id"],
                        "model_key": model_run["model_key"],
                        "run_id": model_run["run_id"],
                        "record_index": None,
                        "reason": "dispatch_failed",
                        "message": model_run.get("error") or "dispatch failed",
                    }
                )
            else:
                try:
                    parsed_items, parse_issues = extract_json_array(response_text_from_payload(response_payload))
                except Exception as exc:
                    parsed_items = []
                    parse_issues = []
                    run_status = "failed"
                    run_issues.append(
                        {
                            "batch_id": batch_id,
                            "page_id": page_entry["page_id"],
                            "model_key": model_run["model_key"],
                            "run_id": model_run["run_id"],
                            "record_index": None,
                            "reason": "parse_failed",
                            "message": str(exc),
                        }
                    )

                for parse_issue in parse_issues:
                    run_issues.append(
                        {
                            "batch_id": batch_id,
                            "page_id": page_entry["page_id"],
                            "model_key": model_run["model_key"],
                            "run_id": model_run["run_id"],
                            "record_index": None,
                            "reason": parse_issue["reason"],
                            "message": parse_issue.get("field", "response"),
                        }
                    )

                for index, raw_item in enumerate(parsed_items):
                    normalized, item_issues = normalize_item(raw_item)
                    record = NormalizedInvoiceRecord(
                        batch_id=batch_id,
                        doc_id=page_summary["doc_id"],
                        file_id=page_summary["file_id"],
                        page_id=page_entry["page_id"],
                        model_key=model_run["model_key"],
                        run_id=model_run["run_id"],
                        record_index=index,
                        **normalized,
                    ).model_dump()
                    run_records.append(record)
                    normalized_lines.append(record)
                    for item_issue in item_issues:
                        run_issues.append(
                            {
                                "batch_id": batch_id,
                                "page_id": page_entry["page_id"],
                                "model_key": model_run["model_key"],
                                "run_id": model_run["run_id"],
                                "record_index": index,
                                "reason": item_issue["reason"],
                                "message": item_issue["field"],
                                "raw_value": item_issue.get("raw_value"),
                            }
                        )

            write_json_atomic(run_dir / "normalized.json", run_records)
            write_json_atomic(run_dir / "issues.json", run_issues)
            issue_lines.extend(run_issues)

            page_summary["models"].append(
                {
                    "model_key": model_run["model_key"],
                    "run_id": model_run["run_id"],
                    "status": run_status,
                    "records": run_records,
                    "issues": run_issues,
                }
            )
        summary_pages.append(page_summary)

    write_jsonl(normalized_jsonl_path(batch_id), normalized_lines)
    write_jsonl(issues_jsonl_path(batch_id), issue_lines)

    summary = {
        "batch_id": batch_id,
        "status": "done",
        "record_count": len(normalized_lines),
        "issue_count": len(issue_lines),
        "pages": summary_pages,
        "normalized_results_path": str(normalized_jsonl_path(batch_id)),
        "issues_path": str(issues_jsonl_path(batch_id)),
    }
    write_json_atomic(summary_path(batch_id), summary)
    append_batch_log(batch_id, "ai_cleaner", f"Normalized {len(normalized_lines)} records with {len(issue_lines)} issues.")
    return success_response(
        {
            "batch_id": batch_id,
            "status": "done",
            "record_count": len(normalized_lines),
            "issue_count": len(issue_lines),
        }
    )


@app.get("/api/batches/{batch_id}/results")
def batch_results(batch_id: str) -> dict[str, Any]:
    return success_response(get_results(batch_id))
