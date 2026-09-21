import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from app.db import init_db
from app.services.analytics import get_import_history, query_frame
from app.services.ingestion import import_internal_csv, import_vendor_file
from app.services.member_ingestion import import_members_csv
from app.services.member_pnl_ingestion import (
    import_member_pnl_csv,
    parse_report_dates_from_filename,
)
from app.ui import (
    BRAND_INTERNAL,
    BRAND_VENDOR,
    datetime_column_config,
    empty_state,
    format_count,
    merged_column_config,
    page_header,
    relabel_source_column,
    rename_columns,
    section_title,
    setup_page,
)

setup_page("RP Dashboard", ":bar_chart:")
page_header(
    "RP Dashboard",
    f"Manual ingestion for {BRAND_INTERNAL} and {BRAND_VENDOR} transactions.",
)


# ---------------------------------------------------------------------------
# Sidebar admin (collapsed by default)
# ---------------------------------------------------------------------------


with st.sidebar:
    with st.expander("Admin", expanded=False):
        st.caption("One-time setup and migrations.")
        if st.button("Initialize / Migrate DB", use_container_width=True):
            try:
                with st.status("Initializing database schema...", expanded=False) as status:
                    init_db()
                    status.update(label="Database schema initialized.", state="complete")
                st.success("Database schema is up to date.")
            except Exception as exc:
                st.error("Database initialization failed.")
                st.caption(str(exc))


# ---------------------------------------------------------------------------
# Top KPI strip (counts + last import)
# ---------------------------------------------------------------------------


def _load_top_stats():
    try:
        return query_frame(
            """
            SELECT
                SUM(CASE WHEN source_system = 'internal' THEN 1 ELSE 0 END) AS internal_rows,
                SUM(CASE WHEN source_system = 'vendor' THEN 1 ELSE 0 END) AS vendor_rows
            FROM transactions_normalized
            """
        )
    except Exception:
        return None


def _load_last_import():
    try:
        return query_frame(
            """
            SELECT MAX(uploaded_at) AS last_uploaded_at
            FROM import_batches
            """
        )
    except Exception:
        return None


stats = _load_top_stats()
last_import = _load_last_import()

kpi_cols = st.columns(3)
if stats is not None and not stats.empty:
    row = stats.iloc[0]
    kpi_cols[0].metric(f"{BRAND_INTERNAL} Rows", format_count(row.get("internal_rows")))
    kpi_cols[1].metric(f"{BRAND_VENDOR} Rows", format_count(row.get("vendor_rows")))
else:
    kpi_cols[0].metric(f"{BRAND_INTERNAL} Rows", "-")
    kpi_cols[1].metric(f"{BRAND_VENDOR} Rows", "-")

if last_import is not None and not last_import.empty and last_import.iloc[0]["last_uploaded_at"] is not None:
    last_ts = last_import.iloc[0]["last_uploaded_at"]
    kpi_cols[2].metric("Last Import", str(last_ts).split(".")[0])
else:
    kpi_cols[2].metric("Last Import", "Never")


# ---------------------------------------------------------------------------
# Tabbed uploaders
# ---------------------------------------------------------------------------


section_title(
    "Upload Data",
    f"Import {BRAND_INTERNAL} transactions, member registry (for FTD), "
    f"daily member P&L, or {BRAND_VENDOR} gateway files.",
)

tab_internal, tab_members, tab_pnl, tab_vendor = st.tabs(
    [
        f":inbox_tray:  Upload {BRAND_INTERNAL} Transactions",
        ":busts_in_silhouette:  Upload Members",
        ":trophy:  Upload Member P&L",
        f":inbox_tray:  Upload {BRAND_VENDOR} Transactions",
    ]
)

with tab_internal:
    st.caption(
        f"Import {BRAND_INTERNAL} deposit and withdrawal records from CSV. "
        "Duplicate files (same content hash) are skipped automatically."
    )
    internal_file = st.file_uploader(
        f"Upload {BRAND_INTERNAL} CSV (Deposit & Withdraw log)",
        type=["csv"],
        key="internal_uploader",
    )
    if internal_file is not None:
        if st.button(f"Import {BRAND_INTERNAL} File", type="primary", use_container_width=True):
            try:
                with st.status(f"Importing {BRAND_INTERNAL} CSV in batches...", expanded=False) as status:
                    result = import_internal_csv(internal_file.name, internal_file.getvalue())
                    status.update(label="Import finished.", state="complete")
                if result.duplicate_file:
                    st.warning(f"This {BRAND_INTERNAL} file was already imported (same hash).")
                else:
                    st.success(f"Imported {BRAND_INTERNAL} rows: {result.inserted_rows:,}")
            except Exception as exc:
                st.error(f"{BRAND_INTERNAL} import failed.")
                st.caption(str(exc))

with tab_members:
    st.caption(
        "Import the member registry CSV (Member ID, Login ID, Date Created required). "
        "Used by RP1M Overview for first-time deposit (FTD) metrics. "
        "Re-importing updates existing members by Member ID."
    )
    members_file = st.file_uploader(
        "Upload Members CSV",
        type=["csv"],
        key="members_uploader",
    )
    if members_file is not None:
        if st.button("Import Members File", type="primary", use_container_width=True):
            try:
                with st.status("Importing members CSV in batches...", expanded=False) as status:
                    result = import_members_csv(members_file.name, members_file.getvalue())
                    status.update(label="Import finished.", state="complete")
                if result.duplicate_file:
                    st.warning("This members file was already imported (same hash).")
                else:
                    msg = f"Upserted members: {result.upserted_rows:,}"
                    if result.skipped_rows:
                        msg += f" (skipped {result.skipped_rows:,} rows missing Member ID or Date Created)"
                    st.success(msg)
            except Exception as exc:
                st.error("Members import failed.")
                st.caption(str(exc))

with tab_pnl:
    st.caption(
        "Import a daily Member P&L CSV (`4.3_P_&_L_By_Member_YYYYMMDD_YYYYMMDD_ALL.csv`). "
        "The report date is read from the filename. Re-importing the same day updates existing "
        "member rows. Open **Player Winnings** after import to analyze results."
    )
    pnl_file = st.file_uploader(
        "Upload Member P&L CSV",
        type=["csv"],
        key="member_pnl_uploader",
    )
    parsed_pnl_dates = (
        parse_report_dates_from_filename(pnl_file.name) if pnl_file is not None else None
    )
    if parsed_pnl_dates:
        parsed_start, parsed_end = parsed_pnl_dates
        if parsed_start == parsed_end:
            st.caption(f"Parsed report date: **{parsed_start.strftime('%d %b %Y')}**")
        else:
            st.warning(
                f"Filename dates differ ({parsed_start.strftime('%d %b %Y')} → "
                f"{parsed_end.strftime('%d %b %Y')}). This snapshot will be stored under the "
                f"**start** date unless you override it below."
            )
    elif pnl_file is not None:
        st.warning(
            "Could not parse YYYYMMDD_YYYYMMDD from the filename. "
            "Set a report date below before importing."
        )

    override_pnl_date = st.checkbox(
        "Override report date",
        value=pnl_file is not None and parsed_pnl_dates is None,
        key="member_pnl_override_date",
    )
    pnl_report_date = None
    if override_pnl_date:
        date_kwargs = {}
        if parsed_pnl_dates:
            date_kwargs["value"] = parsed_pnl_dates[0]
        pnl_report_date = st.date_input(
            "Report date",
            format="DD/MM/YYYY",
            key="member_pnl_report_date",
            **date_kwargs,
        )

    if pnl_file is not None:
        if st.button("Import Member P&L File", type="primary", use_container_width=True):
            if parsed_pnl_dates is None and pnl_report_date is None:
                st.error("Set a report date before importing this file.")
            else:
                try:
                    with st.status("Importing member P&L CSV in batches...", expanded=False) as status:
                        result = import_member_pnl_csv(
                            pnl_file.name,
                            pnl_file.getvalue(),
                            report_date=pnl_report_date,
                        )
                        status.update(label="Import finished.", state="complete")
                    if result.duplicate_file:
                        st.warning("This Member P&L file was already imported (same hash).")
                    else:
                        msg = (
                            f"Upserted member P&L rows: {result.upserted_rows:,} "
                            f"for {result.report_date.strftime('%d %b %Y')}"
                        )
                        if result.skipped_rows:
                            msg += f" (skipped {result.skipped_rows:,} rows missing Member ID)"
                        st.success(msg)
                        if result.date_range_warning and result.parsed_start and result.parsed_end:
                            st.warning(
                                "Filename start and end dates differ; snapshot stored under "
                                f"{result.report_date.strftime('%d %b %Y')}."
                            )
                except Exception as exc:
                    st.error("Member P&L import failed.")
                    st.caption(str(exc))

with tab_vendor:
    st.caption(
        f"Upload {BRAND_VENDOR} gateway data files. XLSX is preferred and CSV is supported. "
        "Duplicate files (same content hash) are skipped automatically."
    )
    vendor_file = st.file_uploader(
        f"Upload {BRAND_VENDOR} file (.xlsx preferred, .csv supported)",
        type=["xlsx", "csv"],
        key="vendor_uploader",
    )
    if vendor_file is not None:
        if st.button(f"Import {BRAND_VENDOR} File", type="primary", use_container_width=True):
            try:
                with st.status(f"Importing {BRAND_VENDOR} file in batches...", expanded=False) as status:
                    result = import_vendor_file(vendor_file.name, vendor_file.getvalue())
                    status.update(label="Import finished.", state="complete")
                if result.duplicate_file:
                    st.warning(f"This {BRAND_VENDOR} file was already imported (same hash).")
                else:
                    st.success(f"Imported {BRAND_VENDOR} rows: {result.inserted_rows:,}")
            except Exception as exc:
                st.error(f"{BRAND_VENDOR} import failed.")
                st.caption(str(exc))


# ---------------------------------------------------------------------------
# Import history
# ---------------------------------------------------------------------------


section_title("Import History", "Most recently imported files and status.")
try:
    history = get_import_history()
    if history.empty:
        empty_state(
            "No imports yet.",
            "Use the upload tabs above to import a file. The history will appear here.",
            icon=":file_folder:",
        )
    else:
        display = relabel_source_column(history, "source_type")
        display = rename_columns(
            display,
            {
                "uploaded_at": "Uploaded At",
                "source_type": "Source",
                "original_filename": "Filename",
                "row_count": "Rows",
                "period_start": "Period Start",
                "period_end": "Period End",
                "status": "Status",
                "file_hash": "File Hash",
            },
        )
        column_config = merged_column_config(
            datetime_column_config(["Uploaded At", "Period Start", "Period End"]),
            {"File Hash": st.column_config.TextColumn("File Hash", help="SHA-256 content hash used to deduplicate files.")},
        )
        st.dataframe(
            display,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
        )
except Exception as exc:
    empty_state(
        "Database not initialized yet.",
        "Open the Admin section in the sidebar and click 'Initialize / Migrate DB'.",
        icon=":warning:",
    )
    st.caption(str(exc))
