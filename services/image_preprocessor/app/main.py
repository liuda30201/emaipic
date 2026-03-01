from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi.responses import FileResponse
from pdf2image import convert_from_path
from PIL import Image, ImageChops

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.ids import new_id
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.models import PROFILE_PRESETS, StepStatus
from libs.common.paths import processed_dir, staging_dir
from libs.common.storage import LocalStorage


app = create_app("image_preprocessor")
storage = LocalStorage()


def staging_manifest_path(batch_id: str) -> Path:
    return staging_dir(batch_id) / "staging_manifest.json"


def processed_manifest_path(batch_id: str) -> Path:
    return processed_dir(batch_id) / "manifest.json"


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


def crop_border(image: Image.Image) -> Image.Image:
    background = Image.new(image.mode, image.size, image.getpixel((0, 0)))
    diff = ImageChops.difference(image, background)
    bbox = diff.getbbox()
    if bbox:
        return image.crop(bbox)
    return image


def resize_image(image: Image.Image, max_long_edge: int) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_long_edge:
        return image
    ratio = max_long_edge / float(longest)
    return image.resize((int(width * ratio), int(height * ratio)), Image.Resampling.LANCZOS)


def denoise_image(image: Image.Image) -> Image.Image:
    array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    denoised = cv2.fastNlMeansDenoisingColored(array, None, 10, 10, 7, 21)
    rgb = cv2.cvtColor(denoised, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def load_pages_from_file(file_path: Path) -> list[Image.Image]:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return [image.convert("RGB") for image in convert_from_path(str(file_path))]
    if suffix == ".ofd":
        fail("OFD adapter is unsupported by default. Plug in a converter before enabling OFD.", status_code=422, code="unsupported_ofd")
    with Image.open(file_path) as image:
        return [image.convert("RGB")]


def save_image(image: Image.Image, target_relpath: Path, profile_name: str) -> str:
    preset = PROFILE_PRESETS[profile_name]
    target_path = storage.resolve(target_relpath)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save(target_path, format=preset["format"], quality=preset["quality"])
        return str(target_relpath)
    except OSError:
        fallback_relpath = target_relpath.with_suffix(".jpg")
        fallback_path = storage.resolve(fallback_relpath)
        image.save(fallback_path, format="JPEG", quality=80)
        return str(fallback_relpath)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return success_response({"service": "image_preprocessor", "status": "ok"})


@app.post("/api/batches/{batch_id}/process")
def process_batch(batch_id: str, profile: str = "prod_default") -> dict[str, Any]:
    if profile not in PROFILE_PRESETS:
        fail("unknown profile", code="invalid_profile")
    staging_manifest = get_staging_manifest(batch_id)
    preset = PROFILE_PRESETS[profile]
    pages: list[dict[str, Any]] = []
    issues: list[str] = []
    batch_processed_dir = processed_dir(batch_id)
    (batch_processed_dir / "pages").mkdir(parents=True, exist_ok=True)
    (batch_processed_dir / "thumbs").mkdir(parents=True, exist_ok=True)

    for file_entry in staging_manifest.get("files", []):
        source_path = DATA_ROOT / file_entry["relpath"]
        if not source_path.exists():
            issues.append(f"{file_entry['filename']}: staging file missing")
            continue
        try:
            images = load_pages_from_file(source_path)
        except Exception as exc:
            page_id = new_id("page")
            pages.append(
                {
                    "batch_id": batch_id,
                    "page_id": page_id,
                    "doc_id": file_entry["doc_id"],
                    "file_id": file_entry["file_id"],
                    "page_no": 1,
                    "profile": profile,
                    "file_type": file_entry.get("file_type"),
                    "original_relpath": file_entry["source_relpath"],
                    "image_relpath": None,
                    "thumb_relpath": None,
                    "status": StepStatus.failed.value,
                    "issues": [str(exc)],
                    "thumb_url": None,
                    "image_url": None,
                }
            )
            issues.append(f"{file_entry['filename']}: {exc}")
            continue

        for index, image in enumerate(images, start=1):
            working = image.convert("RGB")
            if preset["crop_border"]:
                working = crop_border(working)
            if preset["denoise"]:
                working = denoise_image(working)
            working = resize_image(working, preset["max_long_edge"])
            page_id = new_id("page")
            image_relpath = Path("processed") / batch_id / "pages" / f"{page_id}{preset['extension']}"
            saved_image_relpath = save_image(working, image_relpath, profile)

            thumb_relpath = None
            if preset["thumbs"]:
                thumb = resize_image(working.copy(), preset["thumb_long_edge"])
                thumb_path = Path("processed") / batch_id / "thumbs" / f"{page_id}.jpg"
                storage.resolve(thumb_path).parent.mkdir(parents=True, exist_ok=True)
                thumb.save(storage.resolve(thumb_path), format="JPEG", quality=80)
                thumb_relpath = str(thumb_path)

            pages.append(
                {
                    "batch_id": batch_id,
                    "page_id": page_id,
                    "doc_id": file_entry["doc_id"],
                    "file_id": file_entry["file_id"],
                    "page_no": index,
                    "profile": profile,
                    "file_type": file_entry.get("file_type"),
                    "original_relpath": file_entry["source_relpath"],
                    "staging_relpath": file_entry["relpath"],
                    "image_relpath": saved_image_relpath,
                    "thumb_relpath": thumb_relpath,
                    "status": StepStatus.done.value,
                    "issues": [],
                    "thumb_url": f"/api/batches/{batch_id}/pages/{page_id}/thumb" if thumb_relpath else None,
                    "image_url": f"/api/batches/{batch_id}/pages/{page_id}/image",
                }
            )

    manifest = {
        "batch_id": batch_id,
        "status": "done",
        "profile": profile,
        "preset": preset,
        "pages": pages,
        "issues": issues,
    }
    write_json_atomic(processed_manifest_path(batch_id), manifest)
    append_batch_log(batch_id, "image_preprocessor", f"Processed {len(pages)} pages using profile={profile}.")
    return success_response({"batch_id": batch_id, "status": "done", "page_count": len(pages), "issues": issues})


@app.get("/api/batches/{batch_id}/manifest")
def get_manifest(batch_id: str) -> dict[str, Any]:
    return success_response(get_processed_manifest(batch_id))


@app.get("/api/batches/{batch_id}/pages")
def list_pages(batch_id: str) -> dict[str, Any]:
    manifest = get_processed_manifest(batch_id)
    return success_response({"batch_id": batch_id, "pages": manifest.get("pages", [])})


def get_page_file(batch_id: str, page_id: str, key: str) -> Path:
    manifest = get_processed_manifest(batch_id)
    for page in manifest.get("pages", []):
        if page["page_id"] == page_id:
            relpath = page.get(key)
            if not relpath:
                fail(f"{key} missing for page {page_id}", status_code=404, code="not_found")
            target = DATA_ROOT / relpath
            if not target.exists():
                fail(f"file missing for page {page_id}", status_code=404, code="not_found")
            return target
    fail(f"page {page_id} not found", status_code=404, code="not_found")


@app.get("/api/batches/{batch_id}/pages/{page_id}/image")
def get_page_image(batch_id: str, page_id: str) -> FileResponse:
    return FileResponse(get_page_file(batch_id, page_id, "image_relpath"))


@app.get("/api/batches/{batch_id}/pages/{page_id}/thumb")
def get_page_thumb(batch_id: str, page_id: str) -> FileResponse:
    return FileResponse(get_page_file(batch_id, page_id, "thumb_relpath"))
