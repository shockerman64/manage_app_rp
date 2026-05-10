import streamlit as st

from app.db import init_db
from app.services.analytics import get_import_history
from app.services.ingestion import import_internal_csv, import_vendor_file
from app.ui import section_title

st.set_page_config(page_title="RP Dashboard", page_icon=":bar_chart:", layout="wide")

st.title("RP Dashboard - Casino Metrics")
st.caption("Manual ingestion for internal transactions and vendor gateway data.")

with st.sidebar:
    st.header("Actions")
    if st.button("Initialize / Migrate DB", use_container_width=True):
        try:
            with st.spinner("Initializing database schema..."):
                init_db()
            st.success("Database schema initialized.")
        except Exception as exc:
            st.error("Database initialization failed.")
            st.caption(str(exc))

section_title(
    "Upload Internal Transactions",
    "Import internal deposit and withdrawal records from CSV.",
)
upload_left, upload_right = st.columns(2, gap="large")
with upload_left:
    internal_file = st.file_uploader(
        "Upload internal CSV (Deposit & Withdraw log)",
        type=["csv"],
        key="internal_uploader",
    )
    if internal_file is not None:
        if st.button("Import Internal File", type="primary", use_container_width=True):
            try:
                with st.spinner("Importing internal CSV in batches..."):
                    result = import_internal_csv(internal_file.name, internal_file.getvalue())
                if result.duplicate_file:
                    st.warning("This internal file was already imported (same hash).")
                else:
                    st.success(f"Imported internal rows: {result.inserted_rows}")
            except Exception as exc:
                st.error("Internal import failed.")
                st.caption(str(exc))

with upload_right:
    section_title(
        "Upload Vendor Transactions",
        "Upload gateway data files. XLSX is preferred and CSV is supported.",
    )
    vendor_file = st.file_uploader(
        "Upload vendor file (.xlsx preferred, .csv supported)",
        type=["xlsx", "csv"],
        key="vendor_uploader",
    )
    if vendor_file is not None:
        if st.button("Import Vendor File", type="primary", use_container_width=True):
            try:
                with st.spinner("Importing vendor file in batches..."):
                    result = import_vendor_file(vendor_file.name, vendor_file.getvalue())
                if result.duplicate_file:
                    st.warning("This vendor file was already imported (same hash).")
                else:
                    st.success(f"Imported vendor rows: {result.inserted_rows}")
            except Exception as exc:
                st.error("Vendor import failed.")
                st.caption(str(exc))

section_title("Import History", "Most recently imported files and status.")
try:
    history = get_import_history()
    if history.empty:
        st.info("No import history yet.")
    else:
        st.dataframe(history, use_container_width=True, hide_index=True)
except Exception as exc:
    st.info("Initialize the database first, then import files.")
    st.caption(str(exc))
