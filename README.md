# Equity Compensation Management Agent

A working API service and Claude-powered agent for equity administration. It handles ISO, NSO, RSU and ESPP grant lifecycles, estimates tax withholding, reconciles ledgers, drafts reporting objects for Forms 3921, 3922 and 1099-B, and validates Rule 10b5-1 trading plans (adoption eligibility and trade pre-clearance).

**Design principle:** Claude plans and explains, and deterministic Python tools do every calculation. The model never does tax math in its head and never writes to a database or calls a vendor API. It only emits payloads that wait for human approval.

> Portfolio demo built on fictional data. All figures are administrative estimates, not tax or legal advice.

## Quick start

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY to use the chat endpoint
docker compose up --build       # API on :8000, Postgres on :5432 with schema + seed data
open http://localhost:8000/docs # interactive Swagger UI
```

Without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -q                       # 29 tests, expected values hand-calculated
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/equity/calculate-tax-withholding` | Federal (22%/37% split), state, local, SS, Medicare and Additional Medicare withholding; shares to cover |
| POST | `/api/v1/equity/reconcile-ledger` | Cap table vs payroll/brokerage: field mismatches, missing and duplicate records |
| POST | `/api/v1/equity/vesting-schedule` | Cliff and periodic schedule; vested/unvested as of a date (30/360 or Actual/365) |
| POST | `/api/v1/equity/reports/form-3921` | ISO exercise reporting object, AMT preference, qualifying-disposition date |
| POST | `/api/v1/equity/reports/form-3922` | ESPP transfer reporting object |
| POST | `/api/v1/equity/reports/form-1099b` | 1099-B lot detail with Form 8949 basis adjustments (code B) |
| POST | `/api/v1/equity/trading-plan/validate-adoption` | Rule 10b5-1 adoption check: cooling-off, no-MNPI/good-faith, certification, overlap and single-trade limits |
| POST | `/api/v1/equity/trading-plan/check-trade` | Pre-clear a trade: cooling-off elapsed, plan active, outside blackout, matches plan instructions |
| POST | `/api/v1/equity/integration-payload` | Dry-run payload for Carta / Shareworks / Workday, with idempotency key |
| POST | `/api/v1/agent/chat` | Conversational agent (Claude tool use over the endpoints above) |

The full spec is in `openapi.yaml`. Sample calls are in `examples/requests.http`.

## Project layout

```
app/config.py            tax-year constants and state rates (update each January)
app/models.py            request/response models (Decimal throughout)
app/engine/tax.py        withholding engine
app/engine/vesting.py    schedules and day-count conventions
app/engine/reconcile.py  ledger reconciliation
app/engine/reporting.py  Forms 3921, 3922, 1099-B
app/engine/trading_plan.py  Rule 10b5-1 adoption + trade pre-clearance
app/engine/integrations.py  approval-gated outbound payloads
app/agent/               system prompt and Claude tool-use loop
db/schema.sql, seed.sql  PostgreSQL DDL and sample data (verified on Postgres 16)
tests/                   pytest suite
```

## Spec corrections made while building

The original spec was strong. The build turned up these gaps, which are fixed here:

1. **ISOs and ESPP have no withholding at exercise or purchase.** The spec applied supplemental rates to "option exercises" generally. Only NSO exercises and RSU releases are supplemental wages. An ISO exercise creates an AMT preference item and is reported on Form 3921. The engine returns zero withholding plus an AMT note.
2. **The $1M threshold splits inside one event.** If YTD supplemental wages are $900k and a vest adds $200k, $100k is withheld at 22% and $100k at 37%. Applying 37% to the whole event over-withholds.
3. **FICA was missing.** Withholding now includes the Social Security wage base cap ($184,500 for 2026) and the 0.9% Additional Medicare tax above $200k. That needed a new `ytd_fica_wages` input. The withholding table gains an `additional_medicare_tax` column.
4. **Precision rule clarified.** Shares and per-share prices use 4 decimals, and money uses cents, matching the DDL's `NUMERIC(…,2)` money columns. A "4 decimals everywhere" rule would have conflicted with the schema.
5. **1099-B needs a SALE transaction type.** The ledger had no way to record a sale. `SALE` and `CANCELLATION` were added, along with a `PARTIALLY_EXERCISED` status.
6. **ESPP was in the DB enum but not the API enum.** They are now aligned.
7. **Vesting terms had nowhere to live.** The grants table now stores months, cliff, frequency and day-count convention. CHECK constraints enforce that RSUs have no strike and that an ISO strike is at or above FMV at grant (IRC 422).
8. **Vendor API safety.** Carta, Shareworks and Workday payloads are dry runs that need human approval, carry idempotency keys, and use illustrative field mappings. Each vendor's real schema sits behind its partner agreement.

## Deploy (Render)

`render.yaml` is a Blueprint: in Render, **New + → Blueprint**, connect this repo, and set
`ANTHROPIC_API_KEY` as a dashboard secret (it is `sync:false`, never stored in git). The
container reads `$PORT` automatically. The calculation endpoints need no key; only the chat
endpoint does.

**Protecting the key.** `/api/v1/agent/chat` is rate limited — `CHAT_RATE_LIMIT_PER_HOUR`
per visitor (default 20) and `CHAT_DAILY_CAP` total per day (default 500) — so a public
deployment can't run up the bill. Point the deploy at a dedicated Anthropic key with a low
monthly budget cap. The limiter is in-memory (per instance, resets on restart); back it with
Redis for multi-instance use. The calculation endpoints are unguarded and free to call.

## Known limits (demo scope)

- State rates are a sample table (`app/config.py`); unknown states return $0 with a warning. Use `state_rate_override` in the meantime.
- No reciprocity, multi-state sourcing, or local jurisdiction tables.
- No authentication or persistence layer in the API. The schema is provided for the execution service.
- 3921/3922/1099-B outputs are review objects, not IRS FIRE e-file records.
- The Rule 10b5-1 module checks the *structural* conditions (cooling-off dates, certification presence, overlap and single-trade limits, blackout and schedule matching) against facts the caller asserts. It does not determine whether a person actually possesses MNPI or is acting in good faith, and is not legal advice. Business-day math skips weekends only (no federal-holiday calendar), so a cooling-off end date that lands just after a holiday may be one day early — confirm against a holiday calendar.
