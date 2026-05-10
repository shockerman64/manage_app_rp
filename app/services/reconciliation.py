from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from app.config import RECON_INTERNAL_AMOUNT_MULTIPLIER, RECON_TIME_TOLERANCE_MINUTES
from app.db import engine


@dataclass
class ReconciliationSummary:
    run_id: str
    counts: dict[str, int]


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

            if ticket_no and not correlation_id:
                result_status = "internal_only"
                reason = "No matching vendor correlation_id"
                delta_seconds = None
            elif correlation_id and not ticket_no:
                result_status = "vendor_only"
                reason = "No matching internal Ticket #"
                delta_seconds = None
            else:
                delta_seconds = int(abs((internal_time - vendor_time).total_seconds())) if internal_time and vendor_time else None
                scaled_internal_amount = (
                    Decimal(str(internal_amount)) * amount_multiplier
                    if internal_amount is not None
                    else None
                )
                vendor_amount_decimal = (
                    Decimal(str(vendor_amount)) if vendor_amount is not None else None
                )
                if scaled_internal_amount != vendor_amount_decimal:
                    result_status = "amount_mismatch"
                    reason = f"Different amount values after applying internal multiplier x{amount_multiplier}"
                elif _normalize_status("internal", internal_status) != _normalize_status("vendor", vendor_status):
                    result_status = "status_mismatch"
                    reason = "Different status values"
                elif delta_seconds is not None and delta_seconds > tolerance_minutes * 60:
                    result_status = "time_mismatch"
                    reason = f"Time difference greater than {tolerance_minutes} minutes"
                else:
                    result_status = "matched"
                    reason = "All checks passed"

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

    return ReconciliationSummary(run_id=run_id, counts=dict(counts))
