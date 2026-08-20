# Ledgerly

**Turns a small business's raw sales, inventory, and cash data into explainable, forward-looking cash intelligence — through natural language.**

![Databricks Free Edition](https://img.shields.io/badge/Databricks-Free%20Edition-FF3621?logo=databricks&logoColor=white)
![Databricks Genie](https://img.shields.io/badge/Databricks-Genie-2C6B49)
![Status](https://img.shields.io/badge/status-Phase%200%20MVP-a8641f)

## ⚡ What is Ledgerly?

82% of small business failures trace back to cash flow mismanagement, not lack of demand. Ledgerly is a Genie-powered "data analyst in a box": one synthetic café/wholesale business's data flows through a Unity Catalog medallion pipeline into a trained 90-day cash forecast and a Genie space that understands the business, not just the tables — so an owner can ask "are we going to be okay in October?" and get a real answer.

## 🧠 AI-Powered Intelligence

- 90-day recursive cash flow forecast (scikit-learn `GradientBoostingRegressor`, trained on real seasonality patterns)
- Confidence intervals that widen with forecast horizon, plus a per-day `risk_flag`
- Natural-language business Q&A via **Databricks Genie**, with instructions that encode real logic — "at-risk customer," "cash runway," "reorder point" — not just column descriptions
- Plain-English risk explanations (Bedrock-generated when configured, templated fallback otherwise)

## 🏗️ Architecture

![Ledgerly architecture: synthetic data through Unity Catalog Bronze/Silver/Gold into cash forecasting and Genie](docs/images/01-architecture.png)
*From synthetic data to AI-powered cash intelligence.*

## 🔄 Data & AI Flow

`Generate → Land → Bronze → Silver → Gold → Forecast → Genie → App`

| Stage | What happens |
|---|---|
| Generate | `generate_data.py` writes 5 synthetic CSVs — one tenant, real seasonality, two chronic late payers, one engineered cash squeeze |
| Bronze → Silver | `01_load_to_unity_catalog.py` types, cleans, and de-dupes into Delta |
| Gold | Business logic computed once in SQL: at-risk customers, reorder points, cash runway |
| Forecast | `02_train_cash_forecast_model.py` trains locally (SageMaker opt-in) and writes `cash_forecast` |
| Genie | Reads Gold + `cash_forecast`, answers open-ended questions with the same definitions |
| App | Streamlit Databricks App — KPI header, Genie chat, Predicted Cash Risk panel |

## 📊 Real Databricks Results

<table>
<tr><td width="50%">

![Genie forecast response with 90-day chart and confidence bands](docs/images/02-genie-forecast.png)
*Genie answering a day-30/60/90 forecast question directly from `cash_forecast`.*

</td><td width="50%">

![Unity Catalog showing the 15 bronze/silver/gold tables](docs/images/03-unity-catalog.png)
*The Bronze → Silver → Gold pipeline as it actually lands in Unity Catalog — 15 tables.*

</td></tr>
</table>

![Genie space with suggested business questions](docs/images/04-genie-chat.png)
*The Genie space, ready for open-ended questions about cash, customers, and inventory.*

## 🚀 Key Highlights

| Capability | What it delivers |
|---|---|
| Forecasting | 90-day forward cash prediction, recursive daily model |
| Explainability | Confidence intervals + per-day risk flags, not a black box |
| Genie | Natural-language financial analysis over curated Gold tables |
| Architecture | Databricks medallion pipeline, single-tenant, multi-tenant-ready |

## 🛠️ Tech Stack

**Databricks** (Free Edition) · **Unity Catalog** · **Delta Lake** · **Databricks Genie** · **Databricks Apps (Streamlit)** · **Python / SQL** · **scikit-learn**

*Optional, off by default:* AWS SageMaker (training job only, no endpoint ever created) · AWS Bedrock (Claude 3 Haiku, templated-sentence fallback otherwise).

## ▶️ Quick Start

```bash
python3 generate_data.py          # generates data/*.csv
python3 02_train_cash_forecast_model.py   # local model, no cloud needed
```
Then load `01_load_to_unity_catalog.py` and `02_train_cash_forecast_model.py`
into a Databricks workspace, set up the Genie space, and deploy `app.py` as
a Databricks App. **Full step-by-step instructions (zero prior Databricks
Apps experience assumed): [DEPLOYMENT.md](DEPLOYMENT.md).**
AWS setup (optional): [AWS_SETUP.md](AWS_SETUP.md).

## 🎯 Why Ledgerly?

Ledgerly moves financial analysis from historical reporting toward predictive, explainable, AI-assisted decision-making. Instead of a dashboard that only answers the questions its designer anticipated, an owner asks Genie directly — and the answer comes from the same business-logic definitions the forecast and KPIs are built on, not a separate guess.

## 📁 Project Structure

```
ledgerly/
  generate_data.py                  # synthetic data generator
  01_load_to_unity_catalog.py       # Bronze → Silver → Gold loader
  02_train_cash_forecast_model.py   # 90-day cash forecast (local + SageMaker opt-in)
  app.py / app.yaml                 # Databricks App (Streamlit)
  genie_agent_setup.md              # Genie space instructions + example SQL
  DEPLOYMENT.md · AWS_SETUP.md · ROADMAP.md · PROJECT_STORY.md
```

## 🔮 Future Scope

- Real POS integration (Stripe test-mode) and a customer churn model
- Row-level security to turn the `tenant_id` column into real multi-tenancy
- Per-SKU inventory forecasting + proactive nudges

See [ROADMAP.md](ROADMAP.md) for the full plan.

## 👤 Author

Built by [@srimonishan](https://github.com/srimonishan) for the Databricks Community Contest — Genie-Powered App Challenge.

---

*Ledgerly turns financial data into decisions before the cash runs out.*
