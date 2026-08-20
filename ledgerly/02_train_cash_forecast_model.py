"""
Ledgerly — 30/60/90-day cash flow forecasting (Phase 0)
=========================================================
Trains a model on daily net cash flow (inflows - outflows) and recursively
projects the cash balance forward 90 days, writing a `cash_forecast` table
with a predicted balance, a heuristic confidence band, and a risk flag.

Two training paths, CLEARLY SEPARATED below:

  - `train_local_fallback()` — the DEFAULT path. A single scikit-learn
    GradientBoostingRegressor trained in-process. No AWS calls, no cost,
    works with zero configuration. This is what runs unless you
    explicitly opt into SageMaker.

  - `train_sagemaker()` — OPT-IN ONLY, gated behind
    `LEDGERLY_USE_SAGEMAKER=true`. Submits a SageMaker *training job* on a
    small instance, downloads the resulting model artifact from S3, and
    loads it for LOCAL inference. It deliberately never calls
    `estimator.deploy()` — there is no persistent SageMaker endpoint at
    any point, so there is nothing to forget to tear down. If you ever DO
    create an endpoint by hand while experimenting, delete it immediately
    after — see the reminder printed at the end of that function.

Both paths produce the exact same artifact shape (a fitted regressor +
a residual std for the confidence band), so everything downstream
(recursive forecasting, table writing) is shared code.

Run modes:
  - Inside a Databricks notebook/job with a SparkSession available: reads
    `workspace.ledgerly.gold_cash_position` and writes
    `workspace.ledgerly.cash_forecast` as a Delta table.
  - As a plain local script (no Spark): reads `data/daily_cash_position.csv`
    and writes `data/cash_forecast.csv` — useful for iterating on the
    model before ever touching a Databricks workspace.

Env vars:
  LEDGERLY_USE_SAGEMAKER   "true" to use the SageMaker path (default: unset/local)
  LEDGERLY_CATALOG         Unity Catalog catalog name (default: workspace)
  LEDGERLY_SCHEMA          Unity Catalog schema name (default: ledgerly)
  LEDGERLY_TENANT_ID       tenant id to stamp on output rows (default: demo_business_001)

  --- only needed for the SageMaker path ---
  AWS_ROLE_ARN             IAM role SageMaker assumes to run the training job
                            (placeholder — set to your own role ARN, never hardcode)
  LEDGERLY_S3_BUCKET       S3 bucket for training data / model artifacts
  AWS_REGION               e.g. "us-east-1"
"""

import os
import sys
import json
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

TENANT_ID = os.environ.get("LEDGERLY_TENANT_ID", "demo_business_001")
CATALOG = os.environ.get("LEDGERLY_CATALOG", "workspace")
SCHEMA = os.environ.get("LEDGERLY_SCHEMA", "ledgerly")
FORECAST_HORIZON_DAYS = 90
RISK_BALANCE_THRESHOLD = 2000.00  # matches the "cash crunch" definition in genie_agent_setup.md

LOCAL_INPUT_CSV = "data/daily_cash_position.csv"
LOCAL_OUTPUT_CSV = "data/cash_forecast.csv"


# ---------------------------------------------------------------------------
# Data loading — Spark (Databricks) if available, else local CSV
# ---------------------------------------------------------------------------
def get_spark():
    try:
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        return spark
    except Exception:
        return None


def load_daily_cash_position() -> pd.DataFrame:
    spark = get_spark()
    if spark is not None:
        table = f"{CATALOG}.{SCHEMA}.gold_cash_position"
        print(f"Loading {table} via Spark...")
        sdf = spark.sql(f"""
            SELECT position_date, opening_balance, total_inflows, total_outflows, closing_balance
            FROM {table}
            WHERE tenant_id = '{TENANT_ID}'
            ORDER BY position_date
        """)
        df = sdf.toPandas()
    else:
        print(f"No active Spark session — loading local CSV: {LOCAL_INPUT_CSV}")
        df = pd.read_csv(LOCAL_INPUT_CSV)
        df = df[df["tenant_id"] == TENANT_ID].rename(columns={"position_date": "position_date"})
        df = df[["position_date", "opening_balance", "total_inflows", "total_outflows", "closing_balance"]]

    df["position_date"] = pd.to_datetime(df["position_date"])
    df = df.sort_values("position_date").reset_index(drop=True)
    df["net_flow"] = df["total_inflows"] - df["total_outflows"]
    return df


# ---------------------------------------------------------------------------
# Feature engineering — shared by both training paths and by the recursive
# forecast loop
# ---------------------------------------------------------------------------
FEATURE_COLS = [
    "day_of_week", "day_of_month", "month", "is_weekend",
    "lag_1", "lag_7", "lag_14", "lag_30",
    "roll_mean_7", "roll_mean_30", "days_since_start",
]


def build_features(net_flow_series: pd.Series, dates: pd.Series, start_date) -> pd.DataFrame:
    """Given a (possibly partly synthetic/forecasted) net_flow history aligned
    with `dates`, build the feature matrix for every date in the series."""
    s = net_flow_series.reset_index(drop=True)
    d = pd.to_datetime(dates).reset_index(drop=True)

    feat = pd.DataFrame({
        "day_of_week": d.dt.dayofweek,
        "day_of_month": d.dt.day,
        "month": d.dt.month,
        "is_weekend": (d.dt.dayofweek >= 5).astype(int),
        "lag_1": s.shift(1),
        "lag_7": s.shift(7),
        "lag_14": s.shift(14),
        "lag_30": s.shift(30),
        "roll_mean_7": s.shift(1).rolling(7).mean(),
        "roll_mean_30": s.shift(1).rolling(30).mean(),
        "days_since_start": (d - pd.Timestamp(start_date)).dt.days,
    })
    return feat


def make_training_set(history: pd.DataFrame):
    feat = build_features(history["net_flow"], history["position_date"], history["position_date"].min())
    feat["target"] = history["net_flow"].values
    feat = feat.dropna().reset_index(drop=True)
    X = feat[FEATURE_COLS]
    y = feat["target"]
    return X, y


# ---------------------------------------------------------------------------
# PATH A (default): local scikit-learn fallback — no AWS, no cost
# ---------------------------------------------------------------------------
def train_local_fallback(X: pd.DataFrame, y: pd.Series):
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import train_test_split

    # time-ordered holdout (last 15%) for an honest residual estimate —
    # never shuffle time series data for validation
    split = int(len(X) * 0.85)
    X_train, X_val = X.iloc[:split], X.iloc[split:]
    y_train, y_val = y.iloc[:split], y.iloc[split:]

    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    model.fit(X_train, y_train)

    val_pred = model.predict(X_val)
    residual_std = float(np.std(y_val.values - val_pred))
    mae = float(np.mean(np.abs(y_val.values - val_pred)))
    print(f"[local fallback] validation MAE on daily net flow: ${mae:,.2f} "
          f"(residual std: ${residual_std:,.2f}, n_val={len(X_val)})")

    # refit on all available data for the final forecasting model
    model.fit(X, y)
    return model, residual_std


# ---------------------------------------------------------------------------
# PATH B (opt-in): SageMaker training job. No endpoint is ever created —
# we submit a training job, pull the artifact from S3, and run inference
# locally. Gated entirely behind LEDGERLY_USE_SAGEMAKER=true.
# ---------------------------------------------------------------------------
SAGEMAKER_TRAIN_SCRIPT = '''
# This file is written to a temp dir and shipped to SageMaker as the
# training entry point. It mirrors train_local_fallback() above, trained
# inside a SageMaker-managed container instead of in-process.
import argparse, os, joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=str, default=os.environ.get("SM_MODEL_DIR"))
    parser.add_argument("--train", type=str, default=os.environ.get("SM_CHANNEL_TRAIN"))
    args = parser.parse_args()

    df = pd.read_csv(os.path.join(args.train, "training_data.csv"))
    feature_cols = [c for c in df.columns if c != "target"]
    X, y = df[feature_cols], df["target"]

    model = GradientBoostingRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
    model.fit(X, y)
    joblib.dump(model, os.path.join(args.model_dir, "model.joblib"))
'''


def train_sagemaker(X: pd.DataFrame, y: pd.Series):
    """Opt-in path. Requires: AWS_ROLE_ARN, LEDGERLY_S3_BUCKET, AWS_REGION.
    Submits a training job on ml.m5.large (cheap, no GPU), downloads the
    model artifact, loads it locally. NEVER deploys a persistent endpoint.
    """
    import boto3
    import sagemaker
    from sagemaker.sklearn.estimator import SKLearn
    import joblib
    import tarfile

    role_arn = os.environ.get("AWS_ROLE_ARN")
    bucket = os.environ.get("LEDGERLY_S3_BUCKET")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not role_arn or not bucket:
        raise RuntimeError(
            "SageMaker path requires AWS_ROLE_ARN and LEDGERLY_S3_BUCKET env vars. "
            "Falling back to local training is recommended unless you specifically "
            "need to demonstrate the SageMaker integration."
        )

    session = sagemaker.Session(boto3.Session(region_name=region))

    with tempfile.TemporaryDirectory() as tmp:
        script_path = os.path.join(tmp, "train.py")
        with open(script_path, "w") as f:
            f.write(SAGEMAKER_TRAIN_SCRIPT)

        train_csv_path = os.path.join(tmp, "training_data.csv")
        train_df = X.copy()
        train_df["target"] = y.values
        train_df.to_csv(train_csv_path, index=False)

        train_s3_uri = session.upload_data(
            path=train_csv_path, bucket=bucket, key_prefix="ledgerly/cash-forecast/train"
        )

        estimator = SKLearn(
            entry_point=script_path,
            role=role_arn,
            instance_type="ml.m5.large",   # small & cheap — no GPU needed
            instance_count=1,
            framework_version="1.2-1",
            sagemaker_session=session,
        )

        print("[sagemaker] submitting training job (this incurs AWS cost)...")
        estimator.fit({"train": train_s3_uri})
        print("[sagemaker] training job complete. Downloading model artifact "
              "(no endpoint was created).")

        model_s3_uri = estimator.model_data
        local_tar = os.path.join(tmp, "model.tar.gz")
        s3 = boto3.client("s3", region_name=region)
        bucket_name, key = model_s3_uri.replace("s3://", "").split("/", 1)
        s3.download_file(bucket_name, key, local_tar)

        with tarfile.open(local_tar) as tar:
            tar.extractall(tmp)
        model = joblib.load(os.path.join(tmp, "model.joblib"))

    val_pred = model.predict(X.iloc[-max(30, int(len(X) * 0.15)):])
    residual_std = float(np.std(y.iloc[-len(val_pred):].values - val_pred))

    print("[sagemaker] REMINDER: this path only ran a training job — no endpoint "
          "exists. If you separately created one while experimenting in the AWS "
          "console, delete it now: aws sagemaker delete-endpoint --endpoint-name <name>")
    return model, residual_std


# ---------------------------------------------------------------------------
# Recursive multi-day forecast (shared by both training paths)
# ---------------------------------------------------------------------------
def recursive_forecast(model, residual_std, history: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    start_date = history["position_date"].min()
    working_dates = list(history["position_date"])
    working_flows = list(history["net_flow"])
    last_balance = float(history["closing_balance"].iloc[-1])
    last_date = history["position_date"].iloc[-1]

    rows = []
    balance = last_balance
    for h in range(1, horizon_days + 1):
        forecast_date = last_date + timedelta(days=h)
        working_dates.append(forecast_date)

        feat = build_features(
            pd.Series(working_flows + [0.0]),  # placeholder target row, unused
            pd.Series(working_dates),
            start_date,
        ).iloc[[-1]][FEATURE_COLS]

        predicted_net_flow = float(model.predict(feat)[0])
        working_flows.append(predicted_net_flow)
        balance += predicted_net_flow

        # Uncertainty grows with the forecast horizon since day-h+1 depends on
        # day-h's own prediction (recursive/compounding error) — a simple
        # sqrt(h) widening heuristic, not a rigorous prediction interval.
        band = 1.645 * residual_std * np.sqrt(h)
        risk_flag = bool(balance < RISK_BALANCE_THRESHOLD or (balance - band) < 0)

        rows.append({
            "tenant_id": TENANT_ID,
            "forecast_date": forecast_date.date().isoformat(),
            "predicted_balance": round(balance, 2),
            "confidence_lower": round(balance - band, 2),
            "confidence_upper": round(balance + band, 2),
            "risk_flag": risk_flag,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Output — Delta table via Spark if available, else local CSV
# ---------------------------------------------------------------------------
def write_forecast(forecast_df: pd.DataFrame):
    spark = get_spark()
    if spark is not None:
        table = f"{CATALOG}.{SCHEMA}.cash_forecast"
        sdf = spark.createDataFrame(forecast_df)
        sdf.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
        print(f"Wrote {len(forecast_df)} rows to {table}")
    else:
        os.makedirs(os.path.dirname(LOCAL_OUTPUT_CSV), exist_ok=True)
        forecast_df.to_csv(LOCAL_OUTPUT_CSV, index=False)
        print(f"Wrote {len(forecast_df)} rows to {LOCAL_OUTPUT_CSV}")


def main():
    history = load_daily_cash_position()
    print(f"Loaded {len(history)} days of history "
          f"({history['position_date'].min().date()} to {history['position_date'].max().date()})")

    X, y = make_training_set(history)
    print(f"Training set: {len(X)} rows, {len(FEATURE_COLS)} features")

    use_sagemaker = os.environ.get("LEDGERLY_USE_SAGEMAKER", "").lower() == "true"
    if use_sagemaker:
        print("LEDGERLY_USE_SAGEMAKER=true — using SageMaker training path (AWS cost applies).")
        model, residual_std = train_sagemaker(X, y)
    else:
        print("Using local scikit-learn fallback (default, no AWS cost).")
        model, residual_std = train_local_fallback(X, y)

    forecast_df = recursive_forecast(model, residual_std, history, FORECAST_HORIZON_DAYS)

    at_risk_days = int(forecast_df["risk_flag"].sum())
    print(f"\nForecast summary ({FORECAST_HORIZON_DAYS}-day horizon):")
    print(f"  Day 30 predicted balance: ${forecast_df.iloc[29]['predicted_balance']:,.2f}")
    print(f"  Day 60 predicted balance: ${forecast_df.iloc[59]['predicted_balance']:,.2f}")
    print(f"  Day 90 predicted balance: ${forecast_df.iloc[89]['predicted_balance']:,.2f}")
    print(f"  Days flagged at-risk: {at_risk_days} / {FORECAST_HORIZON_DAYS}")

    write_forecast(forecast_df)


if __name__ == "__main__":
    main()
