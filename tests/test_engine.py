"""Expected values are hand-calculated (shown in comments), not copied from engine output."""
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.engine import reconcile, reporting, tax, trading_plan, vesting
from app.engine.vesting import days_30_360
from app.engine.trading_plan import add_business_days, _months_before
from app.main import app
from app.models import (BlackoutWindow, Form1099BRequest, Form3921Request, PlanAdoptionRequest,
                        ReconciliationRequest, SaleLot, ScheduledTrade, TaxCalculationRequest,
                        TradeCheckRequest, VestingRequest)

client = TestClient(app)


def calc(**kw):
    base = dict(participant_id="P-1", state_code="TX")
    base.update(kw)
    return tax.calculate(TaxCalculationRequest(**base))


def test_rsu_basic_ca():
    r = calc(award_type="RSU", shares=D("1200"), market_price=D("55.25"), state_code="CA")
    assert r.gross_value == D("66300.00")          # 1200 * 55.25
    assert r.federal_withholding == D("14586.00")   # 66300 * 22%
    assert r.state_withholding == D("6782.49")      # 66300 * 10.23%
    assert r.social_security_withholding == D("4110.60")  # 66300 * 6.2%
    assert r.medicare_withholding == D("961.35")    # 66300 * 1.45%
    assert r.total_withholding == D("26440.44")
    assert r.shares_to_cover_taxes == D("478.5600")  # 26440.44 / 55.25
    assert r.whole_shares_to_cover == 479
    assert r.net_value == D("39859.56")
    assert "not constitute tax" in r.disclaimer


def test_crosses_1m_and_wage_base_and_addl_medicare():
    # 200,000 taxable; 900k YTD supplemental -> 100k @22% + 100k @37% = 22,000 + 37,000
    r = calc(award_type="RSU", shares=D("2000"), market_price=D("100"),
             ytd_supplemental_income=D("900000"), ytd_fica_wages=D("150000"))
    assert r.federal_withholding == D("59000.00")
    assert r.federal_rate_breakdown == {"wages_at_22pct": D("100000.00"), "wages_at_37pct": D("100000.00")}
    assert r.social_security_withholding == D("2139.00")      # (184,500 - 150,000) * 6.2%
    assert r.medicare_withholding == D("2900.00")             # 200,000 * 1.45%
    assert r.additional_medicare_withholding == D("1350.00")  # (350,000 - 200,000) * 0.9%


def test_all_above_1m_uses_37():
    r = calc(award_type="RSU", shares=D("10"), market_price=D("100"), ytd_supplemental_income=D("1500000"),
             ytd_fica_wages=D("1500000"))
    assert r.federal_withholding == D("370.00")
    assert r.social_security_withholding == D("0.00")
    assert r.additional_medicare_withholding == D("9.00")


def test_nso_spread_only():
    r = calc(award_type="NSO", shares=D("2500"), market_price=D("30"), strike_price=D("12"))
    assert r.exercise_cost == D("30000.00")
    assert r.taxable_gain == D("45000.00")           # 2500 * (30 - 12)
    assert r.federal_withholding == D("9900.00")     # 45000 * 22%
    assert r.fica_withholding == D("3442.50")        # 2790.00 + 652.50
    assert r.total_withholding == D("13342.50")
    assert r.net_value == D("31657.50")              # 75000 - 30000 - 13342.50


def test_underwater_nso():
    r = calc(award_type="NSO", shares=D("100"), market_price=D("5"), strike_price=D("10"))
    assert r.taxable_gain == D("0.00") and r.total_withholding == D("0.00")


def test_iso_no_withholding_amt_note():
    r = calc(award_type="ISO", shares=D("1000"), market_price=D("20"), strike_price=D("8"), state_code="CA")
    assert r.total_withholding == D("0.00")
    assert any("AMT preference" in n and "12000.00" in n for n in r.notes)


def test_unknown_state_and_override():
    assert any("No supplemental rate" in n for n in calc(award_type="RSU", shares=D("1"), market_price=D("100"),
                                                         state_code="OH").notes)
    r = calc(award_type="RSU", shares=D("1"), market_price=D("100"), state_code="OH", state_rate_override=D("0.035"))
    assert r.state_withholding == D("3.50")


def test_vesting_quarterly_with_cliff():
    s = vesting.build_schedule(VestingRequest(grant_id="G", total_units=D("4800"), vesting_start_date=date(2025, 1, 15),
                                              vesting_months=48, cliff_months=12, frequency_months=3,
                                              as_of=date(2026, 10, 8)))
    assert s.schedule[0].vest_date == date(2026, 1, 15) and s.schedule[0].shares_vesting == D("1200.0000")
    assert len(s.schedule) == 13 and all(t.shares_vesting == D("300.0000") for t in s.schedule[1:])
    assert sum(t.shares_vesting for t in s.schedule) == D("4800")
    assert s.vested_as_of == D("1800.0000")  # cliff 1200 + Apr 15 + Jul 15


def test_vesting_uneven_sums_exactly():
    s = vesting.build_schedule(VestingRequest(grant_id="G", total_units=D("1000"), vesting_start_date=date(2024, 1, 31)))
    assert s.schedule[0].shares_vesting == D("250.0000")   # floor(1000 * 12/48)
    assert sum(t.shares_vesting for t in s.schedule) == D("1000")
    assert s.schedule[1].vest_date == date(2025, 2, 28)    # month-end clamp
    frac = vesting.build_schedule(VestingRequest(grant_id="G", total_units=D("1000"), vesting_start_date=date(2024, 1, 31),
                                                 whole_shares=False)).schedule
    assert all(t.shares_vesting.as_tuple().exponent == -4 for t in frac)


def test_day_counts():
    assert days_30_360(date(2023, 3, 1), date(2024, 3, 1)) == 360
    assert days_30_360(date(2024, 1, 31), date(2024, 3, 31)) == 60
    s = vesting.build_schedule(VestingRequest(grant_id="G", total_units=D("100"), vesting_start_date=date(2023, 3, 1),
                                              as_of=date(2025, 3, 1), day_count="30/360"))
    assert s.elapsed_fraction == D("0.500000")


def test_reconcile():
    cap = [{"grant_id": "G1", "event_date": "2026-01-15", "shares_impacted": "1200", "gross_amount": "66300.00",
            "tax_withheld_amount": "26440.44"},
           {"grant_id": "G2", "event_date": "2026-03-10", "shares_impacted": "2500", "gross_amount": "75000.00"}]
    pay = [{"grant_id": "G1", "event_date": "2026-01-15T22:00:00Z", "shares_impacted": 1200,
            "gross_amount": 66300.00, "tax_withheld_amount": 26440.40},
           {"grant_id": "G9", "event_date": "2026-02-01", "shares_impacted": 10}]
    r = reconcile.reconcile(ReconciliationRequest(period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
                                                  cap_table_records=cap, payroll_records=pay))
    kinds = sorted(d.field_mismatch for d in r.discrepancy_details)
    assert r.status == "DISCREPANCY_DETECTED"
    assert kinds == ["MISSING_IN_CAP_TABLE", "MISSING_IN_PAYROLL", "tax_withheld_amount"]
    assert [d.difference for d in r.discrepancy_details if d.field_mismatch == "tax_withheld_amount"] == ["-0.04"]
    ok = reconcile.reconcile(ReconciliationRequest(period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
                                                   cap_table_records=cap[:1], payroll_records=[cap[0]]))
    assert ok.status == "BALANCED"


def test_1099b_rsu_basis_adjustment():
    r = reporting.form_1099b(Form1099BRequest(recipient_name="P-0001", tax_year=2026, lots=[SaleLot(
        description="100 sh DEMO", award_type="RSU", shares=D("100"), date_acquired=date(2025, 1, 15),
        date_sold=date(2026, 3, 2), proceeds=D("6000"), broker_reported_basis=D("0"),
        ordinary_income_recognized=D("4250"))]))
    lot = r["lots"][0]
    assert lot["2_term"] == "LONG"
    assert lot["form_8949"] == {"box": "E", "adjustment_code": "B", "basis_adjustment": "4250.00",
                                "adjusted_basis": "4250.00", "gain_or_loss": "1750.00"}


def test_form_3921():
    f = reporting.form_3921(Form3921Request(employee_name="P-0003", employee_tin_last4="1234",
        grant_date=date(2023, 3, 1), exercise_date=date(2026, 2, 10), exercise_price_per_share=D("8"),
        fmv_per_share_on_exercise=D("20"), shares_transferred=D("1000")))
    assert f["derived"]["amt_preference_bargain_element"] == "12000.00"
    assert f["derived"]["qualifying_disposition_earliest_date"] == "2027-02-10"
    assert f["employee"]["tin_masked"] == "***-**-1234"


def test_api_endpoints():
    r = client.post("/api/v1/equity/calculate-tax-withholding", json={
        "participant_id": "P-1", "award_type": "RSU", "shares": 1200, "market_price": 55.25, "state_code": "ca"})
    assert r.status_code == 200 and r.json()["total_withholding"] == "26440.44"
    assert client.post("/api/v1/equity/calculate-tax-withholding", json={"participant_id": "P"}).status_code == 422
    r = client.post("/api/v1/equity/integration-payload", json={"target": "carta", "action": "record_vest",
                                                                "data": {"grant_id": "G"}})
    assert r.status_code == 422 and "Missing required fields" in r.json()["detail"]
    r = client.post("/api/v1/equity/integration-payload", json={"target": "carta", "action": "record_vest", "data": {
        "grant_id": "G", "participant_id": "P", "vest_date": "2026-01-15", "shares": 1200, "fmv": 55.25}})
    assert r.json()["execution_mode"] == "DRY_RUN_PENDING_APPROVAL"
    assert client.get("/openapi.json").status_code == 200


# ---------- Rule 10b5-1 trading plans ----------

def adopt(**kw):
    base = dict(plan_id="PL-1", participant_id="P-0001", insider_role="DIRECTOR_OR_OFFICER",
                adoption_date=date(2026, 3, 2), aware_of_mnpi=False, acting_in_good_faith=True,
                includes_required_certification=True)
    base.update(kw)
    return trading_plan.validate_adoption(PlanAdoptionRequest(**base))


def test_business_days_and_months_before():
    assert add_business_days(date(2026, 5, 1), 2) == date(2026, 5, 5)   # Fri +2 bd -> Tue
    assert add_business_days(date(2026, 5, 4), 1) == date(2026, 5, 5)   # Mon +1 bd -> Tue
    assert _months_before(date(2026, 3, 2), 12) == date(2025, 3, 2)


def test_cooling_off_director_with_earnings():
    # later of (Mar 2 + 90 = May 31) or (May 1 + 2 bd = May 5), capped at 120 (Jun 30) -> May 31
    r = adopt(next_earnings_disclosure_date=date(2026, 5, 1), first_trade_date=date(2026, 6, 1))
    assert r.status == "ELIGIBLE"
    assert r.cooling_off_end_date == date(2026, 5, 31)
    assert r.earliest_permitted_trade_date == date(2026, 5, 31)
    assert r.warnings == []
    assert all(r.checks.values())


def test_cooling_off_director_no_earnings_warns():
    r = adopt()  # no earnings date -> 90-day prong, with a warning
    assert r.cooling_off_end_date == date(2026, 5, 31)
    assert any("not evaluated" in w or "was not supplied" in w for w in r.warnings)
    assert "90 days after adoption" in r.cooling_off_rule_applied


def test_cooling_off_other_person_30_days():
    r = adopt(insider_role="OTHER_PERSON", includes_required_certification=False)
    assert r.cooling_off_end_date == date(2026, 4, 1)      # Mar 2 + 30 days
    assert "certification_included" not in r.checks         # not required for non-officers
    assert r.status == "ELIGIBLE"


def test_adoption_violations_stack():
    r = adopt(aware_of_mnpi=True, acting_in_good_faith=False, includes_required_certification=False,
              first_trade_date=date(2026, 3, 10))
    assert r.status == "NOT_ELIGIBLE"
    assert len(r.violations) == 4   # MNPI, good faith, certification, first trade before cooling-off
    assert r.checks["not_aware_of_mnpi_at_adoption"] is False
    assert r.checks["first_trade_after_cooling_off"] is False


def test_single_trade_plan_limit():
    blocked = adopt(is_single_trade_plan=True, prior_single_trade_plan_dates=[date(2025, 6, 1)])
    assert blocked.status == "NOT_ELIGIBLE"
    assert blocked.checks["single_trade_plan_within_limit"] is False
    ok = adopt(is_single_trade_plan=True, prior_single_trade_plan_dates=[date(2024, 1, 1)])  # >12 months prior
    assert ok.checks["single_trade_plan_within_limit"] is True


def test_overlapping_plan_and_exception():
    assert adopt(has_overlapping_plan=True).status == "NOT_ELIGIBLE"
    assert adopt(has_overlapping_plan=True, overlapping_plan_is_permitted_exception=True).status == "ELIGIBLE"


def test_adoption_expiration_guard():
    with pytest.raises(ValueError):
        adopt(plan_expiration_date=date(2026, 3, 2))


def check(**kw):
    base = dict(plan_id="PL-1", insider_role="DIRECTOR_OR_OFFICER", adoption_date=date(2026, 3, 2),
                next_earnings_disclosure_date=date(2026, 5, 1), plan_expiration_date=date(2027, 3, 2),
                trade_date=date(2026, 6, 15), side="SELL", shares=D("1000"), price=D("52"),
                scheduled_trades=[ScheduledTrade(trade_date=date(2026, 6, 15), side="SELL",
                                                 shares=D("1000"), limit_price=D("50"))])
    base.update(kw)
    return trading_plan.check_trade(TradeCheckRequest(**base))


def test_trade_permitted():
    r = check()
    assert r.status == "PERMITTED" and r.reasons == []
    assert all(r.checks.values())


def test_trade_blocked_cooling_off_blackout_and_limit():
    r = check(trade_date=date(2026, 5, 15), price=D("40"),
              blackout_windows=[BlackoutWindow(start=date(2026, 5, 10), end=date(2026, 5, 20))])
    assert r.status == "BLOCKED"
    assert r.checks["cooling_off_satisfied"] is False
    assert r.checks["outside_blackout_window"] is False
    assert r.checks["matches_plan_instruction"] is False   # no scheduled trade on May 15


def test_trade_blocked_when_terminated():
    r = check(earliest_permitted_trade_date=date(2026, 5, 31), plan_terminated=True)
    assert r.status == "BLOCKED"
    assert r.checks["plan_active"] is False
    assert any("terminated" in x for x in r.reasons)


def test_trading_plan_endpoints():
    r = client.post("/api/v1/equity/trading-plan/validate-adoption", json={
        "plan_id": "PL-9", "participant_id": "P-0001", "insider_role": "DIRECTOR_OR_OFFICER",
        "adoption_date": "2026-03-02", "aware_of_mnpi": False, "includes_required_certification": True,
        "next_earnings_disclosure_date": "2026-05-01", "first_trade_date": "2026-06-01"})
    assert r.status_code == 200
    assert r.json()["status"] == "ELIGIBLE" and r.json()["earliest_permitted_trade_date"] == "2026-05-31"
    r = client.post("/api/v1/equity/trading-plan/check-trade", json={
        "plan_id": "PL-9", "insider_role": "DIRECTOR_OR_OFFICER", "adoption_date": "2026-03-02",
        "next_earnings_disclosure_date": "2026-05-01", "trade_date": "2026-04-10", "side": "SELL", "shares": 500})
    assert r.status_code == 200 and r.json()["status"] == "BLOCKED"


def test_ui_served_at_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "Equity Compensation Management Agent" in r.text
    assert "text/html" in r.headers["content-type"]


def test_chat_rate_limit(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "CHAT_RATE_PER_HOUR", 2)
    main._chat_hits.clear()
    main._chat_day["date"] = None
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    codes = [client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code
             for _ in range(4)]
    # First 2 pass the limiter (then 503, no key); the rest are blocked by the limiter.
    assert codes.count(429) >= 1 and 503 in codes
    main._chat_hits.clear()
    main._chat_day["date"] = None


def test_agent_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_agent_tool_dispatch():
    from app.agent.agent import TOOLS, _run_tool
    import json
    assert len(TOOLS) == 9
    out = json.loads(_run_tool("calculate_tax_withholding", {"participant_id": "P", "award_type": "NSO",
        "shares": "2500", "market_price": "30", "strike_price": "12", "state_code": "TX"}))
    assert out["total_withholding"] == "13342.50"
    assert "error" in json.loads(_run_tool("calculate_tax_withholding", {"participant_id": "P"}))


def test_agent_loop_with_fake_client(monkeypatch):
    """Exercises the tool-use loop with SDK message objects, without a network call."""
    import anthropic
    from anthropic.types import Message
    from app.agent import agent as ag

    def msg(content, stop):
        return Message.model_validate({"id": "m", "type": "message", "role": "assistant", "model": "x",
                                       "content": content, "stop_reason": stop, "stop_sequence": None,
                                       "usage": {"input_tokens": 1, "output_tokens": 1}})
    replies = iter([
        msg([{"type": "tool_use", "id": "tu1", "name": "calculate_tax_withholding", "input": {
            "participant_id": "P", "award_type": "RSU", "shares": "1200", "market_price": "55.25", "state_code": "CA"}}],
            "tool_use"),
        msg([{"type": "text", "text": "Total withholding is $26,440.44."}], "end_turn"),
    ])
    seen = []

    class FakeMessages:
        def create(self, **kw):
            seen.append(kw)
            return next(replies)

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient())
    out = ag.chat([{"role": "user", "content": "RSU 1200 @ 55.25 CA"}])
    assert out["reply"].startswith("Total withholding")
    assert out["tool_calls"][0]["output"]["total_withholding"] == "26440.44"
    second = seen[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and second[-2]["role"] == "assistant"
