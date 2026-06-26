from __future__ import annotations

from datetime import date

import pandas as pd

from app.config import FTD_CUTOFF_MONTH, ftd_cutoff_date
from app.services.analytics import query_frame

_MEMBER_JOIN = """
    (
        (t.member_id IS NOT NULL AND TRIM(t.member_id) <> '' AND t.member_id = m.member_id)
        OR (
            (t.member_id IS NULL OR TRIM(t.member_id) = '')
            AND t.login_id IS NOT NULL
            AND TRIM(t.login_id) <> ''
            AND t.login_id = m.login_id
        )
    )
"""

_AFFILIATED_MEMBER_FILTER = "COALESCE(TRIM(m.raw_data->>'Referral ID'), '') <> ''"

_EFFECTIVE_FTD_CTE = f"""
effective_ftd AS (
    SELECT
        t.id,
        t.amount,
        t.txn_datetime_local,
        t.txn_datetime_local::date AS txn_day,
        t.ticket_no,
        COALESCE(t.member_id, t.login_id) AS user_key
    FROM (
        SELECT
            t.*,
            CASE
                WHEN m.created_date < :cutoff_date THEN :cutoff_ts
                ELSE TIMESTAMPTZ '-infinity'
            END AS deposit_window_start
        FROM transactions_normalized t
        INNER JOIN members m ON {_MEMBER_JOIN}
        WHERE t.source_system = 'internal'
          AND t.txn_type ILIKE 'Deposit'
          AND t.status ILIKE 'Approved'
    ) t
    WHERE t.txn_datetime_local >= t.deposit_window_start
),
ranked_ftd AS (
    SELECT
        id,
        amount,
        txn_datetime_local,
        txn_day,
        user_key,
        ROW_NUMBER() OVER (
            PARTITION BY user_key
            ORDER BY txn_datetime_local ASC NULLS LAST, ticket_no
        ) AS rn
    FROM effective_ftd
),
ftd_rows AS (
    SELECT id, amount, txn_datetime_local, txn_day
    FROM ranked_ftd
    WHERE rn = 1
)
"""


def _date_clauses(
    start_date: date | None,
    end_date: date | None,
    *,
    column: str = "txn_datetime_local",
) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict = {}
    if start_date is not None:
        clauses.append(f"{column}::date >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        clauses.append(f"{column}::date <= :end_date")
        params["end_date"] = end_date
    if not clauses:
        return "TRUE", params
    return " AND ".join(clauses), params


def get_member_registry_count() -> int:
    frame = query_frame("SELECT COUNT(*) AS cnt FROM members")
    if frame.empty:
        return 0
    return int(frame.iloc[0]["cnt"] or 0)


def fetch_overview_kpis(start_date: date | None, end_date: date | None) -> pd.Series:
    date_filter, params = _date_clauses(start_date, end_date)
    ftd_date_filter, _ = _date_clauses(start_date, end_date, column="f.txn_datetime_local")
    cutoff = ftd_cutoff_date()
    params["cutoff_date"] = cutoff
    params["cutoff_ts"] = pd.Timestamp(cutoff)

    sql = f"""
    WITH {_EFFECTIVE_FTD_CTE},
    filtered AS (
        SELECT *
        FROM transactions_normalized t
        WHERE t.source_system = 'internal'
          AND t.status ILIKE 'Approved'
          AND {date_filter}
    ),
    ftd_in_range AS (
        SELECT f.id, f.amount
        FROM ftd_rows f
        WHERE {ftd_date_filter}
    )
    SELECT
        COALESCE(SUM(CASE WHEN txn_type ILIKE 'Deposit' THEN amount END), 0) AS total_deposit_amount,
        COUNT(*) FILTER (WHERE txn_type ILIKE 'Deposit') AS total_deposits,
        COALESCE(SUM(CASE WHEN txn_type ILIKE 'Withdraw' THEN amount END), 0) AS total_withdraw_amount,
        COUNT(*) FILTER (WHERE txn_type ILIKE 'Withdraw') AS total_withdraws,
        (SELECT COALESCE(SUM(amount), 0) FROM ftd_in_range) AS ftd_amount,
        (SELECT COUNT(*) FROM ftd_in_range) AS ftd_count,
        (
            SELECT COUNT(DISTINCT COALESCE(t.member_id, t.login_id))
            FROM transactions_normalized t
            LEFT JOIN members m ON {_MEMBER_JOIN}
            WHERE t.source_system = 'internal'
              AND t.txn_type ILIKE 'Deposit'
              AND t.status ILIKE 'Approved'
              AND {date_filter}
              AND m.member_id IS NULL
        ) AS unmatched_member_keys
    FROM filtered
    """
    frame = query_frame(sql, params)
    if frame.empty:
        return pd.Series(dtype=object)
    return frame.iloc[0]


def fetch_daily_breakdown(start_date: date | None, end_date: date | None) -> pd.DataFrame:
    date_filter, params = _date_clauses(start_date, end_date)
    ftd_date_filter, _ = _date_clauses(start_date, end_date, column="f.txn_datetime_local")
    member_date_filter, _ = _date_clauses(start_date, end_date, column="m.created_date")
    cutoff = ftd_cutoff_date()
    params["cutoff_date"] = cutoff
    params["cutoff_ts"] = pd.Timestamp(cutoff)

    sql = f"""
    WITH {_EFFECTIVE_FTD_CTE},
    approved AS (
        SELECT
            txn_datetime_local::date AS day,
            txn_type,
            amount
        FROM transactions_normalized t
        WHERE t.source_system = 'internal'
          AND t.status ILIKE 'Approved'
          AND {date_filter}
    ),
    daily_totals AS (
        SELECT
            day,
            COUNT(*) FILTER (WHERE txn_type ILIKE 'Deposit') AS deposit_count,
            COALESCE(SUM(amount) FILTER (WHERE txn_type ILIKE 'Deposit'), 0) AS deposit_amount,
            COUNT(*) FILTER (WHERE txn_type ILIKE 'Withdraw') AS withdraw_count,
            COALESCE(SUM(amount) FILTER (WHERE txn_type ILIKE 'Withdraw'), 0) AS withdraw_amount
        FROM approved
        GROUP BY day
    ),
    ftd_daily AS (
        SELECT
            f.txn_day AS day,
            COUNT(*) AS ftd_count,
            COALESCE(SUM(f.amount), 0) AS ftd_amount
        FROM ftd_rows f
        WHERE {ftd_date_filter}
        GROUP BY f.txn_day
    ),
    affiliated_ftd_daily AS (
        SELECT
            f.txn_day AS day,
            COUNT(*) AS affiliated_ftd_count
        FROM ftd_rows f
        INNER JOIN transactions_normalized t ON t.id = f.id
        INNER JOIN members m ON {_MEMBER_JOIN}
        WHERE {ftd_date_filter}
          AND {_AFFILIATED_MEMBER_FILTER}
        GROUP BY f.txn_day
    ),
    member_created AS (
        SELECT
            m.created_date AS day,
            COUNT(*) AS new_registers,
            COUNT(*) FILTER (WHERE {_AFFILIATED_MEMBER_FILTER}) AS affiliated_registers
        FROM members m
        WHERE {member_date_filter}
        GROUP BY m.created_date
    ),
    days AS (
        SELECT day FROM daily_totals
        UNION
        SELECT day FROM ftd_daily
        UNION
        SELECT day FROM member_created
    )
    SELECT
        days.day,
        COALESCE(d.deposit_count, 0) AS deposit_count,
        COALESCE(d.deposit_amount, 0) AS deposit_amount,
        COALESCE(f.ftd_count, 0) AS ftd_count,
        COALESCE(f.ftd_amount, 0) AS ftd_amount,
        COALESCE(af.affiliated_ftd_count, 0) AS affiliated_ftd_count,
        COALESCE(d.withdraw_count, 0) AS withdraw_count,
        COALESCE(d.withdraw_amount, 0) AS withdraw_amount,
        COALESCE(mc.new_registers, 0) AS new_registers,
        COALESCE(mc.affiliated_registers, 0) AS affiliated_registers
    FROM days
    LEFT JOIN daily_totals d ON d.day = days.day
    LEFT JOIN ftd_daily f ON f.day = days.day
    LEFT JOIN affiliated_ftd_daily af ON af.day = days.day
    LEFT JOIN member_created mc ON mc.day = days.day
    ORDER BY days.day
    """
    return query_frame(sql, params)


def ftd_cutoff_label() -> str:
    return FTD_CUTOFF_MONTH
