"""Rule 10b5-1 trading-plan validation: adoption eligibility and trade pre-clearance.

Rule 10b5-1(c) gives an affirmative defense to insider-trading liability for trades
made under a plan adopted while not aware of material nonpublic information (MNPI).
The SEC's 2023 amendments added cooling-off periods, a good-faith condition, a
director/officer certification, and limits on overlapping and single-trade plans.

This module encodes those gating conditions deterministically. It does NOT judge
whether someone is "actually" aware of MNPI or acting in good faith -- those are
facts the caller asserts. It checks the structural requirements the rule imposes
on dates, overlaps, and plan counts, and pre-clears a proposed trade against an
adopted plan's parameters. It is an administrative aid, not a legal opinion.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from app import config as C
from app.models import (InsiderRole, PlanAdoptionRequest, PlanAdoptionResponse, ScheduledTrade,
                        TradeCheckRequest, TradeCheckResponse, TradeSide)


def add_business_days(start: date, n: int) -> date:
    """Add n business days (Mon-Fri), ignoring federal holidays."""
    cur = start
    added = 0
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # Mon=0 .. Fri=4
            added += 1
    return cur


def _months_before(d: date, n: int) -> date:
    """The date n calendar months before d, clamped to month end."""
    m = d.month - n
    y = d.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cooling_off(role: InsiderRole, adoption_date: date,
                earnings_disclosure_date: date | None) -> tuple[date, str, list[str]]:
    """Return (cooling_off_end_date, rule_description, warnings)."""
    warnings: list[str] = []
    if role == InsiderRole.DIRECTOR_OR_OFFICER:
        ninety = adoption_date + timedelta(days=C.COOLING_OFF_DIRECTOR_OFFICER_DAYS)
        cap = adoption_date + timedelta(days=C.COOLING_OFF_DIRECTOR_OFFICER_CAP_DAYS)
        if earnings_disclosure_date is not None:
            earnings_prong = add_business_days(earnings_disclosure_date, C.EARNINGS_DISCLOSURE_BUSINESS_DAYS)
            end = max(ninety, earnings_prong)
            rule = ("later of 90 days after adoption or 2 business days after disclosure of results "
                    f"for the quarter of adoption, capped at {C.COOLING_OFF_DIRECTOR_OFFICER_CAP_DAYS} days")
        else:
            end = ninety
            rule = ("90 days after adoption; results-disclosure prong not evaluated "
                    "(no earnings date supplied)")
            warnings.append(
                "next_earnings_disclosure_date was not supplied, so the 2-business-days-after-results "
                f"prong was not evaluated; it can extend the cooling-off period up to "
                f"{C.COOLING_OFF_DIRECTOR_OFFICER_CAP_DAYS} days after adoption.")
        end = min(end, cap)
        return end, rule, warnings
    end = adoption_date + timedelta(days=C.COOLING_OFF_OTHER_PERSON_DAYS)
    return end, f"{C.COOLING_OFF_OTHER_PERSON_DAYS} days after adoption (person other than a director/officer)", warnings


def validate_adoption(req: PlanAdoptionRequest) -> PlanAdoptionResponse:
    violations: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    if req.plan_expiration_date is not None and req.plan_expiration_date <= req.adoption_date:
        raise ValueError("plan_expiration_date must be after adoption_date")

    end, rule, w = cooling_off(req.insider_role, req.adoption_date, req.next_earnings_disclosure_date)
    warnings.extend(w)

    # No MNPI at adoption
    checks["not_aware_of_mnpi_at_adoption"] = not req.aware_of_mnpi
    if req.aware_of_mnpi:
        violations.append("Adopter is aware of material nonpublic information at adoption; a 10b5-1 plan "
                          "cannot be established while aware of MNPI.")

    # Good faith
    checks["acting_in_good_faith"] = req.acting_in_good_faith
    if not req.acting_in_good_faith:
        violations.append("Plan is not represented as entered into in good faith; Rule 10b5-1(c) requires "
                          "good faith and no plan or scheme to evade.")

    # Director/officer certification
    if req.insider_role == InsiderRole.DIRECTOR_OR_OFFICER:
        checks["certification_included"] = req.includes_required_certification
        if not req.includes_required_certification:
            violations.append("Director/officer plan must include the required certification of no-MNPI "
                              "awareness and good faith (Rule 10b5-1(c)(1)(ii)(C)).")

    # Overlapping plans
    overlap_ok = (not req.has_overlapping_plan) or req.overlapping_plan_is_permitted_exception
    checks["no_prohibited_overlapping_plan"] = overlap_ok
    if not overlap_ok:
        violations.append("An overlapping outstanding 10b5-1 plan for open-market trades exists; overlapping "
                          "plans are prohibited except for the narrow later-commencing and sell-to-cover exceptions.")

    # Single-trade-plan limit (one per consecutive 12 months)
    if req.is_single_trade_plan:
        floor = _months_before(req.adoption_date, C.SINGLE_TRADE_PLAN_WINDOW_MONTHS)
        recent = [d for d in req.prior_single_trade_plan_dates if floor < d <= req.adoption_date]
        checks["single_trade_plan_within_limit"] = not recent
        if recent:
            violations.append(
                f"{len(recent)} prior single-trade plan(s) within {C.SINGLE_TRADE_PLAN_WINDOW_MONTHS} months "
                f"(since {floor.isoformat()}); only one single-trade plan is allowed per consecutive 12-month period.")

    # First trade must be on/after the cooling-off end
    if req.first_trade_date is not None:
        ok = req.first_trade_date >= end
        checks["first_trade_after_cooling_off"] = ok
        if not ok:
            violations.append(f"First scheduled trade {req.first_trade_date.isoformat()} precedes the end of the "
                              f"cooling-off period ({end.isoformat()}).")

    status = "ELIGIBLE" if not violations else "NOT_ELIGIBLE"
    return PlanAdoptionResponse(
        plan_id=req.plan_id,
        participant_id=req.participant_id,
        status=status,
        cooling_off_end_date=end,
        earliest_permitted_trade_date=end,
        cooling_off_rule_applied=rule,
        checks=checks,
        violations=violations,
        warnings=warnings,
        disclaimer=C.DISCLAIMER,
    )


def _matches_schedule(req: TradeCheckRequest) -> tuple[bool, str | None]:
    """Does the proposed trade correspond to one of the plan's scheduled instructions?"""
    for s in req.scheduled_trades:
        if s.trade_date != req.trade_date or s.side != req.side:
            continue
        if abs(req.shares - s.shares) > req.share_tolerance:
            continue
        if s.limit_price is not None and req.price is not None:
            if req.side == TradeSide.SELL and req.price < s.limit_price:
                return False, (f"Proposed sell price {req.price} is below the plan limit {s.limit_price} "
                               f"for {req.trade_date.isoformat()}.")
            if req.side == TradeSide.BUY and req.price > s.limit_price:
                return False, (f"Proposed buy price {req.price} is above the plan limit {s.limit_price} "
                               f"for {req.trade_date.isoformat()}.")
        return True, None
    return False, ("Proposed trade does not match any scheduled instruction in the plan "
                   "(date, side, or share count).")


def check_trade(req: TradeCheckRequest) -> TradeCheckResponse:
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    earliest = req.earliest_permitted_trade_date
    if earliest is None:
        earliest, _, _ = cooling_off(req.insider_role, req.adoption_date, req.next_earnings_disclosure_date)

    # Cooling-off elapsed
    cooled = req.trade_date >= earliest
    checks["cooling_off_satisfied"] = cooled
    if not cooled:
        reasons.append(f"Trade date {req.trade_date.isoformat()} is before the earliest permitted trade date "
                       f"{earliest.isoformat()} (cooling-off period not elapsed).")

    # Plan active and in force
    not_before_adoption = req.trade_date >= req.adoption_date
    if not not_before_adoption:
        reasons.append(f"Trade date {req.trade_date.isoformat()} precedes plan adoption "
                       f"{req.adoption_date.isoformat()}.")
    not_expired = req.plan_expiration_date is None or req.trade_date <= req.plan_expiration_date
    if not not_expired:
        reasons.append(f"Trade date is after plan expiration {req.plan_expiration_date.isoformat()}.")
    if req.plan_terminated:
        reasons.append("Plan is terminated; trades can no longer rely on the plan's affirmative defense.")
    checks["plan_active"] = not_before_adoption and not_expired and not req.plan_terminated

    # Blackout window
    in_blackout = any(b.start <= req.trade_date <= b.end for b in req.blackout_windows)
    checks["outside_blackout_window"] = not in_blackout
    if in_blackout:
        reasons.append("Trade date falls within a company blackout window.")

    # Matches a scheduled instruction
    if req.scheduled_trades:
        matched, why = _matches_schedule(req)
        checks["matches_plan_instruction"] = matched
        if not matched and why:
            reasons.append(why)

    status = "PERMITTED" if not reasons else "BLOCKED"
    return TradeCheckResponse(
        plan_id=req.plan_id,
        status=status,
        trade_date=req.trade_date,
        checks=checks,
        reasons=reasons,
        disclaimer=C.DISCLAIMER,
    )
