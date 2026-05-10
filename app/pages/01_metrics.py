import pandas as pd
import plotly.express as px
import streamlit as st

from app.services.analytics import query_frame
from app.ui import amount_column_config, format_count, format_money, format_percent, section_title

st.set_page_config(page_title="Metrics", page_icon=":bar_chart:", layout="wide")

st.title("Metrics")
st.caption("Performance metrics from normalized transaction data.")

section_title("Filters")
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date", value=None)
with col2:
    end_date = st.date_input("End date", value=None)

if start_date and end_date and start_date > end_date:
    st.error("Start date must be earlier than or equal to end date.")
    st.stop()

where = ["source_system = 'internal'"]
params: dict = {}
if start_date:
    where.append("txn_datetime_local::date >= :start_date")
    params["start_date"] = start_date
if end_date:
    where.append("txn_datetime_local::date <= :end_date")
    params["end_date"] = end_date

where_sql = " AND ".join(where)

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

if kpi.empty:
    st.info("No data found for current filters.")
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
    if not daily.empty:
        melted = daily.melt(id_vars="day", value_vars=["deposits", "withdraws"], var_name="type", value_name="amount")
        fig = px.line(melted, x="day", y="amount", color="type", title="Daily Deposits vs Withdrawals")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No daily trend data for the selected filters.")

with insight_col:
    st.subheader("Quick Insights")
    st.metric("Avg Withdraw", format_money(summary["avg_withdraw"]))
    st.metric("Selected Date Start", str(start_date) if start_date else "All")
    st.metric("Selected Date End", str(end_date) if end_date else "All")
    st.caption("Use filters to narrow trends and download focused records.")

breakdown_cols = st.columns(3)
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

with breakdown_cols[0]:
    st.subheader("By Pay Method")
    if by_method.empty:
        st.info("No pay method data available.")
    else:
        st.dataframe(
            by_method,
            column_config=amount_column_config(["total_amount"]),
            use_container_width=True,
            hide_index=True,
        )
with breakdown_cols[1]:
    st.subheader("By Status")
    if by_status.empty:
        st.info("No status data available.")
    else:
        st.dataframe(
            by_status,
            column_config=amount_column_config(["total_amount"]),
            use_container_width=True,
            hide_index=True,
        )
with breakdown_cols[2]:
    st.subheader("Top Merchants")
    if by_merchant.empty:
        st.info("No merchant data available.")
    else:
        st.dataframe(
            by_merchant,
            column_config=amount_column_config(["total_amount"]),
            use_container_width=True,
            hide_index=True,
        )

section_title("Filtered Data Export", "Showing top 500 rows in-app. Download for full filtered records.")
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
    st.info("No detail rows match the current filters.")
else:
    st.dataframe(
        details.head(500),
        column_config=amount_column_config(["amount", "fee"]),
        use_container_width=True,
        hide_index=True,
    )
    csv_bytes = details.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download filtered CSV",
        data=csv_bytes,
        file_name="metrics_filtered.csv",
        mime="text/csv",
        type="secondary",
    )
