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
    assert normalized["items"] == [{"name": "服务", "spec": None, "unit": None, "qty": None, "price": None, "amount": None, "tax": None}]
    assert issues == []


def test_normalize_item_supports_v2_items_payload() -> None:
    normalized, issues = normalize_item(
        {
            "is_invoice": True,
            "num": "INV-2",
            "date": "2026/03/01",
            "buyer": "甲",
            "seller": "乙",
            "items": [
                {
                    "商品名称": "柴油",
                    "spec": "0#",
                    "unit": "升",
                    "qty": "10",
                    "price": "7.11",
                    "amount": "71.1",
                    "tax": "-4.27",
                }
            ],
            "amt": "-71.1",
            "tax": "-4.27",
            "total": "-75.37",
        },
        prompt_version="v2",
    )
    assert normalized["prompt_version"] == "v2"
    assert normalized["normalized_payload"]["items"][0]["name"] == "柴油"
    assert normalized["normalized_payload"]["items"][0]["amount"] == "71.10"
    assert normalized["tax"] == "-4.27"
    assert issues == []
