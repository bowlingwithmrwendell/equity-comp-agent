"""Vesting schedule generation and day-count vested-fraction calculations."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from app import config as C
from app.models import DayCount, VestingRequest, VestingResponse, VestTranche


def add_months(d: date, months: int) -> date:
    """Add calendar months; clamp to month end (Jan 31 + 1 month -> Feb 28/29)."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def days_30_360(start: date, end: date) -> int:
    """30/360 US (Bond Basis)."""
    d1 = min(start.day, 30)
    d2 = 30 if (end.day == 31 and d1 == 30) else end.day
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)


def year_fraction(start: date, end: date, conv: DayCount) -> Decimal:
    if conv == DayCount.THIRTY_360:
        return Decimal(days_30_360(start, end)) / Decimal(360)
    return Decimal((end - start).days) / Decimal(365)


def build_schedule(req: VestingRequest) -> VestingResponse:
    if req.cliff_months > req.vesting_months:
        raise ValueError("cliff_months cannot exceed vesting_months")
    if req.vesting_months % req.frequency_months:
        raise ValueError("vesting_months must be a multiple of frequency_months")

    periods = req.vesting_months // req.frequency_months
    per_period = req.total_units / periods
    q = Decimal("1") if req.whole_shares else C.UNIT_Q

    # Each period's target cumulative amount; tranche = floor(cumulative) - prior. This spreads
    # rounding evenly and guarantees the schedule sums exactly to total_units.
    dates_and_cum: list[tuple[date, Decimal]] = []
    for i in range(1, periods + 1):
        m = i * req.frequency_months
        cum = (per_period * i).quantize(q, rounding=ROUND_DOWN)
        if i == periods:
            cum = req.total_units
        if m < req.cliff_months:
            continue
        dates_and_cum.append((add_months(req.vesting_start_date, m), cum))

    schedule: list[VestTranche] = []
    prior = Decimal("0")
    for n, (d, cum) in enumerate(dates_and_cum, start=1):
        schedule.append(VestTranche(tranche=n, vest_date=d,
                                    shares_vesting=(cum - prior).quantize(C.UNIT_Q),
                                    cumulative_vested=cum.quantize(C.UNIT_Q)))
        prior = cum

    resp = VestingResponse(grant_id=req.grant_id, schedule=schedule,
                           total_units=req.total_units.quantize(C.UNIT_Q), day_count=req.day_count)

    if req.as_of:
        vested = sum((t.shares_vesting for t in schedule if t.vest_date <= req.as_of), Decimal("0"))
        end = add_months(req.vesting_start_date, req.vesting_months)
        total_frac = year_fraction(req.vesting_start_date, end, req.day_count)
        elapsed = year_fraction(req.vesting_start_date, min(max(req.as_of, req.vesting_start_date), end), req.day_count)
        resp.as_of = req.as_of
        resp.vested_as_of = vested.quantize(C.UNIT_Q)
        resp.unvested_as_of = (req.total_units - vested).quantize(C.UNIT_Q)
        resp.elapsed_fraction = (elapsed / total_frac).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return resp
