from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from app.db import engine


def query_frame(sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def get_import_history() -> pd.DataFrame:
    return query_frame(
        """
        SELECT
            uploaded_at,
            source_type,
            original_filename,
            row_count,
            period_start,
            period_end,
            status,
            file_hash
        FROM import_batches
        ORDER BY uploaded_at DESC
        """
    )
