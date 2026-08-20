# Ledgerly — Project Story

Submitted to the Databricks Community Contest, Genie-Powered App Challenge,
Track A (Real-World Problem Solver).

## The problem

82% of small business failures trace back to cash flow mismanagement, not
lack of demand (U.S. Bank study) — businesses with real customers and real
revenue still fail because nobody saw a cash crunch coming until it was too
late to react. There are 365–445 million MSMEs in emerging markets alone.
Virtually none of them can afford a data analyst, a controller, or even a
part-time bookkeeper who proactively watches cash flow rather than just
recording it after the fact.

The tools that exist for small businesses are either accounting software
that reports what already happened, or enterprise BI that assumes a data
team to configure it. Neither answers the question an owner actually has at
11pm: *"can I make payroll next month, and if not, why not?"*

## Who this is for

A small business owner who has sales, inventory, and cash data sitting in
whatever system they use day-to-day (a POS, a spreadsheet, an invoicing
tool) but no one translating it into a decision. Ledgerly's synthetic
Phase 0 business, **Brew & Bloom**, is deliberately concrete rather than
generic: a single-location café that also sells wholesale bulk coffee and
supplies to about ten corporate/small-business accounts on invoice terms.
That hybrid — steady daily retail cash plus lumpy, sometimes-late wholesale
receivables — is exactly the shape of business where cash flow problems
hide until they don't.

## Why Genie, specifically

A dashboard answers the questions its designer anticipated. Genie answers
the question the owner actually has, in their own words, against their own
data — "who's late paying us," "what should I reorder," "are we going to
be okay in October" — without anyone having to build a report for each one
first. That's the difference between a copilot and a chatbot bolted onto a
BI tool: Genie in this app is not a "help" widget next to the real product,
it *is* the interface to the business logic. The KPI header is scaffolding
for instant-load, always-visible numbers; Genie is where the actual
analysis happens.

## Architecture / data flow

```
generate_data.py
      │  5 synthetic CSVs (products, customers, sales_transactions,
      │  cash_ledger, daily_cash_position) — one tenant, tenant_id
      │  column present on every table from day one
      ▼
Unity Catalog Volume (workspace.ledgerly.raw_data)
      │  manual upload (Free Edition has no external ingestion yet)
      ▼
01_load_to_unity_catalog.py  (Bronze → Silver → Gold, Delta)
      │  Bronze: raw + ingestion metadata, explicit schemas
      │  Silver: typed, deduplicated, cleaned
      │  Gold: gold_daily_sales_summary, gold_customer_receivables,
      │        gold_product_performance, gold_cash_position
      │        — business logic (at-risk, needs_reorder, runway)
      │        computed once, here, in SQL — not left for Genie
      │        or the app to re-derive per question
      ├──────────────────────────────┐
      ▼                              ▼
02_train_cash_forecast_model.py   Genie Space
      │  local scikit-learn          │  reads gold_* + cash_forecast
      │  (SageMaker opt-in,          │  Instructions encode the same
      │  never leaves an             │  definitions as the gold SQL
      │  endpoint running)           │  (cash runway, at-risk customer,
      ▼                              │  reorder point) in plain language
cash_forecast table                 │  + synonyms + example SQL
      │                              │
      └──────────────┬───────────────┘
                      ▼
            Databricks App (Streamlit)
      KPI header (direct SQL, instant)  │  Genie chat panel  │  Predicted
                                         │  (WorkspaceClient  │  Cash Risk
                                         │  .genie API)       │  panel (reads
                                         │                    │  cash_forecast,
                                         │                    │  Bedrock/
                                         │                    │  templated
                                         │                    │  explanation)
```

## What users can ask Genie

See `genie_agent_setup.md` for the full instructions, synonyms, and 7
benchmark questions used to validate the space, but the shape of what it
answers:

- *Cash position:* "How much cash do we have?" "What's our runway?" "When
  was our cash position worst, and why?"
- *Receivables / at-risk customers:* "Who's slow to pay us?" "How much do
  our wholesale customers owe us?"
- *Inventory:* "What needs reordering this week?"
- *Sales performance:* "How did wholesale compare to retail last month?"
  "What are our best sellers?"
- *Forecast:* "Are we at risk of running low on cash in the next 60 days?"

Each of these maps to a specific, previously-ambiguous business term
("at-risk," "runway," "reorder point") that the Genie instructions pin down
to an exact, auditable SQL definition — see Section 2 of
`genie_agent_setup.md`. That precision is what makes Genie's answers
trustworthy enough for an owner to act on rather than double-check.

## How Genie powers the main experience

The KPI header and the forecast panel exist to give the owner an
always-on, glanceable pulse (direct SQL, no latency, no ambiguity — the
five numbers that matter most). Genie is where the actual reasoning
happens: it's the only place in the app that can answer a question nobody
anticipated wiring up a specific query for. The business-logic definitions
in the Genie instructions aren't decoration — they're the same definitions
computed in the Gold-layer SQL (`is_at_risk_customer`,
`estimated_cash_runway_days`, `needs_reorder`), stated twice deliberately:
once as executable SQL for the app's fast-path KPIs, once as plain-language
instructions so Genie reasons about arbitrary follow-up questions using the
identical logic instead of drifting from it. If Genie were removed from
this app, what's left is a static dashboard that can't answer "why" or
"what about" — which is exactly the gap between reporting and being an
analyst.

## What we learned building this

The single biggest lesson was that **synthetic data has to be tuned like a
simulation, not just generated once and shipped.** The first pass of
`generate_data.py` produced a business that was wildly, unrealistically
profitable — cash balance grew from $15,000 to nearly $494,000 over two
years because wholesale order sizes and retail margins were disconnected
from any notion of realistic operating costs. The engineered "near-cash-
crunch" didn't even register — it was a rounding error against that much
cash. Fixing it took three iterations: first the wholesale/payroll/COGS
ratios were wrong in the other direction (the business bled cash for 702
of 730 days, deeply negative), then a second pass overcorrected on starting
buffer, and only the third pass — tying inventory-restock cash outflow to
actual trailing revenue, de-staggering biweekly payroll from monthly rent
so they stopped compounding into false dips, and sizing the engineered
pre-holiday prepay ($23,000) against a realistic operating buffer
($23,000 starting balance) — produced a business that's healthy
(23% YoY revenue growth, $46,283 ending balance) with exactly one sharp,
identifiable scare: cash bottoms out at $797.66 on 2025-10-08, the single
lowest point across the entire two-year history, then recovers through the
holiday season. That's the difference between "data that has a chart with
a dip in it" and "data a forecasting model can actually learn a meaningful
pattern from" — flat or randomly-noisy synthetic data makes any forecast
meaningless, which the non-negotiable quality bar for this project called
out from the start, and which turned out to require real iteration to
satisfy rather than being a given.

A related lesson: the forecast model's own honesty needed the same care.
A recursive 90-day forecast (each day's prediction feeding the next day's
lag features) compounds error in a way a single-step validation MAE
doesn't capture — the confidence band widens with `sqrt(horizon)`
specifically to represent that growing uncertainty honestly rather than
reporting a falsely tight interval 90 days out.

Finally, building the SageMaker path taught a discipline worth stating
plainly: the safest way to guarantee "never leave an endpoint running" is
to never create one in the first place. The SageMaker path here submits a
training job, downloads the resulting model artifact from S3, and runs
inference locally — there's no `deploy()` call anywhere in the code, so
there's no endpoint to remember to tear down, no matter how a demo run
goes.

## What's next

Everything not built here — real POS integration, churn modeling, actual
multi-tenant row-level security, inventory forecasting, proactive nudges,
production infrastructure — is documented in
[ROADMAP.md](ROADMAP.md), including specifically how Phase 0's design
(the `tenant_id` column, the Bronze/Silver/Gold split, the train+fallback
model pattern) was built to make each of those additive rather than a
rewrite.
