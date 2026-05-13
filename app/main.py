import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from app.db import init_db
from app.services.analytics import get_import_history, query_frame
from app.services.ingestion import import_internal_csv, import_vendor_file
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

    st.markdown("---")
    st.caption("Navigation")
    st.markdown(
        "- **Home**: import data\n"
        "- **Metrics**: KPIs & trends\n"
        "- **View Transactions**: browse all rows\n"
        "- **Reconciliation**: match RP1M vs OASIS PAY"
    )


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


section_title("Upload Transactions", f"Pick a source tab to import a {BRAND_INTERNAL} or {BRAND_VENDOR} file.")

tab_internal, tab_vendor = st.tabs(
    [f":inbox_tray:  Upload {BRAND_INTERNAL} Transactions", f":inbox_tray:  Upload {BRAND_VENDOR} Transactions"]
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
