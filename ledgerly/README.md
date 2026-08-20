# Ledgerly (Phase 0 — Contest MVP)

A Genie-powered "data analyst in a box" for small businesses. This is the
Phase 0 build for the Databricks Community Contest (Genie-Powered App
Challenge, Track A). See [ROADMAP.md](ROADMAP.md) for what's intentionally
**not** built yet, and [PROJECT_STORY.md](PROJECT_STORY.md) for the
contest write-up.

Everything here uses one synthetic business ("Brew & Bloom," a café +
wholesale-beans hybrid) and is designed to fit entirely inside a
**Databricks Free Edition** account: one workspace, one SQL warehouse, one
Databricks App.

---

## What you'll end up with

- 5 synthetic CSVs with a real cash-flow story baked in (seasonality, two
  chronic slow-paying wholesale customers, one engineered near-cash-crunch)
- A Bronze → Silver → Gold Delta pipeline in Unity Catalog
- A Genie space that understands "cash runway," "at-risk customer," and
  "reorder point" as real business logic, not just column names
- A trained 30/60/90-day cash forecast model + its `cash_forecast` table
- A Databricks App (Streamlit) with a KPI header, a Genie chat panel, and a
  Predicted Cash Risk panel

## Prerequisites

- A [Databricks Free Edition](https://www.databricks.com/learn/free-edition)
  account (free, no credit card).
- Python 3.10+ locally (for running the generator and, optionally, the app
  before deploying).
- The [Databricks CLI](https://docs.databricks.com/en/dev-tools/cli/index.html)
  installed locally (`pip install databricks-cli` or the newer unified
  `databricks` CLI — `brew install databricks/tap/databricks` on macOS, or
  see the docs link for other platforms), authenticated against your
  workspace (`databricks auth login`).
- (Optional, only if you want the SageMaker/Bedrock paths instead of the
  local fallbacks) An AWS account with a small credit budget.

---

## Step 1 — Generate the synthetic data (local)

```bash
cd ledgerly
python3 -m venv .venv && source .venv/bin/activate
pip install pandas numpy
python3 generate_data.py
```

This writes 5 CSVs to `data/` and prints a sanity-check summary. Confirm
the printed summary shows a clear dip around **2025-10-08** (the engineered
crunch) and no chronic negative balance elsewhere — if you've changed the
generator's parameters, re-check this before moving on; the rest of the
pipeline (forecast model, Genie benchmark answers) assumes this story
holds.

## Step 2 — Upload the CSVs to a Unity Catalog Volume

1. In your Databricks workspace, go to **Catalog** (left sidebar).
2. Confirm you have a catalog named `workspace` (Free Edition's default —
   if yours is named differently, you'll pass it as a widget value in
   Step 3).
3. Click **Create schema**, name it `ledgerly`, catalog `workspace`.
4. Inside `workspace.ledgerly`, click **Create** → **Volume**, name it
   `raw_data`.
5. Open the new volume and use **Upload to this volume** to upload all 5
   files from your local `data/` folder (`products.csv`, `customers.csv`,
   `sales_transactions.csv`, `cash_ledger.csv`, `daily_cash_position.csv`).

## Step 3 — Run the Unity Catalog loader notebook

1. In the workspace, **Workspace** → your user folder → **Import** →
   upload `01_load_to_unity_catalog.py` from this repo. Databricks will
   recognize it as a notebook (it has the `# Databricks notebook source`
   header).
2. Open it. It runs on **serverless compute** by default in Free Edition —
   no cluster to configure.
3. At the top, set the widgets if your catalog name differs from
   `workspace` or you used a different volume path. Defaults match Steps
   1–2 above.
4. **Run All**. It creates the catalog/schema/volume if missing, then
   builds `bronze_*` → `silver_*` → `gold_*` tables. The last two cells
   print a sanity check — confirm the lowest `gold_cash_position` balance
   is around 2025-10-08, and the two chronic-late-payer customers show up
   with the highest `avg_days_past_terms` in `gold_customer_receivables`.

## Step 4 — Set up the Genie space

Follow **[genie_agent_setup.md](genie_agent_setup.md)** exactly — it has
the instructions text to paste in, the tables to attach, the example SQL,
and 7 benchmark questions to validate the space before you demo it. Note
the Genie space ID from its URL — you'll need it in Step 6.

## Step 5 — Train the cash forecast model

You have two ways to run this; pick one.

**Option A — as a Databricks Job (recommended, keeps everything in the
workspace):**
1. **Workspace** → import `02_train_cash_forecast_model.py` into your user
   folder (this one is a plain script, not a notebook — Databricks will
   store it as a workspace file).
2. **Workflows** → **Create Job**. Add a task of type **Python Script**,
   source **Workspace**, and browse to the file you just imported.
3. Under the task's **Environment and libraries**, add pip packages:
   `scikit-learn`, `joblib`.
4. Use **Serverless** compute for the task (Free Edition default).
5. **Run now**. It reads `workspace.ledgerly.gold_cash_position` and
   writes `workspace.ledgerly.cash_forecast` — the run's log output prints
   validation MAE and the 30/60/90-day predicted balances.

**Option B — run it locally first (useful for iterating on the model):**
```bash
pip install scikit-learn joblib
python3 02_train_cash_forecast_model.py
```
With no Spark session available, this reads/writes local CSVs instead
(`data/daily_cash_position.csv` → `data/cash_forecast.csv`) — handy for
checking the model behaves sensibly before ever touching the workspace.
Note this local run does **not** update the `cash_forecast` Delta table —
run Option A too before deploying the app.

Once `cash_forecast` exists, go back to your Genie space (Step 4) and add
that table to it — it wasn't available yet when you first set the space
up.

**SageMaker path (optional, costs money, off by default):** set
`LEDGERLY_USE_SAGEMAKER=true` plus `AWS_ROLE_ARN`, `LEDGERLY_S3_BUCKET`,
`AWS_REGION` env vars before running Option A/B locally with AWS
credentials configured. It only submits a training job and downloads the
resulting model — it never creates a SageMaker endpoint, so there is
nothing to remember to tear down. See the docstring at the top of
`02_train_cash_forecast_model.py` for details, and **see
[AWS_SETUP.md](AWS_SETUP.md) for the full step-by-step AWS console setup**
(S3 bucket, IAM role/user, credentials, cleanup checklist) — it's not
optional reading if you've never wired AWS credentials into a Databricks
Job/App before.

## Step 6 — Deploy the Databricks App

1. Edit `app.yaml` in this repo: replace `REPLACE_WITH_SQL_WAREHOUSE_ID`
   (both places) with your Free Edition SQL warehouse's ID — find it under
   **SQL Warehouses** → your warehouse → **Connection details** →
   **Warehouse ID**. Replace `REPLACE_WITH_GENIE_SPACE_ID` with the Genie
   space ID from Step 4.
2. Sync this whole `ledgerly/` folder into your workspace:
   ```bash
   databricks sync . /Workspace/Users/<your-workspace-username>/ledgerly
   ```
   (Find `<your-workspace-username>` — usually your login email — under
   your account profile in the Databricks UI top-right corner.)
3. Create the app:
   ```bash
   databricks apps create ledgerly
   ```
4. Deploy it:
   ```bash
   databricks apps deploy ledgerly \
     --source-code-path /Workspace/Users/<your-workspace-username>/ledgerly
   ```
5. In the Databricks UI, go to **Compute** → **Apps** → **ledgerly** →
   **Resources** tab. Attach your SQL warehouse there too (belt-and-
   suspenders alongside the `app.yaml` value — the app's env var lookup in
   `app.py` prefers `DATABRICKS_WAREHOUSE_ID` directly, but the resource
   attachment is what actually grants the app's identity permission to
   query it).
6. Still in the Apps UI, open your Genie space's **Share** dialog and grant
   the app's service principal (shown on the app's **Details** tab) at
   least **Can view** access — without this, Genie calls from the app will
   fail with a permission error even though `GENIE_SPACE_ID` is set
   correctly.
7. Open the app URL shown in the Apps UI. You should see the KPI header
   load instantly, and the Genie chat panel + Predicted Cash Risk panel
   both working.

### Free Edition constraint reminders

- **One SQL warehouse, one App per account** — this whole design assumes
  that. Don't create a second warehouse or a second app; reuse this one.
- **Apps auto-stop after 24h of inactivity.** If the URL stops responding,
  go to **Compute** → **Apps** → **ledgerly** → **Start**. No redeploy
  needed, your data and Genie space are untouched.
- If you ever change `app.yaml`, re-run the `databricks apps deploy`
  command from Step 6.4 to push the update.

---

## Running the app locally before deploying (optional)

```bash
pip install -r requirements.txt
streamlit run app.py
```

With no `DATABRICKS_WAREHOUSE_ID` set, the app runs in **local demo
mode**: the KPI header and Predicted Cash Risk panel read straight from
the `data/` CSVs (including `data/cash_forecast.csv` if you ran Step 5
Option B), so you can sanity-check the UI without a live workspace. The
Genie chat panel will show a message explaining it needs a real
deployment — Genie itself cannot run outside Databricks.

---

## Environment variables reference

| Variable | Where set | Purpose |
|---|---|---|
| `LEDGERLY_TENANT_ID` | `app.yaml` / notebook widget | single-tenant ID stamped on all rows (Phase 0: always `demo_business_001`) |
| `LEDGERLY_CATALOG` / `LEDGERLY_SCHEMA` | `app.yaml` / notebook widget | Unity Catalog location, default `workspace.ledgerly` |
| `DATABRICKS_WAREHOUSE_ID` | `app.yaml` | SQL warehouse the app queries directly for the KPI header |
| `GENIE_SPACE_ID` | `app.yaml` | which Genie space the chat panel talks to |
| `LEDGERLY_USE_BEDROCK` | `app.yaml` (optional) | `"true"` to use Bedrock for the risk explanation instead of the templated fallback |
| `LEDGERLY_USE_SAGEMAKER` | shell env, forecast script only | `"true"` to train via SageMaker instead of local scikit-learn |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | shell env locally, Databricks secret in the Job/App | AWS credentials — see [AWS_SETUP.md](AWS_SETUP.md), never hardcode these |
| `AWS_ROLE_ARN`, `LEDGERLY_S3_BUCKET`, `AWS_REGION` | shell env | only needed for the SageMaker path — see [AWS_SETUP.md](AWS_SETUP.md) |

No credentials, account IDs, or ARNs are hardcoded anywhere in this repo —
every placeholder above is something you fill in yourself.

---

## Troubleshooting

- **Genie chat errors with a permission message** → you likely skipped
  Step 6.6 (sharing the Genie space with the app's service principal).
- **KPI header shows "—" or an error** → check `DATABRICKS_WAREHOUSE_ID`
  in the app's environment variables matches a real warehouse ID, and
  that the warehouse is running (Free Edition warehouses can also
  auto-stop).
- **Forecast panel says "no forecast available"** → Step 5 hasn't been run
  against the workspace yet (Option B alone only writes the local CSV).
- **Genie gives inconsistent or wrong answers** → re-run the 7 benchmark
  questions in `genie_agent_setup.md` §6 and compare against the expected
  answers; if they've drifted, revisit the Instructions text in the Genie
  space (Step 4) rather than the app code.
