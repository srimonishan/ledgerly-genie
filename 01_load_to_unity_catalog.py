# Databricks notebook source
# MAGIC %md
# MAGIC # Ledgerly — Unity Catalog Loader (Bronze → Silver → Gold)
# MAGIC
# MAGIC Phase 0: single synthetic tenant. Loads the 5 CSVs produced by
# MAGIC `generate_data.py` into a medallion-architecture set of Delta tables
# MAGIC under `workspace.ledgerly` (catalog.schema — adjust the widgets below if
# MAGIC your Free Edition workspace uses a different default catalog name).
# MAGIC
# MAGIC **Design note for future phases:** every table carries a `tenant_id`
# MAGIC column, hardcoded to one value for now via the `tenant_id` widget. Phase 1
# MAGIC turns this into real Unity Catalog row-level security (row filters) — that
# MAGIC is additive on top of this schema, not a rewrite. Do not remove the column
# MAGIC or its presence in every table even though there's only one tenant today.
# MAGIC
# MAGIC **Before running:** upload the 5 CSVs from `data/` to a Unity Catalog
# MAGIC Volume (see README.md for exact steps) and point the `raw_volume_path`
# MAGIC widget at that folder.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog name")
dbutils.widgets.text("schema", "ledgerly", "Schema name")
dbutils.widgets.text("tenant_id", "demo_business_001", "Tenant ID (Phase 0: single value)")
dbutils.widgets.text("raw_volume_path", "/Volumes/workspace/ledgerly/raw_data", "Path to uploaded CSVs")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
TENANT_ID = dbutils.widgets.get("tenant_id")
RAW_PATH = dbutils.widgets.get("raw_volume_path")

print(f"catalog={CATALOG} schema={SCHEMA} tenant_id={TENANT_ID} raw_path={RAW_PATH}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Catalog / schema / volume setup

# COMMAND ----------

spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.raw_data")
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Bronze — raw ingestion, explicit schemas, ingestion metadata
# MAGIC
# MAGIC Explicit `StructType` schemas (not `inferSchema`) so bad/malformed rows in
# MAGIC future real-POS ingestion (Phase 1+) fail loudly instead of silently
# MAGIC mistyping a column.

# COMMAND ----------

from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, DoubleType, BooleanType, DateType
)
from pyspark.sql import functions as F

def read_csv(name: str, schema: StructType):
    path = f"{RAW_PATH}/{name}.csv"
    return (
        spark.read.format("csv")
        .option("header", "true")
        .schema(schema)
        .load(path)
        .withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_source_file", F.lit(f"{name}.csv"))
    )

products_schema = StructType([
    StructField("tenant_id", StringType()),
    StructField("product_id", StringType()),
    StructField("product_name", StringType()),
    StructField("category", StringType()),
    StructField("unit_cost", DoubleType()),
    StructField("unit_price", DoubleType()),
    StructField("current_stock", IntegerType()),
    StructField("reorder_point", IntegerType()),
    StructField("reorder_quantity", IntegerType()),
])

customers_schema = StructType([
    StructField("tenant_id", StringType()),
    StructField("customer_id", StringType()),
    StructField("customer_name", StringType()),
    StructField("segment", StringType()),
    StructField("signup_date", StringType()),
    StructField("payment_terms_days", IntegerType()),
    StructField("chronic_late_payer", BooleanType()),
])

sales_schema = StructType([
    StructField("tenant_id", StringType()),
    StructField("transaction_id", StringType()),
    StructField("transaction_date", StringType()),
    StructField("customer_id", StringType()),
    StructField("product_id", StringType()),
    StructField("quantity", IntegerType()),
    StructField("unit_price", DoubleType()),
    StructField("total_amount", DoubleType()),
    StructField("payment_method", StringType()),
    StructField("channel", StringType()),
])

ledger_schema = StructType([
    StructField("tenant_id", StringType()),
    StructField("ledger_id", StringType()),
    StructField("entry_date", StringType()),
    StructField("flow_type", StringType()),
    StructField("category", StringType()),
    StructField("amount", DoubleType()),
    StructField("description", StringType()),
    StructField("customer_id", StringType()),
])

position_schema = StructType([
    StructField("tenant_id", StringType()),
    StructField("position_date", StringType()),
    StructField("opening_balance", DoubleType()),
    StructField("total_inflows", DoubleType()),
    StructField("total_outflows", DoubleType()),
    StructField("closing_balance", DoubleType()),
])

bronze_products = read_csv("products", products_schema)
bronze_customers = read_csv("customers", customers_schema)
bronze_sales = read_csv("sales_transactions", sales_schema)
bronze_ledger = read_csv("cash_ledger", ledger_schema)
bronze_position = read_csv("daily_cash_position", position_schema)

for name, df in [
    ("bronze_products", bronze_products),
    ("bronze_customers", bronze_customers),
    ("bronze_sales_transactions", bronze_sales),
    ("bronze_cash_ledger", bronze_ledger),
    ("bronze_daily_cash_position", bronze_position),
]:
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    print(f"wrote {CATALOG}.{SCHEMA}.{name}: {df.count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Silver — typed, cleaned, deduplicated
# MAGIC
# MAGIC Casts date strings to real `DATE` types, drops rows missing required
# MAGIC keys, and de-dupes on natural primary keys (idempotent re-runs).

# COMMAND ----------

silver_products = (
    spark.table("bronze_products")
    .dropna(subset=["tenant_id", "product_id"])
    .dropDuplicates(["tenant_id", "product_id"])
    .select("tenant_id", "product_id", "product_name", "category",
            "unit_cost", "unit_price", "current_stock", "reorder_point", "reorder_quantity")
)

silver_customers = (
    spark.table("bronze_customers")
    .dropna(subset=["tenant_id", "customer_id"])
    .dropDuplicates(["tenant_id", "customer_id"])
    .withColumn("signup_date", F.to_date("signup_date"))
    .select("tenant_id", "customer_id", "customer_name", "segment", "signup_date",
            "payment_terms_days", "chronic_late_payer")
)

silver_sales = (
    spark.table("bronze_sales_transactions")
    .dropna(subset=["tenant_id", "transaction_id", "transaction_date"])
    .dropDuplicates(["tenant_id", "transaction_id"])
    .withColumn("transaction_date", F.to_date("transaction_date"))
    .select("tenant_id", "transaction_id", "transaction_date", "customer_id", "product_id",
            "quantity", "unit_price", "total_amount", "payment_method", "channel")
)

silver_ledger = (
    spark.table("bronze_cash_ledger")
    .dropna(subset=["tenant_id", "ledger_id", "entry_date"])
    .dropDuplicates(["tenant_id", "ledger_id"])
    .withColumn("entry_date", F.to_date("entry_date"))
    .select("tenant_id", "ledger_id", "entry_date", "flow_type", "category",
            "amount", "description", "customer_id")
)

silver_position = (
    spark.table("bronze_daily_cash_position")
    .dropna(subset=["tenant_id", "position_date"])
    .dropDuplicates(["tenant_id", "position_date"])
    .withColumn("position_date", F.to_date("position_date"))
    .select("tenant_id", "position_date", "opening_balance", "total_inflows",
            "total_outflows", "closing_balance")
)

for name, df in [
    ("silver_products", silver_products),
    ("silver_customers", silver_customers),
    ("silver_sales_transactions", silver_sales),
    ("silver_cash_ledger", silver_ledger),
    ("silver_daily_cash_position", silver_position),
]:
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    print(f"wrote {CATALOG}.{SCHEMA}.{name}: {df.count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Gold — business-ready tables Genie will actually query
# MAGIC
# MAGIC These encode the business logic once, in SQL, rather than leaving Genie
# MAGIC to re-derive it per question:
# MAGIC - `gold_daily_sales_summary` — daily revenue split by channel
# MAGIC - `gold_customer_receivables` — per-customer AR aging + an empirically
# MAGIC   computed "at-risk" flag (avg days-to-pay vs. their stated terms)
# MAGIC - `gold_product_performance` — units/revenue per product + a computed
# MAGIC   reorder flag (current_stock <= reorder_point)
# MAGIC - `gold_cash_position` — daily balance plus rolling 7/30-day burn rate
# MAGIC   and a simple cash-runway-in-days estimate

# COMMAND ----------

# gold_daily_sales_summary
spark.sql(f"""
CREATE OR REPLACE TABLE gold_daily_sales_summary AS
SELECT
    tenant_id,
    transaction_date,
    channel,
    COUNT(*) AS transaction_count,
    SUM(total_amount) AS total_revenue
FROM silver_sales_transactions
GROUP BY tenant_id, transaction_date, channel
""")
print("wrote gold_daily_sales_summary")

# COMMAND ----------

# gold_customer_receivables — ties each wholesale invoice (a sales_transactions
# row on the 'wholesale' channel) to its matching payment in cash_ledger by
# customer + amount, then aggregates to a per-customer AR / risk picture.
spark.sql(f"""
CREATE OR REPLACE TABLE gold_customer_receivables AS
WITH invoices AS (
    SELECT tenant_id, customer_id, transaction_date AS invoice_date, total_amount
    FROM silver_sales_transactions
    WHERE channel = 'wholesale'
),
payments AS (
    SELECT tenant_id, customer_id, entry_date AS payment_date, amount
    FROM silver_cash_ledger
    WHERE category = 'wholesale_invoice_payment'
),
matched AS (
    SELECT
        i.tenant_id, i.customer_id, i.invoice_date, i.total_amount,
        p.payment_date,
        DATEDIFF(p.payment_date, i.invoice_date) AS days_to_pay
    FROM invoices i
    LEFT JOIN payments p
        ON i.tenant_id = p.tenant_id
        AND i.customer_id = p.customer_id
        AND i.total_amount = p.amount
)
SELECT
    m.tenant_id,
    m.customer_id,
    c.customer_name,
    c.payment_terms_days,
    c.chronic_late_payer AS labeled_chronic_late_payer,
    COUNT(*) AS invoice_count,
    SUM(CASE WHEN m.payment_date IS NULL THEN m.total_amount ELSE 0 END) AS outstanding_ar,
    ROUND(AVG(m.days_to_pay), 1) AS avg_days_to_pay,
    ROUND(AVG(m.days_to_pay) - c.payment_terms_days, 1) AS avg_days_past_terms,
    -- empirically at-risk: pays >15 days past their own stated terms on average
    (AVG(m.days_to_pay) - c.payment_terms_days) > 15 AS is_at_risk_customer,
    MAX(m.invoice_date) AS last_invoice_date
FROM matched m
JOIN silver_customers c ON m.tenant_id = c.tenant_id AND m.customer_id = c.customer_id
GROUP BY m.tenant_id, m.customer_id, c.customer_name, c.payment_terms_days, c.chronic_late_payer
""")
print("wrote gold_customer_receivables")

# COMMAND ----------

# gold_product_performance
spark.sql(f"""
CREATE OR REPLACE TABLE gold_product_performance AS
SELECT
    p.tenant_id,
    p.product_id,
    p.product_name,
    p.category,
    p.unit_cost,
    p.unit_price,
    p.current_stock,
    p.reorder_point,
    p.reorder_quantity,
    COALESCE(s.units_sold, 0) AS units_sold,
    COALESCE(s.total_revenue, 0.0) AS total_revenue,
    (p.current_stock <= p.reorder_point) AS needs_reorder
FROM silver_products p
LEFT JOIN (
    SELECT tenant_id, product_id, SUM(quantity) AS units_sold, SUM(total_amount) AS total_revenue
    FROM silver_sales_transactions
    GROUP BY tenant_id, product_id
) s ON p.tenant_id = s.tenant_id AND p.product_id = s.product_id
""")
print("wrote gold_product_performance")

# COMMAND ----------

# gold_cash_position — daily balance + rolling burn rate + naive runway estimate.
# "Cash runway" here = closing_balance / avg_daily_net_burn_30d, capped/nulled
# when burn is non-positive (business is cash-flow-positive -> no runway risk).
spark.sql(f"""
CREATE OR REPLACE TABLE gold_cash_position AS
SELECT
    tenant_id,
    position_date,
    opening_balance,
    total_inflows,
    total_outflows,
    closing_balance,
    AVG(total_outflows - total_inflows) OVER (
        PARTITION BY tenant_id ORDER BY position_date
        ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
    ) AS avg_daily_net_burn_30d,
    CASE
        WHEN AVG(total_outflows - total_inflows) OVER (
            PARTITION BY tenant_id ORDER BY position_date
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) > 0
        THEN ROUND(closing_balance / AVG(total_outflows - total_inflows) OVER (
            PARTITION BY tenant_id ORDER BY position_date
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ), 1)
        ELSE NULL
    END AS estimated_cash_runway_days
FROM silver_daily_cash_position
""")
print("wrote gold_cash_position")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Sanity check — confirm the crunch story survived the load

# COMMAND ----------

display(spark.sql(f"""
SELECT position_date, closing_balance, estimated_cash_runway_days
FROM gold_cash_position
ORDER BY closing_balance ASC
LIMIT 5
"""))

display(spark.sql(f"""
SELECT customer_name, payment_terms_days, avg_days_to_pay, avg_days_past_terms, is_at_risk_customer, outstanding_ar
FROM gold_customer_receivables
ORDER BY avg_days_past_terms DESC
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC Tables now live at `{CATALOG}.{SCHEMA}.{{bronze,silver,gold}}_*`.
# MAGIC Next: `genie_agent_setup.md` to configure the Genie space over the
# MAGIC `gold_*` tables, then `02_train_cash_forecast_model.py`.
