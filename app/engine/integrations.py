"""Build outbound payloads for external systems. Nothing here sends a request.

The agent never calls third-party APIs directly: it emits a reviewable, idempotent
payload that a separate execution service submits after human approval.
The field mappings below are ILLUSTRATIVE placeholders. Carta, Shareworks and
Workday each publish their own schemas under partner agreements; map these
fields to the vendor's actual contract before use.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.models import IntegrationPayloadRequest

ENDPOINT_HINTS = {
    ("carta", "record_vest"): "POST {carta_base}/issuers/{issuer_id}/securities/{security_id}/vesting-events",
    ("carta", "record_exercise"): "POST {carta_base}/issuers/{issuer_id}/securities/{security_id}/exercises",
    ("shareworks", "record_vest"): "POST {shareworks_base}/participants/{participant_id}/releases",
    ("shareworks", "record_exercise"): "POST {shareworks_base}/participants/{participant_id}/exercises",
    ("workday_payroll", "post_withholding"): "POST {workday_base}/payroll/one-time-payments",
}

REQUIRED = {
    "record_vest": ["grant_id", "participant_id", "vest_date", "shares", "fmv"],
    "record_exercise": ["grant_id", "participant_id", "exercise_date", "shares", "strike_price", "fmv"],
    "post_withholding": ["participant_id", "pay_date", "gross_amount", "federal_tax", "state_tax",
                         "social_security_tax", "medicare_tax"],
}


def build_payload(req: IntegrationPayloadRequest) -> dict:
    missing = [f for f in REQUIRED[req.action] if f not in req.data]
    if missing:
        raise ValueError(f"Missing required fields for {req.action}: {missing}")
    canonical = json.dumps({"t": req.target, "a": req.action, "d": req.data}, sort_keys=True, default=str)
    return {
        "execution_mode": "DRY_RUN_PENDING_APPROVAL",
        "target_system": req.target,
        "action": req.action,
        "endpoint_template": ENDPOINT_HINTS.get((req.target, req.action), "UNMAPPED - define in execution service"),
        "idempotency_key": hashlib.sha256(canonical.encode()).hexdigest()[:32],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "body": req.data,
        "requires": ["human_approval", "vendor_schema_mapping", "oauth_credentials_in_execution_service"],
    }

