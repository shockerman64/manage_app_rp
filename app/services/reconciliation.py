from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd
from sqlalchemy import text

from app.config import RECON_INTERNAL_AMOUNT_MULTIPLIER, RECON_TIME_TOLERANCE_MINUTES
from app.db import engine


@dataclass
class ReconciliationSummary:
    run_id: str
    counts: dict[str, int]
    reopened_rows: int = 0


def _countable_status_sql(column: str) -> str:
    if not column.replace("_", "").replace(".", "").isalnum():
        raise ValueError(f"Unsafe status column: {column}")
    return f"UPPER(BTRIM(COALESCE({column}, ''))) <> 'REJECTED'"


def countable_result_sql() -> str:
    """Reconciliation results with a rejected side are left out of counts."""
    return f"{_countable_status_sql('internal_status')} AND {_countable_status_sql('vendor_status')}"


def classify_pair(
    *,
    ticket_no: str | None,
    correlation_id: str | None,
    internal_amount,
    vendor_amount,
    internal_status: str | None,
    vendor_status: str | None,
    internal_time,
    vendor_time,
    amount_multiplier: Decimal,
    tolerance_minutes: int,
) -> tuple[str, str, int | None]:
    """Return result status, reason, and absolute time delta for one ticket pair."""
    if ticket_no and not correlation_id:
        return "internal_only", "No matching vendor correlation_id", None
    if correlation_id and not ticket_no:
        return "vendor_only", "No matching internal Ticket #", None

    delta_seconds = (
        int(abs((internal_time - vendor_time).total_seconds()))
        if internal_time and vendor_time
        else None
    )
    scaled_internal_amount = (
        Decimal(str(internal_amount)) * amount_multiplier if internal_amount is not None else None
    )
    vendor_amount_decimal = Decimal(str(vendor_amount)) if vendor_amount is not None else None
    if scaled_internal_amount != vendor_amount_decimal:
        return (
            "amount_mismatch",
            f"Different amount values after applying internal multiplier x{amount_multiplier}",
            delta_seconds,
        )
    if _normalize_status("internal", internal_status) != _normalize_status("vendor", vendor_status):
        return "status_mismatch", "Different status values", delta_seconds
    if delta_seconds is not None and delta_seconds > tolerance_minutes * 60:
        return (
            "time_mismatch",
            f"Time difference greater than {tolerance_minutes} minutes",
            delta_seconds,
        )
    return "matched", "All checks passed", delta_seconds


def _normalize_status(source: str, status: str | None) -> str:
    value = (status or "").strip().upper()
    if source == "internal":
        mapping = {
            "APPROVED": "SUCCESS",
            "REJECTED": "FAILED",
            "CANCELED": "FAILED",
            "CANCELLED": "FAILED",
            "PENDING": "PENDING",
        }
        return mapping.get(value, value)
    mapping = {
        "FINALIZED": "SUCCESS",
        "SETTLED": "SUCCESS",
        "SUCCESS": "SUCCESS",
        "FAILED": "FAILED",
        "REJECTED": "FAILED",
        "EXPIRED": "FAILED",
        "PENDING": "PENDING",
    }
    return mapping.get(value, value)


def run_reconciliation(time_tolerance_minutes: int | None = None) -> ReconciliationSummary:
    tolerance_minutes = time_tolerance_minutes or RECON_TIME_TOLERANCE_MINUTES
    amount_multiplier = Decimal(str(RECON_INTERNAL_AMOUNT_MULTIPLIER))
    counts: Counter[str] = Counter()
    with engine.begin() as conn:
        run_id = str(conn.execute(text("INSERT INTO reconciliation_runs DEFAULT VALUES RETURNING id")).scalar_one())
        conn.execute(text("DELETE FROM reconciliation_results WHERE run_id = :run_id"), {"run_id": run_id})
        # One-sided rows are provisional. When the other source arrives later
        # (for example an API disbursement for a QRIS IM withdrawal), drop the
        # old result so this run can match the pair.
        conn.execute(
            text(
                """
                DELETE FROM reconciliation_results
                WHERE UPPER(BTRIM(COALESCE(internal_status, ''))) = 'REJECTED'
                   OR UPPER(BTRIM(COALESCE(vendor_status, ''))) = 'REJECTED'
                """
            )
        )
        reopened_rows = conn.execute(
            text(
                """
                DELETE FROM reconciliation_results rr
                WHERE (
                    rr.result_status = 'internal_only'
                    AND EXISTS (
                        SELECT 1
                        FROM transactions_normalized v
                        WHERE v.source_system = 'vendor'
                          AND v.correlation_id = rr.ticket_no
                          AND UPPER(BTRIM(COALESCE(v.status, ''))) <> 'REJECTED'
                    )
                ) OR (
                    rr.result_status = 'vendor_only'
                    AND EXISTS (
                        SELECT 1
                        FROM transactions_normalized i
                        WHERE i.source_system = 'internal'
                          AND i.pay_method = 'QRIS IM'
                          AND i.ticket_no = rr.correlation_id
                          AND UPPER(BTRIM(COALESCE(i.status, ''))) <> 'REJECTED'
                    )
                )
                """
            )
        ).rowcount
        if reopened_rows is None or reopened_rows < 0:
            reopened_rows = 0

        rows = conn.execute(
            text(
                """
                WITH internal_qris AS (
                    SELECT
                        ticket_no,
                        amount,
                        status,
                        txn_datetime_vendor,
                        txn_datetime_local
                    FROM transactions_normalized
                    WHERE source_system = 'internal'
                      AND pay_method = 'QRIS IM'
                      AND ticket_no IS NOT NULL
                      AND UPPER(BTRIM(COALESCE(status, ''))) <> 'REJECTED'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM reconciliation_results rr
                          WHERE rr.ticket_no = transactions_normalized.ticket_no
                             OR rr.correlation_id = transactions_normalized.ticket_no
                      )
                ),
                vendor_qris AS (
                    SELECT
                        correlation_id,
                        amount,
                        status,
                        txn_datetime_vendor
                    FROM transactions_normalized
                    WHERE source_system = 'vendor'
                      AND correlation_id IS NOT NULL
                      AND UPPER(BTRIM(COALESCE(status, ''))) <> 'REJECTED'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM reconciliation_results rr
                          WHERE rr.ticket_no = transactions_normalized.correlation_id
                             OR rr.correlation_id = transactions_normalized.correlation_id
                      )
                )
                SELECT
                    i.ticket_no,
                    v.correlation_id,
                    i.amount AS internal_amount,
                    v.amount AS vendor_amount,
                    i.status AS internal_status,
                    v.status AS vendor_status,
                    i.txn_datetime_vendor AS internal_vendor_time,
                    v.txn_datetime_vendor AS vendor_time
                FROM internal_qris i
                FULL OUTER JOIN vendor_qris v
                    ON i.ticket_no = v.correlation_id
                """
            )
        )

        for row in rows:
            data = dict(row._mapping)
            ticket_no = data["ticket_no"]
            correlation_id = data["correlation_id"]
            internal_amount = data["internal_amount"]
            vendor_amount = data["vendor_amount"]
            internal_status = data["internal_status"]
            vendor_status = data["vendor_status"]
            internal_time = data["internal_vendor_time"]
            vendor_time = data["vendor_time"]

            result_status, reason, delta_seconds = classify_pair(
                ticket_no=ticket_no,
                correlation_id=correlation_id,
                internal_amount=internal_amount,
                vendor_amount=vendor_amount,
                internal_status=internal_status,
                vendor_status=vendor_status,
                internal_time=internal_time,
                vendor_time=vendor_time,
                amount_multiplier=amount_multiplier,
                tolerance_minutes=tolerance_minutes,
            )

            conn.execute(
                text(
                    """
                    INSERT INTO reconciliation_results (
                        run_id, ticket_no, correlation_id, result_status,
                        internal_amount, vendor_amount, internal_status, vendor_status,
                        internal_txn_datetime_vendor_tz, vendor_txn_datetime, delta_seconds, reason
                    )
                    VALUES (
                        :run_id, :ticket_no, :correlation_id, :result_status,
                        :internal_amount, :vendor_amount, :internal_status, :vendor_status,
                        :internal_time, :vendor_time, :delta_seconds, :reason
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "ticket_no": ticket_no,
                    "correlation_id": correlation_id,
                    "result_status": result_status,
                    "internal_amount": internal_amount,
                    "vendor_amount": vendor_amount,
                    "internal_status": internal_status,
                    "vendor_status": vendor_status,
                    "internal_time": internal_time,
                    "vendor_time": vendor_time,
                    "delta_seconds": delta_seconds,
                    "reason": reason,
                },
            )
            counts[result_status] += 1

        if not counts:
            conn.execute(text("DELETE FROM reconciliation_runs WHERE id = :run_id"), {"run_id": run_id})
            run_id = ""

    return ReconciliationSummary(run_id=run_id, counts=dict(counts), reopened_rows=int(reopened_rows))


def compare_local_day(txn_date: date, time_tolerance_minutes: int | None = None) -> pd.DataFrame:
    """Compare QRIS rows from both sources on one local calendar date.

    Rejected rows are omitted. Amounts on the RP1M side stay in source units;
    ``internal_amount_scaled`` applies the reconciliation multiplier.
    """
    tolerance_minutes = (
        RECON_TIME_TOLERANCE_MINUTES if time_tolerance_minutes is None else time_tolerance_minutes
    )
    amount_multiplier = Decimal(str(RECON_INTERNAL_AMOUNT_MULTIPLIER))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH internal_qris AS (
                    SELECT
                        ticket_no,
                        amount,
                        status,
                        txn_type,
                        txn_datetime_vendor,
                        txn_datetime_local
                    FROM transactions_normalized
                    WHERE source_system = 'internal'
                      AND pay_method = 'QRIS IM'
                      AND ticket_no IS NOT NULL
                      AND UPPER(BTRIM(COALESCE(status, ''))) <> 'REJECTED'
                      AND txn_datetime_local::date = :txn_date
                ),
                vendor_qris AS (
                    SELECT
                        correlation_id,
                        amount,
                        status,
                        txn_type,
                        txn_datetime_vendor,
                        txn_datetime_local
                    FROM transactions_normalized
                    WHERE source_system = 'vendor'
                      AND correlation_id IS NOT NULL
                      AND UPPER(BTRIM(COALESCE(status, ''))) <> 'REJECTED'
                      AND txn_datetime_local::date = :txn_date
                )
                SELECT
                    i.ticket_no,
                    v.correlation_id,
                    COALESCE(i.txn_type, v.txn_type) AS txn_type,
                    i.amount AS internal_amount,
                    v.amount AS vendor_amount,
                    i.status AS internal_status,
                    v.status AS vendor_status,
                    i.txn_datetime_vendor AS internal_vendor_time,
                    v.txn_datetime_vendor AS vendor_time
                FROM internal_qris i
                FULL OUTER JOIN vendor_qris v
                    ON i.ticket_no = v.correlation_id
                """
            ),
            {"txn_date": txn_date},
        ).mappings().all()

    classified: list[dict] = []
    for row in rows:
        result_status, reason, delta_seconds = classify_pair(
            ticket_no=row["ticket_no"],
            correlation_id=row["correlation_id"],
            internal_amount=row["internal_amount"],
            vendor_amount=row["vendor_amount"],
            internal_status=row["internal_status"],
            vendor_status=row["vendor_status"],
            internal_time=row["internal_vendor_time"],
            vendor_time=row["vendor_time"],
            amount_multiplier=amount_multiplier,
            tolerance_minutes=tolerance_minutes,
        )
        internal_amount = row["internal_amount"]
        scaled = (
            Decimal(str(internal_amount)) * amount_multiplier if internal_amount is not None else None
        )
        classified.append(
            {
                "result_status": result_status,
                "ticket_no": row["ticket_no"],
                "correlation_id": row["correlation_id"],
                "txn_type": row["txn_type"],
                "internal_amount": internal_amount,
                "internal_amount_scaled": scaled,
                "vendor_amount": row["vendor_amount"],
                "internal_status": row["internal_status"],
                "vendor_status": row["vendor_status"],
                "internal_txn_datetime_vendor_tz": row["internal_vendor_time"],
                "vendor_txn_datetime": row["vendor_time"],
                "delta_seconds": delta_seconds,
                "reason": reason,
            }
        )
    return pd.DataFrame(classified)
