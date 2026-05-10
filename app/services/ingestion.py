from __future__ import annotations

import hashlib
import io
import json
import csv
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import text

from app.db import engine


def _clean_excel_string(value: object) -> str | None:
    if value is None:
        return None
    output = str(value).strip()
    if output.startswith('="') and output.endswith('"'):
        output = output[2:-1]
    if output == "":
        return None
    return output


def _to_decimal(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text_value = str(value).replace(",", "").replace('"', "").strip()
    if text_value in ("", "nan", "None"):
        return None
    return float(text_value)


def _normalize_columns(columns: list[object]) -> list[str]:
    normalized: list[str] = []
    for col in columns:
        value = _clean_excel_string(col) or ""
        value = value.replace("\ufeff", "").strip()
        normalized.append(value)
    return normalized


def _read_csv_auto(content: bytes) -> pd.DataFrame:
    text_content = content.decode("utf-8-sig", errors="replace")
    sample = text_content[:4096]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        if sample.count(";") > sample.count(","):
            delimiter = ";"
    return pd.read_csv(io.StringIO(text_content), sep=delimiter)


def _json_safe_dict(input_dict: dict) -> dict:
    output: dict = {}
    for key, value in input_dict.items():
        if pd.isna(value):
            output[key] = None
        else:
            output[key] = value
    return output


@dataclass
class ImportResult:
    batch_id: str
    inserted_rows: int
    duplicate_file: bool


_INTERNAL_RAW_INSERT_SQL = text(
    """
    INSERT INTO internal_transactions_raw (
        batch_id, row_number, group_name, merchant, login_id, member_id,
        txn_datetime_raw, txn_type, pay_method, currency, amount, fee,
        ticket_no, status, approval_remark, raw_data
    )
    VALUES (
        :batch_id, :row_number, :group_name, :merchant, :login_id, :member_id,
        :txn_datetime_raw, :txn_type, :pay_method, :currency, :amount, :fee,
        :ticket_no, :status, :approval_remark, CAST(:raw_data AS jsonb)
    )
    """
)

_INTERNAL_NORMALIZED_INSERT_SQL = text(
    """
    INSERT INTO transactions_normalized (
        source_system, source_row_id, batch_id, reference_id, ticket_no,
        merchant, group_name, login_id, member_id, txn_type, pay_method,
        currency, amount, fee, status, txn_datetime_local, txn_datetime_vendor, meta
    )
    VALUES (
        'internal', NULL, :batch_id, :reference_id, :ticket_no,
        :merchant, :group_name, :login_id, :member_id, :txn_type, :pay_method,
        :currency, :amount, :fee, :status, :txn_datetime_local, :txn_datetime_vendor,
        '{}'::jsonb
    )
    ON CONFLICT (source_system, reference_id) DO NOTHING
    """
)

_VENDOR_RAW_INSERT_SQL = text(
    """
    INSERT INTO vendor_transactions_raw (
        batch_id, row_number, client_name, txn_time_raw, correlation_id, amount,
        currency, fee_amount, status, payment_type, merchant_name, username, raw_data
    )
    VALUES (
        :batch_id, :row_number, :client_name, :txn_time_raw, :correlation_id, :amount,
        :currency, :fee_amount, :status, :payment_type, :merchant_name, :username,
        CAST(:raw_data AS jsonb)
    )
    """
)

_VENDOR_NORMALIZED_INSERT_SQL = text(
    """
    INSERT INTO transactions_normalized (
        source_system, source_row_id, batch_id, reference_id, correlation_id, merchant,
        login_id, txn_type, pay_method, currency, amount, fee, status,
        txn_datetime_local, txn_datetime_vendor, meta
    )
    VALUES (
        'vendor', NULL, :batch_id, :reference_id, :correlation_id, :merchant,
        :login_id, :txn_type, 'QRIS IM', :currency, :amount, :fee, :status,
        :txn_datetime_local, :txn_datetime_vendor, '{}'::jsonb
    )
    ON CONFLICT (source_system, reference_id) DO NOTHING
    """
)


def _execute_in_chunks(conn, stmt, rows: list[dict], chunk_size: int = 500) -> None:
    for start in range(0, len(rows), chunk_size):
        conn.execute(stmt, rows[start : start + chunk_size])


def _create_batch(source_type: str, filename: str, content: bytes) -> tuple[str | None, bool]:
    digest = hashlib.sha256(content).hexdigest()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO import_batches (source_type, original_filename, file_hash, status)
                VALUES (:source_type, :filename, :digest, 'completed')
                ON CONFLICT (source_type, file_hash) DO NOTHING
                RETURNING id
                """
            ),
            {"source_type": source_type, "filename": filename, "digest": digest},
        ).fetchone()
        if not row:
            return None, True
        return str(row[0]), False


def import_internal_csv(filename: str, content: bytes) -> ImportResult:
    batch_id, duplicate = _create_batch("internal", filename, content)
    if duplicate or not batch_id:
        return ImportResult(batch_id="", inserted_rows=0, duplicate_file=True)

    frame = _read_csv_auto(content)
    frame.columns = _normalize_columns(list(frame.columns))

    required = {
        "Date",
        "Type",
        "Pay Method",
        "Currency",
        "Amount",
        "Fee",
        "Ticket #",
        "Status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Internal file missing columns: {sorted(missing)}")

    raw_rows: list[dict] = []
    normalized_rows: list[dict] = []
    period_start: datetime | None = None
    period_end: datetime | None = None
    for idx, row in frame.iterrows():
        clean = {k: _clean_excel_string(v) for k, v in row.to_dict().items()}
        txn_dt = pd.to_datetime(clean.get("Date"), errors="coerce")
        if pd.notna(txn_dt):
            dt = txn_dt.to_pydatetime()
            period_start = dt if period_start is None else min(period_start, dt)
            period_end = dt if period_end is None else max(period_end, dt)
            txn_local = dt
            txn_vendor = dt - pd.Timedelta(hours=1)
        else:
            txn_local = None
            txn_vendor = None

        amount = _to_decimal(row.get("Amount"))
        fee = _to_decimal(clean.get("Fee"))
        ticket = clean.get("Ticket #")
        if amount is None or ticket is None:
            continue

        json_clean = _json_safe_dict(clean)
        raw_rows.append(
            {
                "batch_id": batch_id,
                "row_number": idx + 2,
                "group_name": clean.get("Group"),
                "merchant": clean.get("Merchant"),
                "login_id": clean.get("Login ID"),
                "member_id": clean.get("Member ID"),
                "txn_datetime_raw": clean.get("Date"),
                "txn_type": clean.get("Type"),
                "pay_method": clean.get("Pay Method"),
                "currency": clean.get("Currency"),
                "amount": amount,
                "fee": fee,
                "ticket_no": ticket,
                "status": clean.get("Status"),
                "approval_remark": clean.get("Approval Remark"),
                "raw_data": json.dumps(json_clean, allow_nan=False),
            }
        )
        normalized_rows.append(
            {
                "batch_id": batch_id,
                "reference_id": ticket,
                "ticket_no": ticket,
                "merchant": clean.get("Merchant"),
                "group_name": clean.get("Group"),
                "login_id": clean.get("Login ID"),
                "member_id": clean.get("Member ID"),
                "txn_type": clean.get("Type"),
                "pay_method": clean.get("Pay Method"),
                "currency": clean.get("Currency"),
                "amount": amount,
                "fee": fee,
                "status": clean.get("Status"),
                "txn_datetime_local": txn_local,
                "txn_datetime_vendor": txn_vendor,
            }
        )

    inserted_rows = len(normalized_rows)
    with engine.begin() as conn:
        if raw_rows:
            _execute_in_chunks(conn, _INTERNAL_RAW_INSERT_SQL, raw_rows)
        if normalized_rows:
            _execute_in_chunks(conn, _INTERNAL_NORMALIZED_INSERT_SQL, normalized_rows)

        conn.execute(
            text(
                """
                UPDATE import_batches
                SET row_count = :row_count, period_start = :period_start, period_end = :period_end
                WHERE id = :batch_id
                """
            ),
            {
                "row_count": inserted_rows,
                "period_start": period_start,
                "period_end": period_end,
                "batch_id": batch_id,
            },
        )

    return ImportResult(batch_id=batch_id, inserted_rows=inserted_rows, duplicate_file=False)


def import_vendor_file(filename: str, content: bytes) -> ImportResult:
    batch_id, duplicate = _create_batch("vendor", filename, content)
    if duplicate or not batch_id:
        return ImportResult(batch_id="", inserted_rows=0, duplicate_file=True)

    if filename.lower().endswith(".xlsx"):
        frame = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    else:
        frame = _read_csv_auto(content)

    frame.columns = [col.strip().lower() for col in _normalize_columns(list(frame.columns))]
    required = {
        "transaction_time",
        "correlation_id",
        "amount",
        "currency",
        "fee_amount",
        "status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Vendor file missing columns: {sorted(missing)}")

    raw_rows: list[dict] = []
    normalized_rows: list[dict] = []
    period_start: datetime | None = None
    period_end: datetime | None = None
    for idx, row in frame.iterrows():
        raw = {str(k): row[k] for k in frame.columns}
        raw_json = _json_safe_dict(raw)
        correlation_id = _clean_excel_string(raw.get("correlation_id"))
        amount = _to_decimal(raw.get("amount"))
        if correlation_id is None or amount is None:
            continue
        txn_dt = pd.to_datetime(raw.get("transaction_time"), errors="coerce")
        txn_value = txn_dt.to_pydatetime() if pd.notna(txn_dt) else None
        if txn_value:
            period_start = txn_value if period_start is None else min(period_start, txn_value)
            period_end = txn_value if period_end is None else max(period_end, txn_value)

        raw_rows.append(
            {
                "batch_id": batch_id,
                "row_number": idx + 2,
                "client_name": _clean_excel_string(raw.get("client_name")),
                "txn_time_raw": _clean_excel_string(raw.get("transaction_time")),
                "correlation_id": correlation_id,
                "amount": amount,
                "currency": _clean_excel_string(raw.get("currency")),
                "fee_amount": _to_decimal(raw.get("fee_amount")),
                "status": _clean_excel_string(raw.get("status")),
                "payment_type": _clean_excel_string(raw.get("payment_type")),
                "merchant_name": _clean_excel_string(raw.get("merchant_name")),
                "username": _clean_excel_string(raw.get("username")),
                "raw_data": json.dumps(raw_json, default=str, allow_nan=False),
            }
        )
        normalized_rows.append(
            {
                "batch_id": batch_id,
                "reference_id": correlation_id,
                "correlation_id": correlation_id,
                "merchant": _clean_excel_string(raw.get("merchant_name")),
                "login_id": _clean_excel_string(raw.get("username")),
                "txn_type": "Deposit",
                "currency": _clean_excel_string(raw.get("currency")),
                "amount": amount,
                "fee": _to_decimal(raw.get("fee_amount")),
                "status": _clean_excel_string(raw.get("status")),
                "txn_datetime_local": txn_value + pd.Timedelta(hours=1) if txn_value else None,
                "txn_datetime_vendor": txn_value,
            }
        )

    inserted_rows = len(normalized_rows)
    with engine.begin() as conn:
        if raw_rows:
            _execute_in_chunks(conn, _VENDOR_RAW_INSERT_SQL, raw_rows)
        if normalized_rows:
            _execute_in_chunks(conn, _VENDOR_NORMALIZED_INSERT_SQL, normalized_rows)

        conn.execute(
            text(
                """
                UPDATE import_batches
                SET row_count = :row_count, period_start = :period_start, period_end = :period_end
                WHERE id = :batch_id
                """
            ),
            {
                "row_count": inserted_rows,
                "period_start": period_start,
                "period_end": period_end,
                "batch_id": batch_id,
            },
        )

    return ImportResult(batch_id=batch_id, inserted_rows=inserted_rows, duplicate_file=False)
