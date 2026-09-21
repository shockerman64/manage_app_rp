# RP Dashboard (Streamlit + Supabase Postgres)

Internal tool for manual daily transaction imports, persistent metrics, transaction browsing, and QRIS reconciliation across **RP1M** (internal) and **OASIS PAY** (vendor gateway) sources.

> Note on naming: throughout the UI we use the brand labels **RP1M** and **OASIS PAY**.
> Internally (database values, function names, SQL literals), the original `internal` / `vendor` identifiers are preserved to avoid migration churn.

## Features

- Manual **RP1M** CSV import with dedupe by file hash
- Manual **Members** CSV import (member registry for FTD) with upsert by Member ID
- Manual **Member P&L** CSV import (daily `4.3_P_&_L_By_Member` snapshots) with upsert by report date + Member ID
- Manual **OASIS PAY** XLSX/CSV import with dedupe by file hash
- Persistent Postgres storage (raw + normalized + members + member P&L + reconciliation tables)
- **RP1M Overview** page: Approved deposit/withdraw totals, member-aware **first-time deposit (FTD)** metrics, date presets, daily breakdown table, CSV export
- **Player Winnings** page: daily member P&L KPIs (winnings, losses, net G/L), top winners/losers, daily trend, browse table, CSV export
- **Metrics** page: KPIs (deposit, withdraw, net flow, approval rate), date presets, daily trend chart, breakdowns by pay method / status / merchant
- **View Transactions** page: unified browser for every RP1M + OASIS PAY row with rich filters (date presets, source, type, status, pay method, amount range, free-text search), KPI strip, daily volume chart, paginated table, and full-CSV export
- **Reconciliation** page:
  - Internal scope: `Pay Method = QRIS IM`
  - Match key: RP1M `Ticket #` vs OASIS PAY `correlation_id`
  - Time normalization: RP1M is treated as vendor timezone +1 hour
  - Run Health indicator, matched-vs-mismatched donut, daily summary, import diagnostics

## Pages / Navigation

1. **Home** (`app/main.py`) - upload RP1M transactions, Members registry, Member P&L, and OASIS PAY files via tabs; import history
2. **RP1M Overview** (`app/pages/04_rp1m_overview.py`) - Approved deposit/withdraw and FTD KPIs with daily breakdown
3. **Player Winnings** (`app/pages/05_player_winnings.py`) - daily member P&L winnings/losses from P&L By Member CSVs
4. **Metrics** (`app/pages/01_metrics.py`) - RP1M KPIs and trends (all statuses)
5. **View Transactions** (`app/pages/02_view_transactions.py`) - browse all RP1M + OASIS PAY rows
6. **Reconciliation** (`app/pages/03_reconciliation.py`) - run / inspect reconciliation

The DB init button now lives in the sidebar's collapsed **Admin** expander; the sidebar also shows a live "Database connected / unavailable" status pill.

## Setup

1. Create and activate virtual env.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create `.env` from `.env.example` and set `DATABASE_URL` (and optionally `FTD_CUTOFF_MONTH`, default `2026-04`).
4. Run app:

   ```bash
   streamlit run app/main.py
   ```

## Typical Workflow

1. Open the sidebar **Admin** expander and click **Initialize / Migrate DB**
2. Open the **RP1M** upload tab and import an internal CSV
3. Open the **Upload Members** tab and import the member registry CSV (required for FTD on RP1M Overview)
4. Open the **Upload Member P&L** tab and import a daily `4.3_P_&_L_By_Member_YYYYMMDD_YYYYMMDD_ALL.csv`
5. Open the **OASIS PAY** upload tab and import a vendor XLSX/CSV
6. Open **RP1M Overview** for Approved deposit/withdraw and FTD metrics
7. Open **Player Winnings** for daily member P&L (winnings, losses, top players)
8. Open the **Metrics** page for broader RP1M KPI dashboards
9. Open **View Transactions** to filter / search / export across both sources
10. Open **Reconciliation** and click **Run Reconciliation** to compare QRIS rows

### Members CSV (FTD)

Required columns: `Member ID`, `Login ID`, `Date Created`.

Optional mapped columns include `Group`, `Merchant`, `Member Group`, `Currency`, `Verify Status`, `Status`, `Last Update`, `Last Login Time`. All other export columns are stored in `raw_data`.

### Member P&L CSV (Player Winnings)

Daily `4.3_P_&_L_By_Member_YYYYMMDD_YYYYMMDD_ALL.csv` exports have no date column; the report date is parsed from the filename. Re-importing the same day upserts by `(report_date, Member ID)`.

Required columns: `Member ID`, `Login ID`, `Stakes-Total G/L`.

Headline winnings use **Stakes-Total G/L** (player perspective: positive = player won). **Stakes-Gain/Loss** is shown as pure betting result (excludes comm and bonus).

### FTD cutoff month

Set `FTD_CUTOFF_MONTH=YYYY-MM` in `.env` (first day of that month is used in SQL). Members registered **before** that month treat their first **Approved** deposit on or after the cutoff as FTD. Members registered **on or after** the cutoff use their earliest **Approved** deposit in loaded transaction data.

RP1M transaction history before the cutoff month is not in the database; FTD is a business rule aligned to partial imports, not lifetime platform truth.

## Database Compatibility Note

- The app uses `psycopg` via SQLAlchemy and explicitly disables automatic prepared statements (`prepare_threshold=None`) in `app/db.py`.
- This avoids `DuplicatePreparedStatement` errors when running through transaction poolers such as Supabase pooler/pgBouncer.

## Troubleshooting Imports

- If you see `(psycopg.errors.DuplicatePreparedStatement) ... already exists`, confirm your runtime is using the latest `app/db.py` configuration and restart the Streamlit process.

## Schema

Schema migration SQL is in `sql/schema.sql`.

The schema retains `source_system IN ('internal', 'vendor')` for transactions and adds a `members` table, a `member_pnl_daily` table, plus `import_batches.source_type` values `'members'` and `'member_pnl'`. The brand labels **RP1M** / **OASIS PAY** / **Members** / **Member P&L** are applied at the display layer via `app/ui.py` (`SOURCE_LABELS`, etc.).
