"""Claude-powered agent. The model plans and explains; deterministic tools do the math."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from app.engine import integrations, reconcile, reporting, tax, trading_plan, vesting
from app.models import (Form1099BRequest, Form3921Request, Form3922Request, IntegrationPayloadRequest,
                        PlanAdoptionRequest, ReconciliationRequest, TaxCalculationRequest,
                        TradeCheckRequest, VestingRequest)

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOOL_ROUNDS = 8


def _tool(name: str, desc: str, model: type[BaseModel], fn: Callable[[Any], Any]) -> dict:
    schema = model.model_json_schema()
    return {"name": name, "description": desc, "input_schema": schema, "_model": model, "_fn": fn}


TOOLS = [
    _tool("calculate_tax_withholding",
          "Estimate federal, state, local and FICA withholding for an RSU release, NSO/ISO exercise or ESPP purchase.",
          TaxCalculationRequest, tax.calculate),
    _tool("generate_vesting_schedule",
          "Build a vesting schedule (cliff + periodic) and optionally vested/unvested units as of a date.",
          VestingRequest, vesting.build_schedule),
    _tool("reconcile_ledger",
          "Compare cap table records to payroll/brokerage records and list discrepancies.",
          ReconciliationRequest, reconcile.reconcile),
    _tool("generate_form_3921", "Draft a Form 3921 object for an ISO exercise.", Form3921Request, reporting.form_3921),
    _tool("generate_form_3922", "Draft a Form 3922 object for an ESPP share transfer.", Form3922Request, reporting.form_3922),
    _tool("generate_1099b_reconciliation",
          "Draft 1099-B lot detail with Form 8949 cost-basis adjustments.", Form1099BRequest, reporting.form_1099b),
    _tool("validate_trading_plan_adoption",
          "Validate a Rule 10b5-1 trading plan at adoption: cooling-off period, no-MNPI and good-faith "
          "conditions, director/officer certification, and the overlapping-plan and single-trade-plan limits.",
          PlanAdoptionRequest, trading_plan.validate_adoption),
    _tool("check_trading_plan_trade",
          "Pre-clear a proposed trade against an adopted 10b5-1 plan: cooling-off elapsed, plan active, "
          "outside blackout windows, and consistent with the plan's scheduled instructions.",
          TradeCheckRequest, trading_plan.check_trade),
    _tool("build_integration_payload",
          "Build a dry-run, approval-gated payload for Carta, Shareworks or Workday Payroll. Never sends anything.",
          IntegrationPayloadRequest, integrations.build_payload),
]
_BY_NAME = {t["name"]: t for t in TOOLS}


def _run_tool(name: str, args: dict) -> str:
    t = _BY_NAME.get(name)
    if not t:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        result = t["_fn"](t["_model"].model_validate(args))
        if isinstance(result, BaseModel):
            return result.model_dump_json()
        return json.dumps(result, default=str)
    except Exception as e:  # validation or business-rule errors go back to the model
        return json.dumps({"error": type(e).__name__, "detail": str(e)})


def chat(messages: list[dict]) -> dict:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set; the /agent/chat endpoint needs it. "
                           "All other endpoints work without it.")
    import anthropic

    client = anthropic.Anthropic()
    api_tools = [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]
    convo = list(messages)
    tool_log = []
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.messages.create(model=MODEL, max_tokens=4096, system=SYSTEM_PROMPT,
                                      tools=api_tools, messages=convo)
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {"reply": text, "tool_calls": tool_log}
        convo.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out = _run_tool(b.name, b.input)
                tool_log.append({"tool": b.name, "input": b.input, "output": json.loads(out)})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        convo.append({"role": "user", "content": results})
    return {"reply": "Stopped after too many tool calls; please narrow the request.", "tool_calls": tool_log}
