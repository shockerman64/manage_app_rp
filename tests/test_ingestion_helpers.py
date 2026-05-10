from app.services.ingestion import _clean_excel_string, _to_decimal


def test_clean_excel_string_strips_excel_wrapper():
    assert _clean_excel_string('="779741363"') == "779741363"
    assert _clean_excel_string("  plain  ") == "plain"
    assert _clean_excel_string("") is None


def test_to_decimal_parsing():
    assert _to_decimal("1,200.50") == 1200.50
    assert _to_decimal(42) == 42.0
    assert _to_decimal(None) is None
