import os
import re
from datetime import date

from dotenv import load_dotenv

load_dotenv()

_FTD_CUTOFF_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_ftd_cutoff_month(raw: str) -> str:
    value = raw.strip()
    if not _FTD_CUTOFF_MONTH_PATTERN.match(value):
        raise ValueError(
            f"FTD_CUTOFF_MONTH must be YYYY-MM (e.g. 2026-04), got: {raw!r}"
        )
    return value


DATABASE_URL = require_env("DATABASE_URL")
RECON_TIME_TOLERANCE_MINUTES = int(os.getenv("RECON_TIME_TOLERANCE_MINUTES", "10"))
RECON_INTERNAL_AMOUNT_MULTIPLIER = float(os.getenv("RECON_INTERNAL_AMOUNT_MULTIPLIER", "1000"))
FTD_CUTOFF_MONTH = _parse_ftd_cutoff_month(os.getenv("FTD_CUTOFF_MONTH", "2026-04"))
BACKOFFICE_MERCHANT = os.getenv("BACKOFFICE_MERCHANT", "RP1M (161)").strip()


def ftd_cutoff_date() -> date:
    """First calendar day of the configured FTD cutoff month."""
    year_str, month_str = FTD_CUTOFF_MONTH.split("-")
    return date(int(year_str), int(month_str), 1)
