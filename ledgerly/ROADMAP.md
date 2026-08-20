# Ledgerly — Roadmap (Phases 1–3)

This is the zero-to-production plan beyond the contest MVP. **None of this
is built yet** — it's documented here so the repo tells the full story for
anyone reviewing it (contest judges, portfolio viewers), and so Phase 0's
design choices can be checked against it: everything below should be
*additive* on top of Phase 0, not a rewrite.

What Phase 0 already put in place to make that true:
- Every table carries a `tenant_id` column (hardcoded to one value today) —
  Phase 1's row-level security attaches to this column directly.
- The Bronze → Silver → Gold split in `01_load_to_unity_catalog.py` means a
  new data source (Phase 1's POS integration) only needs a new Bronze
  loader; Silver/Gold logic doesn't change.
- `02_train_cash_forecast_model.py`'s local-fallback / SageMaker-opt-in
  split is the template Phase 1's churn model and Phase 2's inventory model
  reuse — same pattern, new target variable.
- The Genie space only ever sees `gold_*` tables — adding a new gold table
  (e.g. churn scores) extends what Genie can answer without touching what's
  already working.

---

## Phase 1 — Post-contest hardening

- **Real POS integration**, starting with Stripe test-mode data (free,
  realistic transaction shapes) instead of the synthetic generator.
- **Customer churn / value-at-risk model** — same train+fallback pattern as
  the Phase 0 cash flow model (local scikit-learn default, SageMaker
  training-job opt-in), predicting which wholesale customers are at risk of
  churning, not just paying late.
- **Real Unity Catalog row-level security**: turn the Phase 0 `tenant_id`
  placeholder into actual row filters scoping every query to the caller's
  tenant. Test with 2–3 synthetic tenants side by side to prove isolation
  holds before trusting it with anyone else's data.

## Phase 2 — Predictive breadth + proactive layer

- **Inventory demand forecasting** — per-SKU stockout/overstock prediction,
  replacing Phase 0's static `needs_reorder` threshold flag with an actual
  forecast of when each product will run out.
- **Bedrock-generated proactive nudges** — not just answering questions
  reactively, but surfacing them unprompted: "your cash position is
  trending negative around the 14th, here's why and here's a suggested
  fix."
- **EventBridge + SNS/SES** scheduled delivery of those nudges by email or
  SMS.

## Phase 3 — Actual production

- **Lambda-normalized webhook ingestion** landing in S3, with Auto Loader
  into Bronze — real multi-source, multi-tenant ingestion at scale,
  replacing the CSV-upload-to-Volume flow.
- **Per-business-type base models fine-tuned per tenant** (not one model
  trained from scratch per tenant) — necessary for SageMaker cost sanity
  once there's more than a handful of tenants.
- **Auth, billing, proper observability/cost monitoring, infra-as-code**
  (Terraform/CloudFormation) for repeatable deploys.
- **Migrate the frontend off the Databricks App sandbox** to a dedicated
  multi-tenant web app (e.g. Next.js + Databricks SQL API) once traffic
  outgrows the single-App Free Edition constraint.
- **A real pilot business connected**, for an actual validated case study —
  everything before this point is synthetic by design.
