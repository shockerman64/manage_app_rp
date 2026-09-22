import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.services.member_pnl_metrics import (
    fetch_available_report_dates,
    fetch_daily_trend,
    fetch_member_rows,
    fetch_top_losers,
    fetch_top_winners,
    fetch_winnings_kpis,
    get_pnl_row_count,
)
from app.ui import (
    empty_state,
    format_count,
    format_money,
    kpi_card,
    merged_column_config,
    page_header,
    rename_columns,
    section_title,
    setup_page,
    signed_kpi_card,
    style_signed_amounts,
)

setup_page("Player Winnings", ":trophy:")
page_header(
    "Daily Player Winnings",
    "Member-perspective P&L from daily **P&L By Member** exports. "
    "**Total G/L** is the headline (includes comm and bonus). "
    "**Betting G/L** is the pure staking result.",
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
if "winnings_preset" not in st.session_state:
    st.session_state["winnings_preset"] = "This month"

try:
    pnl_count = get_pnl_row_count()
except Exception as exc:
    st.error("Failed to load member P&L data. Initialize / migrate the database from Home → Admin.")
    st.caption(str(exc))
    st.stop()

st.caption(
    f"Stored snapshots: **{pnl_count:,}** member-day row(s). "
    "Positive Total G/L means the player won."
)

section_title("Filters")
filter_cols = st.columns([1.3, 2, 1.5])
with filter_cols[0]:
    preset = st.selectbox("Date range preset", PRESETS, key="winnings_preset")


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
            key="winnings_custom_range",
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
        st.text_input("Active range", value=label, disabled=True, key="winnings_active_range_display")

with filter_cols[2]:
    search = st.text_input(
        "Search login / member ID",
        value="",
        placeholder="e.g. barbie",
        key="winnings_search",
    )

if preset == "This week":
    st.caption("This week starts on Monday (local calendar).")

if start_date and end_date and start_date > end_date:
    st.error("Start date must be earlier than or equal to end date.")
    st.stop()

if pnl_count == 0:
    empty_state(
        "No member P&L snapshots imported yet.",
        "Import a daily P&L By Member CSV from **Home → Upload Member P&L**.",
        icon=":trophy:",
    )
    st.stop()

try:
    summary = fetch_winnings_kpis(start_date, end_date, search)
except Exception as exc:
    st.error("Failed to load player winnings metrics.")
    st.caption(str(exc))
    st.stop()

overview_empty = int(summary.get("row_count") or 0) == 0

section_title("Overview", "Totals use **Stakes-Total G/L** (player perspective). Negative values are red.")
if overview_empty:
    empty_state(
        "No member P&L rows found for the selected overview range.",
        "Try a wider date preset or clear the search. Member tables below still use their own day.",
        icon=":trophy:",
    )
else:
    row1 = st.columns(3)
    row2 = st.columns(3)
    with row1[0]:
        kpi_card("Total Player Winnings", format_money(summary.get("total_winnings")), tone="positive")
    with row1[1]:
        kpi_card("Winners", format_count(summary.get("winners_count")), tone="neutral")
    with row1[2]:
        signed_kpi_card("Net Player G/L", summary.get("net_player_gl"))
    with row2[0]:
        kpi_card("Total Player Losses", format_money(summary.get("total_losses")), tone="negative")
    with row2[1]:
        kpi_card("Losers", format_count(summary.get("losers_count")), tone="neutral")
    with row2[2]:
        kpi_card("Active Players", format_count(summary.get("active_players")), tone="neutral")

COLUMN_LABELS = {
    "report_date": "Date",
    "login_id": "Login ID",
    "member_id": "Member ID",
    "member_group": "Member Group",
    "currency": "Currency",
    "valid_stake_amt": "Valid Stake",
    "gain_loss": "Betting G/L",
    "comm": "Comm",
    "bonus_amt": "Bonus",
    "total_gl": "Total G/L",
    "transfer_in_amt": "Transfer In",
    "transfer_out_amt": "Transfer Out",
    "stakes_count": "Stakes",
    "stake_amt": "Stake Amt",
}
AMOUNT_COLUMNS = [
    "Valid Stake",
    "Betting G/L",
    "Comm",
    "Bonus",
    "Total G/L",
    "Transfer In",
    "Transfer Out",
    "Stake Amt",
]


def _display_members(frame: pd.DataFrame) -> pd.DataFrame:
    return rename_columns(frame, COLUMN_LABELS)


def _member_column_config() -> dict:
    return merged_column_config(
        {"Date": st.column_config.DateColumn("Date", format="DD MMM YYYY")}
    )


def _styled_members(frame: pd.DataFrame):
    display = _display_members(frame)
    return style_signed_amounts(display, AMOUNT_COLUMNS)


section_title("Daily Trend", "Winnings, losses, and net player G/L by report date.")
try:
    daily = fetch_daily_trend(start_date, end_date, search)
except Exception as exc:
    st.error("Failed to load daily trend.")
    st.caption(str(exc))
    daily = pd.DataFrame()

if daily is None or daily.empty:
    empty_state("No daily trend rows for the selected filters.", "Try widening the date range.")
else:
    melted = daily.melt(
        id_vars="day",
        value_vars=["winnings", "losses", "net_player_gl"],
        var_name="Type",
        value_name="Amount",
    )
    melted["Type"] = melted["Type"].map(
        {
            "winnings": "Winnings",
            "losses": "Losses",
            "net_player_gl": "Net Player G/L",
        }
    )
    fig = px.line(
        melted,
        x="day",
        y="Amount",
        color="Type",
        labels={"day": "Date"},
        color_discrete_map={
            "Winnings": "#34D399",
            "Losses": "#F87171",
            "Net Player G/L": "#818CF8",
        },
    )
    fig.update_layout(
        height=340,
        margin=dict(l=10, r=10, t=10, b=10),
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

section_title("Daily member tables", "These tables use a single report date. Overview and the trend chart still use the range filter above.")
try:
    available_days = fetch_available_report_dates()
except Exception as exc:
    st.error("Failed to load available table dates.")
    st.caption(str(exc))
    st.stop()
if not available_days:
    empty_state(
        "No daily snapshots available for the member tables.",
        "Import a Member P&L CSV from Home.",
        icon=":trophy:",
    )
    st.stop()

in_range_days = [
    day
    for day in available_days
    if (start_date is None or day >= start_date) and (end_date is None or day <= end_date)
]
preferred_day = in_range_days[0] if in_range_days else available_days[0]
if (
    "winnings_table_day" not in st.session_state
    or st.session_state["winnings_table_day"] not in available_days
):
    st.session_state["winnings_table_day"] = preferred_day

table_day = st.selectbox(
    "Table date",
    options=available_days,
    format_func=lambda day: day.strftime("%d %b %Y"),
    key="winnings_table_day",
    help="Top Winners, Top Losers, and All Members show this one day's snapshot only.",
)
day_label = table_day.strftime("%d %b %Y")

top_cols = st.columns(2)
with top_cols[0]:
    section_title("Top Winners", f"Highest positive Total G/L on **{day_label}**.")
    try:
        winners = fetch_top_winners(table_day, table_day, search=search)
    except Exception as exc:
        st.error("Failed to load top winners.")
        st.caption(str(exc))
        winners = pd.DataFrame()
    if winners is None or winners.empty:
        empty_state("No winners on this day.", "Players with Total G/L above zero will appear here.")
    else:
        winners_display = _styled_members(winners)
        st.dataframe(
            winners_display,
            column_config=_member_column_config(),
            use_container_width=True,
            hide_index=True,
        )

with top_cols[1]:
    section_title("Top Losers", f"Lowest (most negative) Total G/L on **{day_label}**.")
    try:
        losers = fetch_top_losers(table_day, table_day, search=search)
    except Exception as exc:
        st.error("Failed to load top losers.")
        st.caption(str(exc))
        losers = pd.DataFrame()
    if losers is None or losers.empty:
        empty_state("No losers on this day.", "Players with Total G/L below zero will appear here.")
    else:
        losers_display = _styled_members(losers)
        st.dataframe(
            losers_display,
            column_config=_member_column_config(),
            use_container_width=True,
            hide_index=True,
        )

section_title("All Members", f"Every member snapshot on **{day_label}**, including transfers.")
try:
    members = fetch_member_rows(table_day, table_day, search)
except Exception as exc:
    st.error("Failed to load member P&L rows.")
    st.caption(str(exc))
    members = pd.DataFrame()

if members is None or members.empty:
    empty_state("No member rows for this day.", "Pick another table date, or import that day's P&L file.")
else:
    members_display = _styled_members(members)
    st.dataframe(
        members_display,
        column_config=_member_column_config(),
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        ":arrow_down: Download member P&L CSV",
        data=members.to_csv(index=False).encode("utf-8"),
        file_name=f"player_winnings_{table_day.strftime('%Y%m%d')}.csv",
        mime="text/csv",
        type="secondary",
    )
