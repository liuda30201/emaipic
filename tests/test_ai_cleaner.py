from services.ai_cleaner.app.main import extract_json_array, normalize_item


def test_extract_json_array_repairs_wrapped_text() -> None:
    payload, issues = extract_json_array('prefix [{"is_invoice":true,"num":"A-1"}] suffix')
    assert payload[0]["num"] == "A-1"
    assert issues[0]["reason"] == "repaired_outer_array"


def test_normalize_item_coerces_boolean_and_decimals() -> None:
    normalized, issues = normalize_item(
        {
            "is_invoice": "true",
            "num": "INV-1",
            "date": "20260301",
            "item": "服务",
            "buyer": "甲",
            "seller": "乙",
            "amt": "100",
            "tax": "6",
            "total": "106",
        }
    )
    assert normalized["is_invoice"] is True
    assert normalized["date"] == "2026-03-01"
    assert normalized["amt"] == "100.00"
    assert issues == []
