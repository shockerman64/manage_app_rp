import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.config import RECON_INTERNAL_AMOUNT_MULTIPLIER, RECON_TIME_TOLERANCE_MINUTES
from app.services.analytics import query_frame
from app.services.reconciliation import compare_local_day, countable_result_sql, run_reconciliation
from app.ui import (
    BRAND_INTERNAL,
    BRAND_VENDOR,
    RESULT_STATUS_LABELS,
    amount_column_config,
    datetime_column_config,
    empty_state,
    format_count,
    format_money,
    merged_column_config,
    page_header,
    relabel_result_status,
    rename_columns,
    result_status_from_label,
    section_title,
    setup_page,
)

_COUNTED_RESULTS = countable_result_sql()
_NOT_REJECTED = "UPPER(BTRIM(COALESCE(status, ''))) <> 'REJECTED'"


def _render_selected_day(txn_date, tolerance_minutes: int) -> None:
    label = txn_date.strftime("%d/%m/%Y")
    st.subheader(f"Day comparison · {label}")
    st.caption(
        f"QRIS {BRAND_INTERNAL} rows against {BRAND_VENDOR} rows on this local date. "
        f"{BRAND_INTERNAL} amounts use the x{RECON_INTERNAL_AMOUNT_MULTIPLIER:g} multiplier. "
        "Rejected transactions are excluded. A drifted ticket is missing on one side, "
        "or failed the amount, status, or time check."
    )
    try:
        compared = compare_local_day(txn_date, time_tolerance_minutes=tolerance_minutes)
    except Exception as exc:
        st.error("Failed to compare this date.")
        st.caption(str(exc))
        return

    if compared.empty:
        empty_state(f"No QRIS transactions on {label}.")
        return

    internal_rows = compared["ticket_no"].notna()
    vendor_rows = compared["correlation_id"].notna()
    internal_amount = compared.loc[internal_rows, "internal_amount_scaled"].fillna(0).sum()
    vendor_amount = compared.loc[vendor_rows, "vendor_amount"].fillna(0).sum()
    drifted = compared.loc[compared["result_status"] != "matched"].copy()

    metric_cols = st.columns(5)
    metric_cols[0].metric(f"{BRAND_INTERNAL} Txns", format_count(int(internal_rows.sum())))
    metric_cols[1].metric(f"{BRAND_INTERNAL} Amount", format_money(internal_amount))
    metric_cols[2].metric(f"{BRAND_VENDOR} Txns", format_count(int(vendor_rows.sum())))
    metric_cols[3].metric(f"{BRAND_VENDOR} Amount", format_money(vendor_amount))
    metric_cols[4].metric("Difference", format_money(internal_amount - vendor_amount))

    st.subheader("Drifted transactions")
    if drifted.empty:
        empty_state(
            f"No drifted transactions on {label}.",
            "Every ticket on this local date matched.",
        )
        return

    drifted = drifted.sort_values(
        ["result_status", "ticket_no", "correlation_id"],
        na_position="last",
    )
    drifted_display = relabel_result_status(drifted, "result_status")
    drifted_display = rename_columns(
        drifted_display,
        {
            "result_status": "Status",
            "ticket_no": "Ticket #",
            "correlation_id": "Correlation ID",
            "txn_type": "Type",
            "internal_amount_scaled": f"{BRAND_INTERNAL} Amount",
            "vendor_amount": f"{BRAND_VENDOR} Amount",
            "internal_status": f"{BRAND_INTERNAL} Status",
            "vendor_status": f"{BRAND_VENDOR} Status",
            "internal_txn_datetime_vendor_tz": f"{BRAND_INTERNAL} Time (vendor tz)",
            "vendor_txn_datetime": f"{BRAND_VENDOR} Time",
            "delta_seconds": "Delta (sec)",
            "reason": "Reason",
        },
    )
    drifted_display = drifted_display.drop(columns=["internal_amount"], errors="ignore")
    column_config = merged_column_config(
        amount_column_config([f"{BRAND_INTERNAL} Amount", f"{BRAND_VENDOR} Amount"]),
        datetime_column_config([f"{BRAND_INTERNAL} Time (vendor tz)", f"{BRAND_VENDOR} Time"]),
    )
    st.dataframe(
        drifted_display,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        f":arrow_down: Download drifted transactions for {label}",
        data=drifted_display.to_csv(index=False).encode("utf-8"),
        file_name=f"reconciliation_drift_{txn_date.isoformat()}.csv",
        mime="text/csv",
        type="secondary",
    )


setup_page("Reconciliation", ":link:")
page_header(
    "Reconciliation",
    f"Compare QRIS {BRAND_INTERNAL} deposits and withdrawals with {BRAND_VENDOR} gateway deposits and API disbursements.",
)


# ---------------------------------------------------------------------------
# Run configuration
# ---------------------------------------------------------------------------


top_left, top_right = st.columns([2, 1], gap="large")
with top_left:
    section_title(
        "Run Configuration",
        f"QRIS {BRAND_INTERNAL} deposits are compared with {BRAND_VENDOR} gateway deposits. "
        f"QRIS {BRAND_INTERNAL} withdrawals are compared with {BRAND_VENDOR} API disbursements.",
    )
    st.info(
        f"Amount normalization: {BRAND_INTERNAL} amount x {RECON_INTERNAL_AMOUNT_MULTIPLIER:g}. "
        "Records already reconciled are skipped. One-sided rows are checked again when the other side is imported. "
        "Rejected transactions are excluded."
    )
    tolerance_minutes = st.number_input(
        "Time tolerance (minutes)",
        min_value=0,
        max_value=120,
        value=RECON_TIME_TOLERANCE_MINUTES,
        step=1,
        help="Maximum allowed timestamp difference when matching transactions.",
    )

if "reconciliation_running" not in st.session_state:
    st.session_state.reconciliation_running = False

with top_left:
    if st.button(
        "Run Reconciliation",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.reconciliation_running,
    ):
        try:
            st.session_state.reconciliation_running = True
            with st.status("Running reconciliation. This may take a while...", expanded=True) as status:
                result = run_reconciliation(time_tolerance_minutes=int(tolerance_minutes))
                status.update(label=f"Reconciliation completed. Run ID: {result.run_id}", state="complete")
            st.success(f"Reconciliation completed. Run ID: {result.run_id}")
            if result.reopened_rows:
                st.caption(f"Re-checked {result.reopened_rows:,} previously one-sided rows.")
            counts_display = {RESULT_STATUS_LABELS.get(k, k): v for k, v in result.counts.items()}
            st.json(counts_display)
        except Exception as exc:
            st.error("Reconciliation failed.")
            st.caption(str(exc))
        finally:
            st.session_state.reconciliation_running = False


# ---------------------------------------------------------------------------
# Load runs
# ---------------------------------------------------------------------------


try:
    runs = query_frame(
        """
        SELECT id, created_at
        FROM reconciliation_runs
        ORDER BY created_at DESC
        """
    )
except Exception as exc:
    st.error("Failed to load reconciliation runs.")
    st.caption(str(exc))
    st.stop()

if runs.empty:
    empty_state(
        "No reconciliation run yet.",
        f"Import both data sources (Home page) and run reconciliation above.",
        icon=":link:",
    )
    st.stop()

latest_run_id = runs.iloc[0]["id"]
latest_run_created_at = runs.iloc[0]["created_at"]

with top_right:
    st.subheader("Quick Snapshot")
    st.metric("Latest Run Time", f"{latest_run_created_at}")
    st.metric("Total Runs", format_count(len(runs)))
    st.caption("Showing latest run details by default.")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


tab_latest, tab_history = st.tabs([":mag: Latest Run Details", ":calendar: Run History & Daily Summary"])

with tab_latest:
    try:
        summary = query_frame(
            f"""
            SELECT result_status, COUNT(*) AS count_rows
            FROM reconciliation_results
            WHERE run_id = :run_id
              AND {_COUNTED_RESULTS}
            GROUP BY result_status
            ORDER BY count_rows DESC
            """,
            {"run_id": latest_run_id},
        )
        details = query_frame(
            f"""
            SELECT
                result_status, ticket_no, correlation_id,
                internal_amount, vendor_amount,
                internal_status, vendor_status,
                internal_txn_datetime_vendor_tz, vendor_txn_datetime,
                delta_seconds, reason
            FROM reconciliation_results
            WHERE run_id = :run_id
              AND {_COUNTED_RESULTS}
            ORDER BY id DESC
            """,
            {"run_id": latest_run_id},
        )
    except Exception as exc:
        st.error("Failed to load latest reconciliation details.")
        st.caption(str(exc))
        st.stop()

    matched_count = int(
        summary.loc[summary["result_status"] == "matched", "count_rows"].sum() if not summary.empty else 0
    )
    total_count = int(summary["count_rows"].sum() if not summary.empty else 0)
    mismatch_count = total_count - matched_count
    if total_count == 0:
        run_health = "No records"
    elif matched_count == total_count:
        run_health = "Healthy"
    elif matched_count == 0:
        run_health = "Critical"
    else:
        run_health = "Drift"

    health_cols = st.columns(4)
    health_cols[0].metric("Total", format_count(total_count))
    health_cols[1].metric("Matched", format_count(matched_count))
    health_cols[2].metric("Mismatched", format_count(mismatch_count))
    match_rate = (matched_count / total_count * 100.0) if total_count else 0.0
    health_cols[3].metric("Run Health", run_health, delta=f"{match_rate:.1f}% match rate")

    summary_col, chart_col = st.columns([1.4, 1], gap="large")

    with summary_col:
        st.subheader("Summary")
        if summary.empty:
            empty_state("No summary rows for the latest run.")
        else:
            display_summary = relabel_result_status(summary, "result_status")
            display_summary = rename_columns(
                display_summary,
                {"result_status": "Status", "count_rows": "Count"},
            )
            st.dataframe(display_summary, use_container_width=True, hide_index=True)

    with chart_col:
        st.subheader("Match Distribution")
        if total_count == 0:
            empty_state("Nothing to plot yet.")
        else:
            chart_frame = relabel_result_status(summary.copy(), "result_status")
            fig = px.pie(
                chart_frame,
                names="result_status",
                values="count_rows",
                hole=0.55,
                color="result_status",
                color_discrete_map={
                    "Matched": "#34D399",
                    f"{BRAND_INTERNAL} Only": "#6366F1",
                    f"{BRAND_VENDOR} Only": "#06B6D4",
                    "Amount Mismatch": "#F59E0B",
                    "Status Mismatch": "#FB923C",
                    "Time Mismatch": "#F87171",
                },
            )
            fig.update_layout(
                height=240,
                margin=dict(l=10, r=10, t=10, b=10),
                template="plotly_dark",
                showlegend=True,
                legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02),
            )
            fig.update_traces(textinfo="percent")
            st.plotly_chart(fig, use_container_width=True)

    # Detail filters
    available_status_labels = sorted(
        {RESULT_STATUS_LABELS.get(v, v) for v in details["result_status"].dropna().unique().tolist()}
    )
    filter_status_labels = st.multiselect(
        "Filter status",
        options=available_status_labels,
        default=[],
        key="latest_filter_status",
        help="Leave empty to show all statuses.",
    )
    if filter_status_labels:
        filter_status_db = {result_status_from_label(lbl) for lbl in filter_status_labels}
        details = details[details["result_status"].isin(filter_status_db)]

    st.subheader("Reconciliation Details")
    if details.empty:
        empty_state("No reconciliation details for the current filter.")
    else:
        details_display = relabel_result_status(details, "result_status")
        details_display = rename_columns(
            details_display,
            {
                "result_status": "Status",
                "ticket_no": "Ticket #",
                "correlation_id": "Correlation ID",
                "internal_amount": f"{BRAND_INTERNAL} Amount",
                "vendor_amount": f"{BRAND_VENDOR} Amount",
                "internal_status": f"{BRAND_INTERNAL} Status",
                "vendor_status": f"{BRAND_VENDOR} Status",
                "internal_txn_datetime_vendor_tz": f"{BRAND_INTERNAL} Time (vendor tz)",
                "vendor_txn_datetime": f"{BRAND_VENDOR} Time",
                "delta_seconds": "Delta (sec)",
                "reason": "Reason",
            },
        )
        column_config = merged_column_config(
            amount_column_config([f"{BRAND_INTERNAL} Amount", f"{BRAND_VENDOR} Amount"]),
            datetime_column_config([f"{BRAND_INTERNAL} Time (vendor tz)", f"{BRAND_VENDOR} Time"]),
        )
        st.dataframe(
            details_display,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
        )

    if not details.empty:
        st.download_button(
            ":arrow_down: Download reconciliation results CSV",
            data=details.to_csv(index=False).encode("utf-8"),
            file_name="reconciliation_results.csv",
            mime="text/csv",
            type="secondary",
        )


with tab_history:
    selected_txn_date = None
    history_left, history_right = st.columns([2, 1], gap="large")
    with history_left:
        st.subheader("Run History")
        history_display = rename_columns(
            runs,
            {"id": "Run ID", "created_at": "Created At"},
        )
        st.dataframe(
            history_display,
            column_config=datetime_column_config(["Created At"]),
            use_container_width=True,
            hide_index=True,
        )

    with history_right:
        st.subheader("Transaction Date Filter")
        runs_created_at = runs.copy()
        runs_created_at["created_at"] = pd.to_datetime(runs_created_at["created_at"], errors="coerce")
        runs_created_at = runs_created_at.dropna(subset=["created_at"])
        if runs_created_at.empty:
            st.error("Run history has no valid created_at timestamps.")
            st.stop()
        default_end_date = runs_created_at["created_at"].dt.date.max()
        default_start_date = runs_created_at["created_at"].dt.date.min()
        selected_date_range = st.date_input(
            "Select transaction date range",
            value=(default_start_date, default_end_date),
            format="DD/MM/YYYY",
            key="history_txn_date_range",
        )
        if not isinstance(selected_date_range, tuple) or len(selected_date_range) != 2:
            empty_state("Please select a start and end date.")
            st.stop()
        start_date, end_date = selected_date_range
        if start_date > end_date:
            st.error("Start date cannot be after end date.")
            st.stop()

    try:
        run_summary = query_frame(
            f"""
            SELECT
                COUNT(*) AS total_records,
                SUM(CASE WHEN result_status = 'matched' THEN 1 ELSE 0 END) AS matched_records,
                SUM(CASE WHEN result_status <> 'matched' THEN 1 ELSE 0 END) AS mismatched_records
            FROM reconciliation_results
            WHERE COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date)
                  BETWEEN :start_date AND :end_date
              AND {_COUNTED_RESULTS}
            """,
            {"start_date": start_date, "end_date": end_date},
        )
    except Exception as exc:
        st.error("Failed to load run summary.")
        st.caption(str(exc))
        st.stop()

    total_records = int(run_summary.iloc[0]["total_records"] or 0)
    matched_records = int(run_summary.iloc[0]["matched_records"] or 0)
    mismatched_records = int(run_summary.iloc[0]["mismatched_records"] or 0)

    if total_records == 0:
        run_health = "No records"
    elif matched_records == total_records:
        run_health = "Healthy"
    elif mismatched_records == total_records:
        run_health = "Critical"
    else:
        run_health = "Drift"
    match_rate = (matched_records / total_records * 100.0) if total_records else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", format_count(total_records))
    c2.metric("Matched", format_count(matched_records))
    c3.metric("Mismatch", format_count(mismatched_records))
    c4.metric("Run Health", run_health, delta=f"{match_rate:.1f}% match rate" if total_records else None)

    summary_col, status_col = st.columns(2, gap="large")
    with summary_col:
        st.subheader("Daily Reconciliation Summary")
        st.caption(
            "Source totals are calculated from imported transactions. "
            f"{BRAND_INTERNAL} includes QRIS IM deposits and withdrawals. "
            f"{BRAND_VENDOR} includes gateway deposits and API disbursements. "
            "Rejected transactions are excluded. "
            "Reconciled totals come from reconciliation results and can be lower until a new run picks up one-sided rows. "
            "Select a date to compare both sources and list the drifted tickets."
        )
        try:
            daily_summary = query_frame(
                f"""
                WITH internal_daily AS (
                    SELECT
                        txn_datetime_local::date AS txn_date,
                        COUNT(*) AS internal_transactions,
                        COALESCE(SUM(amount), 0) * :internal_amount_multiplier AS internal_total_amount
                    FROM transactions_normalized
                    WHERE source_system = 'internal'
                      AND pay_method = 'QRIS IM'
                      AND {_NOT_REJECTED}
                      AND txn_datetime_local::date BETWEEN :start_date AND :end_date
                    GROUP BY txn_datetime_local::date
                ),
                vendor_daily AS (
                    SELECT
                        txn_datetime_local::date AS txn_date,
                        COUNT(*) AS vendor_transactions,
                        COALESCE(SUM(amount), 0) AS vendor_total_amount
                    FROM transactions_normalized
                    WHERE source_system = 'vendor'
                      AND {_NOT_REJECTED}
                      AND txn_datetime_local::date BETWEEN :start_date AND :end_date
                    GROUP BY txn_datetime_local::date
                ),
                recon_daily AS (
                    SELECT
                        COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date) AS txn_date,
                        COUNT(*) AS total_records,
                        SUM(CASE WHEN result_status = 'matched' THEN 1 ELSE 0 END) AS matched_records,
                        SUM(CASE WHEN result_status <> 'matched' THEN 1 ELSE 0 END) AS mismatched_records
                    FROM reconciliation_results
                    WHERE COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date)
                          BETWEEN :start_date AND :end_date
                      AND {_COUNTED_RESULTS}
                    GROUP BY COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date)
                ),
                all_dates AS (
                    SELECT txn_date FROM internal_daily
                    UNION
                    SELECT txn_date FROM vendor_daily
                    UNION
                    SELECT txn_date FROM recon_daily
                )
                SELECT
                    d.txn_date,
                    COALESCE(i.internal_transactions, 0) AS internal_transactions,
                    COALESCE(i.internal_total_amount, 0) AS internal_total_amount,
                    COALESCE(v.vendor_transactions, 0) AS vendor_transactions,
                    COALESCE(v.vendor_total_amount, 0) AS vendor_total_amount,
                    COALESCE(r.total_records, 0) AS total_records,
                    COALESCE(r.matched_records, 0) AS matched_records,
                    COALESCE(r.mismatched_records, 0) AS mismatched_records,
                    ROUND(
                        100.0 * COALESCE(r.matched_records, 0) / NULLIF(COALESCE(r.total_records, 0), 0),
                        2
                    ) AS match_rate_pct
                FROM all_dates d
                LEFT JOIN internal_daily i ON i.txn_date = d.txn_date
                LEFT JOIN vendor_daily v ON v.txn_date = d.txn_date
                LEFT JOIN recon_daily r ON r.txn_date = d.txn_date
                ORDER BY d.txn_date DESC
                """,
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "internal_amount_multiplier": RECON_INTERNAL_AMOUNT_MULTIPLIER,
                },
            )
        except Exception as exc:
            st.error("Failed to load daily reconciliation summary.")
            st.caption(str(exc))
            daily_summary = None
        if daily_summary is not None and not daily_summary.empty:
            daily_summary_display = rename_columns(
                daily_summary,
                {
                    "txn_date": "Date",
                    "internal_transactions": f"Source {BRAND_INTERNAL} Txns",
                    "internal_total_amount": f"Source {BRAND_INTERNAL} Amount",
                    "vendor_transactions": f"Source {BRAND_VENDOR} Txns",
                    "vendor_total_amount": f"Source {BRAND_VENDOR} Amount",
                    "total_records": "Reconciled Total",
                    "matched_records": "Reconciled Matched",
                    "mismatched_records": "Reconciled Mismatched",
                    "match_rate_pct": "Match Rate %",
                },
            )
            daily_selection = st.dataframe(
                daily_summary_display,
                column_config=amount_column_config(
                    [f"Source {BRAND_INTERNAL} Amount", f"Source {BRAND_VENDOR} Amount"]
                ),
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="daily_summary_date",
            )
            selected_positions = daily_selection.selection.rows
            if selected_positions:
                selected_value = daily_summary.iloc[selected_positions[0]]["txn_date"]
                selected_txn_date = pd.to_datetime(selected_value).date()
        elif daily_summary is not None:
            empty_state("No daily summary rows for this date range.")

    with status_col:
        st.subheader("Daily Status Breakdown")
        try:
            daily_status = query_frame(
                f"""
                SELECT
                    COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date) AS txn_date,
                    result_status,
                    COUNT(*) AS count_rows
                FROM reconciliation_results
                WHERE COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date)
                      BETWEEN :start_date AND :end_date
                  AND {_COUNTED_RESULTS}
                GROUP BY COALESCE(vendor_txn_datetime::date, internal_txn_datetime_vendor_tz::date), result_status
                ORDER BY txn_date DESC, count_rows DESC
                """,
                {"start_date": start_date, "end_date": end_date},
            )
        except Exception as exc:
            st.error("Failed to load daily status breakdown.")
            st.caption(str(exc))
            daily_status = None
        if daily_status is not None and not daily_status.empty:
            daily_status_display = relabel_result_status(daily_status, "result_status")
            daily_status_display = rename_columns(
                daily_status_display,
                {"txn_date": "Date", "result_status": "Status", "count_rows": "Count"},
            )
            st.dataframe(daily_status_display, use_container_width=True, hide_index=True)
        elif daily_status is not None:
            empty_state("No status breakdown for this date range.")

        st.subheader(f"Import Diagnostics (Latest {BRAND_VENDOR} Batch)")
        st.caption(
            "Helps explain why source daily counts may differ from manual file checks "
            "(e.g. missing key fields or duplicate correlation IDs)."
        )
        try:
            vendor_diag = query_frame(
                """
                WITH latest_vendor_batch AS (
                    SELECT id, uploaded_at, original_filename
                    FROM import_batches
                    WHERE source_type = 'vendor'
                    ORDER BY uploaded_at DESC
                    LIMIT 1
                ),
                raw_stats AS (
                    SELECT
                        COUNT(*) AS raw_rows,
                        SUM(
                            CASE
                                WHEN correlation_id IS NULL OR BTRIM(correlation_id) = '' THEN 1
                                ELSE 0
                            END
                        ) AS missing_correlation_id_rows,
                        SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows
                    FROM vendor_transactions_raw
                    WHERE batch_id = (SELECT id FROM latest_vendor_batch)
                ),
                duplicate_stats AS (
                    SELECT COALESCE(SUM(cnt - 1), 0) AS duplicate_correlation_id_rows
                    FROM (
                        SELECT correlation_id, COUNT(*) AS cnt
                        FROM vendor_transactions_raw
                        WHERE batch_id = (SELECT id FROM latest_vendor_batch)
                          AND correlation_id IS NOT NULL
                          AND BTRIM(correlation_id) <> ''
                        GROUP BY correlation_id
                        HAVING COUNT(*) > 1
                    ) d
                ),
                normalized_stats AS (
                    SELECT COUNT(*) AS normalized_rows
                    FROM transactions_normalized
                    WHERE source_system = 'vendor'
                      AND batch_id = (SELECT id FROM latest_vendor_batch)
                )
                SELECT
                    lb.id AS vendor_batch_id,
                    lb.uploaded_at AS vendor_batch_uploaded_at,
                    lb.original_filename AS vendor_filename,
                    COALESCE(rs.raw_rows, 0) AS raw_rows,
                    COALESCE(ns.normalized_rows, 0) AS normalized_rows,
                    COALESCE(rs.missing_correlation_id_rows, 0) AS missing_correlation_id_rows,
                    COALESCE(rs.missing_amount_rows, 0) AS missing_amount_rows,
                    COALESCE(ds.duplicate_correlation_id_rows, 0) AS duplicate_correlation_id_rows
                FROM latest_vendor_batch lb
                LEFT JOIN raw_stats rs ON TRUE
                LEFT JOIN normalized_stats ns ON TRUE
                LEFT JOIN duplicate_stats ds ON TRUE
                """,
            )
        except Exception as exc:
            st.error("Failed to load import diagnostics.")
            st.caption(str(exc))
            vendor_diag = None

        if vendor_diag is None or vendor_diag.empty:
            empty_state(f"No {BRAND_VENDOR} import batch available for diagnostics.")
        else:
            diag_display = rename_columns(
                vendor_diag,
                {
                    "vendor_batch_id": f"{BRAND_VENDOR} Batch ID",
                    "vendor_batch_uploaded_at": "Uploaded At",
                    "vendor_filename": "Filename",
                    "raw_rows": "Raw Rows",
                    "normalized_rows": "Normalized Rows",
                    "missing_correlation_id_rows": "Missing Correlation ID",
                    "missing_amount_rows": "Missing Amount",
                    "duplicate_correlation_id_rows": "Duplicate Correlation ID",
                },
            )
            st.dataframe(
                diag_display,
                column_config=datetime_column_config(["Uploaded At"]),
                use_container_width=True,
                hide_index=True,
            )

    if selected_txn_date is not None:
        _render_selected_day(selected_txn_date, int(tolerance_minutes))
