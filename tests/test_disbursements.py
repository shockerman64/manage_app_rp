from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from app.services.ingestion import (
    _correlation_id_as_text,
    _is_api_request,
    parse_disbursement_frame,
)
from app.services.reconciliation import _normalize_status

SAMPLE_DISBURSEMENT = Path(__file__).resolve().parents[1] / (
    "Disbursements 2026-10-05 00_00 to 2026-10-06 23_59.xlsx"
)


def test_api_request_type_is_case_insensitive():
    assert _is_api_request("API")
    assert _is_api_request(" api ")
    assert not _is_api_request("DASHBOARD")
    assert not _is_api_request(None)


def test_correlation_id_keeps_whole_numbers_as_digits():
    assert _correlation_id_as_text(888553815) == "888553815"
    assert _correlation_id_as_text(888553815.0) == "888553815"
    assert _correlation_id_as_text('="888553815"') == "888553815"
    assert _correlation_id_as_text(None) is None


def test_parse_keeps_api_withdrawals_and_drops_dashboard_rows():
    frame = pd.DataFrame(
        [
            {
                "transaction_time": "2026-10-05 21:55:41",
                "correlation_id": 888553815,
                "amount": 50000,
                "fee_amount": 2500,
                "status": "FINALIZED",
                "request_type": "API",
                "withdrawal_method": "py_disbursement_btr",
                "bank_name": "DANA",
                "username": None,
            },
            {
                "transaction_time": "2026-10-06 10:41:52",
                "correlation_id": 202610787690140254,
                "amount": 49500000,
                "fee_amount": 2500,
                "status": "FINALIZED",
                "request_type": "DASHBOARD",
                "withdrawal_method": "bt_disbursement",
                "bank_name": "Bank Central Asia (BCA)",
            },
            {
                "transaction_time": "2026-10-06 11:01:20",
                "correlation_id": None,
                "amount": 50000,
                "status": "FINALIZED",
                "request_type": "API",
            },
        ]
    )

    parsed = parse_disbursement_frame(frame)

    assert parsed.skipped_non_api == 1
    assert parsed.skipped_incomplete == 1
    assert len(parsed.normalized_rows) == 1
    row = parsed.normalized_rows[0]
    assert row["correlation_id"] == "888553815"
    assert row["amount"] == 50000
    assert row["status"] == "FINALIZED"
    assert row["txn_datetime_vendor"] == datetime(2026, 10, 5, 21, 55, 41)
    assert row["txn_datetime_local"] == datetime(2026, 10, 5, 22, 55, 41)
    assert parsed.raw_rows[0]["payment_type"] == "py_disbursement_btr"
    assert '"request_type": "API"' in row["meta"]


def test_parse_requires_disbursement_columns():
    with pytest.raises(ValueError, match="request_type"):
        parse_disbursement_frame(pd.DataFrame({"amount": [1], "status": ["FINALIZED"]}))


def test_vendor_rejected_maps_like_internal_rejected():
    assert _normalize_status("vendor", "FINALIZED") == "SUCCESS"
    assert _normalize_status("vendor", "REJECTED") == "FAILED"
    assert _normalize_status("internal", "Approved") == "SUCCESS"
    assert _normalize_status("internal", "Rejected") == "FAILED"


@pytest.mark.skipif(not SAMPLE_DISBURSEMENT.exists(), reason="sample disbursement file not present")
def test_sample_disbursement_file_keeps_api_rows_only():
    frame = pd.read_excel(SAMPLE_DISBURSEMENT, engine="openpyxl")
    parsed = parse_disbursement_frame(frame)
    ids = {row["correlation_id"] for row in parsed.normalized_rows}

    assert parsed.skipped_non_api == 3
    assert parsed.skipped_incomplete == 0
    assert len(parsed.normalized_rows) == 16
    assert sum(row["amount"] for row in parsed.normalized_rows) == 3_982_000
    assert "888553815" in ids
    assert "202610787690140254" not in ids
    assert all(row["amount"] == int(row["amount"]) for row in parsed.normalized_rows)
