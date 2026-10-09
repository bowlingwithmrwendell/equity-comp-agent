"""Tax-year constants and state supplemental rates.

All figures are for tax year 2026. Update this file each January.
State rates are a SAMPLE table for demo purposes -- verify against each
state's current withholding guide before relying on them. Callers can also
pass `state_rate_override` on any calculation request.
"""
from decimal import Decimal as D

TAX_YEAR = 2026

# Federal supplemental wage withholding (IRS Pub. 15 / Treas. Reg. 31.3402(g)-1)
FED_SUPPLEMENTAL_RATE = D("0.22")            # optional flat rate, supplemental wages <= $1M
FED_MANDATORY_RATE = D("0.37")               # mandatory on supplemental wages > $1M in the year
FED_MANDATORY_THRESHOLD = D("1000000.00")

# FICA
SOCIAL_SECURITY_RATE = D("0.062")
SOCIAL_SECURITY_WAGE_BASE = D("184500.00")   # 2026 OASDI taxable maximum
MEDICARE_RATE = D("0.0145")
ADDITIONAL_MEDICARE_RATE = D("0.009")
ADDITIONAL_MEDICARE_THRESHOLD = D("200000.00")  # employer withholding trigger, any filing status

# Sample state supplemental flat rates (decimal). Zero = no state wage income tax.
STATE_SUPPLEMENTAL_RATES = {
    "AK": D("0"), "FL": D("0"), "NV": D("0"), "NH": D("0"), "SD": D("0"),
    "TN": D("0"), "TX": D("0"), "WA": D("0"), "WY": D("0"),
    "CA": D("0.1023"),   # CA rate for stock options and bonuses
    "IL": D("0.0495"),
    "MA": D("0.05"),
    "NY": D("0.117"),
    "PA": D("0.0307"),
}

# ---- Rule 10b5-1 trading plans (17 CFR 240.10b5-1, as amended eff. Feb 27, 2023) ----
# Cooling-off period before the first trade may occur under a newly adopted or
# modified plan. Directors and Section 16 officers: the later of 90 days after
# adoption OR two business days after disclosure of financial results for the
# fiscal quarter of adoption, capped at 120 days. All other persons: 30 days.
COOLING_OFF_DIRECTOR_OFFICER_DAYS = 90
COOLING_OFF_DIRECTOR_OFFICER_CAP_DAYS = 120
EARNINGS_DISCLOSURE_BUSINESS_DAYS = 2
COOLING_OFF_OTHER_PERSON_DAYS = 30
# At most one single-trade plan may be adopted in any consecutive 12-month period.
SINGLE_TRADE_PLAN_WINDOW_MONTHS = 12

# Money is rounded to cents; shares and per-share prices to 4 decimal places.
MONEY_Q = D("0.01")
UNIT_Q = D("0.0001")

DISCLAIMER = (
    "ADMINISTRATIVE ESTIMATE ONLY: These figures are generated for equity "
    "administration and planning purposes. They do not constitute tax, legal, "
    "or investment advice. Confirm all amounts with payroll, your broker, and a "
    "qualified tax professional before filing or acting on them."
)
