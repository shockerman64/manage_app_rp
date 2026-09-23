from datetime import date

import pandas as pd

from app.services.august_cohort import (
    build_cohort_comparison,
    comparison_summary,
    export_filename,
    filter_comparison,
    list_cohort_csvs,
    parse_cohort_csv,
    project_root,
    registration_span,
)

_SAMPLE = (
    "Group,Merchant,Login ID,Member ID,Status,Date Created,Last Login Time\n"
    '="RP1M (G109)",="RP1M (161)",="Alpha",="100@161",="Active",="08/31/2026 22:33:41",="08/31/2026 22:33:41"\n'
    '="RP1M (G109)",="RP1M (161)",="Beta",="200@161",="Active",="08/02/2026 10:00:00",="09/20/2026 08:00:00"\n'
    '="RP1M (G109)",="RP1M (161)",="Gamma",="300@161",="Active",="08/15/2026 12:00:00",="08/15/2026 12:05:00"\n'
).encode()


def test_parse_cohort_csv_strips_excel_wrappers():
    frame = parse_cohort_csv(_SAMPLE)
    assert list(frame["login_id"]) == ["Alpha", "Gamma", "Beta"]
    assert frame.loc[frame["login_id"] == "Alpha", "member_id"].iloc[0] == "100@161"
    registered = frame.loc[frame["login_id"] == "Alpha", "registered_at"].iloc[0]
    assert registered == pd.Timestamp("2026-08-31 22:33:41")


def test_parse_cohort_csv_keeps_newest_duplicate_member():
    content = (
        "Login ID,Member ID,Date Created,Last Login Time\n"
        "OldLogin,100@161,08/01/2026 01:00:00,08/01/2026 01:00:00\n"
        "NewLogin,100@161,08/20/2026 01:00:00,08/21/2026 01:00:00\n"
    ).encode()
    frame = parse_cohort_csv(content)
    assert len(frame) == 1
    assert frame.iloc[0]["login_id"] == "NewLogin"


def test_comparison_flags_first_deposit_and_inactivity():
    cohort = parse_cohort_csv(_SAMPLE)
    members = pd.DataFrame(
        [
            {
                "member_id": "100@161",
                "login_id": "Alpha",
                "status": "Active",
                "last_login_at": pd.Timestamp("2026-08-31 22:33:41"),
            },
            {
                "member_id": "200@161",
                "login_id": "Beta",
                "status": "Active",
                "last_login_at": pd.Timestamp("2026-09-01 08:00:00"),
            },
        ]
    )
    deposits = pd.DataFrame(
        [
            {
                "member_id": "200@161",
                "login_id": "Beta",
                "amount": 50,
                "txn_datetime_local": pd.Timestamp("2026-09-02 09:00:00"),
            },
            {
                "member_id": "200@161",
                "login_id": "Beta",
                "amount": 20,
                "txn_datetime_local": pd.Timestamp("2026-08-10 09:00:00"),
            },
            {
                "member_id": "300@161",
                "login_id": "Gamma",
                "amount": 75,
                "txn_datetime_local": pd.Timestamp("2026-08-16 09:00:00"),
            },
        ]
    )

    compared = build_cohort_comparison(
        cohort,
        members,
        deposits,
        as_of=date(2026, 9, 23),
        inactive_days=14,
    )
    by_login = compared.set_index("login_id")

    alpha = by_login.loc["Alpha"]
    assert bool(alpha["in_database"]) is True
    assert bool(alpha["has_first_deposit"]) is False
    assert int(alpha["days_since_login"]) == 23
    assert bool(alpha["inactive"]) is True

    beta = by_login.loc["Beta"]
    assert bool(beta["has_first_deposit"]) is True
    assert float(beta["first_deposit_amount"]) == 20
    assert beta["first_deposit_at"] == pd.Timestamp("2026-08-10 09:00:00")
    assert int(beta["approved_deposit_count"]) == 2
    assert beta["last_login_source"] == "Cohort file"
    assert int(beta["days_since_login"]) == 3
    assert bool(beta["inactive"]) is False

    gamma = by_login.loc["Gamma"]
    assert bool(gamma["in_database"]) is False
    assert bool(gamma["has_first_deposit"]) is True
    assert gamma["last_login_source"] == "Cohort file"

    summary = comparison_summary(compared)
    assert summary["players"] == 3
    assert summary["in_database"] == 2
    assert summary["not_in_database"] == 1
    assert summary["first_deposit"] == 2
    assert summary["no_first_deposit"] == 1
    assert summary["inactive"] == 2

    inactive = filter_comparison(compared, segment="inactive", search="")
    assert set(inactive["login_id"]) == {"Alpha", "Gamma"}
    no_deposit = filter_comparison(compared, segment="no_first_deposit", search="alp")
    assert list(no_deposit["login_id"]) == ["Alpha"]


def test_list_cohort_csvs_keeps_member_exports_only(tmp_path):
    (tmp_path / "RP_JULY_2026.csv").write_text(
        "Login ID,Member ID,Date Created\n",
        encoding="utf-8",
    )
    (tmp_path / "transactions.csv").write_text("Ticket #,Amount\n", encoding="utf-8")
    found = list_cohort_csvs(tmp_path)
    assert [path.name for path in found] == ["RP_JULY_2026.csv"]


def test_export_filename_uses_source_month():
    assert export_filename("RP_SEPTEMBER_2026.csv", "inactive") == "rp_september_2026_inactive.csv"
    assert export_filename("RP_AUGUST_2026.csv", "no_first_deposit") == "rp_august_2026_no_first_deposit.csv"


def test_registration_span_covers_the_file_range():
    frame = parse_cohort_csv(_SAMPLE)
    assert registration_span(frame) == "02 Aug 2026 – 31 Aug 2026"


def test_real_member_exports_parse_when_present():
    files = list_cohort_csvs(project_root())
    if not files:
        return
    for path in files:
        frame = parse_cohort_csv(path.read_bytes())
        assert not frame.empty
        assert frame["member_id"].dropna().is_unique
