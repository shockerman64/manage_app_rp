CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS import_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL CHECK (source_type IN ('internal', 'vendor', 'members', 'member_pnl')),
    original_filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    period_start TIMESTAMPTZ NULL,
    period_end TIMESTAMPTZ NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, file_hash)
);

CREATE TABLE IF NOT EXISTS internal_transactions_raw (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    group_name TEXT NULL,
    merchant TEXT NULL,
    login_id TEXT NULL,
    member_id TEXT NULL,
    txn_datetime_raw TEXT NULL,
    txn_type TEXT NULL,
    pay_method TEXT NULL,
    currency TEXT NULL,
    amount NUMERIC(18, 2) NULL,
    fee NUMERIC(18, 2) NULL,
    ticket_no TEXT NULL,
    status TEXT NULL,
    approval_remark TEXT NULL,
    raw_data JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_internal_raw_batch ON internal_transactions_raw(batch_id);
CREATE INDEX IF NOT EXISTS idx_internal_raw_ticket ON internal_transactions_raw(ticket_no);
CREATE INDEX IF NOT EXISTS idx_internal_raw_pay_method ON internal_transactions_raw(pay_method);

CREATE TABLE IF NOT EXISTS vendor_transactions_raw (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    client_name TEXT NULL,
    txn_time_raw TEXT NULL,
    correlation_id TEXT NULL,
    amount NUMERIC(18, 2) NULL,
    currency TEXT NULL,
    fee_amount NUMERIC(18, 2) NULL,
    status TEXT NULL,
    payment_type TEXT NULL,
    merchant_name TEXT NULL,
    username TEXT NULL,
    raw_data JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vendor_raw_batch ON vendor_transactions_raw(batch_id);
CREATE INDEX IF NOT EXISTS idx_vendor_raw_corr ON vendor_transactions_raw(correlation_id);

CREATE TABLE IF NOT EXISTS transactions_normalized (
    id BIGSERIAL PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (source_system IN ('internal', 'vendor')),
    source_row_id BIGINT NULL,
    batch_id UUID NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    reference_id TEXT NOT NULL,
    ticket_no TEXT NULL,
    correlation_id TEXT NULL,
    merchant TEXT NULL,
    group_name TEXT NULL,
    login_id TEXT NULL,
    member_id TEXT NULL,
    txn_type TEXT NULL,
    pay_method TEXT NULL,
    currency TEXT NULL,
    amount NUMERIC(18, 2) NOT NULL,
    fee NUMERIC(18, 2) NULL,
    status TEXT NULL,
    txn_datetime_local TIMESTAMPTZ NULL,
    txn_datetime_vendor TIMESTAMPTZ NULL,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_norm_batch ON transactions_normalized(batch_id);
CREATE INDEX IF NOT EXISTS idx_norm_lookup_ticket ON transactions_normalized(ticket_no);
CREATE INDEX IF NOT EXISTS idx_norm_lookup_corr ON transactions_normalized(correlation_id);
CREATE INDEX IF NOT EXISTS idx_norm_query_datetime ON transactions_normalized(txn_datetime_local);
CREATE INDEX IF NOT EXISTS idx_norm_query_type ON transactions_normalized(txn_type);
CREATE INDEX IF NOT EXISTS idx_norm_query_status ON transactions_normalized(status);
CREATE INDEX IF NOT EXISTS idx_norm_query_pay_method ON transactions_normalized(pay_method);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    internal_batch_id UUID NULL REFERENCES import_batches(id),
    vendor_batch_id UUID NULL REFERENCES import_batches(id),
    notes TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reconciliation_results (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES reconciliation_runs(id) ON DELETE CASCADE,
    ticket_no TEXT NULL,
    correlation_id TEXT NULL,
    result_status TEXT NOT NULL CHECK (
        result_status IN (
            'matched',
            'internal_only',
            'vendor_only',
            'amount_mismatch',
            'status_mismatch',
            'time_mismatch'
        )
    ),
    internal_amount NUMERIC(18, 2) NULL,
    vendor_amount NUMERIC(18, 2) NULL,
    internal_status TEXT NULL,
    vendor_status TEXT NULL,
    internal_txn_datetime_vendor_tz TIMESTAMPTZ NULL,
    vendor_txn_datetime TIMESTAMPTZ NULL,
    delta_seconds INTEGER NULL,
    reason TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recon_run ON reconciliation_results(run_id);
CREATE INDEX IF NOT EXISTS idx_recon_status ON reconciliation_results(result_status);

CREATE TABLE IF NOT EXISTS members (
    id BIGSERIAL PRIMARY KEY,
    member_id TEXT NOT NULL UNIQUE,
    login_id TEXT NULL,
    created_date DATE NOT NULL,
    group_name TEXT NULL,
    merchant TEXT NULL,
    member_group TEXT NULL,
    currency TEXT NULL,
    verify_status TEXT NULL,
    status TEXT NULL,
    last_update_at TIMESTAMPTZ NULL,
    last_login_at TIMESTAMPTZ NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    batch_id UUID NULL REFERENCES import_batches(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_members_login_id ON members(login_id);
CREATE INDEX IF NOT EXISTS idx_members_created_date ON members(created_date);

CREATE TABLE IF NOT EXISTS member_pnl_daily (
    id BIGSERIAL PRIMARY KEY,
    report_date DATE NOT NULL,
    member_id TEXT NOT NULL,
    login_id TEXT NULL,
    group_name TEXT NULL,
    merchant TEXT NULL,
    member_group TEXT NULL,
    currency TEXT NULL,
    transfer_in_count INTEGER NULL,
    transfer_in_amt NUMERIC(18, 4) NULL,
    transfer_out_count INTEGER NULL,
    transfer_out_amt NUMERIC(18, 4) NULL,
    adjustment_count INTEGER NULL,
    adjustment_amt NUMERIC(18, 4) NULL,
    stakes_count INTEGER NULL,
    stake_amt NUMERIC(18, 4) NULL,
    valid_stake_amt NUMERIC(18, 4) NULL,
    gain_loss NUMERIC(18, 4) NULL,
    comm NUMERIC(18, 4) NULL,
    bonus_amt NUMERIC(18, 4) NULL,
    stakes_adj_count INTEGER NULL,
    stakes_adj_amt NUMERIC(18, 4) NULL,
    total_gl NUMERIC(18, 4) NULL,
    prize NUMERIC(18, 4) NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    batch_id UUID NULL REFERENCES import_batches(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_date, member_id)
);

CREATE INDEX IF NOT EXISTS idx_member_pnl_daily_report_date ON member_pnl_daily(report_date);
CREATE INDEX IF NOT EXISTS idx_member_pnl_daily_login_id ON member_pnl_daily(login_id);
CREATE INDEX IF NOT EXISTS idx_member_pnl_daily_total_gl ON member_pnl_daily(total_gl);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Expand source_type check on databases created before members / member P&L imports.
ALTER TABLE import_batches DROP CONSTRAINT IF EXISTS import_batches_source_type_check;
ALTER TABLE import_batches ADD CONSTRAINT import_batches_source_type_check
    CHECK (source_type IN ('internal', 'vendor', 'members', 'member_pnl'));
