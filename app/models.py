"""Pydantic request/response models. Monetary and share values use Decimal end to end."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AwardType(str, Enum):
    ISO = "ISO"
    NSO = "NSO"
    RSU = "RSU"
    ESPP = "ESPP"


class DayCount(str, Enum):
    THIRTY_360 = "30/360"
    ACTUAL_365 = "ACTUAL/365"


# ---------- Tax withholding ----------

class TaxCalculationRequest(BaseModel):
    participant_id: str
    award_type: AwardType
    shares: Decimal = Field(gt=0)
    market_price: Decimal = Field(ge=0, description="FMV per share on the vest/exercise date")
    strike_price: Decimal = Field(default=Decimal("0"), ge=0)
    state_code: str = Field(min_length=2, max_length=2)
    ytd_supplemental_income: Decimal = Field(default=Decimal("0"), ge=0,
        description="Supplemental wages already paid this year, before this event")
    ytd_fica_wages: Decimal = Field(default=Decimal("0"), ge=0,
        description="FICA wages already paid this year (for the SS wage base and Additional Medicare threshold)")
    state_rate_override: Optional[Decimal] = Field(default=None, ge=0, le=1)
    local_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    @field_validator("state_code")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class TaxCalculationResponse(BaseModel):
    participant_id: str
    award_type: AwardType
    gross_value: Decimal
    exercise_cost: Decimal
    taxable_gain: Decimal
    federal_withholding: Decimal
    federal_rate_breakdown: dict[str, Decimal]
    state_withholding: Decimal
    state_rate_applied: Decimal
    local_withholding: Decimal
    social_security_withholding: Decimal
    medicare_withholding: Decimal
    additional_medicare_withholding: Decimal
    fica_withholding: Decimal
    total_withholding: Decimal
    net_value: Decimal
    shares_to_cover_taxes: Decimal
    whole_shares_to_cover: int
    notes: list[str]
    disclaimer: str


# ---------- Vesting ----------

class VestingRequest(BaseModel):
    grant_id: str
    total_units: Decimal = Field(gt=0)
    vesting_start_date: date
    vesting_months: int = Field(default=48, gt=0)
    cliff_months: int = Field(default=12, ge=0)
    frequency_months: int = Field(default=1, gt=0)
    whole_shares: bool = Field(default=True, description="Round each tranche down to whole shares; remainder lands in the final tranche")
    as_of: Optional[date] = None
    day_count: DayCount = DayCount.ACTUAL_365


class VestTranche(BaseModel):
    tranche: int
    vest_date: date
    shares_vesting: Decimal
    cumulative_vested: Decimal


class VestingResponse(BaseModel):
    grant_id: str
    schedule: list[VestTranche]
    total_units: Decimal
    as_of: Optional[date] = None
    vested_as_of: Optional[Decimal] = None
    unvested_as_of: Optional[Decimal] = None
    elapsed_fraction: Optional[Decimal] = None
    day_count: DayCount


# ---------- Reconciliation ----------

class ReconciliationRequest(BaseModel):
    period_start: date
    period_end: date
    cap_table_records: list[dict[str, Any]]
    payroll_records: list[dict[str, Any]]
    match_key: list[str] = Field(default_factory=lambda: ["grant_id", "event_date"])
    compare_fields: list[str] = Field(default_factory=lambda: [
        "shares_impacted", "fmv_at_event", "gross_amount", "tax_withheld_amount", "net_shares_issued"])


class Discrepancy(BaseModel):
    grant_id: Optional[str]
    match_key: str
    field_mismatch: str
    expected_value: Optional[str]
    actual_value: Optional[str]
    difference: Optional[str] = None


class ReconciliationResponse(BaseModel):
    status: Literal["BALANCED", "DISCREPANCY_DETECTED"]
    period_start: date
    period_end: date
    records_compared: int
    total_discrepancies: int
    discrepancy_details: list[Discrepancy]
    disclaimer: str


# ---------- Reporting ----------

class Form3921Request(BaseModel):
    """ISO exercise -> Form 3921."""
    employee_name: str
    employee_tin_last4: str = Field(min_length=4, max_length=4)
    grant_date: date
    exercise_date: date
    exercise_price_per_share: Decimal
    fmv_per_share_on_exercise: Decimal
    shares_transferred: Decimal
    transferor_if_not_corporation: Optional[str] = None


class Form3922Request(BaseModel):
    """ESPP first transfer of legal title -> Form 3922."""
    employee_name: str
    employee_tin_last4: str = Field(min_length=4, max_length=4)
    grant_date: date
    exercise_date: date
    fmv_per_share_on_grant: Decimal
    fmv_per_share_on_exercise: Decimal
    price_paid_per_share: Decimal
    shares_transferred: Decimal
    date_legal_title_transferred: date
    exercise_price_if_on_grant_date: Optional[Decimal] = None


class SaleLot(BaseModel):
    description: str = Field(description="e.g. '100 sh ACME Corp'")
    award_type: AwardType
    shares: Decimal = Field(gt=0)
    date_acquired: date
    date_sold: date
    proceeds: Decimal
    broker_reported_basis: Decimal = Field(description="Box 1e as reported by the broker (often $0 for RSU/NSO shares)")
    ordinary_income_recognized: Decimal = Field(default=Decimal("0"),
        description="W-2 income already recognized on vest/exercise; added back to basis")
    exercise_cost_paid: Decimal = Field(default=Decimal("0"))
    wash_sale_loss_disallowed: Decimal = Field(default=Decimal("0"))
    basis_reported_to_irs: bool = False


class Form1099BRequest(BaseModel):
    recipient_name: str
    tax_year: int
    lots: list[SaleLot]


# ---------- Rule 10b5-1 trading plans ----------

class InsiderRole(str, Enum):
    DIRECTOR_OR_OFFICER = "DIRECTOR_OR_OFFICER"   # Section 16 director or officer
    OTHER_PERSON = "OTHER_PERSON"                 # any other covered employee/insider


class TradeSide(str, Enum):
    SELL = "SELL"
    BUY = "BUY"


class PlanAdoptionRequest(BaseModel):
    """Validate a proposed Rule 10b5-1 plan at adoption (or modification, which the
    rule treats as a new adoption)."""
    plan_id: str
    participant_id: str
    insider_role: InsiderRole
    adoption_date: date
    aware_of_mnpi: bool = Field(description="Is the adopter aware of material nonpublic information at adoption?")
    acting_in_good_faith: bool = Field(default=True,
        description="Is the plan represented as entered into in good faith and not to evade the rule?")
    includes_required_certification: bool = Field(default=False,
        description="Director/officer plans must certify no-MNPI awareness and good faith at adoption.")
    next_earnings_disclosure_date: Optional[date] = Field(default=None,
        description="Date financial results for the quarter of adoption are/were disclosed (drives the 2-business-day prong).")
    first_trade_date: Optional[date] = Field(default=None,
        description="First trade scheduled under the plan; checked against the cooling-off period.")
    plan_expiration_date: Optional[date] = None
    is_single_trade_plan: bool = Field(default=False,
        description="Plan is designed to execute as a single transaction.")
    prior_single_trade_plan_dates: list[date] = Field(default_factory=list,
        description="Adoption dates of the adopter's prior single-trade plans (for the one-per-12-months limit).")
    has_overlapping_plan: bool = Field(default=False,
        description="Does an overlapping outstanding 10b5-1 plan for open-market trades already exist?")
    overlapping_plan_is_permitted_exception: bool = Field(default=False,
        description="True if the overlap qualifies for a later-commencing or sell-to-cover exception.")


class PlanAdoptionResponse(BaseModel):
    plan_id: str
    participant_id: str
    status: Literal["ELIGIBLE", "NOT_ELIGIBLE"]
    cooling_off_end_date: date
    earliest_permitted_trade_date: date
    cooling_off_rule_applied: str
    checks: dict[str, bool]
    violations: list[str]
    warnings: list[str]
    disclaimer: str


class BlackoutWindow(BaseModel):
    start: date
    end: date


class ScheduledTrade(BaseModel):
    trade_date: date
    side: TradeSide
    shares: Decimal = Field(gt=0)
    limit_price: Optional[Decimal] = Field(default=None, ge=0,
        description="Optional limit: for a SELL, the minimum acceptable price; for a BUY, the maximum.")


class TradeCheckRequest(BaseModel):
    """Pre-clearance for a proposed trade said to be made pursuant to an adopted plan."""
    plan_id: str
    insider_role: InsiderRole
    adoption_date: date
    next_earnings_disclosure_date: Optional[date] = None
    earliest_permitted_trade_date: Optional[date] = Field(default=None,
        description="Override; if omitted it is recomputed from role, adoption date and earnings date.")
    plan_expiration_date: Optional[date] = None
    plan_terminated: bool = False
    # proposed trade
    trade_date: date
    side: TradeSide
    shares: Decimal = Field(gt=0)
    price: Optional[Decimal] = Field(default=None, ge=0)
    blackout_windows: list[BlackoutWindow] = Field(default_factory=list)
    scheduled_trades: list[ScheduledTrade] = Field(default_factory=list,
        description="The plan's scheduled instructions; if provided, the trade must match one.")
    share_tolerance: Decimal = Field(default=Decimal("0"), ge=0,
        description="Allowed absolute difference between proposed and scheduled shares.")


class TradeCheckResponse(BaseModel):
    plan_id: str
    status: Literal["PERMITTED", "BLOCKED"]
    trade_date: date
    checks: dict[str, bool]
    reasons: list[str]
    disclaimer: str


# ---------- Integration payloads ----------

class IntegrationPayloadRequest(BaseModel):
    target: Literal["carta", "shareworks", "workday_payroll"]
    action: Literal["record_vest", "record_exercise", "post_withholding"]
    data: dict[str, Any]


# ---------- Agent ----------

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentChatRequest(BaseModel):
    messages: list[ChatMessage]
