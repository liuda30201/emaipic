from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class BatchStep(BaseModel):
    name: str
    status: StepStatus = StepStatus.pending
    detail: Optional[str] = None


class InvoiceFields(BaseModel):
    invoice_no: Optional[str] = None
    invoice_date: Optional[str] = None
    buyer_name: Optional[str] = None
    seller_name: Optional[str] = None
    service_name: Optional[str] = None
    amount: Optional[str] = None
    tax: Optional[str] = None
    total: Optional[str] = None


class CleanIssue(BaseModel):
    field: str
    reason: str
    raw_value: Optional[str] = None


class ProcessedPage(BaseModel):
    batch_id: str
    page_id: str
    doc_id: str
    file_id: str
    page_no: int
    profile: str
    original_relpath: str
    image_relpath: Optional[str] = None
    thumb_relpath: Optional[str] = None
    status: StepStatus = StepStatus.done
    issues: list[str] = Field(default_factory=list)


class SearchFilters(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    buyer: Optional[str] = None
    seller: Optional[str] = None
    invoice_no: Optional[str] = None
    min_total: Optional[str] = None
    max_total: Optional[str] = None
    batch_id: Optional[str] = None

    def compact(self) -> dict[str, Any]:
        data = self.model_dump()
        return {key: value for key, value in data.items() if value not in (None, "")}


PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "prod_default": {
        "format": "WEBP",
        "extension": ".webp",
        "quality": 75,
        "max_long_edge": 1800,
        "crop_border": True,
        "denoise": False,
        "thumbs": True,
        "thumb_long_edge": 600,
    },
    "prod_fallback": {
        "format": "JPEG",
        "extension": ".jpg",
        "quality": 80,
        "max_long_edge": 1800,
        "crop_border": True,
        "denoise": False,
        "thumbs": True,
        "thumb_long_edge": 600,
    },
    "prod_high_readability": {
        "format": "WEBP",
        "extension": ".webp",
        "quality": 80,
        "max_long_edge": 2000,
        "crop_border": True,
        "denoise": False,
        "thumbs": True,
        "thumb_long_edge": 600,
    },
}
