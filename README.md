# RP Dashboard (Streamlit + Supabase Postgres)

Internal tool for manual daily transaction imports, persistent metrics, transaction browsing, and QRIS reconciliation across **RP1M** (internal) and **OASIS PAY** (vendor gateway) sources.

> Note on naming: throughout the UI we use the brand labels **RP1M** and **OASIS PAY**.
> Internally (database values, function names, SQL literals), the original `internal` / `vendor` identifiers are preserved to avoid migration churn.

## Features

- Manual **RP1M** CSV import with dedupe by file hash
- Manual **OASIS PAY** XLSX/CSV import with dedupe by file hash
- Persistent Postgres storage (raw + normalized + reconciliation tables)
- **Metrics** page: KPIs (deposit, withdraw, net flow, approval rate), date presets, daily trend chart, breakdowns by pay method / status / merchant
- **View Transactions** page: unified browser for every RP1M + OASIS PAY row with rich filters (date presets, source, type, status, pay method, amount range, free-text search), KPI strip, daily volume chart, paginated table, and full-CSV export
- **Reconciliation** page:
  - Internal scope: `Pay Method = QRIS IM`
  - Match key: RP1M `Ticket #` vs OASIS PAY `correlation_id`
  - Time normalization: RP1M is treated as vendor timezone +1 hour
  - Run Health indicator, matched-vs-mismatched donut, daily summary, import diagnostics

## Pages / Navigation

1. **Home** (`app/main.py`) - upload RP1M and OASIS PAY files via tabs, import history
2. **Metrics** (`app/pages/01_metrics.py`) - RP1M KPIs and trends
3. **View Transactions** (`app/pages/02_view_transactions.py`) - browse all RP1M + OASIS PAY rows
4. **Reconciliation** (`app/pages/03_reconciliation.py`) - run / inspect reconciliation

The DB init button now lives in the sidebar's collapsed **Admin** expander; the sidebar also shows a live "Database connected / unavailable" status pill.

## Setup

1. Create and activate virtual env.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create `.env` from `.env.example` and set `DATABASE_URL`.
4. Run app:

   ```bash
   streamlit run app/main.py
   ```

## Typical Workflow

1. Open the sidebar **Admin** expander and click **Initialize / Migrate DB**
2. Open the **RP1M** upload tab and import an internal CSV
3. Open the **OASIS PAY** upload tab and import a vendor XLSX/CSV
4. Open the **Metrics** page for KPI dashboards
5. Open **View Transactions** to filter / search / export across both sources
6. Open **Reconciliation** and click **Run Reconciliation** to compare QRIS rows

## Database Compatibility Note

- The app uses `psycopg` via SQLAlchemy and explicitly disables automatic prepared statements (`prepare_threshold=None`) in `app/db.py`.
- This avoids `DuplicatePreparedStatement` errors when running through transaction poolers such as Supabase pooler/pgBouncer.

## Troubleshooting Imports

- If you see `(psycopg.errors.DuplicatePreparedStatement) ... already exists`, confirm your runtime is using the latest `app/db.py` configuration and restart the Streamlit process.

## Schema

Schema migration SQL is in `sql/schema.sql`.

The schema retains `source_system IN ('internal', 'vendor')` and the original raw-table names (`internal_transactions_raw`, `vendor_transactions_raw`). The brand labels **RP1M** / **OASIS PAY** are applied only at the display layer via `app/ui.py` (`SOURCE_LABELS`, `RESULT_STATUS_LABELS`, `relabel_source_column`, etc.).
