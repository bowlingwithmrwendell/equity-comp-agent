"""Tax withholding estimator for RSU vests and option exercises.

Rules implemented (tax year in app/config.py):
* RSU release / NSO exercise -> supplemental wages.
  Federal: 22% on the portion of YTD supplemental wages up to $1M, 37% (mandatory)
  on the portion above. The split is applied *within* an event that crosses $1M.
* FICA: Social Security 6.2% up to the remaining wage base; Medicare 1.45% on all;
  Additional Medicare 0.9% on wages above $200,000 YTD (employer withholding trigger).
* ISO exercise and ESPP purchase: no federal income tax or FICA withholding at
  exercise/purchase (IRC 421(b), 3121(a)(22)). The ISO bargain element is an AMT
  preference item; reported on Form 3921 / 3922 instead.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from app import config as C
from app.models import AwardType, TaxCalculationRequest, TaxCalculationResponse

ZERO = Decimal("0")


def money(x: Decimal) -> Decimal:
    return x.quantize(C.MONEY_Q, rounding=ROUND_HALF_UP)


def units(x: Decimal) -> Decimal:
    return x.quantize(C.UNIT_Q, rounding=ROUND_HALF_UP)


def state_rate(code: str, override: Decimal | None) -> tuple[Decimal, str | None]:
    if override is not None:
        return override, f"State rate override applied: {override}"
    if code in C.STATE_SUPPLEMENTAL_RATES:
        return C.STATE_SUPPLEMENTAL_RATES[code], None
    return ZERO, (f"No supplemental rate configured for {code}; state withholding set to 0. "
                  "Pass state_rate_override to supply one.")


def federal_supplemental(taxable: Decimal, ytd_supplemental: Decimal) -> tuple[Decimal, dict[str, Decimal]]:
    room_at_22 = max(ZERO, C.FED_MANDATORY_THRESHOLD - ytd_supplemental)
    at_22 = min(taxable, room_at_22)
    at_37 = taxable - at_22
    tax = money(at_22 * C.FED_SUPPLEMENTAL_RATE + at_37 * C.FED_MANDATORY_RATE)
    return tax, {"wages_at_22pct": money(at_22), "wages_at_37pct": money(at_37)}


def fica(taxable: Decimal, ytd_wages: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    ss_room = max(ZERO, C.SOCIAL_SECURITY_WAGE_BASE - ytd_wages)
    ss = money(min(taxable, ss_room) * C.SOCIAL_SECURITY_RATE)
    medicare = money(taxable * C.MEDICARE_RATE)
    over = max(ZERO, ytd_wages + taxable - max(ytd_wages, C.ADDITIONAL_MEDICARE_THRESHOLD))
    addl = money(over * C.ADDITIONAL_MEDICARE_RATE)
    return ss, medicare, addl


def calculate(req: TaxCalculationRequest) -> TaxCalculationResponse:
    notes: list[str] = []
    gross = money(req.shares * req.market_price)
    strike = req.strike_price if req.award_type != AwardType.RSU else ZERO
    if req.award_type == AwardType.RSU and req.strike_price > 0:
        notes.append("RSUs have no strike price; strike_price ignored.")
    exercise_cost = money(req.shares * strike)
    spread = max(ZERO, gross - exercise_cost)
    if gross < exercise_cost:
        notes.append("Option is underwater (market price below strike); no taxable spread.")

    s_rate, s_note = state_rate(req.state_code, req.state_rate_override)
    if s_note:
        notes.append(s_note)

    fed = st = loc = ss = med = addl = ZERO
    breakdown = {"wages_at_22pct": ZERO, "wages_at_37pct": ZERO}

    if req.award_type in (AwardType.RSU, AwardType.NSO):
        taxable = spread
        fed, breakdown = federal_supplemental(taxable, req.ytd_supplemental_income)
        st = money(taxable * s_rate)
        loc = money(taxable * req.local_rate)
        ss, med, addl = fica(taxable, req.ytd_fica_wages)
        if breakdown["wages_at_37pct"] > 0:
            notes.append("Part or all of this event exceeds $1M YTD supplemental wages; 37% mandatory rate applied to that portion.")
        if req.ytd_fica_wages + taxable > C.SOCIAL_SECURITY_WAGE_BASE:
            notes.append("Social Security wage base reached; SS withholding capped.")
    else:
        taxable = ZERO
        if req.award_type == AwardType.ISO:
            notes.append(
                f"ISO exercise: no regular income tax or FICA withholding. Bargain element of "
                f"${money(spread)} is an AMT preference item (Form 6251) and is reported on Form 3921. "
                "A disqualifying disposition (sale within 2 years of grant or 1 year of exercise) "
                "converts it to ordinary W-2 income.")
        else:
            notes.append(
                "ESPP purchase: no withholding at purchase. Ordinary income is determined at sale "
                "(qualifying vs. disqualifying disposition) and reported via Form 3922 and W-2.")

    fica_total = ss + med + addl
    total = fed + st + loc + fica_total
    net = money(gross - exercise_cost - total)
    exact_cover = units(total / req.market_price) if req.market_price > 0 else ZERO
    whole_cover = math.ceil(total / req.market_price) if req.market_price > 0 else 0
    if req.award_type == AwardType.NSO and exercise_cost > 0:
        notes.append(f"Shares to cover reflects taxes only; a sell-to-cover exercise also needs "
                     f"${exercise_cost} for the exercise cost.")

    return TaxCalculationResponse(
        participant_id=req.participant_id,
        award_type=req.award_type,
        gross_value=gross,
        exercise_cost=exercise_cost,
        taxable_gain=money(taxable),
        federal_withholding=fed,
        federal_rate_breakdown=breakdown,
        state_withholding=st,
        state_rate_applied=s_rate,
        local_withholding=loc,
        social_security_withholding=ss,
        medicare_withholding=med,
        additional_medicare_withholding=addl,
        fica_withholding=fica_total,
        total_withholding=total,
        net_value=net,
        shares_to_cover_taxes=exact_cover,
        whole_shares_to_cover=whole_cover,
        notes=notes,
        disclaimer=C.DISCLAIMER,
    )
