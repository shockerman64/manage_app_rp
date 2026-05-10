from __future__ import annotations

import pandas as pd
import streamlit as st


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


def amount_column_config(columns: list[str]) -> dict[str, st.column_config.NumberColumn]:
    config: dict[str, st.column_config.NumberColumn] = {}
    for column in columns:
        config[column] = st.column_config.NumberColumn(column, format="%,.2f")
    return config
