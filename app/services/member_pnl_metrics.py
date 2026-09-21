from __future__ import annotations

from datetime import date

import pandas as pd

from app.services.analytics import query_frame


def _date_clauses(
    start_date: date | None,
    end_date: date | None,
    *,
    column: str = "report_date",
) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict = {}
    if start_date is not None:
        clauses.append(f"{column} >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        clauses.append(f"{column} <= :end_date")
        params["end_date"] = end_date
    if not clauses:
        return "TRUE", params
    return " AND ".join(clauses), params


def _search_clause(search: str | None) -> tuple[str, dict]:
    value = (search or "").strip()
    if not value:
        return "TRUE", {}
    return (
        "(login_id ILIKE :search OR member_id ILIKE :search)",
        {"search": f"%{value}%"},
    )


def _where(
    start_date: date | None,
    end_date: date | None,
    search: str | None = None,
) -> tuple[str, dict]:
    date_sql, params = _date_clauses(start_date, end_date)
    search_sql, search_params = _search_clause(search)
    params.update(search_params)
    return f"{date_sql} AND {search_sql}", params


def get_pnl_row_count() -> int:
    frame = query_frame("SELECT COUNT(*) AS cnt FROM member_pnl_daily")
    if frame.empty:
        return 0
    return int(frame.iloc[0]["cnt"] or 0)


def fetch_winnings_kpis(
    start_date: date | None,
    end_date: date | None,
    search: str | None = None,
) -> pd.Series:
    where_sql, params = _where(start_date, end_date, search)
    sql = f"""
    SELECT
        COALESCE(SUM(total_gl) FILTER (WHERE total_gl > 0), 0) AS total_winnings,
        COUNT(*) FILTER (WHERE total_gl > 0) AS winners_count,
        COALESCE(ABS(SUM(total_gl) FILTER (WHERE total_gl < 0)), 0) AS total_losses,
        COUNT(*) FILTER (WHERE total_gl < 0) AS losers_count,
        COALESCE(SUM(total_gl), 0) AS net_player_gl,
        COUNT(*) FILTER (
            WHERE COALESCE(stakes_count, 0) > 0
               OR COALESCE(total_gl, 0) <> 0
        ) AS active_players,
        COUNT(*) AS row_count
    FROM member_pnl_daily
    WHERE {where_sql}
    """
    frame = query_frame(sql, params)
    if frame.empty:
        return pd.Series(dtype=object)
    return frame.iloc[0]


def fetch_daily_trend(
    start_date: date | None,
    end_date: date | None,
    search: str | None = None,
) -> pd.DataFrame:
    where_sql, params = _where(start_date, end_date, search)
    sql = f"""
    SELECT
        report_date AS day,
        COALESCE(SUM(total_gl) FILTER (WHERE total_gl > 0), 0) AS winnings,
        COALESCE(ABS(SUM(total_gl) FILTER (WHERE total_gl < 0)), 0) AS losses,
        COALESCE(SUM(total_gl), 0) AS net_player_gl,
        COUNT(*) FILTER (WHERE total_gl > 0) AS winners_count,
        COUNT(*) FILTER (WHERE total_gl < 0) AS losers_count
    FROM member_pnl_daily
    WHERE {where_sql}
    GROUP BY report_date
    ORDER BY report_date
    """
    return query_frame(sql, params)


_MEMBER_SELECT = """
    report_date,
    login_id,
    member_id,
    member_group,
    currency,
    valid_stake_amt,
    gain_loss,
    comm,
    bonus_amt,
    total_gl,
    transfer_in_amt,
    transfer_out_amt,
    stakes_count,
    stake_amt
"""


def fetch_top_winners(
    start_date: date | None,
    end_date: date | None,
    *,
    limit: int = 20,
    search: str | None = None,
) -> pd.DataFrame:
    where_sql, params = _where(start_date, end_date, search)
    params["limit"] = limit
    sql = f"""
    SELECT {_MEMBER_SELECT}
    FROM member_pnl_daily
    WHERE {where_sql}
      AND total_gl > 0
    ORDER BY total_gl DESC, valid_stake_amt DESC NULLS LAST, login_id
    LIMIT :limit
    """
    return query_frame(sql, params)


def fetch_top_losers(
    start_date: date | None,
    end_date: date | None,
    *,
    limit: int = 20,
    search: str | None = None,
) -> pd.DataFrame:
    where_sql, params = _where(start_date, end_date, search)
    params["limit"] = limit
    sql = f"""
    SELECT {_MEMBER_SELECT}
    FROM member_pnl_daily
    WHERE {where_sql}
      AND total_gl < 0
    ORDER BY total_gl ASC, valid_stake_amt DESC NULLS LAST, login_id
    LIMIT :limit
    """
    return query_frame(sql, params)


def fetch_member_rows(
    start_date: date | None,
    end_date: date | None,
    search: str | None = None,
) -> pd.DataFrame:
    where_sql, params = _where(start_date, end_date, search)
    sql = f"""
    SELECT {_MEMBER_SELECT}
    FROM member_pnl_daily
    WHERE {where_sql}
    ORDER BY report_date DESC, total_gl DESC NULLS LAST, login_id
    """
    return query_frame(sql, params)
