from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd
import streamlit as st


BRAND_INTERNAL = "RP1M"
BRAND_VENDOR = "OASIS PAY"

SOURCE_LABELS: dict[str, str] = {
    "internal": BRAND_INTERNAL,
    "vendor": BRAND_VENDOR,
}

LABEL_TO_SOURCE: dict[str, str] = {label: source for source, label in SOURCE_LABELS.items()}

RESULT_STATUS_LABELS: dict[str, str] = {
    "matched": "Matched",
    "internal_only": f"{BRAND_INTERNAL} Only",
    "vendor_only": f"{BRAND_VENDOR} Only",
    "amount_mismatch": "Amount Mismatch",
    "status_mismatch": "Status Mismatch",
    "time_mismatch": "Time Mismatch",
}

LABEL_TO_RESULT_STATUS: dict[str, str] = {label: status for status, label in RESULT_STATUS_LABELS.items()}


# ---------------------------------------------------------------------------
# Brand / label helpers
# ---------------------------------------------------------------------------


def source_label(value: object) -> str:
    """Return the display brand for an internal source_system code."""
    if value is None:
        return ""
    key = str(value).strip().lower()
    return SOURCE_LABELS.get(key, str(value))


def source_from_label(value: str) -> str:
    """Return the DB source code for a display brand label."""
    return LABEL_TO_SOURCE.get(value, value)


def result_status_label(value: object) -> str:
    if value is None:
        return ""
    key = str(value).strip().lower()
    return RESULT_STATUS_LABELS.get(key, str(value))


def result_status_from_label(value: str) -> str:
    return LABEL_TO_RESULT_STATUS.get(value, value)


def relabel_source_column(frame: pd.DataFrame, column: str = "source_system") -> pd.DataFrame:
    if frame is None or frame.empty or column not in frame.columns:
        return frame
    relabeled = frame.copy()
    relabeled[column] = relabeled[column].map(lambda v: source_label(v))
    return relabeled


def relabel_result_status(frame: pd.DataFrame, column: str = "result_status") -> pd.DataFrame:
    if frame is None or frame.empty or column not in frame.columns:
        return frame
    relabeled = frame.copy()
    relabeled[column] = relabeled[column].map(lambda v: result_status_label(v))
    return relabeled


def rename_columns(frame: pd.DataFrame, mapping: Mapping[str, str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    safe_mapping = {k: v for k, v in mapping.items() if k in frame.columns}
    if not safe_mapping:
        return frame
    return frame.rename(columns=safe_mapping)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def section_title(title: str, caption: str | None = None) -> None:
    st.subheader(title)
    if caption:
        st.caption(caption)


def format_money(value: object) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):,.2f}"
    except Exception:
        return "-"


def format_count(value: object) -> str:
    try:
        if value is None or pd.isna(value):
            return "0"
        return f"{int(value):,}"
    except Exception:
        return "0"


def format_percent(value: object, digits: int = 2) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}%"
    except Exception:
        return "-"


def format_amount_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    formatted = frame.copy()
    for column in columns:
        if column in formatted.columns:
            formatted[column] = formatted[column].apply(format_money)
    return formatted


def amount_column_config(columns: Iterable[str]) -> dict[str, st.column_config.NumberColumn]:
    config: dict[str, st.column_config.NumberColumn] = {}
    for column in columns:
        config[column] = st.column_config.NumberColumn(column, format="%,.2f")
    return config


def datetime_column_config(
    columns: Iterable[str],
    fmt: str = "DD MMM YYYY, HH:mm",
) -> dict[str, st.column_config.DatetimeColumn]:
    config: dict[str, st.column_config.DatetimeColumn] = {}
    for column in columns:
        config[column] = st.column_config.DatetimeColumn(column, format=fmt)
    return config


def merged_column_config(*configs: Mapping[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {}
    for cfg in configs:
        if cfg:
            merged.update(cfg)
    return merged


# ---------------------------------------------------------------------------
# Empty state component
# ---------------------------------------------------------------------------


def empty_state(title: str, hint: str | None = None, icon: str = ":mag:") -> None:
    """A compact, friendly empty-state block used in place of bare st.info."""
    body = f"**{icon} {title}**"
    if hint:
        body += f"\n\n{hint}"
    st.info(body)


# ---------------------------------------------------------------------------
# Global look & feel
# ---------------------------------------------------------------------------


_GLOBAL_CSS = """
<style>
/* Tighter top padding so the title sits closer to the navigation */
.main .block-container {
    padding-top: 1.6rem;
    padding-bottom: 3rem;
    max-width: 1400px;
}

/* Brand accent bar */
.rp-accent-bar {
    height: 4px;
    width: 100%;
    background: linear-gradient(90deg, #4F46E5 0%, #7C3AED 50%, #06B6D4 100%);
    border-radius: 4px;
    margin: 0 0 1.2rem 0;
}

/* Rounded card look for metrics */
[data-testid="stMetric"] {
    background-color: rgba(30, 41, 59, 0.55);
    border: 1px solid rgba(148, 163, 184, 0.15);
    padding: 0.85rem 1rem;
    border-radius: 12px;
    transition: border-color 120ms ease-in-out, transform 120ms ease-in-out;
}
[data-testid="stMetric"]:hover {
    border-color: rgba(99, 102, 241, 0.55);
    transform: translateY(-1px);
}
[data-testid="stMetricLabel"] p {
    color: #94A3B8 !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
[data-testid="stMetricValue"] {
    font-size: 1.4rem !important;
}

/* Subtle hover for dataframe rows */
[data-testid="stDataFrame"] div[role="row"]:hover {
    background-color: rgba(99, 102, 241, 0.08);
}

/* Tabs: a bit more breathing room */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    padding: 0.55rem 1rem;
    border-radius: 8px 8px 0 0;
}

/* Sidebar branding */
.rp-sidebar-brand {
    font-size: 1.15rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    margin-bottom: 0.1rem;
}
.rp-sidebar-brand-sub {
    color: #94A3B8;
    font-size: 0.78rem;
    margin-bottom: 0.85rem;
}

/* Status pill */
.rp-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    border: 1px solid rgba(148, 163, 184, 0.25);
}
.rp-pill-ok {
    color: #34D399;
    background: rgba(16, 185, 129, 0.12);
    border-color: rgba(16, 185, 129, 0.35);
}
.rp-pill-err {
    color: #F87171;
    background: rgba(239, 68, 68, 0.12);
    border-color: rgba(239, 68, 68, 0.35);
}
.rp-pill-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
}
</style>
"""


def inject_global_css() -> None:
    """Inject the shared look-and-feel CSS. Safe to call once per page."""
    if st.session_state.get("_rp_css_injected"):
        return
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)
    st.session_state["_rp_css_injected"] = True


def accent_bar() -> None:
    st.markdown('<div class="rp-accent-bar"></div>', unsafe_allow_html=True)


def render_sidebar_brand() -> None:
    st.sidebar.markdown(
        '<div class="rp-sidebar-brand">RP Dashboard</div>'
        f'<div class="rp-sidebar-brand-sub">{BRAND_INTERNAL} &middot; {BRAND_VENDOR}</div>',
        unsafe_allow_html=True,
    )


def render_db_status_pill() -> None:
    """Show a small connection status pill in the sidebar."""
    try:
        from sqlalchemy import text

        from app.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        st.sidebar.markdown(
            '<span class="rp-pill rp-pill-ok"><span class="rp-pill-dot"></span>Database connected</span>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.sidebar.markdown(
            '<span class="rp-pill rp-pill-err"><span class="rp-pill-dot"></span>Database unavailable</span>',
            unsafe_allow_html=True,
        )


def page_header(title: str, caption: str | None = None) -> None:
    """Standard page header: accent bar + title + optional caption."""
    inject_global_css()
    accent_bar()
    st.title(title)
    if caption:
        st.caption(caption)


def setup_page(
    page_title: str,
    page_icon: str,
    *,
    sidebar_brand: bool = True,
    db_status: bool = True,
) -> None:
    """Apply consistent page setup: config + CSS + sidebar branding."""
    st.set_page_config(page_title=page_title, page_icon=page_icon, layout="wide")
    inject_global_css()
    if sidebar_brand:
        render_sidebar_brand()
    if db_status:
        render_db_status_pill()
