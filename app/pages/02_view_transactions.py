import math
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
    BRAND_VENDOR,
    SOURCE_LABELS,
    amount_column_config,
    datetime_column_config,
    empty_state,
    format_count,
    format_money,
    merged_column_config,
    relabel_source_column,
    rename_columns,
    section_title,
    setup_page,
    source_from_label,
    page_header,
)


setup_page("View Transactions", ":mag:")
page_header(
    "View Transactions",
    f"Browse every {BRAND_INTERNAL} and {BRAND_VENDOR} transaction with filters, KPIs, and export.",
)


# ---------------------------------------------------------------------------
# Load filter option metadata
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def _load_filter_options() -> dict:
    try:
        bounds = query_frame(
            """
            SELECT
                MIN(txn_datetime_local)::date AS min_date,
                MAX(txn_datetime_local)::date AS max_date,
                COALESCE(MIN(amount), 0) AS min_amount,
                COALESCE(MAX(amount), 0) AS max_amount
            FROM transactions_normalized
            """
        )
        types = query_frame(
            """
            SELECT DISTINCT txn_type
            FROM transactions_normalized
            WHERE txn_type IS NOT NULL AND BTRIM(txn_type) <> ''
            ORDER BY txn_type
            """
        )
        statuses = query_frame(
            """
            SELECT DISTINCT status
            FROM transactions_normalized
            WHERE status IS NOT NULL AND BTRIM(status) <> ''
            ORDER BY status
            """
        )
        methods = query_frame(
            """
            SELECT DISTINCT pay_method
            FROM transactions_normalized
            WHERE pay_method IS NOT NULL AND BTRIM(pay_method) <> ''
            ORDER BY pay_method
            """
        )
    except Exception as exc:
        return {"error": str(exc)}

    row = bounds.iloc[0] if not bounds.empty else {}
    return {
        "min_date": row.get("min_date"),
        "max_date": row.get("max_date"),
        "min_amount": float(row.get("min_amount") or 0),
        "max_amount": float(row.get("max_amount") or 0),
        "types": types["txn_type"].dropna().tolist() if not types.empty else [],
        "statuses": statuses["status"].dropna().tolist() if not statuses.empty else [],
        "pay_methods": methods["pay_method"].dropna().tolist() if not methods.empty else [],
    }


options = _load_filter_options()
if "error" in options:
    st.error("Failed to load filter options.")
    st.caption(options["error"])
    st.stop()

min_date = options.get("min_date")
max_date = options.get("max_date")
if not min_date or not max_date:
    empty_state(
        "No transactions yet",
        f"Import a {BRAND_INTERNAL} CSV or {BRAND_VENDOR} file from the Home page to populate this view.",
        icon=":inbox_tray:",
    )
    st.stop()


# ---------------------------------------------------------------------------
# Layout: Metrics (top) -> Filters -> Table -> Daily Volume (bottom)
# Reserve the top container so KPIs render above filters even though their
# values depend on the filter inputs evaluated below.
# ---------------------------------------------------------------------------


metrics_container = st.container()


# ---------------------------------------------------------------------------
# Filter bar
# ---------------------------------------------------------------------------


section_title("Filters", "Combine filters to narrow the result set. All filters apply together.")

PRESETS = ["Today", "Last 7 days", "Last 30 days", "This month", "All", "Custom"]
if "vt_date_preset" not in st.session_state:
    st.session_state["vt_date_preset"] = "Last 30 days"
if "vt_page" not in st.session_state:
    st.session_state["vt_page"] = 1


def _resolve_preset(preset: str) -> tuple[date, date]:
    today = date.today()
    if preset == "Today":
        return today, today
    if preset == "Last 7 days":
        return today - timedelta(days=6), today
    if preset == "Last 30 days":
        return today - timedelta(days=29), today
    if preset == "This month":
        return today.replace(day=1), today
    return min_date, max_date


row1 = st.columns([1.3, 2, 1.4, 1.4])
with row1[0]:
    preset = st.selectbox(
        "Date range preset",
        PRESETS,
        index=PRESETS.index(st.session_state["vt_date_preset"])
        if st.session_state["vt_date_preset"] in PRESETS
        else 2,
        key="vt_date_preset",
    )
with row1[1]:
    if preset == "Custom":
        default_range = (max(min_date, max_date - timedelta(days=29)), max_date)
        date_range = st.date_input(
            "Custom date range",
            value=default_range,
            min_value=min_date,
            max_value=max_date,
            format="DD/MM/YYYY",
            key="vt_custom_range",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date, end_date = default_range
    else:
        start_date, end_date = _resolve_preset(preset)
        st.text_input(
            "Active range",
            value=f"{start_date.strftime('%d %b %Y')}  ->  {end_date.strftime('%d %b %Y')}",
            disabled=True,
            key="vt_active_range_display",
        )
with row1[2]:
    source_labels = [SOURCE_LABELS["internal"], SOURCE_LABELS["vendor"]]
    selected_sources = st.multiselect(
        "Source",
        options=source_labels,
        default=source_labels,
        key="vt_sources",
    )
with row1[3]:
    selected_types = st.multiselect(
        "Transaction type",
        options=options["types"],
        default=[],
        key="vt_types",
        help="Leave empty to include all types.",
    )

row2 = st.columns([1.4, 1.4, 2, 1.4])
with row2[0]:
    selected_statuses = st.multiselect(
        "Status",
        options=options["statuses"],
        default=[],
        key="vt_statuses",
        help="Leave empty to include all statuses.",
    )
with row2[1]:
    selected_methods = st.multiselect(
        "Pay method",
        options=options["pay_methods"],
        default=[],
        key="vt_methods",
        help="Leave empty to include all pay methods.",
    )
with row2[2]:
    min_amt = float(options["min_amount"] or 0)
    max_amt = float(options["max_amount"] or 0)
    if max_amt <= min_amt:
        max_amt = min_amt + 1
    amount_range = st.slider(
        "Amount range",
        min_value=float(min_amt),
        max_value=float(max_amt),
        value=(float(min_amt), float(max_amt)),
        key="vt_amount_range",
    )
with row2[3]:
    search_term = st.text_input(
        "Search",
        value="",
        placeholder="Ticket / correlation / login / member / merchant",
        key="vt_search",
    )

if start_date > end_date:
    st.error("Start date cannot be after end date.")
    st.stop()


# ---------------------------------------------------------------------------
# Build WHERE clause
# ---------------------------------------------------------------------------


where: list[str] = [
    "txn_datetime_local::date BETWEEN :start_date AND :end_date",
    "amount BETWEEN :min_amount AND :max_amount",
]
params: dict = {
    "start_date": start_date,
    "end_date": end_date,
    "min_amount": amount_range[0],
    "max_amount": amount_range[1],
}

db_sources = [source_from_label(label) for label in selected_sources]
if not db_sources:
    empty_state("Pick at least one source", f"Select {BRAND_INTERNAL}, {BRAND_VENDOR}, or both above.")
    st.stop()

source_keys: list[str] = []
for idx, code in enumerate(db_sources):
    key = f"src_{idx}"
    source_keys.append(f":{key}")
    params[key] = code
where.append(f"source_system IN ({', '.join(source_keys)})")

if selected_types:
    type_keys = []
    for idx, value in enumerate(selected_types):
        key = f"type_{idx}"
        type_keys.append(f":{key}")
        params[key] = value
    where.append(f"txn_type IN ({', '.join(type_keys)})")

if selected_statuses:
    status_keys = []
    for idx, value in enumerate(selected_statuses):
        key = f"status_{idx}"
        status_keys.append(f":{key}")
        params[key] = value
    where.append(f"status IN ({', '.join(status_keys)})")

if selected_methods:
    method_keys = []
    for idx, value in enumerate(selected_methods):
        key = f"method_{idx}"
        method_keys.append(f":{key}")
        params[key] = value
    where.append(f"pay_method IN ({', '.join(method_keys)})")

if search_term and search_term.strip():
    where.append(
        "("
        "ticket_no ILIKE :search "
        "OR correlation_id ILIKE :search "
        "OR login_id ILIKE :search "
        "OR member_id ILIKE :search "
        "OR merchant ILIKE :search"
        ")"
    )
    params["search"] = f"%{search_term.strip()}%"

where_sql = " AND ".join(where)


# ---------------------------------------------------------------------------
# KPI strip
# ---------------------------------------------------------------------------


try:
    kpi_frame = query_frame(
        f"""
        SELECT
            COUNT(*) AS txn_count,
            COALESCE(SUM(amount), 0) AS total_amount,
            COALESCE(AVG(amount), 0) AS avg_amount,
            COUNT(DISTINCT COALESCE(member_id, login_id)) AS distinct_members
        FROM transactions_normalized
        WHERE {where_sql}
        """,
        params,
    )
except Exception as exc:
    st.error("Failed to load KPI summary.")
    st.caption(str(exc))
    st.stop()

if kpi_frame.empty:
    empty_state("No transactions match the current filters.", "Try widening the date range or clearing filters.")
    st.stop()

kpi = kpi_frame.iloc[0]
txn_count = int(kpi["txn_count"] or 0)

with metrics_container:
    section_title("Overview", "KPI summary for the current filter selection.")
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Total Transactions", format_count(txn_count))
    kpi_cols[1].metric("Total Amount", format_money(kpi["total_amount"]))
    kpi_cols[2].metric("Avg Amount", format_money(kpi["avg_amount"]))
    kpi_cols[3].metric("Distinct Members", format_count(kpi["distinct_members"]))
    st.markdown("&nbsp;", unsafe_allow_html=True)

if txn_count == 0:
    empty_state("No transactions match the current filters.", "Try widening the date range or clearing filters.")
    st.stop()


# ---------------------------------------------------------------------------
# Results table with pagination (directly below the filters)
# ---------------------------------------------------------------------------


section_title("Transactions", f"{format_count(txn_count)} matching record(s).")

page_size_col, page_col, _spacer = st.columns([1, 1, 4])
with page_size_col:
    page_size = st.selectbox("Page size", [50, 100, 250], index=0, key="vt_page_size")

total_pages = max(1, math.ceil(txn_count / page_size))
if st.session_state["vt_page"] > total_pages:
    st.session_state["vt_page"] = 1

with page_col:
    page_number = st.number_input(
        "Page",
        min_value=1,
        max_value=total_pages,
        value=st.session_state["vt_page"],
        step=1,
        key="vt_page_input",
    )
    st.session_state["vt_page"] = int(page_number)

offset = (int(page_number) - 1) * int(page_size)
params_with_paging = {**params, "_limit": int(page_size), "_offset": int(offset)}

try:
    rows = query_frame(
        f"""
        SELECT
            source_system,
            ticket_no,
            correlation_id,
            merchant,
            login_id,
            member_id,
            txn_type,
            pay_method,
            currency,
            amount,
            fee,
            status,
            txn_datetime_local
        FROM transactions_normalized
        WHERE {where_sql}
        ORDER BY txn_datetime_local DESC NULLS LAST, id DESC
        LIMIT :_limit OFFSET :_offset
        """,
        params_with_paging,
    )
except Exception as exc:
    st.error("Failed to load transactions.")
    st.caption(str(exc))
    st.stop()

if rows.empty:
    empty_state("No transactions on this page.", "Move to a lower page or widen your filters.")
else:
    display = relabel_source_column(rows, "source_system")
    display = rename_columns(
        display,
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
    column_config = merged_column_config(
        amount_column_config(["Amount", "Fee"]),
        datetime_column_config(["Local Time"]),
    )
    st.dataframe(
        display,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        f"Showing rows {offset + 1:,} - {min(offset + len(rows), txn_count):,} "
        f"of {txn_count:,} (page {int(page_number)} of {total_pages})."
    )


# ---------------------------------------------------------------------------
# CSV export (full filtered set)
# ---------------------------------------------------------------------------


with st.expander("Export full filtered CSV", expanded=False):
    st.caption(
        "Downloads every row matching the current filters (not just the visible page). "
        "Large exports may take a moment."
    )
    if st.button("Prepare export", type="secondary", key="vt_export_btn"):
        with st.spinner("Building CSV..."):
            try:
                full = query_frame(
                    f"""
                    SELECT
                        source_system,
                        ticket_no,
                        correlation_id,
                        merchant,
                        login_id,
                        member_id,
                        txn_type,
                        pay_method,
                        currency,
                        amount,
                        fee,
                        status,
                        txn_datetime_local
                    FROM transactions_normalized
                    WHERE {where_sql}
                    ORDER BY txn_datetime_local DESC NULLS LAST
                    """,
                    params,
                )
            except Exception as exc:
                st.error("Failed to build CSV export.")
                st.caption(str(exc))
                full = pd.DataFrame()
        if not full.empty:
            export = relabel_source_column(full, "source_system")
            export = rename_columns(
                export,
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
            st.download_button(
                "Download CSV",
                data=export.to_csv(index=False).encode("utf-8"),
                file_name=f"transactions_{start_date.isoformat()}_{end_date.isoformat()}.csv",
                mime="text/csv",
                type="primary",
                use_container_width=True,
            )


# ---------------------------------------------------------------------------
# Daily volume chart (kept at the bottom so it does not break the
# Metrics -> Filters -> Table flow above)
# ---------------------------------------------------------------------------


try:
    daily = query_frame(
        f"""
        SELECT
            txn_datetime_local::date AS day,
            source_system,
            COUNT(*) AS txn_count,
            COALESCE(SUM(amount), 0) AS total_amount
        FROM transactions_normalized
        WHERE {where_sql}
        GROUP BY 1, 2
        ORDER BY 1
        """,
        params,
    )
except Exception as exc:
    st.error("Failed to load daily volume chart.")
    st.caption(str(exc))
    daily = pd.DataFrame()

section_title("Daily Volume", f"Per-day transaction count by source ({BRAND_INTERNAL} / {BRAND_VENDOR}).")
if daily.empty:
    empty_state("No daily volume to plot.", "Adjust filters to include more days.")
else:
    daily_display = daily.copy()
    daily_display["Source"] = daily_display["source_system"].map(lambda v: SOURCE_LABELS.get(v, v))
    fig = px.bar(
        daily_display,
        x="day",
        y="txn_count",
        color="Source",
        barmode="stack",
        title=None,
        labels={"day": "Date", "txn_count": "Transactions"},
        color_discrete_map={
            SOURCE_LABELS["internal"]: "#6366F1",
            SOURCE_LABELS["vendor"]: "#06B6D4",
        },
    )
    fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
