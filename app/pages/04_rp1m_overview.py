import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from app.config import FTD_CUTOFF_MONTH
from app.services.rp1m_metrics import (
    fetch_daily_breakdown,
    fetch_overview_kpis,
    ftd_cutoff_label,
    get_member_registry_count,
)
from app.ui import (
    BRAND_INTERNAL,
    amount_column_config,
    empty_state,
    format_count,
    format_money,
    page_header,
    rename_columns,
    section_title,
    setup_page,
)

setup_page("RP1M Overview", ":money_with_wings:")
page_header(
    f"{BRAND_INTERNAL} Deposits & Withdrawals",
    "Approved deposit and withdrawal totals with member-aware first-time deposit (FTD) metrics.",
)

PRESETS = [
    "Today",
    "This week",
    "This month",
    "Last 7 days",
    "Last 30 days",
    "All",
    "Custom",
]
if "overview_preset" not in st.session_state:
    st.session_state["overview_preset"] = "This month"

member_count = get_member_registry_count()
st.caption(
    f"FTD cutoff month: **{ftd_cutoff_label()}** (from `FTD_CUTOFF_MONTH`). "
    f"Members registry: **{member_count:,}** row(s). "
    "Only **Approved** transactions are included."
)

section_title("Filters")
filter_cols = st.columns([1.3, 2])
with filter_cols[0]:
    preset = st.selectbox("Date range preset", PRESETS, key="overview_preset")


def _monday_of_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _resolve_preset(preset_value: str) -> tuple[date | None, date | None]:
    today = date.today()
    if preset_value == "Today":
        return today, today
    if preset_value == "This week":
        return _monday_of_week(today), today
    if preset_value == "This month":
        return today.replace(day=1), today
    if preset_value == "Last 7 days":
        return today - timedelta(days=6), today
    if preset_value == "Last 30 days":
        return today - timedelta(days=29), today
    if preset_value == "All":
        return None, None
    return None, None


if preset == "Custom":
    with filter_cols[1]:
        default_range = (date.today().replace(day=1), date.today())
        date_range = st.date_input(
            "Custom date range",
            value=default_range,
            format="DD/MM/YYYY",
            key="overview_custom_range",
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
        st.text_input("Active range", value=label, disabled=True, key="overview_active_range_display")

if preset == "This week":
    st.caption("This week starts on Monday (local calendar).")

if start_date and end_date and start_date > end_date:
    st.error("Start date must be earlier than or equal to end date.")
    st.stop()

if member_count == 0:
    st.warning(
        "No members imported yet. Deposit and withdrawal totals still work; "
        "import a Members CSV from **Home → Upload Members** to enable FTD metrics."
    )

try:
    summary = fetch_overview_kpis(start_date, end_date)
except Exception as exc:
    st.error("Failed to load overview metrics.")
    st.caption(str(exc))
    st.stop()

total_activity = int(summary.get("total_deposits") or 0) + int(summary.get("total_withdraws") or 0)
if total_activity == 0 and member_count == 0:
    empty_state(
        "No approved transactions found for the selected range.",
        f"Try a wider date preset, or import {BRAND_INTERNAL} data from the Home page.",
        icon=":money_with_wings:",
    )
    st.stop()

ftd_enabled = member_count > 0
ftd_amount = float(summary.get("ftd_amount") or 0) if ftd_enabled else None
ftd_count = int(summary.get("ftd_count") or 0) if ftd_enabled else None
unmatched = int(summary.get("unmatched_member_keys") or 0)

section_title("Overview", "Totals for the selected date range (Approved only).")
row1 = st.columns(3)
row2 = st.columns(3)
row1[0].metric("Total Deposit Amount", format_money(summary.get("total_deposit_amount")))
row1[1].metric("Total Deposits", format_count(summary.get("total_deposits")))
row1[2].metric(
    "FTD Amount",
    format_money(ftd_amount) if ftd_enabled else "—",
    help=f"First deposit per member rules; cutoff month {FTD_CUTOFF_MONTH}.",
)
row2[0].metric("FTD Count", format_count(ftd_count) if ftd_enabled else "—")
row2[1].metric("Total Withdraw Amount", format_money(summary.get("total_withdraw_amount")))
row2[2].metric("Total Withdraws", format_count(summary.get("total_withdraws")))

if ftd_enabled and unmatched > 0:
    st.caption(
        f"{unmatched:,} distinct member/login key(s) in this range had approved deposits "
        "but no matching Members registry row (excluded from FTD)."
    )

section_title("Daily Breakdown", "One row per calendar day in the selected range.")
try:
    daily = fetch_daily_breakdown(start_date, end_date)
except Exception as exc:
    st.error("Failed to load daily breakdown.")
    st.caption(str(exc))
    daily = None

if daily is None or daily.empty:
    empty_state("No daily breakdown rows for the selected range.", "Try widening the date range.")
else:
    display = rename_columns(
        daily,
        {
            "day": "Date",
            "deposit_count": "Deposit Count",
            "deposit_amount": "Deposit Amount",
            "ftd_count": "FTD Count",
            "ftd_amount": "FTD Amount",
            "withdraw_count": "Withdraw Count",
            "withdraw_amount": "Withdraw Amount",
            "new_registers": "New Registers",
        },
    )
    if not ftd_enabled:
        display = display.drop(columns=["FTD Count", "FTD Amount"], errors="ignore")

    st.dataframe(
        display,
        column_config=amount_column_config(
            [c for c in ["Deposit Amount", "FTD Amount", "Withdraw Amount"] if c in display.columns]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        ":arrow_down: Download daily breakdown CSV",
        data=daily.to_csv(index=False).encode("utf-8"),
        file_name="rp1m_overview_daily.csv",
        mime="text/csv",
        type="secondary",
    )
