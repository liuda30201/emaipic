import json
import sys
import types

import numpy as np
from PIL import Image

import libs.common.paths as common_paths
from libs.common.storage import LocalStorage


if "cv2" not in sys.modules:
    cv2_stub = types.SimpleNamespace()
    cv2_stub.COLOR_RGB2BGR = 0
    cv2_stub.COLOR_BGR2RGB = 1
    cv2_stub.COLOR_RGB2GRAY = 2
    cv2_stub.CV_64F = np.float64

    def cvt_color(array, code):
        if code == cv2_stub.COLOR_RGB2GRAY and getattr(array, "ndim", 0) == 3:
            return array.mean(axis=2).astype(np.uint8)
        return array

    cv2_stub.cvtColor = cvt_color
    cv2_stub.fastNlMeansDenoisingColored = lambda array, *_args, **_kwargs: array
    cv2_stub.Laplacian = lambda array, *_args, **_kwargs: np.asarray(array, dtype=np.float64)
    sys.modules["cv2"] = cv2_stub

if "pdf2image" not in sys.modules:
    pdf2image_stub = types.SimpleNamespace()
    pdf2image_stub.convert_from_path = lambda *_args, **_kwargs: []
    sys.modules["pdf2image"] = pdf2image_stub

from services.image_preprocessor.app import main as image_preprocessor


def test_small_text_profile_uses_adaptive_quality_and_upscale(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(common_paths, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(image_preprocessor, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(image_preprocessor, "storage", LocalStorage(tmp_path))
    monkeypatch.setattr(image_preprocessor, "append_batch_log", lambda *args, **kwargs: None)

    batch_id = "batch_small"
    source_relpath = "staging/batch_small/files/doc_1/file_1.jpg"
    source_path = tmp_path / source_relpath
    source_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1247, 792), color=(255, 255, 255)).save(source_path, format="JPEG", quality=95)

    manifest_path = tmp_path / "staging" / batch_id / "staging_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "files": [
                    {
                        "file_id": "file_1",
                        "doc_id": "doc_1",
                        "filename": "file_1.jpg",
                        "relpath": source_relpath,
                        "source_relpath": "raw/batch_small/attachments/file_1.jpg",
                        "file_type": "jpg",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    image_preprocessor.process_batch(batch_id, profile="prod_default")

    processed_manifest = json.loads((tmp_path / "processed" / batch_id / "manifest.json").read_text(encoding="utf-8"))
    page = processed_manifest["pages"][0]
    assert page["adaptive_applied"] is True
    assert page["output_quality"] >= 90
    assert page["upscale_applied"] is True
    assert page["output_size"]["w"] > 1247 or page["output_size"]["h"] > 792
