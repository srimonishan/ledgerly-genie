"""
Ledgerly — Databricks App (Streamlit)
=======================================
Three panels:
  1. KPI header — direct SQL against the gold tables, instant load.
  2. Genie chat panel — natural-language Q&A via WorkspaceClient().genie.
  3. Predicted Cash Risk panel — reads the cash_forecast table, plots it,
     and asks Bedrock for a plain-English explanation (templated-sentence
     fallback if Bedrock isn't configured).

Two run modes, detected automatically from environment variables:
  - "databricks" mode: DATABRICKS_WAREHOUSE_ID is set (normal case once
    deployed as a Databricks App with a SQL warehouse resource attached).
    KPIs and the forecast panel query Unity Catalog directly; Genie chat
    is fully live.
  - "local_demo" mode: no warehouse configured. KPIs and the forecast
    panel read straight from the local data/ CSVs instead (including
    data/cash_forecast.csv from 02_train_cash_forecast_model.py's local
    fallback), so the app is inspectable with `streamlit run app.py`
    before anything is deployed. Genie chat shows a message explaining
    it needs a live Databricks deployment.
"""

import os
import json
from datetime import datetime

import pandas as pd
import streamlit as st

TENANT_ID = os.environ.get("LEDGERLY_TENANT_ID", "demo_business_001")
CATALOG = os.environ.get("LEDGERLY_CATALOG", "workspace")
SCHEMA = os.environ.get("LEDGERLY_SCHEMA", "ledgerly")

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID") or os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID")
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID")
RISK_BALANCE_THRESHOLD = 2000.00

MODE = "databricks" if WAREHOUSE_ID else "local_demo"

st.set_page_config(page_title="Ledgerly", page_icon="💵", layout="wide")


# ---------------------------------------------------------------------------
# Databricks SQL + Genie client (only constructed in databricks mode)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_workspace_client():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def run_sql(query: str) -> pd.DataFrame:
    """Execute a SQL statement against the configured warehouse and return
    a pandas DataFrame. Instant-load KPI queries — keep these simple
    aggregates over small gold tables, not ad-hoc Genie-generated SQL."""
    w = get_workspace_client()
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=query,
        catalog=CATALOG,
        schema=SCHEMA,
        wait_timeout="30s",
    )
    cols = [c.name for c in resp.manifest.schema.columns]
    rows = resp.result.data_array or []
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# KPI header data
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def get_kpis():
    if MODE == "databricks":
        cash = run_sql(f"""
            SELECT closing_balance, estimated_cash_runway_days
            FROM gold_cash_position WHERE tenant_id = '{TENANT_ID}'
            ORDER BY position_date DESC LIMIT 1
        """)
        mtd = run_sql(f"""
            SELECT SUM(total_revenue) AS mtd_revenue
            FROM gold_daily_sales_summary
            WHERE tenant_id = '{TENANT_ID}'
              AND date_trunc('MONTH', transaction_date) = date_trunc('MONTH', current_date())
        """)
        at_risk = run_sql(f"""
            SELECT COUNT(*) AS n FROM gold_customer_receivables
            WHERE tenant_id = '{TENANT_ID}' AND is_at_risk_customer = true
        """)
        reorder = run_sql(f"""
            SELECT COUNT(*) AS n FROM gold_product_performance
            WHERE tenant_id = '{TENANT_ID}' AND needs_reorder = true
        """)
        return {
            "closing_balance": float(cash["closing_balance"].iloc[0]) if len(cash) else None,
            "runway_days": (
                float(cash["estimated_cash_runway_days"].iloc[0])
                if len(cash) and cash["estimated_cash_runway_days"].iloc[0] is not None else None
            ),
            "mtd_revenue": float(mtd["mtd_revenue"].iloc[0] or 0),
            "at_risk_customers": int(at_risk["n"].iloc[0]),
            "products_to_reorder": int(reorder["n"].iloc[0]),
        }
    else:
        position = pd.read_csv("data/daily_cash_position.csv")
        position = position[position["tenant_id"] == TENANT_ID].sort_values("position_date")
        sales = pd.read_csv("data/sales_transactions.csv")
        sales = sales[sales["tenant_id"] == TENANT_ID]
        sales["transaction_date"] = pd.to_datetime(sales["transaction_date"])

        last = position.iloc[-1]
        last_date = pd.to_datetime(position["position_date"]).iloc[-1]
        mtd_revenue = sales[
            (sales["transaction_date"].dt.year == last_date.year)
            & (sales["transaction_date"].dt.month == last_date.month)
        ]["total_amount"].sum()

        # replicate the trailing-30-day burn / runway calc from the gold table
        pos = position.copy()
        pos["net_burn"] = pos["total_outflows"] - pos["total_inflows"]
        avg_burn_30 = pos["net_burn"].tail(30).mean()
        runway = round(last["closing_balance"] / avg_burn_30, 1) if avg_burn_30 > 0 else None

        customers = pd.read_csv("data/customers.csv")
        tx = pd.read_csv("data/sales_transactions.csv")
        ledger = pd.read_csv("data/cash_ledger.csv")
        wh_tx = tx[(tx.tenant_id == TENANT_ID) & (tx.channel == "wholesale")].groupby(
            ["transaction_date", "customer_id"])["total_amount"].sum().reset_index()
        pay = ledger[(ledger.tenant_id == TENANT_ID) & (ledger.category == "wholesale_invoice_payment")][
            ["entry_date", "customer_id", "amount"]]
        merged = wh_tx.merge(pay, left_on=["customer_id", "total_amount"],
                              right_on=["customer_id", "amount"], how="left")
        merged["transaction_date"] = pd.to_datetime(merged["transaction_date"])
        merged["entry_date"] = pd.to_datetime(merged["entry_date"])
        merged["days_to_pay"] = (merged["entry_date"] - merged["transaction_date"]).dt.days
        per_cust = merged.groupby("customer_id")["days_to_pay"].mean().reset_index()
        per_cust = per_cust.merge(customers[["customer_id", "payment_terms_days"]], on="customer_id")
        per_cust["is_at_risk"] = (per_cust["days_to_pay"] - per_cust["payment_terms_days"]) > 15
        at_risk_count = int(per_cust["is_at_risk"].sum())

        products = pd.read_csv("data/products.csv")
        products = products[products["tenant_id"] == TENANT_ID]
        reorder_count = int((products["current_stock"] <= products["reorder_point"]).sum())

        return {
            "closing_balance": float(last["closing_balance"]),
            "runway_days": runway,
            "mtd_revenue": float(mtd_revenue),
            "at_risk_customers": at_risk_count,
            "products_to_reorder": reorder_count,
        }


# ---------------------------------------------------------------------------
# Cash forecast data
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def get_forecast() -> pd.DataFrame:
    if MODE == "databricks":
        df = run_sql(f"""
            SELECT forecast_date, predicted_balance, confidence_lower, confidence_upper, risk_flag
            FROM cash_forecast WHERE tenant_id = '{TENANT_ID}' ORDER BY forecast_date
        """)
    else:
        try:
            df = pd.read_csv("data/cash_forecast.csv")
            df = df[df["tenant_id"] == TENANT_ID]
        except FileNotFoundError:
            return pd.DataFrame()
    if len(df):
        df["forecast_date"] = pd.to_datetime(df["forecast_date"])
        for col in ["predicted_balance", "confidence_lower", "confidence_upper"]:
            df[col] = df[col].astype(float)
    return df


# ---------------------------------------------------------------------------
# Risk explanation — Bedrock if configured, templated fallback otherwise
# ---------------------------------------------------------------------------
def explain_risk_with_bedrock(forecast_df: pd.DataFrame) -> str | None:
    if os.environ.get("LEDGERLY_USE_BEDROCK", "").lower() != "true":
        return None
    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        summary = forecast_df[["forecast_date", "predicted_balance", "risk_flag"]].to_dict(orient="records")
        prompt = (
            "You are a plain-language financial assistant for a small business owner. "
            "Given this 90-day cash balance forecast (JSON), write 2-3 short sentences "
            "explaining the cash risk outlook in plain English, no jargon. "
            f"Forecast: {json.dumps(summary, default=str)}"
        )
        resp = client.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        body = json.loads(resp["body"].read())
        return body["content"][0]["text"].strip()
    except Exception as e:
        st.caption(f"(Bedrock explanation unavailable: {e} — showing templated summary instead)")
        return None


def explain_risk_templated(forecast_df: pd.DataFrame) -> str:
    if forecast_df.empty:
        return "No forecast data available yet — run 02_train_cash_forecast_model.py first."

    risky = forecast_df[forecast_df["risk_flag"] == True]  # noqa: E712
    start_balance = forecast_df["predicted_balance"].iloc[0]
    end_balance = forecast_df["predicted_balance"].iloc[-1]
    trend = "growing" if end_balance > start_balance else "declining"

    if risky.empty:
        return (
            f"Cash is projected to keep {trend}, from about ${start_balance:,.0f} to "
            f"about ${end_balance:,.0f} over the next {len(forecast_df)} days. "
            f"No days are currently flagged as cash-risk (balance staying above "
            f"${RISK_BALANCE_THRESHOLD:,.0f})."
        )
    else:
        first_risk = risky.iloc[0]
        return (
            f"Heads up: cash is projected to dip below ${RISK_BALANCE_THRESHOLD:,.0f} "
            f"around {first_risk['forecast_date'].strftime('%B %d')}, reaching roughly "
            f"${first_risk['predicted_balance']:,.0f}. {len(risky)} of the next "
            f"{len(forecast_df)} days are flagged at-risk. Consider delaying large "
            f"purchases or following up on outstanding invoices before then."
        )


# ---------------------------------------------------------------------------
# Genie chat
# ---------------------------------------------------------------------------
def ask_genie(question: str):
    w = get_workspace_client()
    if "genie_conversation_id" not in st.session_state:
        result = w.genie.start_conversation_and_wait(space_id=GENIE_SPACE_ID, content=question)
        st.session_state["genie_conversation_id"] = result.conversation_id
    else:
        result = w.genie.create_message_and_wait(
            space_id=GENIE_SPACE_ID,
            conversation_id=st.session_state["genie_conversation_id"],
            content=question,
        )
    return extract_genie_answer(w, result)


def extract_genie_answer(w, result) -> dict:
    """Genie message results carry one or more attachments (text and/or a
    query+result). Pull out whatever's there defensively — the SDK's exact
    attachment shape has shifted across versions."""
    answer = {"text": None, "table": None}
    attachments = getattr(result, "attachments", None) or []
    for att in attachments:
        text_att = getattr(att, "text", None)
        if text_att and getattr(text_att, "content", None):
            answer["text"] = text_att.content
        query_att = getattr(att, "query", None)
        if query_att is not None:
            try:
                query_result = w.genie.get_message_attachment_query_result(
                    space_id=GENIE_SPACE_ID,
                    conversation_id=result.conversation_id,
                    message_id=result.id,
                    attachment_id=att.attachment_id,
                )
                sr = query_result.statement_response
                cols = [c.name for c in sr.manifest.schema.columns]
                rows = sr.result.data_array or []
                answer["table"] = pd.DataFrame(rows, columns=cols)
            except Exception:
                pass
    if answer["text"] is None and answer["table"] is None:
        # last-resort fallback: some SDK versions expose content directly
        answer["text"] = getattr(result, "content", None) or "(No answer text returned.)"
    return answer


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
st.title("💵 Ledgerly — Brew & Bloom")
st.caption(
    "A Genie-powered data analyst for a small business that can't afford one. "
    + ("Connected to Databricks." if MODE == "databricks" else "Running in local demo mode (no Databricks warehouse configured).")
)

# --- KPI header ---
try:
    kpis = get_kpis()
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Cash on hand", f"${kpis['closing_balance']:,.0f}" if kpis['closing_balance'] is not None else "—")
    k2.metric(
        "Cash runway",
        f"{kpis['runway_days']:.0f} days" if kpis["runway_days"] else "Cash-flow positive",
    )
    k3.metric("Revenue (MTD)", f"${kpis['mtd_revenue']:,.0f}")
    k4.metric("At-risk customers", kpis["at_risk_customers"])
    k5.metric("Products to reorder", kpis["products_to_reorder"])
except Exception as e:
    st.error(f"Could not load KPIs: {e}")

st.divider()

col_chat, col_forecast = st.columns([3, 2])

# --- Genie chat panel ---
with col_chat:
    st.subheader("💬 Ask Ledgerly")
    if MODE != "databricks" or not GENIE_SPACE_ID:
        st.info(
            "Genie chat requires a deployed Databricks App with a SQL warehouse and "
            "GENIE_SPACE_ID configured — see README.md. Sample questions this will "
            "answer once connected: \"How much cash do we have?\", \"Who's slow to "
            "pay us?\", \"What needs reordering?\""
        )
    else:
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        for role, content in st.session_state["chat_history"]:
            with st.chat_message(role):
                if isinstance(content, pd.DataFrame):
                    st.dataframe(content, use_container_width=True)
                else:
                    st.write(content)

        question = st.chat_input("Ask about cash flow, customers, or inventory...")
        if question:
            st.session_state["chat_history"].append(("user", question))
            with st.chat_message("user"):
                st.write(question)
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        answer = ask_genie(question)
                        if answer["text"]:
                            st.write(answer["text"])
                            st.session_state["chat_history"].append(("assistant", answer["text"]))
                        if answer["table"] is not None:
                            st.dataframe(answer["table"], use_container_width=True)
                            st.session_state["chat_history"].append(("assistant", answer["table"]))
                    except Exception as e:
                        st.error(f"Genie error: {e}")

# --- Predicted Cash Risk panel ---
with col_forecast:
    st.subheader("🔮 Predicted Cash Risk")
    forecast_df = get_forecast()
    if forecast_df.empty:
        st.warning("No forecast available yet. Run 02_train_cash_forecast_model.py to generate one.")
    else:
        chart_df = forecast_df.set_index("forecast_date")[
            ["predicted_balance", "confidence_lower", "confidence_upper"]
        ]
        st.line_chart(chart_df)

        explanation = explain_risk_with_bedrock(forecast_df) or explain_risk_templated(forecast_df)
        st.write(explanation)

        risky_days = forecast_df[forecast_df["risk_flag"] == True]  # noqa: E712
        if not risky_days.empty:
            st.dataframe(
                risky_days[["forecast_date", "predicted_balance", "confidence_lower", "confidence_upper"]],
                use_container_width=True,
            )

st.divider()
st.caption(f"Ledgerly Phase 0 · tenant: {TENANT_ID} · mode: {MODE} · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
