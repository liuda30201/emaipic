from __future__ import annotations

from unittest.mock import patch

from services.model_hub.app.main import availability_for, load_catalog


def test_models_yaml_loads_required_entries() -> None:
    catalog = load_catalog()
    keys = {item.key for item in catalog}
    assert "mock" in keys
    assert "glm-ocr" in keys
    assert "qwen-vl-ocr" in keys


def test_model_availability_marks_missing_keys_unavailable() -> None:
    catalog = {item.key: item for item in load_catalog()}
    with patch.dict("os.environ", {"GLM_API_KEY": "", "QWEN_API_KEY": ""}, clear=False):
        mock_model = availability_for(catalog["mock"])
        glm_model = availability_for(catalog["glm-ocr"])

    assert mock_model.available is True
    assert glm_model.available is False
    assert glm_model.reason == "Missing API key"
