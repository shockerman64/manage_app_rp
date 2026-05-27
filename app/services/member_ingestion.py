from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text

from app.db import engine
from app.services.ingestion import (
    _clean_excel_string,
    _create_batch,
    _execute_in_chunks,
    _json_safe_dict,
    _normalize_columns,
    _read_csv_auto,
)

MEMBERS_REQUIRED_COLUMNS = ["Member ID", "Login ID", "Date Created"]

MEMBERS_COLUMN_MAP: dict[str, str] = {
    "Group": "group_name",
    "Merchant": "merchant",
    "Login ID": "login_id",
    "Member ID": "member_id",
    "Member Group": "member_group",
    "Currency": "currency",
    "Verify Status": "verify_status",
    "Status": "status",
    "Date Created": "created_date",
    "Last Update": "last_update_at",
    "Last Login Time": "last_login_at",
}

_MEMBERS_UPSERT_SQL = text(
    """
    INSERT INTO members (
        member_id, login_id, created_date, group_name, merchant, member_group,
        currency, verify_status, status, last_update_at, last_login_at,
        raw_data, batch_id
    )
    VALUES (
        :member_id, :login_id, :created_date, :group_name, :merchant, :member_group,
        :currency, :verify_status, :status, :last_update_at, :last_login_at,
        CAST(:raw_data AS jsonb), :batch_id
    )
    ON CONFLICT (member_id) DO UPDATE SET
        login_id = EXCLUDED.login_id,
        created_date = EXCLUDED.created_date,
        group_name = EXCLUDED.group_name,
        merchant = EXCLUDED.merchant,
        member_group = EXCLUDED.member_group,
        currency = EXCLUDED.currency,
        verify_status = EXCLUDED.verify_status,
        status = EXCLUDED.status,
        last_update_at = EXCLUDED.last_update_at,
        last_login_at = EXCLUDED.last_login_at,
        raw_data = EXCLUDED.raw_data,
        batch_id = EXCLUDED.batch_id,
        updated_at = now()
    """
)


@dataclass
class MemberImportResult:
    batch_id: str
    upserted_rows: int
    skipped_rows: int
    duplicate_file: bool


def _parse_date_created(value: object) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _parse_optional_timestamp(value: object) -> datetime | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def import_members_csv(filename: str, content: bytes) -> MemberImportResult:
    batch_id, duplicate = _create_batch("members", filename, content)
    if duplicate or not batch_id:
        return MemberImportResult(batch_id="", upserted_rows=0, skipped_rows=0, duplicate_file=True)

    frame = _read_csv_auto(content)
    frame.columns = _normalize_columns(list(frame.columns))
    # Drop unnamed empty trailing columns from export.
    frame = frame.loc[:, [c for c in frame.columns if c.strip() != ""]]

    missing = set(MEMBERS_REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Members file missing columns: {sorted(missing)}")

    mapped_headers = set(MEMBERS_COLUMN_MAP)
    upsert_rows: list[dict] = []
    skipped_rows = 0
    period_start: date | None = None
    period_end: date | None = None

    for _, row in frame.iterrows():
        clean = {k: _clean_excel_string(v) for k, v in row.to_dict().items()}
        member_id = clean.get("Member ID")
        if not member_id:
            skipped_rows += 1
            continue

        created_date = _parse_date_created(clean.get("Date Created"))
        if created_date is None:
            skipped_rows += 1
            continue

        period_start = created_date if period_start is None else min(period_start, created_date)
        period_end = created_date if period_end is None else max(period_end, created_date)

        raw_only = {
            k: v
            for k, v in _json_safe_dict(clean).items()
            if k not in mapped_headers
        }

        upsert_rows.append(
            {
                "member_id": member_id,
                "login_id": clean.get("Login ID"),
                "created_date": created_date,
                "group_name": clean.get("Group"),
                "merchant": clean.get("Merchant"),
                "member_group": clean.get("Member Group"),
                "currency": clean.get("Currency"),
                "verify_status": clean.get("Verify Status"),
                "status": clean.get("Status"),
                "last_update_at": _parse_optional_timestamp(clean.get("Last Update")),
                "last_login_at": _parse_optional_timestamp(clean.get("Last Login Time")),
                "raw_data": json.dumps(raw_only, allow_nan=False, default=str),
                "batch_id": batch_id,
            }
        )

    upserted_rows = len(upsert_rows)
    with engine.begin() as conn:
        if upsert_rows:
            _execute_in_chunks(conn, _MEMBERS_UPSERT_SQL, upsert_rows)
        conn.execute(
            text(
                """
                UPDATE import_batches
                SET row_count = :row_count,
                    period_start = :period_start,
                    period_end = :period_end
                WHERE id = :batch_id
                """
            ),
            {
                "row_count": upserted_rows,
                "period_start": period_start,
                "period_end": period_end,
                "batch_id": batch_id,
            },
        )

    return MemberImportResult(
        batch_id=batch_id,
        upserted_rows=upserted_rows,
        skipped_rows=skipped_rows,
        duplicate_file=False,
    )
