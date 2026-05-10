# RP Dashboard (Streamlit + Supabase Postgres)

Internal tool for manual daily transaction imports, persistent metrics, and QRIS reconciliation.

## Features

- Manual internal CSV import with dedupe by file hash
- Manual vendor XLSX/CSV import with dedupe by file hash
- Persistent Postgres storage (raw + normalized + reconciliation)
- KPI metrics page (deposit, withdraw, net flow, approval rate)
- Reconciliation page:
  - Internal scope: `Pay Method = QRIS IM`
  - Match key: internal `Ticket #` vs vendor `correlation_id`
  - Time normalization: internal is treated as vendor timezone +1 hour

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

1. Click **Initialize / Migrate DB**
2. Import internal CSV
3. Import vendor XLSX/CSV
4. Open **Metrics** page for dashboard views
5. Open **Reconciliation** page and run reconciliation

## Database Compatibility Note

- The app uses `psycopg` via SQLAlchemy and explicitly disables automatic prepared statements (`prepare_threshold=None`) in `app/db.py`.
- This avoids `DuplicatePreparedStatement` errors when running through transaction poolers such as Supabase pooler/pgBouncer.

## Troubleshooting Imports

- If you see `(psycopg.errors.DuplicatePreparedStatement) ... already exists`, confirm your runtime is using the latest `app/db.py` configuration and restart the Streamlit process.

## Schema

Schema migration SQL is in `sql/schema.sql`.
