from __future__ import annotations

import re

from sqlalchemy import text

from app.db import engine

FACEBOOK_AFFILIATE_CODE_KEY = "facebook_affiliate_code"
_CODE_SPLIT = re.compile(r"[,;\n]+")


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


def parse_facebook_affiliate_codes(raw: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for part in _CODE_SPLIT.split(raw):
        code = part.strip()
        if not code:
            continue
        key = code.upper()
        if key in seen:
            continue
        seen.add(key)
        codes.append(code)
    return codes


def format_facebook_affiliate_codes(codes: list[str]) -> str:
    return ", ".join(codes)


def get_facebook_affiliate_codes() -> list[str]:
    return parse_facebook_affiliate_codes(get_setting(FACEBOOK_AFFILIATE_CODE_KEY))


def get_facebook_affiliate_code() -> str:
    return format_facebook_affiliate_codes(get_facebook_affiliate_codes())


def set_facebook_affiliate_code(raw: str) -> None:
    codes = parse_facebook_affiliate_codes(raw)
    set_setting(FACEBOOK_AFFILIATE_CODE_KEY, format_facebook_affiliate_codes(codes))
