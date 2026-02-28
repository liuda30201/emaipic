from libs.common.models import InvoiceFields, PROFILE_PRESETS, SearchFilters


def test_invoice_fields_accept_nullable_values() -> None:
    payload = InvoiceFields(invoice_no="A001", total=None)
    assert payload.invoice_no == "A001"
    assert payload.total is None


def test_profile_presets_are_available() -> None:
    assert set(PROFILE_PRESETS) == {"prod_default", "prod_fallback", "prod_high_readability"}


def test_search_filters_compact_removes_empty_values() -> None:
    filters = SearchFilters(invoice_no="INV-1", buyer="", seller=None)
    assert filters.compact() == {"invoice_no": "INV-1"}
