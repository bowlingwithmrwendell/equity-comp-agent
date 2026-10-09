"""Draft reporting objects for Forms 3921, 3922 and 1099-B (with cost-basis adjustment).

These are data structures for review by an administrator. They are not e-file
(FIRE) records and are not substitutes for broker- or issuer-filed forms.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import config as C
from app.engine.tax import money, units
from app.models import Form1099BRequest, Form3921Request, Form3922Request


def _one_year_later(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + 1, day=28)


def form_3921(r: Form3921Request) -> dict:
    if r.exercise_date < r.grant_date:
        raise ValueError("exercise_date precedes grant_date")
    spread = money((r.fmv_per_share_on_exercise - r.exercise_price_per_share) * r.shares_transferred)
    return {
        "form": "3921",
        "title": "Exercise of an Incentive Stock Option Under Section 422(b)",
        "employee": {"name": r.employee_name, "tin_masked": f"***-**-{r.employee_tin_last4}"},
        "boxes": {
            "1_date_option_granted": r.grant_date.isoformat(),
            "2_date_option_exercised": r.exercise_date.isoformat(),
            "3_exercise_price_per_share": str(units(r.exercise_price_per_share)),
            "4_fmv_per_share_on_exercise_date": str(units(r.fmv_per_share_on_exercise)),
            "5_number_of_shares_transferred": str(units(r.shares_transferred)),
            "6_transferor_if_other_than_corporation": r.transferor_if_not_corporation,
        },
        "derived": {
            "amt_preference_bargain_element": str(max(spread, Decimal("0"))),
            "qualifying_disposition_earliest_date": max(
                _one_year_later(_one_year_later(r.grant_date)), _one_year_later(r.exercise_date)).isoformat(),
        },
        "filing_deadlines": {"recipient_copy": "January 31 of the following year",
                             "irs_paper": "February 28", "irs_electronic": "March 31"},
        "disclaimer": C.DISCLAIMER,
    }


def form_3922(r: Form3922Request) -> dict:
    return {
        "form": "3922",
        "title": "Transfer of Stock Acquired Through an Employee Stock Purchase Plan Under Section 423(c)",
        "employee": {"name": r.employee_name, "tin_masked": f"***-**-{r.employee_tin_last4}"},
        "boxes": {
            "1_date_option_granted": r.grant_date.isoformat(),
            "2_date_option_exercised": r.exercise_date.isoformat(),
            "3_fmv_per_share_on_grant_date": str(units(r.fmv_per_share_on_grant)),
            "4_fmv_per_share_on_exercise_date": str(units(r.fmv_per_share_on_exercise)),
            "5_price_paid_per_share": str(units(r.price_paid_per_share)),
            "6_number_of_shares_transferred": str(units(r.shares_transferred)),
            "7_date_legal_title_transferred": r.date_legal_title_transferred.isoformat(),
            "8_exercise_price_per_share_if_exercised_on_grant_date":
                None if r.exercise_price_if_on_grant_date is None else str(units(r.exercise_price_if_on_grant_date)),
        },
        "derived": {
            "discount_per_share_at_purchase": str(units(r.fmv_per_share_on_exercise - r.price_paid_per_share)),
            "qualifying_disposition_earliest_date": max(
                _one_year_later(_one_year_later(r.grant_date)), _one_year_later(r.exercise_date)).isoformat(),
        },
        "disclaimer": C.DISCLAIMER,
    }


def form_1099b(r: Form1099BRequest) -> dict:
    lots_out = []
    totals = {"proceeds": Decimal("0"), "reported_basis": Decimal("0"), "adjusted_basis": Decimal("0"),
              "short_term_gain": Decimal("0"), "long_term_gain": Decimal("0")}
    for lot in r.lots:
        if lot.date_sold < lot.date_acquired:
            raise ValueError(f"{lot.description}: date_sold precedes date_acquired")
        long_term = lot.date_sold > _one_year_later(lot.date_acquired)
        # True basis = cash paid + income already taxed on the W-2. If neither was supplied,
        # fall back to what the broker reported.
        true_basis = lot.exercise_cost_paid + lot.ordinary_income_recognized
        adjusted_basis = money(true_basis if true_basis > 0 else lot.broker_reported_basis)
        adjustment = money(adjusted_basis - lot.broker_reported_basis)
        gain = money(lot.proceeds - adjusted_basis + lot.wash_sale_loss_disallowed)
        totals["proceeds"] += lot.proceeds
        totals["reported_basis"] += lot.broker_reported_basis
        totals["adjusted_basis"] += adjusted_basis
        totals["long_term_gain" if long_term else "short_term_gain"] += gain
        lots_out.append({
            "1a_description": lot.description,
            "1b_date_acquired": lot.date_acquired.isoformat(),
            "1c_date_sold": lot.date_sold.isoformat(),
            "1d_proceeds": str(money(lot.proceeds)),
            "1e_cost_or_other_basis_reported": str(money(lot.broker_reported_basis)),
            "1g_wash_sale_loss_disallowed": str(money(lot.wash_sale_loss_disallowed)),
            "2_term": "LONG" if long_term else "SHORT",
            "12_basis_reported_to_irs": lot.basis_reported_to_irs,
            "form_8949": {
                "box": ("D" if long_term else "A") if lot.basis_reported_to_irs else ("E" if long_term else "B"),
                "adjustment_code": "B" if adjustment != 0 else None,
                "basis_adjustment": str(adjustment),
                "adjusted_basis": str(adjusted_basis),
                "gain_or_loss": str(gain),
            },
        })
    return {
        "form": "1099-B (draft reconciliation object)",
        "recipient": r.recipient_name,
        "tax_year": r.tax_year,
        "lots": lots_out,
        "totals": {k: str(money(v)) for k, v in totals.items()},
        "notes": ["Basis adjustment code 'B' on Form 8949 corrects broker-reported basis that omits "
                  "income already taxed on the W-2 (common for RSU and NSO shares) to avoid double taxation."],
        "disclaimer": C.DISCLAIMER,
    }
