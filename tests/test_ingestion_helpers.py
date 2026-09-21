from datetime import date

from app.services.ingestion import _clean_excel_string, _to_decimal
from app.services.member_pnl_ingestion import (
    PNL_COLUMN_MAP,
    PNL_REQUIRED_COLUMNS,
    parse_report_dates_from_filename,
)


def test_clean_excel_string_strips_excel_wrapper():
    assert _clean_excel_string('="779741363"') == "779741363"
    assert _clean_excel_string("  plain  ") == "plain"
    assert _clean_excel_string("") is None


def test_to_decimal_parsing():
    assert _to_decimal("1,200.50") == 1200.50
    assert _to_decimal(42) == 42.0
    assert _to_decimal(None) is None


def test_parse_report_dates_from_filename_daily():
    parsed = parse_report_dates_from_filename(
        "4.3_P_&_L_By_Member_20260920_20260920_ALL.csv"
    )
    assert parsed == (date(2026, 9, 20), date(2026, 9, 20))


def test_parse_report_dates_from_filename_range():
    parsed = parse_report_dates_from_filename("pnl_20260901_20260920.csv")
    assert parsed == (date(2026, 9, 1), date(2026, 9, 20))


def test_parse_report_dates_from_filename_missing():
    assert parse_report_dates_from_filename("members.csv") is None


def test_parse_report_dates_from_filename_invalid_calendar():
    assert parse_report_dates_from_filename("x_20261340_20261340.csv") is None


def test_pnl_required_columns_and_headline_mapping():
    assert set(PNL_REQUIRED_COLUMNS) <= set(PNL_COLUMN_MAP)
    assert PNL_COLUMN_MAP["Stakes-Total G/L"] == "total_gl"
    assert PNL_COLUMN_MAP["Stakes-Gain/Loss"] == "gain_loss"
    assert PNL_COLUMN_MAP["Member ID"] == "member_id"

