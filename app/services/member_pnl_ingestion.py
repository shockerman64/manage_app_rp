from __future__ import annotations

import json
import re
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
    _to_decimal,
)

PNL_REQUIRED_COLUMNS = ["Member ID", "Login ID", "Stakes-Total G/L"]

PNL_COLUMN_MAP: dict[str, str] = {
    "Group": "group_name",
    "Merchant": "merchant",
    "Member Group": "member_group",
    "Login ID": "login_id",
    "Member ID": "member_id",
    "Currency": "currency",
    "Transfer-#In": "transfer_in_count",
    "Transfer-In Amt": "transfer_in_amt",
    "Transfer-#Out": "transfer_out_count",
    "Transfer-Out Amt": "transfer_out_amt",
    "Adjustment-#Adj": "adjustment_count",
    "Adjustment-Adj Amt": "adjustment_amt",
    "Stakes-#Stakes": "stakes_count",
    "Stakes-Stake Amt": "stake_amt",
    "Stakes-Valid Stake Amt": "valid_stake_amt",
    "Stakes-Gain/Loss": "gain_loss",
    "Stakes-Comm": "comm",
    "Stakes-Bonus Amt": "bonus_amt",
    "Stakes-#Adj": "stakes_adj_count",
    "Stakes-Adj Amt": "stakes_adj_amt",
    "Stakes-Total G/L": "total_gl",
    "Stakes-Prize": "prize",
}

_FILENAME_DATE_PATTERN = re.compile(r"(\d{8})_(\d{8})")

_COUNT_COLUMNS = {
    "Transfer-#In",
    "Transfer-#Out",
    "Adjustment-#Adj",
    "Stakes-#Stakes",
    "Stakes-#Adj",
}

_AMOUNT_COLUMNS = {
    "Transfer-In Amt",
    "Transfer-Out Amt",
    "Adjustment-Adj Amt",
    "Stakes-Stake Amt",
    "Stakes-Valid Stake Amt",
    "Stakes-Gain/Loss",
    "Stakes-Comm",
    "Stakes-Bonus Amt",
    "Stakes-Adj Amt",
    "Stakes-Total G/L",
    "Stakes-Prize",
}

_PNL_UPSERT_SQL = text(
    """
    INSERT INTO member_pnl_daily (
        report_date, member_id, login_id, group_name, merchant, member_group,
        currency, transfer_in_count, transfer_in_amt, transfer_out_count,
        transfer_out_amt, adjustment_count, adjustment_amt, stakes_count,
        stake_amt, valid_stake_amt, gain_loss, comm, bonus_amt,
        stakes_adj_count, stakes_adj_amt, total_gl, prize, raw_data, batch_id
    )
    VALUES (
        :report_date, :member_id, :login_id, :group_name, :merchant, :member_group,
        :currency, :transfer_in_count, :transfer_in_amt, :transfer_out_count,
        :transfer_out_amt, :adjustment_count, :adjustment_amt, :stakes_count,
        :stake_amt, :valid_stake_amt, :gain_loss, :comm, :bonus_amt,
        :stakes_adj_count, :stakes_adj_amt, :total_gl, :prize,
        CAST(:raw_data AS jsonb), :batch_id
    )
    ON CONFLICT (report_date, member_id) DO UPDATE SET
        login_id = EXCLUDED.login_id,
        group_name = EXCLUDED.group_name,
        merchant = EXCLUDED.merchant,
        member_group = EXCLUDED.member_group,
        currency = EXCLUDED.currency,
        transfer_in_count = EXCLUDED.transfer_in_count,
        transfer_in_amt = EXCLUDED.transfer_in_amt,
        transfer_out_count = EXCLUDED.transfer_out_count,
        transfer_out_amt = EXCLUDED.transfer_out_amt,
        adjustment_count = EXCLUDED.adjustment_count,
        adjustment_amt = EXCLUDED.adjustment_amt,
        stakes_count = EXCLUDED.stakes_count,
        stake_amt = EXCLUDED.stake_amt,
        valid_stake_amt = EXCLUDED.valid_stake_amt,
        gain_loss = EXCLUDED.gain_loss,
        comm = EXCLUDED.comm,
        bonus_amt = EXCLUDED.bonus_amt,
        stakes_adj_count = EXCLUDED.stakes_adj_count,
        stakes_adj_amt = EXCLUDED.stakes_adj_amt,
        total_gl = EXCLUDED.total_gl,
        prize = EXCLUDED.prize,
        raw_data = EXCLUDED.raw_data,
        batch_id = EXCLUDED.batch_id,
        updated_at = now()
    """
)


@dataclass
class MemberPnlImportResult:
    batch_id: str
    upserted_rows: int
    skipped_rows: int
    duplicate_file: bool
    report_date: date | None = None
    date_range_warning: bool = False
    parsed_start: date | None = None
    parsed_end: date | None = None


def parse_report_dates_from_filename(filename: str) -> tuple[date, date] | None:
    match = _FILENAME_DATE_PATTERN.search(filename)
    if not match:
        return None
    try:
        start = datetime.strptime(match.group(1), "%Y%m%d").date()
        end = datetime.strptime(match.group(2), "%Y%m%d").date()
    except ValueError:
        return None
    return start, end


def _to_amount(value: object) -> float | None:
    decimal = _to_decimal(value)
    if decimal is None or pd.isna(decimal):
        return None
    return float(decimal)


def _to_int(value: object) -> int | None:
    decimal = _to_amount(value)
    if decimal is None:
        return None
    return int(decimal)


def import_member_pnl_csv(
    filename: str,
    content: bytes,
    report_date: date | None = None,
) -> MemberPnlImportResult:
    parsed = parse_report_dates_from_filename(filename)
    parsed_start = parsed[0] if parsed else None
    parsed_end = parsed[1] if parsed else None
    date_range_warning = bool(parsed and parsed_start != parsed_end)

    if report_date is None:
        if parsed_start is None:
            raise ValueError(
                "Could not parse report date from filename. "
                "Expected YYYYMMDD_YYYYMMDD (e.g. 4.3_P_&_L_By_Member_20260920_20260920_ALL.csv) "
                "or provide a report date override."
            )
        report_date = parsed_start

    frame = _read_csv_auto(content)
    frame.columns = _normalize_columns(list(frame.columns))
    frame = frame.loc[:, [c for c in frame.columns if c.strip() != "" and not str(c).startswith("Unnamed:")]]

    missing = set(PNL_REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Member P&L file missing columns: {sorted(missing)}")

    batch_id, duplicate = _create_batch("member_pnl", filename, content)
    if duplicate or not batch_id:
        return MemberPnlImportResult(
            batch_id="",
            upserted_rows=0,
            skipped_rows=0,
            duplicate_file=True,
            report_date=report_date,
            date_range_warning=date_range_warning,
            parsed_start=parsed_start,
            parsed_end=parsed_end,
        )

    mapped_headers = set(PNL_COLUMN_MAP)
    upsert_rows: list[dict] = []
    skipped_rows = 0

    for _, row in frame.iterrows():
        clean = {k: _clean_excel_string(v) for k, v in row.to_dict().items()}
        member_id = clean.get("Member ID")
        if not member_id:
            skipped_rows += 1
            continue

        raw_only = {
            k: v
            for k, v in _json_safe_dict(clean).items()
            if k not in mapped_headers
        }

        record: dict = {
            "report_date": report_date,
            "member_id": member_id,
            "login_id": clean.get("Login ID"),
            "group_name": clean.get("Group"),
            "merchant": clean.get("Merchant"),
            "member_group": clean.get("Member Group"),
            "currency": clean.get("Currency"),
            "raw_data": json.dumps(raw_only, allow_nan=False, default=str),
            "batch_id": batch_id,
        }
        for source_col in _COUNT_COLUMNS:
            record[PNL_COLUMN_MAP[source_col]] = _to_int(row.get(source_col))
        for source_col in _AMOUNT_COLUMNS:
            record[PNL_COLUMN_MAP[source_col]] = _to_amount(row.get(source_col))

        upsert_rows.append(record)

    upserted_rows = len(upsert_rows)
    period_start = datetime.combine(report_date, datetime.min.time())
    period_end = period_start
    with engine.begin() as conn:
        if upsert_rows:
            _execute_in_chunks(conn, _PNL_UPSERT_SQL, upsert_rows)
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

    return MemberPnlImportResult(
        batch_id=batch_id,
        upserted_rows=upserted_rows,
        skipped_rows=skipped_rows,
        duplicate_file=False,
        report_date=report_date,
        date_range_warning=date_range_warning,
        parsed_start=parsed_start,
        parsed_end=parsed_end,
    )
