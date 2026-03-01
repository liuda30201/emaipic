from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi.responses import FileResponse
from pdf2image import convert_from_path
from PIL import Image, ImageChops, ImageFilter

from libs.common.api import create_app, fail, success_response
from libs.common.config import DATA_ROOT
from libs.common.ids import new_id
from libs.common.logging_utils import append_batch_log
from libs.common.manifests import read_json, write_json_atomic
from libs.common.models import PROFILE_PRESETS, StepStatus
from libs.common.paths import processed_dir, staging_dir
from libs.common.storage import LocalStorage


SMALL_TEXT_THRESHOLD = 1600
SMALL_TEXT_MIN_QUALITY = 90
SMALL_TEXT_TARGET_LONG_EDGE = 2000
SHARPNESS_DROP_WARN_RATIO = 0.85

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


def resize_image(image: Image.Image, target_long_edge: int, allow_upscale: bool = False) -> tuple[Image.Image, bool]:
    width, height = image.size
    longest = max(width, height)
    if longest <= 0:
        return image, False
    ratio = 1.0
    if longest > target_long_edge:
        ratio = target_long_edge / float(longest)
    elif allow_upscale and longest < target_long_edge:
        ratio = target_long_edge / float(longest)
    else:
        return image, False

    new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    if new_size == image.size:
        return image, False
    resized = image.resize(new_size, Image.Resampling.LANCZOS)
    return resized, ratio > 1.0


def denoise_image(image: Image.Image) -> Image.Image:
    array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    denoised = cv2.fastNlMeansDenoisingColored(array, None, 10, 10, 7, 21)
    rgb = cv2.cvtColor(denoised, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def apply_unsharp_mask(image: Image.Image) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))


def laplacian_variance(image: Image.Image) -> float:
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def load_pages_from_file(file_path: Path) -> list[Image.Image]:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return [image.convert("RGB") for image in convert_from_path(str(file_path))]
    if suffix == ".ofd":
        fail("OFD adapter is unsupported by default. Plug in a converter before enabling OFD.", status_code=422, code="unsupported_ofd")
    with Image.open(file_path) as image:
        return [image.convert("RGB")]


def build_runtime_preset(profile_name: str, image_size: tuple[int, int]) -> dict[str, Any]:
    preset = dict(PROFILE_PRESETS[profile_name])
    max_edge = max(image_size)
    adaptive_applied = max_edge < SMALL_TEXT_THRESHOLD
    output_quality = int(preset["quality"])
    max_long_edge = int(preset["max_long_edge"])
    allow_upscale = False
    unsharp = False

    if adaptive_applied:
        output_quality = max(output_quality, SMALL_TEXT_MIN_QUALITY)
        max_long_edge = max(max_long_edge, SMALL_TEXT_TARGET_LONG_EDGE)
        allow_upscale = True
        unsharp = True

    runtime = dict(preset)
    runtime.update(
        {
            "adaptive_applied": adaptive_applied,
            "output_quality": output_quality,
            "max_long_edge": max_long_edge,
            "allow_upscale": allow_upscale,
            "unsharp": unsharp,
        }
    )
    return runtime


def save_image(image: Image.Image, target_relpath: Path, runtime_preset: dict[str, Any]) -> tuple[str, str, int]:
    target_path = storage.resolve(target_relpath)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save(target_path, format=runtime_preset["format"], quality=runtime_preset["output_quality"])
        return str(target_relpath), str(runtime_preset["format"]).upper(), int(runtime_preset["output_quality"])
    except OSError:
        fallback_relpath = target_relpath.with_suffix(".jpg")
        fallback_path = storage.resolve(fallback_relpath)
        fallback_quality = max(90 if runtime_preset["adaptive_applied"] else 80, int(runtime_preset["output_quality"]))
        image.save(fallback_path, format="JPEG", quality=fallback_quality)
        return str(fallback_relpath), "JPEG", fallback_quality


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
                    "adaptive_applied": False,
                    "original_size": None,
                    "output_size": None,
                    "output_format": None,
                    "output_quality": None,
                    "upscale_applied": False,
                    "unsharp_applied": False,
                    "sharpness_in": None,
                    "sharpness_out": None,
                }
            )
            issues.append(f"{file_entry['filename']}: {exc}")
            continue

        for index, image in enumerate(images, start=1):
            working = image.convert("RGB")
            original_size = {"w": working.width, "h": working.height}
            runtime_preset = build_runtime_preset(profile, working.size)
            page_issues: list[str] = []
            sharpness_before = laplacian_variance(working) if runtime_preset["adaptive_applied"] else None

            if preset["crop_border"]:
                working = crop_border(working)
            if preset["denoise"]:
                working = denoise_image(working)

            working, upscale_applied = resize_image(
                working,
                runtime_preset["max_long_edge"],
                allow_upscale=runtime_preset["allow_upscale"],
            )

            if runtime_preset["unsharp"]:
                working = apply_unsharp_mask(working)

            sharpness_after = laplacian_variance(working) if runtime_preset["adaptive_applied"] else None
            if (
                runtime_preset["adaptive_applied"]
                and sharpness_before is not None
                and sharpness_after is not None
                and sharpness_after < sharpness_before * SHARPNESS_DROP_WARN_RATIO
            ):
                message = (
                    f"{file_entry['filename']} page {index}: sharpness dropped "
                    f"from {sharpness_before:.2f} to {sharpness_after:.2f}"
                )
                issues.append(message)
                page_issues.append(message)
                append_batch_log(batch_id, "image_preprocessor", f"warning: {message}")

            page_id = new_id("page")
            image_relpath = Path("processed") / batch_id / "pages" / f"{page_id}{preset['extension']}"
            saved_image_relpath, output_format, output_quality = save_image(working, image_relpath, runtime_preset)

            thumb_relpath = None
            if preset["thumbs"]:
                thumb, _ = resize_image(working.copy(), preset["thumb_long_edge"], allow_upscale=False)
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
                    "issues": page_issues,
                    "thumb_url": f"/api/batches/{batch_id}/pages/{page_id}/thumb" if thumb_relpath else None,
                    "image_url": f"/api/batches/{batch_id}/pages/{page_id}/image",
                    "adaptive_applied": runtime_preset["adaptive_applied"],
                    "original_size": original_size,
                    "output_size": {"w": working.width, "h": working.height},
                    "output_format": output_format,
                    "output_quality": output_quality,
                    "upscale_applied": upscale_applied,
                    "unsharp_applied": runtime_preset["unsharp"],
                    "sharpness_in": round(sharpness_before, 2) if sharpness_before is not None else None,
                    "sharpness_out": round(sharpness_after, 2) if sharpness_after is not None else None,
                }
            )

    manifest = {
        "batch_id": batch_id,
        "status": "done",
        "profile": profile,
        "preset": preset,
        "adaptive": {
            "small_text_threshold": SMALL_TEXT_THRESHOLD,
            "small_text_target_long_edge": SMALL_TEXT_TARGET_LONG_EDGE,
            "small_text_min_quality": SMALL_TEXT_MIN_QUALITY,
        },
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
