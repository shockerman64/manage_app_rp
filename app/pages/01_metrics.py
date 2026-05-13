import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.services.analytics import query_frame
from app.ui import (
    BRAND_INTERNAL,
    amount_column_config,
    empty_state,
    format_count,
    format_money,
    format_percent,
    page_header,
    rename_columns,
    section_title,
    setup_page,
)

setup_page("Metrics", ":bar_chart:")
page_header(
    f"{BRAND_INTERNAL} Metrics",
    f"Performance KPIs and daily trends for {BRAND_INTERNAL} deposits and withdrawals.",
)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


PRESETS = ["Last 7 days", "Last 30 days", "This month", "Last 90 days", "All", "Custom"]
if "metrics_preset" not in st.session_state:
    st.session_state["metrics_preset"] = "Last 30 days"

section_title("Filters")
filter_cols = st.columns([1.3, 2])
with filter_cols[0]:
    preset = st.selectbox("Date range preset", PRESETS, key="metrics_preset")


def _resolve_preset(preset_value: str) -> tuple[date | None, date | None]:
    today = date.today()
    if preset_value == "Last 7 days":
        return today - timedelta(days=6), today
    if preset_value == "Last 30 days":
        return today - timedelta(days=29), today
    if preset_value == "Last 90 days":
        return today - timedelta(days=89), today
    if preset_value == "This month":
        return today.replace(day=1), today
    if preset_value == "All":
        return None, None
    return None, None


if preset == "Custom":
    with filter_cols[1]:
        default_range = (date.today() - timedelta(days=29), date.today())
        date_range = st.date_input(
            "Custom date range",
            value=default_range,
            format="DD/MM/YYYY",
            key="metrics_custom_range",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date, end_date = default_range
else:
    start_date, end_date = _resolve_preset(preset)
    with filter_cols[1]:
        if start_date and end_date:
            label = f"{start_date.strftime('%d %b %Y')}  ->  {end_date.strftime('%d %b %Y')}"
        else:
            label = "All available dates"
        st.text_input("Active range", value=label, disabled=True, key="metrics_active_range_display")

if start_date and end_date and start_date > end_date:
    st.error("Start date must be earlier than or equal to end date.")
    st.stop()


# ---------------------------------------------------------------------------
# Build WHERE
# ---------------------------------------------------------------------------


where = ["source_system = 'internal'"]
params: dict = {}
if start_date:
    where.append("txn_datetime_local::date >= :start_date")
    params["start_date"] = start_date
if end_date:
    where.append("txn_datetime_local::date <= :end_date")
    params["end_date"] = end_date

where_sql = " AND ".join(where)


# ---------------------------------------------------------------------------
# KPI summary
# ---------------------------------------------------------------------------


try:
    kpi = query_frame(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN txn_type ILIKE 'Deposit' THEN amount ELSE 0 END), 0) AS total_deposit,
            COALESCE(SUM(CASE WHEN txn_type ILIKE 'Withdraw' THEN amount ELSE 0 END), 0) AS total_withdraw,
            COUNT(*) AS transaction_count,
            COALESCE(AVG(CASE WHEN txn_type ILIKE 'Deposit' THEN amount END), 0) AS avg_deposit,
            COALESCE(AVG(CASE WHEN txn_type ILIKE 'Withdraw' THEN amount END), 0) AS avg_withdraw,
            COALESCE(
              100.0 * SUM(CASE WHEN status ILIKE 'Approved' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
              0
            ) AS approval_rate
        FROM transactions_normalized
        WHERE {where_sql}
        """,
        params,
    )
except Exception as exc:
    st.error("Failed to load KPI metrics.")
    st.caption(str(exc))
    st.stop()

if kpi.empty or int(kpi.iloc[0]["transaction_count"] or 0) == 0:
    empty_state(
        "No data found for the selected range.",
        f"Try a wider date preset, or import a {BRAND_INTERNAL} CSV from the Home page.",
        icon=":bar_chart:",
    )
    st.stop()

summary = kpi.iloc[0]
net_flow = float(summary["total_deposit"] or 0) - float(summary["total_withdraw"] or 0)
cards_top = st.columns(3)
cards_bottom = st.columns(3)
cards_top[0].metric("Total Deposit", format_money(summary["total_deposit"]))
cards_top[1].metric("Total Withdraw", format_money(summary["total_withdraw"]))
cards_top[2].metric("Net Flow", format_money(net_flow))
cards_bottom[0].metric("Transactions", format_count(summary["transaction_count"]))
cards_bottom[1].metric("Approval Rate", format_percent(summary["approval_rate"]))
cards_bottom[2].metric("Avg Deposit", format_money(summary["avg_deposit"]))


# ---------------------------------------------------------------------------
# Daily trend + quick insights
# ---------------------------------------------------------------------------


try:
    daily = query_frame(
        f"""
        SELECT
            txn_datetime_local::date AS day,
            SUM(CASE WHEN txn_type ILIKE 'Deposit' THEN amount ELSE 0 END) AS deposits,
            SUM(CASE WHEN txn_type ILIKE 'Withdraw' THEN amount ELSE 0 END) AS withdraws
        FROM transactions_normalized
        WHERE {where_sql}
        GROUP BY 1
        ORDER BY 1
        """,
        params,
    )
except Exception as exc:
    st.error("Failed to load daily trend chart.")
    st.caption(str(exc))
    daily = pd.DataFrame()

trend_col, insight_col = st.columns([2, 1], gap="large")
with trend_col:
    section_title("Daily Trend", "Deposits vs Withdrawals over time.")
    if not daily.empty:
        melted = daily.melt(
            id_vars="day",
            value_vars=["deposits", "withdraws"],
            var_name="Type",
            value_name="Amount",
        )
        melted["Type"] = melted["Type"].map({"deposits": "Deposits", "withdraws": "Withdrawals"})
        fig = px.line(
            melted,
            x="day",
            y="Amount",
            color="Type",
            labels={"day": "Date"},
            color_discrete_map={"Deposits": "#34D399", "Withdrawals": "#F87171"},
        )
        fig.update_layout(
            height=340,
            margin=dict(l=10, r=10, t=10, b=10),
            template="plotly_dark",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        empty_state("No daily trend data for the selected filters.", "Try widening the date range.")

with insight_col:
    st.subheader("Quick Insights")
    st.metric("Avg Withdraw", format_money(summary["avg_withdraw"]))
    st.metric("Selected Start", str(start_date) if start_date else "All")
    st.metric("Selected End", str(end_date) if end_date else "All")
    st.caption("Use filters to narrow trends and download focused records.")


# ---------------------------------------------------------------------------
# Breakdown tables
# ---------------------------------------------------------------------------


try:
    by_method = query_frame(
        f"""
        SELECT pay_method, COUNT(*) AS txn_count, SUM(amount) AS total_amount
        FROM transactions_normalized
        WHERE {where_sql}
        GROUP BY pay_method
        ORDER BY total_amount DESC NULLS LAST
        """,
        params,
    )
    by_status = query_frame(
        f"""
        SELECT status, COUNT(*) AS txn_count, SUM(amount) AS total_amount
        FROM transactions_normalized
        WHERE {where_sql}
        GROUP BY status
        ORDER BY total_amount DESC NULLS LAST
        """,
        params,
    )
    by_merchant = query_frame(
        f"""
        SELECT merchant, COUNT(*) AS txn_count, SUM(amount) AS total_amount
        FROM transactions_normalized
        WHERE {where_sql}
        GROUP BY merchant
        ORDER BY total_amount DESC NULLS LAST
        LIMIT 20
        """,
        params,
    )
except Exception as exc:
    st.error("Failed to load breakdown tables.")
    st.caption(str(exc))
    by_method = pd.DataFrame()
    by_status = pd.DataFrame()
    by_merchant = pd.DataFrame()


def _render_breakdown(frame: pd.DataFrame, label_col: str, label_name: str) -> None:
    if frame.empty:
        empty_state(f"No {label_name.lower()} data available.", "Try widening the date range.")
        return
    display = rename_columns(
        frame,
        {
            label_col: label_name,
            "txn_count": "Transactions",
            "total_amount": "Total Amount",
        },
    )
    st.dataframe(
        display,
        column_config=amount_column_config(["Total Amount"]),
        use_container_width=True,
        hide_index=True,
    )


section_title("Breakdowns")
breakdown_cols = st.columns(3)
with breakdown_cols[0]:
    st.subheader("By Pay Method")
    _render_breakdown(by_method, "pay_method", "Pay Method")
with breakdown_cols[1]:
    st.subheader("By Status")
    _render_breakdown(by_status, "status", "Status")
with breakdown_cols[2]:
    st.subheader("Top Merchants")
    _render_breakdown(by_merchant, "merchant", "Merchant")


# ---------------------------------------------------------------------------
# Filtered detail rows + export
# ---------------------------------------------------------------------------


section_title("Filtered Records", "Showing top 500 rows in-app. Download for the full filtered set.")
try:
    details = query_frame(
        f"""
        SELECT
            source_system, ticket_no, correlation_id, merchant, login_id, member_id,
            txn_type, pay_method, currency, amount, fee, status, txn_datetime_local
        FROM transactions_normalized
        WHERE {where_sql}
        ORDER BY txn_datetime_local DESC NULLS LAST
        """,
        params,
    )
except Exception as exc:
    st.error("Failed to load filtered export data.")
    st.caption(str(exc))
    details = pd.DataFrame()

if details.empty:
    empty_state("No detail rows match the current filters.", "Try widening the date range or clearing presets.")
else:
    display = rename_columns(
        details,
        {
            "source_system": "Source",
            "ticket_no": "Ticket #",
            "correlation_id": "Correlation ID",
            "merchant": "Merchant",
            "login_id": "Login ID",
            "member_id": "Member ID",
            "txn_type": "Type",
            "pay_method": "Pay Method",
            "currency": "Currency",
            "amount": "Amount",
            "fee": "Fee",
            "status": "Status",
            "txn_datetime_local": "Local Time",
        },
    )
    display["Source"] = display["Source"].map(lambda v: BRAND_INTERNAL if str(v).lower() == "internal" else v)
    st.dataframe(
        display.head(500),
        column_config=amount_column_config(["Amount", "Fee"]),
        use_container_width=True,
        hide_index=True,
    )
    csv_bytes = details.to_csv(index=False).encode("utf-8")
    st.download_button(
        ":arrow_down: Download filtered CSV",
        data=csv_bytes,
        file_name="metrics_filtered.csv",
        mime="text/csv",
        type="secondary",
    )
