-- Sample (fictional) data for the demo. No real participants.
INSERT INTO equity_grants (grant_id, participant_id, award_type, grant_date, vesting_start_date,
    shares_granted, strike_price, fair_market_value_at_grant, vesting_months, cliff_months,
    vesting_frequency_months, day_count, expiration_date)
VALUES
 ('G-RSU-1001', 'P-0001', 'RSU', '2025-01-15', '2025-01-15', 4800.0000, 0.0000, 42.5000, 48, 12, 3, 'ACTUAL/365', NULL),
 ('G-NSO-2001', 'P-0002', 'NSO', '2024-06-01', '2024-06-01', 10000.0000, 12.0000, 12.0000, 48, 12, 1, 'ACTUAL/365', '2034-06-01'),
 ('G-ISO-3001', 'P-0003', 'ISO', '2023-03-01', '2023-03-01', 6000.0000, 8.0000, 8.0000, 48, 12, 1, '30/360', '2033-03-01');

INSERT INTO equity_transactions (transaction_id, grant_id, participant_id, type, event_date,
    shares_impacted, fmv_at_event, gross_amount, tax_withheld_amount, net_shares_issued, state_code)
VALUES
 ('T-5001', 'G-RSU-1001', 'P-0001', 'RELEASE', '2026-01-15 16:00:00-06', 1200.0000, 55.2500, 66300.00, 26440.44, 721.0000, 'CA'),
 ('T-5002', 'G-NSO-2001', 'P-0002', 'EXERCISE', '2026-03-10 16:00:00-06', 2500.0000, 30.0000, 75000.00, 13342.50, 2500.0000, 'TX');

INSERT INTO trading_plans (plan_id, participant_id, insider_role, adoption_date,
    cooling_off_end_date, plan_expiration_date, is_single_trade_plan, status)
VALUES
 ('PL-0001', 'P-0001', 'DIRECTOR_OR_OFFICER', '2026-03-02', '2026-05-31', '2027-03-02', FALSE, 'ELIGIBLE');
