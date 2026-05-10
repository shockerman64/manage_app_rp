from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import DATABASE_URL


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


engine: Engine = create_engine(
    _normalize_database_url(DATABASE_URL),
    pool_pre_ping=True,
    # Avoid prepared-statement collisions with transaction poolers (e.g. Supabase pooler/pgBouncer).
    connect_args={"prepare_threshold": None},
)


def init_db() -> None:
    schema_path = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
    sql_text = schema_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql_text))


def run_query(query: str, params: dict | None = None) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(text(query), params or {})
        return [dict(row._mapping) for row in rows]
