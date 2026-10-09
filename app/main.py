"""FastAPI service: Equity Compensation Management Agent."""
from __future__ import annotations

import datetime
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent import agent
from app.engine import integrations, reconcile, reporting, tax, trading_plan, vesting
from app.models import (AgentChatRequest, Form1099BRequest, Form3921Request, Form3922Request,
                        IntegrationPayloadRequest, PlanAdoptionRequest, PlanAdoptionResponse,
                        ReconciliationRequest, ReconciliationResponse, TaxCalculationRequest,
                        TaxCalculationResponse, TradeCheckRequest, TradeCheckResponse,
                        VestingRequest, VestingResponse)

app = FastAPI(
    title="Equity Compensation Management Agent Integration API",
    version="1.4.0",
    description="Reconcile ledgers, estimate tax withholding, build vesting schedules, draft "
                "tax-reporting objects, and validate Rule 10b5-1 trading plans. All figures are "
                "administrative estimates, not tax or legal advice.",
)

# Permissive CORS for the demo UI (so the page can also be embedded/hosted elsewhere).
# Tighten allow_origins to specific hosts before any non-demo use.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"

# ---- Rate limiting for the public /agent/chat endpoint ----
# Only the agent chat uses the Anthropic API key, so only it is guarded. The
# deterministic calculation endpoints are free to call and are not limited.
# In-memory, per-process: fine for a single demo instance; it resets on restart
# and is not shared across instances. For heavier use, back it with Redis.
CHAT_RATE_PER_HOUR = int(os.getenv("CHAT_RATE_LIMIT_PER_HOUR", "20"))   # per visitor IP
CHAT_DAILY_CAP = int(os.getenv("CHAT_DAILY_CAP", "500"))               # across all visitors
_chat_hits: dict[str, deque] = defaultdict(deque)
_chat_day = {"date": None, "count": 0}
_chat_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _chat_rate_ok(request: Request) -> tuple[bool, str | None]:
    now = time.time()
    ip = _client_ip(request)
    with _chat_lock:
        today = datetime.date.today().isoformat()
        if _chat_day["date"] != today:
            _chat_day["date"], _chat_day["count"] = today, 0
        if _chat_day["count"] >= CHAT_DAILY_CAP:
            return False, "The demo chat has reached today's usage limit. Please try again tomorrow."
        hits = _chat_hits[ip]
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if len(hits) >= CHAT_RATE_PER_HOUR:
            return False, "You've reached the hourly limit for the demo chat. Please try again later."
        hits.append(now)
        _chat_day["count"] += 1
        return True, None


def _guard(fn, *a):
    try:
        return fn(*a)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/equity/calculate-tax-withholding", response_model=TaxCalculationResponse,
          operation_id="calculateTaxWithholding")
def calculate_tax_withholding(req: TaxCalculationRequest):
    return _guard(tax.calculate, req)


@app.post("/api/v1/equity/reconcile-ledger", response_model=ReconciliationResponse, operation_id="reconcileLedger")
def reconcile_ledger(req: ReconciliationRequest):
    if req.period_end < req.period_start:
        raise HTTPException(422, "period_end precedes period_start")
    return _guard(reconcile.reconcile, req)


@app.post("/api/v1/equity/vesting-schedule", response_model=VestingResponse, operation_id="generateVestingSchedule")
def vesting_schedule(req: VestingRequest):
    return _guard(vesting.build_schedule, req)


@app.post("/api/v1/equity/reports/form-3921", operation_id="generateForm3921")
def form_3921(req: Form3921Request):
    return _guard(reporting.form_3921, req)


@app.post("/api/v1/equity/reports/form-3922", operation_id="generateForm3922")
def form_3922(req: Form3922Request):
    return _guard(reporting.form_3922, req)


@app.post("/api/v1/equity/reports/form-1099b", operation_id="generateForm1099B")
def form_1099b(req: Form1099BRequest):
    return _guard(reporting.form_1099b, req)


@app.post("/api/v1/equity/trading-plan/validate-adoption", response_model=PlanAdoptionResponse,
          operation_id="validateTradingPlanAdoption")
def validate_trading_plan_adoption(req: PlanAdoptionRequest):
    return _guard(trading_plan.validate_adoption, req)


@app.post("/api/v1/equity/trading-plan/check-trade", response_model=TradeCheckResponse,
          operation_id="checkTradingPlanTrade")
def check_trading_plan_trade(req: TradeCheckRequest):
    return _guard(trading_plan.check_trade, req)


@app.post("/api/v1/equity/integration-payload", operation_id="buildIntegrationPayload")
def integration_payload(req: IntegrationPayloadRequest):
    return _guard(integrations.build_payload, req)


@app.post("/api/v1/agent/chat", operation_id="agentChat")
def agent_chat(req: AgentChatRequest, request: Request):
    ok, message = _chat_rate_ok(request)
    if not ok:
        raise HTTPException(status_code=429, detail=message)
    try:
        return agent.chat([m.model_dump() for m in req.messages])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


# Static assets for the web UI (mounted last so it never shadows an API route).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
