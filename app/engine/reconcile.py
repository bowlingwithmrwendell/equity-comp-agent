"""Ledger reconciliation: cap table (expected) vs payroll / brokerage (actual)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app import config as C
from app.models import Discrepancy, ReconciliationRequest, ReconciliationResponse

MONEY_FIELDS = {"gross_amount", "tax_withheld_amount"}
TOLERANCE_MONEY = Decimal("0.01")
TOLERANCE_UNITS = Decimal("0.0001")


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date() if "T" in str(v) or " " in str(v) \
        else date.fromisoformat(str(v))


def _key(rec: dict, fields: list[str]) -> str:
    parts = []
    for f in fields:
        v = rec.get(f)
        if f.endswith("date") and v is not None:
            v = _to_date(v).isoformat()
        parts.append(f"{f}={v}")
    return "|".join(parts)


def _in_period(rec: dict, start: date, end: date) -> bool:
    d = _to_date(rec.get("event_date"))
    return d is None or start <= d <= end


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


def reconcile(req: ReconciliationRequest) -> ReconciliationResponse:
    cap = [r for r in req.cap_table_records if _in_period(r, req.period_start, req.period_end)]
    pay = [r for r in req.payroll_records if _in_period(r, req.period_start, req.period_end)]

    cap_idx: dict[str, dict] = {}
    out: list[Discrepancy] = []
    for r in cap:
        k = _key(r, req.match_key)
        if k in cap_idx:
            out.append(Discrepancy(grant_id=r.get("grant_id"), match_key=k, field_mismatch="DUPLICATE_IN_CAP_TABLE",
                                   expected_value="1 record", actual_value="2+ records"))
        cap_idx[k] = r
    pay_idx: dict[str, dict] = {}
    for r in pay:
        k = _key(r, req.match_key)
        if k in pay_idx:
            out.append(Discrepancy(grant_id=r.get("grant_id"), match_key=k, field_mismatch="DUPLICATE_IN_PAYROLL",
                                   expected_value="1 record", actual_value="2+ records"))
        pay_idx[k] = r

    for k, c in cap_idx.items():
        p = pay_idx.get(k)
        if p is None:
            out.append(Discrepancy(grant_id=c.get("grant_id"), match_key=k, field_mismatch="MISSING_IN_PAYROLL",
                                   expected_value="present", actual_value=None))
            continue
        for f in req.compare_fields:
            ev, av = _dec(c.get(f)), _dec(p.get(f))
            if ev is None and av is None:
                continue
            tol = TOLERANCE_MONEY if f in MONEY_FIELDS else TOLERANCE_UNITS
            if ev is None or av is None or abs(ev - av) >= tol:
                out.append(Discrepancy(
                    grant_id=c.get("grant_id"), match_key=k, field_mismatch=f,
                    expected_value=None if ev is None else str(ev),
                    actual_value=None if av is None else str(av),
                    difference=None if (ev is None or av is None) else str(av - ev)))
    for k, p in pay_idx.items():
        if k not in cap_idx:
            out.append(Discrepancy(grant_id=p.get("grant_id"), match_key=k, field_mismatch="MISSING_IN_CAP_TABLE",
                                   expected_value=None, actual_value="present"))

    return ReconciliationResponse(
        status="BALANCED" if not out else "DISCREPANCY_DETECTED",
        period_start=req.period_start, period_end=req.period_end,
        records_compared=len(set(cap_idx) | set(pay_idx)),
        total_discrepancies=len(out), discrepancy_details=out, disclaimer=C.DISCLAIMER)
