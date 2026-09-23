import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from app.services.august_cohort import (
    DEFAULT_INACTIVE_DAYS,
    comparison_summary,
    describe_filters,
    export_filename,
    filter_comparison,
    filter_slug,
    list_cohort_csvs,
    load_cohort_comparison,
    parse_cohort_csv,
    registration_span,
)
from app.ui import (
    amount_column_config,
    datetime_column_config,
    empty_state,
    format_count,
    merged_column_config,
    page_header,
    rename_columns,
    section_title,
    setup_page,
)

setup_page("Cohort Check", ":material/group:")
page_header(
    "Cohort Check",
    "Compare a member export from any month with the current players database. "
    "**First deposit** is the earliest approved RP1M deposit. "
    "**Inactive** means the latest known login (database, or the file when it is newer) "
    "is older than the day threshold.",
)

_COLUMN_LABELS = {
    "login_id": "Login ID",
    "member_id": "Member ID",
    "phone_number": "Phone Number",
    "registered_at": "Registered",
    "cohort_status": "Cohort Status",
    "in_database": "In Database",
    "db_status": "DB Status",
    "has_first_deposit": "First Deposit",
    "first_deposit_at": "First Deposit At",
    "first_deposit_amount": "First Deposit Amount",
    "approved_deposit_count": "Approved Deposits",
    "last_login_at": "Last Login",
    "days_since_login": "Days Since Login",
    "inactive": "Inactive",
    "last_login_source": "Last Login Source",
}

_COLUMN_ORDER = [
    "login_id",
    "member_id",
    "phone_number",
    "has_first_deposit",
    "first_deposit_at",
    "first_deposit_amount",
    "inactive",
    "days_since_login",
    "last_login_at",
    "last_login_source",
    "registered_at",
    "in_database",
    "approved_deposit_count",
    "cohort_status",
    "db_status",
]

_DEPOSIT_OPTIONS = {
    "Any": "any",
    "Made first deposit": "yes",
    "No first deposit": "no",
}
_ACTIVITY_OPTIONS = {
    "Any": "any",
    "Inactive": "inactive",
    "Active": "active",
}
_DATABASE_OPTIONS = {
    "Any": "any",
    "In database": "yes",
    "Not in database": "no",
}


def _yes_no(series: pd.Series) -> pd.Series:
    return series.map(lambda value: "Yes" if bool(value) else "No")


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.loc[:, [column for column in _COLUMN_ORDER if column in frame.columns]].copy()
    for column in ("in_database", "has_first_deposit", "inactive"):
        if column in display.columns:
            display[column] = _yes_no(display[column])
    return rename_columns(display, _COLUMN_LABELS)


def _render_table(frame: pd.DataFrame, *, download_name: str, empty_title: str, empty_hint: str) -> None:
    if frame is None or frame.empty:
        empty_state(empty_title, empty_hint)
        return
    display = _display_frame(frame)
    st.dataframe(
        display,
        column_config=merged_column_config(
            datetime_column_config(["Registered", "First Deposit At", "Last Login"]),
            amount_column_config(["First Deposit Amount"]),
            {"Phone Number": st.column_config.TextColumn("Phone Number")},
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        ":arrow_down: Download this table",
        data=display.to_csv(index=False).encode("utf-8"),
        file_name=download_name,
        mime="text/csv",
        type="secondary",
        key=f"download_{download_name}",
    )


cohort_files = list_cohort_csvs()
file_names = [path.name for path in cohort_files]

source_cols = st.columns([1.4, 1.6])
with source_cols[0]:
    if file_names:
        selected_name = st.selectbox(
            "Member export in the project folder",
            file_names,
            key="cohort_source_file",
            help="Any member CSV in the project folder with Member ID and Login ID. The newest file is listed first.",
        )
    else:
        selected_name = None
        st.caption("No member export CSV is in the project folder yet.")
with source_cols[1]:
    uploaded = st.file_uploader(
        "Or upload a member export",
        type=["csv"],
        help="Uploading a file uses that file for this check. Clear it to return to a project-folder file.",
        key="cohort_uploader",
    )

if uploaded is not None:
    source_name = uploaded.name
    content = uploaded.getvalue()
    st.caption(f"Using uploaded file **{source_name}**.")
elif selected_name:
    source_name = selected_name
    content = next(path for path in cohort_files if path.name == selected_name).read_bytes()
else:
    empty_state(
        "No member export selected.",
        "Put a member CSV in the project folder, or upload one above. "
        "Required columns are Member ID and Login ID.",
        icon=":file_folder:",
    )
    st.stop()

inactive_days = int(
    st.number_input(
        "Inactive after (days)",
        min_value=1,
        max_value=365,
        value=DEFAULT_INACTIVE_DAYS,
        step=1,
        help="A player is inactive when days since the latest known login reach this number.",
        key="cohort_inactive_days",
    )
)

as_of = date.today()
try:
    cohort_rows = len(parse_cohort_csv(content))
    comparison = load_cohort_comparison(content, as_of=as_of, inactive_days=inactive_days)
except Exception as exc:
    st.error("Failed to compare this file with the database.")
    st.caption(str(exc))
    st.stop()

summary = comparison_summary(comparison)
registered = registration_span(comparison)
registered_label = f" · registered **{registered}**" if registered else ""
st.caption(
    f"Source: **{source_name}** · **{format_count(cohort_rows)}** player(s){registered_label} · "
    f"compared on **{as_of.strftime('%d %b %Y')}** · "
    f"inactive threshold **{inactive_days}** day(s). "
    "A player with no login timestamp is counted as inactive. "
    "Deposit history only includes approved RP1M rows already imported."
)

section_title("Comparison", f"Results for **{source_name}**.")
kpi_cols = st.columns(5)
kpi_cols[0].metric("Players", format_count(summary["players"]))
kpi_cols[1].metric("In Database", format_count(summary["in_database"]))
kpi_cols[2].metric("First Deposit", format_count(summary["first_deposit"]))
kpi_cols[3].metric("No First Deposit", format_count(summary["no_first_deposit"]))
kpi_cols[4].metric(f"Inactive ({inactive_days}d+)", format_count(summary["inactive"]))

if summary["not_in_database"]:
    st.caption(
        f"{summary['not_in_database']:,} player(s) from this file are not in the members table. "
        "Import the registry from Home → Upload Members if those rows should match."
    )

if comparison.empty:
    empty_state("No players in this file.", "Check that the CSV has Member ID or Login ID columns.")
    st.stop()

section_title(
    "Table filters",
    "Each choice applies on its own. Pick more than one to keep players who match every choice.",
)
choice_cols = st.columns(4)
with choice_cols[0]:
    deposit_label = st.selectbox("First deposit", list(_DEPOSIT_OPTIONS), key="cohort_filter_deposit")
with choice_cols[1]:
    activity_label = st.selectbox("Activity", list(_ACTIVITY_OPTIONS), key="cohort_filter_activity")
with choice_cols[2]:
    database_label = st.selectbox("Database", list(_DATABASE_OPTIONS), key="cohort_filter_database")
with choice_cols[3]:
    search = st.text_input(
        "Search",
        placeholder="Login, member ID, or phone",
        key="cohort_search",
    )

deposit = _DEPOSIT_OPTIONS[deposit_label]
activity = _ACTIVITY_OPTIONS[activity_label]
in_database = _DATABASE_OPTIONS[database_label]
filtered = filter_comparison(
    comparison,
    deposit=deposit,
    activity=activity,
    in_database=in_database,
    search=search,
)
if activity == "inactive":
    filtered = filtered.sort_values("days_since_login", ascending=False, na_position="last")
elif deposit == "yes":
    filtered = filtered.sort_values("first_deposit_at", ascending=True, na_position="last")

filter_label = describe_filters(deposit, activity, in_database)
st.caption(f"**{len(filtered):,}** of {len(comparison):,} player(s) · **{filter_label}**.")
_render_table(
    filtered,
    download_name=export_filename(source_name, filter_slug(deposit, activity, in_database)),
    empty_title="No players match these filters.",
    empty_hint="Set a filter back to Any, or clear the search.",
)
