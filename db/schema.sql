-- Equity Compensation Management Agent -- PostgreSQL schema
-- Based on the original spec, with these changes (see README "Spec corrections"):
--   * 'SALE' and 'CANCELLATION' transaction types, needed for 1099-B and lifecycle tracking
--   * 'PARTIALLY_EXERCISED' grant status
--   * vesting_terms columns (months, cliff, frequency, day-count convention)
--   * state_code on transactions; additional_medicare_tax on withholding records
--   * CHECK constraints, unique vest dates per grant, updated_at trigger

CREATE TYPE equity_type AS ENUM ('ISO', 'NSO', 'RSU', 'ESPP');
CREATE TYPE grant_status AS ENUM ('ACTIVE', 'FULLY_VESTED', 'PARTIALLY_EXERCISED', 'CANCELLED', 'EXERCISED');
CREATE TYPE transaction_type AS ENUM ('GRANT', 'VEST', 'EXERCISE', 'RELEASE', 'TAX_WITHHOLDING', 'SALE', 'CANCELLATION');
CREATE TYPE day_count_convention AS ENUM ('30/360', 'ACTUAL/365');

CREATE TABLE equity_grants (
    grant_id                   VARCHAR(64) PRIMARY KEY,
    participant_id             VARCHAR(64) NOT NULL,
    award_type                 equity_type NOT NULL,
    grant_date                 DATE NOT NULL,
    vesting_start_date         DATE NOT NULL,
    shares_granted             NUMERIC(18,4) NOT NULL CHECK (shares_granted > 0),
    strike_price               NUMERIC(12,4) NOT NULL DEFAULT 0.0000 CHECK (strike_price >= 0),
    fair_market_value_at_grant NUMERIC(12,4) NOT NULL CHECK (fair_market_value_at_grant >= 0),
    vesting_months             INTEGER NOT NULL DEFAULT 48 CHECK (vesting_months > 0),
    cliff_months               INTEGER NOT NULL DEFAULT 12 CHECK (cliff_months >= 0),
    vesting_frequency_months   INTEGER NOT NULL DEFAULT 1 CHECK (vesting_frequency_months > 0),
    day_count                  day_count_convention NOT NULL DEFAULT 'ACTUAL/365',
    expiration_date            DATE,
    status                     grant_status NOT NULL DEFAULT 'ACTIVE',
    created_at                 TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at                 TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CHECK (vesting_start_date >= grant_date - INTERVAL '1 year'),
    -- RSUs have no strike; ISOs must be granted at or above FMV (IRC 422)
    CHECK (award_type <> 'RSU' OR strike_price = 0),
    CHECK (award_type <> 'ISO' OR strike_price >= fair_market_value_at_grant)
);

CREATE TABLE vesting_schedules (
    schedule_id    VARCHAR(64) PRIMARY KEY,
    grant_id       VARCHAR(64) NOT NULL REFERENCES equity_grants(grant_id) ON DELETE CASCADE,
    vest_date      DATE NOT NULL,
    shares_vesting NUMERIC(18,4) NOT NULL CHECK (shares_vesting > 0),
    is_processed   BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at   TIMESTAMPTZ,
    UNIQUE (grant_id, vest_date),
    CHECK (is_processed = (processed_at IS NOT NULL))
);

CREATE TABLE equity_transactions (
    transaction_id      VARCHAR(64) PRIMARY KEY,
    grant_id            VARCHAR(64) NOT NULL REFERENCES equity_grants(grant_id),
    participant_id      VARCHAR(64) NOT NULL,
    type                transaction_type NOT NULL,
    event_date          TIMESTAMPTZ NOT NULL,
    shares_impacted     NUMERIC(18,4) NOT NULL,
    fmv_at_event        NUMERIC(12,4) NOT NULL,
    gross_amount        NUMERIC(14,2) NOT NULL,
    tax_withheld_amount NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    net_shares_issued   NUMERIC(18,4) NOT NULL,
    state_code          CHAR(2),
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CHECK (net_shares_issued <= shares_impacted)
);

CREATE TABLE tax_withholding_records (
    record_id               VARCHAR(64) PRIMARY KEY,
    transaction_id          VARCHAR(64) NOT NULL REFERENCES equity_transactions(transaction_id),
    federal_tax             NUMERIC(12,2) NOT NULL,
    state_tax               NUMERIC(12,2) NOT NULL,
    social_security_tax     NUMERIC(12,2) NOT NULL,
    medicare_tax            NUMERIC(12,2) NOT NULL,
    additional_medicare_tax NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    local_tax               NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    total_tax               NUMERIC(14,2) NOT NULL,
    CHECK (total_tax = federal_tax + state_tax + social_security_tax + medicare_tax
                       + additional_medicare_tax + local_tax)
);

CREATE INDEX idx_grants_participant ON equity_grants(participant_id);
CREATE INDEX idx_tx_grant          ON equity_transactions(grant_id);
CREATE INDEX idx_tx_participant    ON equity_transactions(participant_id, event_date);
CREATE INDEX idx_vest_date         ON vesting_schedules(vest_date, is_processed);

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_grants_updated BEFORE UPDATE ON equity_grants
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Rule 10b5-1 trading plans (added with the trading-plan module)
CREATE TYPE insider_role AS ENUM ('DIRECTOR_OR_OFFICER', 'OTHER_PERSON');

CREATE TABLE trading_plans (
    plan_id               VARCHAR(64) PRIMARY KEY,
    participant_id        VARCHAR(64) NOT NULL,
    insider_role          insider_role NOT NULL,
    adoption_date         DATE NOT NULL,
    cooling_off_end_date  DATE NOT NULL,
    plan_expiration_date  DATE,
    is_single_trade_plan  BOOLEAN NOT NULL DEFAULT FALSE,
    status                VARCHAR(16) NOT NULL DEFAULT 'ELIGIBLE',
    terminated            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CHECK (plan_expiration_date IS NULL OR plan_expiration_date > adoption_date),
    CHECK (cooling_off_end_date >= adoption_date)
);

CREATE INDEX idx_plans_participant ON trading_plans(participant_id);
