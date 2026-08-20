# Ledgerly — Genie Space Setup

This document is what you paste into the Databricks **Genie** space UI
(instructions box + example SQL / "trusted assets") once
`01_load_to_unity_catalog.py` has run. It defines the business logic Genie
needs — not just table descriptions — because that's the single highest-
weighted judging criterion for this contest (20/40 points: "is Genie
genuinely central to the app's outcome").

---

## 1. Create the Genie space (manual console step)

1. In the Databricks workspace sidebar: **Genie** → **New Genie space**.
2. Name it `Ledgerly Cash & Sales Copilot`.
3. Under **Data**, add these tables from `workspace.ledgerly`:
   - `gold_daily_sales_summary`
   - `gold_customer_receivables`
   - `gold_product_performance`
   - `gold_cash_position`
   - `cash_forecast` (created later, by `02_train_cash_forecast_model.py` —
     come back and add this table after that script has run once)
4. Do **not** add the `bronze_*` or `silver_*` tables to the space — Genie
   should only ever see the curated gold layer. Keeping raw/staging tables
   out of scope is itself part of the business-logic design: it prevents
   Genie from double-counting unmatched invoices or answering from
   un-deduplicated rows.
5. Paste the contents of **Section 2** below into the space's
   **Instructions** box.
6. Add the SQL in **Section 4** as example queries ("trusted assets") —
   in the Genie space UI this is done per-query via **Add as example SQL**
   after running each query once in the SQL editor pane.
7. Note the Genie space ID from the URL (`.../genie/rooms/<space_id>`) —
   the app needs it in `GENIE_SPACE_ID` (see README.md).

---

## 2. Instructions (paste verbatim into the Genie space)

```
You are the financial and sales copilot for Brew & Bloom, a single-location
café that also sells wholesale bulk coffee/supplies to a handful of
corporate and small-business accounts on invoice payment terms. You answer
questions from the owner, who is not an accountant or data analyst — use
plain language, lead with the number/answer, then briefly explain what
drove it if relevant.

All data belongs to one tenant (tenant_id = 'demo_business_001'). Every
query you write MUST filter on tenant_id even though there's only one
value today — this is a hard rule the business's multi-tenant future
depends on, not a stylistic preference.

## Definitions — use these exact meanings, every time

- "Cash runway" / "runway" / "how long can we last": the number of days
  the current cash balance would last at the recent burn rate. This is the
  `estimated_cash_runway_days` column in gold_cash_position, computed as
  closing_balance divided by the trailing 30-day average daily net burn
  (outflows minus inflows). When the business is cash-flow-positive over
  that window, runway is undefined/infinite — report it as "cash flow is
  currently positive, no runway concern" rather than a number.

- "Cash crunch" / "cash risk" / "at risk of running out": a day where
  estimated_cash_runway_days drops below 30, OR closing_balance itself
  drops below $2,000 (whichever is the more precise thing the question is
  asking about — balance-based for "how low did we get", runway-based for
  "are we at risk").

- "At-risk customer" / "slow-paying customer" / "who owes us money" /
  "who's late": a customer where is_at_risk_customer = true in
  gold_customer_receivables. That flag means their average days-to-pay
  is more than 15 days past their own stated payment_terms_days — i.e.
  they are chronically late relative to THEIR OWN agreed terms, not a
  fixed number of days. A customer on 45-day terms who pays in 50 days is
  fine; one on 30-day terms who pays in 90 is at risk. Always mention
  outstanding_ar (money currently owed, unpaid) when discussing an
  at-risk customer — that's the number that actually matters to the
  owner.

- "Reorder point" / "running low" / "what needs restocking": a product
  where needs_reorder = true in gold_product_performance, meaning
  current_stock <= reorder_point. Report current_stock and
  reorder_quantity together so the owner knows both how urgent it is and
  how much to order.

- "Revenue" / "sales": SUM(total_revenue) from gold_daily_sales_summary,
  unless the question specifies "retail" or "wholesale" — those are
  separate values of the channel column and should not be summed together
  when the owner asks about one specifically.

- "Forecast" / "predicted cash" / "will we have enough cash": the
  cash_forecast table (forecast_day, predicted_balance, confidence_lower,
  confidence_upper, risk_flag per future date). This is a MODEL OUTPUT,
  not historical fact — always phrase answers from it as a prediction
  ("projected to be around $X"), never as a certainty, and mention the
  confidence range if the owner asks "how sure are you."

- "30-day forecast" / "60-day forecast" / "90-day forecast" / "day 30" /
  "day 60" / "day 90": filter cash_forecast on forecast_day = 30, 60, or
  90 respectively. Always use forecast_day for this, never compute a date
  offset from forecast_date — forecast_day is the exact, unambiguous
  checkpoint column built for this.

## Synonyms to recognize

- "burn rate" = avg_daily_net_burn_30d
- "how much cash do we have" / "cash on hand" / "bank balance" = most
  recent closing_balance in gold_cash_position
- "best sellers" / "top products" = highest total_revenue or units_sold in
  gold_product_performance
- "slow movers" = lowest units_sold in gold_product_performance (exclude
  products with current_stock = 0 from being called "slow" if they simply
  sold out)
- "owes us" / "receivables" / "AR" = outstanding_ar
- "wholesale customers" / "accounts" / "B2B customers" = rows in
  gold_customer_receivables (retail walk-in traffic is not tracked
  per-customer — never suggest Genie can name an individual walk-in buyer)

## Answering style

- Lead with the direct answer/number in the first sentence.
- Use dollar formatting with commas and two decimals for money, e.g.
  $12,418.62.
- If a question is ambiguous between retail/wholesale/total, answer with
  the total but note the split.
- If asked about a future date beyond the cash_forecast horizon (more than
  90 days out), say the forecast doesn't extend that far rather than
  guessing.
- Never invent a customer, product, or transaction that isn't in the
  data.
```

---

## 3. Table-level descriptions (add these in the Genie table picker's
description fields, if the UI prompts for them per table)

- **gold_daily_sales_summary**: One row per day per channel
  (retail/wholesale). `total_revenue` is the money actually charged that
  day; retail is same-day cash, wholesale is invoiced (see
  gold_customer_receivables for when it's actually collected).
- **gold_customer_receivables**: One row per wholesale customer,
  summarizing every invoice ever issued to them: how many, how much is
  still unpaid (`outstanding_ar`), and how late they typically pay
  relative to their own terms (`avg_days_past_terms`,
  `is_at_risk_customer`). Retail walk-in customers are not in this table.
- **gold_product_performance**: One row per product: current inventory,
  reorder threshold, and lifetime units/revenue sold. `needs_reorder` is
  the operational flag for "should we order more now."
- **gold_cash_position**: One row per calendar day: opening/closing cash
  balance, that day's inflows/outflows, a trailing 30-day burn rate, and
  an estimated runway in days. This is the historical ground truth cash
  position (not a prediction).
- **cash_forecast**: One row per future date (30/60/90-day horizon):
  `forecast_day` (an explicit integer, 1-90, counting days out from the
  most recent historical date — use this column, not date arithmetic on
  `forecast_date`, for any "day 30/60/90" style question), plus
  `predicted_balance`, a `confidence_lower`/`confidence_upper` band, and
  a `risk_flag`. Produced by a trained model, not observed fact.

---

## 4. Example SQL (add each as a Genie "trusted asset" / example query)

**1. Current cash position and burn rate**
```sql
SELECT position_date, closing_balance, avg_daily_net_burn_30d, estimated_cash_runway_days
FROM workspace.ledgerly.gold_cash_position
WHERE tenant_id = 'demo_business_001'
ORDER BY position_date DESC
LIMIT 1;
```

**2. At-risk (chronically late-paying) customers, worst first**
```sql
SELECT customer_name, payment_terms_days, avg_days_to_pay, avg_days_past_terms, outstanding_ar
FROM workspace.ledgerly.gold_customer_receivables
WHERE tenant_id = 'demo_business_001' AND is_at_risk_customer = true
ORDER BY avg_days_past_terms DESC;
```

**3. Products that need reordering right now**
```sql
SELECT product_name, category, current_stock, reorder_point, reorder_quantity
FROM workspace.ledgerly.gold_product_performance
WHERE tenant_id = 'demo_business_001' AND needs_reorder = true
ORDER BY current_stock ASC;
```

**4. Revenue by channel for a given month**
```sql
SELECT channel, SUM(total_revenue) AS revenue, SUM(transaction_count) AS transactions
FROM workspace.ledgerly.gold_daily_sales_summary
WHERE tenant_id = 'demo_business_001'
  AND transaction_date BETWEEN '2025-10-01' AND '2025-10-31'
GROUP BY channel;
```

**5. Worst historical cash-balance days (the crunch period)**
```sql
SELECT position_date, closing_balance, estimated_cash_runway_days
FROM workspace.ledgerly.gold_cash_position
WHERE tenant_id = 'demo_business_001'
ORDER BY closing_balance ASC
LIMIT 10;
```

**6. Forecasted cash risk over the next 90 days**
```sql
SELECT forecast_date, forecast_day, predicted_balance, confidence_lower, confidence_upper, risk_flag
FROM workspace.ledgerly.cash_forecast
WHERE tenant_id = 'demo_business_001'
ORDER BY forecast_day ASC;
```

**6a. Day 30 / 60 / 90 checkpoint forecast**
```sql
SELECT forecast_date, forecast_day, predicted_balance, confidence_lower, confidence_upper, risk_flag
FROM workspace.ledgerly.cash_forecast
WHERE tenant_id = 'demo_business_001' AND forecast_day IN (30, 60, 90)
ORDER BY forecast_day ASC;
```

**7. Best-selling products by revenue**
```sql
SELECT product_name, category, units_sold, total_revenue
FROM workspace.ledgerly.gold_product_performance
WHERE tenant_id = 'demo_business_001'
ORDER BY total_revenue DESC
LIMIT 10;
```

---

## 5. Sample questions (what the owner can actually type into the app)

1. "How much cash do we have right now, and how long will it last?"
2. "Which customers are slow to pay us, and how much do they owe?"
3. "What should I reorder this week?"
4. "How did wholesale revenue compare to retail last month?"
5. "When was our cash position at its worst, and why?"
6. "Are we at risk of running low on cash in the next 60 days?"
7. "What are our best-selling products?"

---

## 6. Benchmark questions (use these to validate the Genie space before
demoing — run each, confirm the answer matches the gold-table math by
hand, and only then trust the space for the live demo)

1. **"What's our current cash balance?"**
   Expected: the `closing_balance` from the most recent row in
   `gold_cash_position` — should match the last row of
   `daily_cash_position.csv` exactly ($46,282.67 area, per the generator's
   most recent sanity check run — re-verify after your own data run).

2. **"Who are our at-risk customers?"**
   Expected: exactly the customers with `is_at_risk_customer = true` —
   in the reference dataset this should be **Maple & Co. Coworking** and
   **Third Street Bookshop Cafe** (the two customers the generator marks
   `chronic_late_payer = True`), each with avg_days_past_terms well above
   the others.

3. **"What happened to our cash around October 2025?"**
   Expected: Genie should identify the sharp balance drop around
   2025-10-08 (the engineered pre-holiday inventory prepay) as the lowest
   point in the historical data, and describe the recovery afterward.

4. **"What needs to be reordered?"**
   Expected: only products where `current_stock <= reorder_point` — cross-
   check against `gold_product_performance` directly; the list should
   change over time as the generator's stock levels are static seed values
   (Phase 0 doesn't decrement stock per sale — see note below).

5. **"Is retail or wholesale bigger for us?"**
   Expected: retail should be clearly larger (~75-80% of total revenue in
   the reference dataset) — if Genie reports wholesale as bigger, the
   space is misreading channel or double-counting, and instructions need
   revisiting.

6. **"What's our cash runway?"**
   Expected: for most historical days this should report "cash flow is
   currently positive, no runway concern" (per the instructions' explicit
   handling of a non-positive burn rate) — it should NOT return a runway
   number every day. Only days following the October 2025 prepay should
   show a finite runway estimate.

7. **"How much does [a non-existent customer] owe us?"**
   Expected: Genie should say it has no record of that customer, not
   fabricate a number. This is a hallucination guardrail check.

> **Known Phase 0 limitation to flag if a judge/tester notices:**
> `products.current_stock` in the synthetic data is a static starting
> value — the generator does not decrement inventory as sales occur, so
> `needs_reorder` reflects the seed state, not a live-depleting count.
> Real inventory depletion tracking is Phase 2 scope (per-SKU demand
> forecasting). Flagging this here so it reads as a known, intentional
> scope boundary rather than a bug if it comes up in review.
