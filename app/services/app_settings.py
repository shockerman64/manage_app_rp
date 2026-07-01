from __future__ import annotations

from sqlalchemy import text

from app.db import engine

FACEBOOK_AFFILIATE_CODE_KEY = "facebook_affiliate_code"


def get_setting(key: str, default: str = "") -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": key},
        ).first()
    if row is None:
        return default
    return str(row[0] or "")


def set_setting(key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (:key, :value, now())
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = now()
                """
            ),
            {"key": key, "value": value},
        )


def get_facebook_affiliate_code() -> str:
    return get_setting(FACEBOOK_AFFILIATE_CODE_KEY).strip()


def set_facebook_affiliate_code(code: str) -> None:
    set_setting(FACEBOOK_AFFILIATE_CODE_KEY, code.strip())
