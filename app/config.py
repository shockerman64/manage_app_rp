import os

from dotenv import load_dotenv

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DATABASE_URL = require_env("DATABASE_URL")
RECON_TIME_TOLERANCE_MINUTES = int(os.getenv("RECON_TIME_TOLERANCE_MINUTES", "10"))
RECON_INTERNAL_AMOUNT_MULTIPLIER = float(os.getenv("RECON_INTERNAL_AMOUNT_MULTIPLIER", "1000"))
