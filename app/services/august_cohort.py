from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from app.db import engine
from app.services.ingestion import _clean_excel_string, _normalize_columns, _read_csv_auto

DEFAULT_INACTIVE_DAYS = 14
_EXPORT_DATETIME_FORMAT = "%m/%d/%Y %H:%M:%S"
_EXPORT_STEM = re.compile(r"[^A-Za-z0-9]+")

_MEMBER_SQL = """
SELECT
    NULLIF(TRIM(member_id), '') AS member_id,
    NULLIF(TRIM(login_id), '') AS login_id,
    status,
    last_login_at,
    NULLIF(TRIM(raw_data->>'Contact Number'), '') AS phone_number
FROM members
WHERE NULLIF(TRIM(member_id), '') = ANY(:member_ids)
   OR NULLIF(TRIM(login_id), '') = ANY(:login_ids)
"""

_DEPOSIT_SQL = """
SELECT
    NULLIF(TRIM(member_id), '') AS member_id,
    NULLIF(TRIM(login_id), '') AS login_id,
    amount,
    txn_datetime_local
FROM transactions_normalized
WHERE source_system = 'internal'
  AND txn_type ILIKE 'Deposit'
  AND status ILIKE 'Approved'
  AND (
        NULLIF(TRIM(member_id), '') = ANY(:member_ids)
        OR NULLIF(TRIM(login_id), '') = ANY(:login_ids)
  )
"""


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _looks_like_member_export(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            header = handle.readline()
    except OSError:
        return False
    normalized = header.replace('="', "").replace('"', "")
    return "Member ID" in normalized and "Login ID" in normalized


def list_cohort_csvs(root: Path | None = None) -> list[Path]:
    """Member-export CSVs in the project folder, newest file first."""
    folder = root or project_root()
    matches = [path for path in folder.glob("*.csv") if path.is_file() and _looks_like_member_export(path)]
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches


def export_filename(source_name: str, segment: str) -> str:
    stem = Path(source_name).stem
    safe = _EXPORT_STEM.sub("_", stem).strip("_").lower() or "cohort"
    return f"{safe}_{segment}.csv"


def registration_span(frame: pd.DataFrame) -> str | None:
    if frame is None or frame.empty or "registered_at" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["registered_at"], errors="coerce").dropna()
    if dates.empty:
        return None
    start = dates.min()
    end = dates.max()
    if start.normalize() == end.normalize():
        return start.strftime("%d %b %Y")
    return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"


def _clean_cell(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return _clean_excel_string(value)
    return _clean_excel_string(value)


def _parse_export_datetimes(series: pd.Series) -> pd.Series:
    primary = pd.to_datetime(series, format=_EXPORT_DATETIME_FORMAT, errors="coerce")
    missing = primary.isna() & series.notna()
    if missing.any():
        primary = primary.copy()
        primary.loc[missing] = pd.to_datetime(series.loc[missing], errors="coerce")
    return primary


def _naive_timestamp(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = pd.Timestamp(ts.to_pydatetime().replace(tzinfo=None))
    return ts


def parse_cohort_csv(content: bytes) -> pd.DataFrame:
    """Parse a member-export CSV into one row per player."""
    frame = _read_csv_auto(content)
    frame.columns = _normalize_columns(list(frame.columns))
    frame = frame.loc[:, [column for column in frame.columns if column.strip() != ""]]

    missing = [column for column in ("Member ID", "Login ID") if column not in frame.columns]
    if missing:
        raise ValueError(f"Cohort file missing columns: {missing}")

    cleaned = frame.map(_clean_cell)
    registered = (
        _parse_export_datetimes(cleaned["Date Created"])
        if "Date Created" in cleaned.columns
        else pd.Series(pd.NaT, index=cleaned.index)
    )
    file_last_login = (
        _parse_export_datetimes(cleaned["Last Login Time"])
        if "Last Login Time" in cleaned.columns
        else pd.Series(pd.NaT, index=cleaned.index)
    )

    parsed = pd.DataFrame(
        {
            "login_id": cleaned["Login ID"],
            "member_id": cleaned["Member ID"],
            "phone_number": cleaned["Contact Number"] if "Contact Number" in cleaned.columns else None,
            "cohort_status": cleaned["Status"] if "Status" in cleaned.columns else None,
            "registered_at": registered,
            "file_last_login_at": file_last_login,
        }
    )
    parsed = parsed[parsed["member_id"].notna() | parsed["login_id"].notna()].copy()

    with_id = parsed[parsed["member_id"].notna()].sort_values(
        "registered_at", ascending=False, na_position="last"
    )
    with_id = with_id.drop_duplicates("member_id", keep="first")
    without_id = parsed[parsed["member_id"].isna()].drop_duplicates("login_id", keep="first")
    parsed = pd.concat([with_id, without_id], ignore_index=True)
    return parsed.sort_values("registered_at", ascending=False, na_position="last").reset_index(drop=True)


def _query_keys(sql: str, member_ids: list[str], login_ids: list[str]) -> pd.DataFrame:
    if not member_ids and not login_ids:
        return pd.DataFrame()
    statement = text(sql).bindparams(
        bindparam("member_ids", type_=ARRAY(String())),
        bindparam("login_ids", type_=ARRAY(String())),
    )
    with engine.begin() as conn:
        return pd.read_sql(
            statement,
            conn,
            params={"member_ids": member_ids, "login_ids": login_ids},
        )


def fetch_matching_members(member_ids: list[str], login_ids: list[str]) -> pd.DataFrame:
    return _query_keys(_MEMBER_SQL, member_ids, login_ids)


def fetch_approved_deposits(member_ids: list[str], login_ids: list[str]) -> pd.DataFrame:
    return _query_keys(_DEPOSIT_SQL, member_ids, login_ids)


def _index_by(rows: list[dict], key: str) -> dict[str, list[int]]:
    indexed: dict[str, list[int]] = {}
    for position, row in enumerate(rows):
        value = row.get(key)
        if not value:
            continue
        indexed.setdefault(str(value), []).append(position)
    return indexed


def _player_deposits(
    deposits: list[dict],
    by_member: dict[str, list[int]],
    by_login: dict[str, list[int]],
    member_id: str | None,
    login_id: str | None,
) -> list[dict]:
    positions: set[int] = set()
    if member_id:
        positions.update(by_member.get(member_id, []))
    if login_id:
        positions.update(by_login.get(login_id, []))
    matched = [deposits[position] for position in positions]

    def _sort_key(row: dict) -> tuple[bool, pd.Timestamp]:
        ts = _naive_timestamp(row.get("txn_datetime_local"))
        if ts is None:
            return True, pd.Timestamp.min
        return False, ts

    matched.sort(key=_sort_key)
    return matched


def build_cohort_comparison(
    cohort: pd.DataFrame,
    members: pd.DataFrame,
    deposits: pd.DataFrame,
    *,
    as_of: date,
    inactive_days: int,
) -> pd.DataFrame:
    """Join a cohort extract to the player registry and approved deposits."""
    member_rows = [] if members is None or members.empty else members.to_dict("records")
    members_by_id = {
        str(row["member_id"]): row
        for row in member_rows
        if row.get("member_id")
    }
    members_by_login = {
        str(row["login_id"]): row
        for row in member_rows
        if row.get("login_id")
    }

    deposit_rows = [] if deposits is None or deposits.empty else deposits.to_dict("records")
    deposits_by_member = _index_by(deposit_rows, "member_id")
    deposits_by_login = _index_by(deposit_rows, "login_id")

    compared: list[dict] = []
    for row in cohort.to_dict("records"):
        member_id = row.get("member_id") or None
        login_id = row.get("login_id") or None
        member = None
        if member_id:
            member = members_by_id.get(str(member_id))
        if member is None and login_id:
            member = members_by_login.get(str(login_id))

        player_deposits = _player_deposits(
            deposit_rows,
            deposits_by_member,
            deposits_by_login,
            str(member_id) if member_id else None,
            str(login_id) if login_id else None,
        )
        first = player_deposits[0] if player_deposits else None
        first_at = _naive_timestamp(first.get("txn_datetime_local")) if first else None
        first_amount = None
        if first is not None and first.get("amount") is not None and not pd.isna(first.get("amount")):
            first_amount = float(first["amount"])

        file_login = _naive_timestamp(row.get("file_last_login_at"))
        db_login = _naive_timestamp(member.get("last_login_at")) if member else None
        last_login = db_login
        last_login_source = "Database" if db_login is not None else ""
        if file_login is not None and (last_login is None or file_login > last_login):
            last_login = file_login
            last_login_source = "Cohort file"

        days_since_login: int | None = None
        if last_login is not None:
            days_since_login = (as_of - last_login.date()).days
            if days_since_login < 0:
                days_since_login = 0
        inactive = days_since_login is None or days_since_login >= inactive_days

        file_phone = _clean_cell(row.get("phone_number"))
        db_phone = _clean_cell(member.get("phone_number")) if member else None
        compared.append(
            {
                "login_id": login_id,
                "member_id": member_id,
                "phone_number": file_phone or db_phone,
                "registered_at": _naive_timestamp(row.get("registered_at")),
                "cohort_status": row.get("cohort_status"),
                "in_database": member is not None,
                "db_status": member.get("status") if member else None,
                "has_first_deposit": first is not None,
                "first_deposit_at": first_at,
                "first_deposit_amount": first_amount,
                "approved_deposit_count": len(player_deposits),
                "last_login_at": last_login,
                "days_since_login": days_since_login,
                "inactive": inactive,
                "last_login_source": last_login_source or None,
            }
        )

    result = pd.DataFrame(compared)
    if result.empty:
        return result
    return result.sort_values("registered_at", ascending=False, na_position="last").reset_index(drop=True)


def comparison_summary(frame: pd.DataFrame) -> dict[str, int]:
    if frame is None or frame.empty:
        return {
            "players": 0,
            "in_database": 0,
            "first_deposit": 0,
            "no_first_deposit": 0,
            "inactive": 0,
            "not_in_database": 0,
        }
    return {
        "players": int(len(frame)),
        "in_database": int(frame["in_database"].sum()),
        "first_deposit": int(frame["has_first_deposit"].sum()),
        "no_first_deposit": int((~frame["has_first_deposit"]).sum()),
        "inactive": int(frame["inactive"].sum()),
        "not_in_database": int((~frame["in_database"]).sum()),
    }


def describe_filters(deposit: str, activity: str, in_database: str) -> str:
    parts: list[str] = []
    if deposit == "yes":
        parts.append("Made first deposit")
    elif deposit == "no":
        parts.append("No first deposit")
    if activity == "inactive":
        parts.append("Inactive")
    elif activity == "active":
        parts.append("Active")
    if in_database == "yes":
        parts.append("In database")
    elif in_database == "no":
        parts.append("Not in database")
    return " and ".join(parts) if parts else "All players"


def filter_slug(deposit: str, activity: str, in_database: str) -> str:
    parts: list[str] = []
    if deposit == "yes":
        parts.append("first_deposit")
    elif deposit == "no":
        parts.append("no_first_deposit")
    if activity == "inactive":
        parts.append("inactive")
    elif activity == "active":
        parts.append("active")
    if in_database == "yes":
        parts.append("in_database")
    elif in_database == "no":
        parts.append("not_in_database")
    return "_".join(parts) if parts else "all"


def filter_comparison(
    frame: pd.DataFrame,
    *,
    deposit: str = "any",
    activity: str = "any",
    in_database: str = "any",
    search: str = "",
) -> pd.DataFrame:
    """Keep rows that match every selected filter. Unset filters stay open."""
    if frame is None or frame.empty:
        return frame
    result = frame
    if deposit == "yes":
        result = result[result["has_first_deposit"]]
    elif deposit == "no":
        result = result[~result["has_first_deposit"]]
    if activity == "inactive":
        result = result[result["inactive"]]
    elif activity == "active":
        result = result[~result["inactive"]]
    if in_database == "yes":
        result = result[result["in_database"]]
    elif in_database == "no":
        result = result[~result["in_database"]]

    needle = search.strip().lower()
    if needle:
        login = result["login_id"].fillna("").astype(str).str.lower()
        member = result["member_id"].fillna("").astype(str).str.lower()
        phone = (
            result["phone_number"].fillna("").astype(str).str.lower()
            if "phone_number" in result.columns
            else pd.Series("", index=result.index)
        )
        phone_digits = phone.str.replace(r"\D", "", regex=True)
        needle_digits = re.sub(r"\D", "", needle)
        matched = login.str.contains(needle, regex=False) | member.str.contains(needle, regex=False)
        matched = matched | phone.str.contains(needle, regex=False)
        if needle_digits:
            matched = matched | phone_digits.str.contains(needle_digits, regex=False)
        result = result[matched]
    return result.reset_index(drop=True)


def load_cohort_comparison(
    content: bytes,
    *,
    as_of: date,
    inactive_days: int,
) -> pd.DataFrame:
    cohort = parse_cohort_csv(content)
    member_ids = [value for value in cohort["member_id"].dropna().unique().tolist() if value]
    login_ids = [value for value in cohort["login_id"].dropna().unique().tolist() if value]
    members = fetch_matching_members(member_ids, login_ids)
    deposits = fetch_approved_deposits(member_ids, login_ids)
    return build_cohort_comparison(
        cohort,
        members,
        deposits,
        as_of=as_of,
        inactive_days=inactive_days,
    )
