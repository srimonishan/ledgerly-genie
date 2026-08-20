"""
Ledgerly — synthetic data generator (Phase 0)
================================================
Generates 5 CSVs for one synthetic single-tenant business:
"Brew & Bloom" — a hybrid café + retail goods shop (coffee/bakery for
walk-in retail customers, plus wholesale bulk-bean accounts for a handful
of corporate/other-café customers on net payment terms).

Tables produced (all under data/):
  - products.csv               (inventory / catalog)
  - customers.csv              (walk-in aggregate + named wholesale accounts)
  - sales_transactions.csv     (every sale, retail + wholesale)
  - cash_ledger.csv            (every cash in/out event: sales receipts,
                                 wholesale invoice payments, COGS restocks,
                                 rent, payroll, utilities, loan payments)
  - daily_cash_position.csv    (running daily opening/closing balance)

Every table carries a `tenant_id` column (hardcoded to one value for Phase 0)
so Phase 1 multi-tenancy is additive, not a rewrite.

Story deliberately baked into the data (do not remove when tuning):
  1. Weekly + monthly seasonality (weekend bump, summer iced-drink bump,
     back-to-school bump, holiday peak, January/February slump) plus a
     mild year-over-year growth trend.
  2. Two chronic slow-paying wholesale customers who routinely pay
     50-100+ days late against net-30 terms.
  3. One near-cash-crunch period: a large pre-holiday inventory prepay
     lands in the same window as multiple late wholesale payments,
     driving the cash balance down to a thin buffer for ~2 weeks before
     recovering on holiday retail volume.

Run: python generate_data.py
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEED = 42
TENANT_ID = "demo_business_001"          # Phase 0: single hardcoded tenant
BUSINESS_NAME = "Brew & Bloom"            # synthetic — never a real business

START_DATE = date(2024, 8, 20)
END_DATE = date(2026, 8, 19)              # 2 full years of daily history
OUT_DIR = "data"

STARTING_CASH_BALANCE = 23_000.00

rng = np.random.default_rng(SEED)


def date_range(start, end):
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


ALL_DATES = date_range(START_DATE, END_DATE)


# ---------------------------------------------------------------------------
# Seasonality helpers (shared by sales + used implicitly by cash flow)
# ---------------------------------------------------------------------------
def day_of_week_multiplier(d: date) -> float:
    # Mon=0 ... Sun=6. Café/retail busier Thu-Sun.
    weights = {0: 0.80, 1: 0.82, 2: 0.88, 3: 1.00, 4: 1.20, 5: 1.35, 6: 1.15}
    return weights[d.weekday()]


def month_seasonality_multiplier(d: date) -> float:
    # Jan/Feb slump, spring ramp, summer iced-drink bump, back-to-school
    # bump in Sep, steady Oct, holiday peak Nov-Dec.
    weights = {
        1: 0.75, 2: 0.78, 3: 0.90, 4: 0.98, 5: 1.02, 6: 1.15,
        7: 1.20, 8: 1.10, 9: 1.12, 10: 1.05, 11: 1.25, 12: 1.40,
    }
    return weights[d.month]


def growth_multiplier(d: date) -> float:
    # ~18% YoY growth, ramped smoothly across the 2-year window.
    days_elapsed = (d - START_DATE).days
    total_days = (END_DATE - START_DATE).days
    return 1.0 + 0.18 * (days_elapsed / total_days)


def is_near_crunch_window(d: date) -> bool:
    # The engineered near-cash-crunch: pre-holiday prepay + late wholesale
    # payments overlapping in the second year, early-to-mid October.
    return date(2025, 10, 6) <= d <= date(2025, 10, 24)


# ---------------------------------------------------------------------------
# 1. Products (inventory catalog)
# ---------------------------------------------------------------------------
def generate_products() -> pd.DataFrame:
    catalog = [
        # (name, category, unit_cost, unit_price, starting_stock, reorder_point, reorder_qty)
        ("Drip Coffee (12oz)", "Beverages", 0.55, 3.25, 500, 100, 400),
        ("Cappuccino (12oz)", "Beverages", 0.80, 4.50, 400, 80, 300),
        ("Latte (12oz)", "Beverages", 0.85, 4.75, 450, 90, 350),
        ("Cold Brew (16oz)", "Beverages", 0.70, 4.95, 350, 70, 300),
        ("Iced Matcha Latte", "Beverages", 1.10, 5.25, 200, 40, 200),
        ("Hot Chocolate", "Beverages", 0.60, 3.75, 150, 30, 150),
        ("Croissant", "Bakery", 0.90, 3.50, 200, 40, 200),
        ("Blueberry Muffin", "Bakery", 0.75, 3.25, 200, 40, 200),
        ("Banana Bread Slice", "Bakery", 0.65, 3.00, 150, 30, 150),
        ("Avocado Toast", "Bakery", 1.80, 7.50, 120, 25, 120),
        ("Breakfast Sandwich", "Bakery", 1.95, 6.95, 150, 30, 150),
        ("Ceramic Mug - Brew & Bloom", "Merchandise", 3.20, 14.00, 120, 20, 100),
        ("Reusable Tumbler", "Merchandise", 4.50, 18.00, 90, 15, 80),
        ("Tote Bag", "Merchandise", 2.80, 12.00, 100, 20, 100),
        ("Whole Bean Coffee 12oz Retail Bag", "Merchandise", 4.00, 15.00, 200, 40, 200),
        ("Gift Card $25", "Merchandise", 0.00, 25.00, 999999, 0, 0),
        ("Wholesale Beans 5lb Bag", "Wholesale", 14.00, 32.00, 300, 60, 300),
        ("Wholesale Beans 25lb Bag", "Wholesale", 60.00, 135.00, 80, 15, 100),
        ("Wholesale Syrup Case (6ct)", "Wholesale", 22.00, 48.00, 60, 10, 60),
        ("Wholesale Cup Sleeves (1000ct)", "Wholesale", 18.00, 35.00, 40, 8, 40),
    ]
    rows = []
    for i, (name, category, cost, price, stock, reorder_pt, reorder_qty) in enumerate(catalog, start=1):
        rows.append({
            "tenant_id": TENANT_ID,
            "product_id": f"P{i:03d}",
            "product_name": name,
            "category": category,
            "unit_cost": round(cost, 2),
            "unit_price": round(price, 2),
            "current_stock": stock,
            "reorder_point": reorder_pt,
            "reorder_quantity": reorder_qty,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Customers
# ---------------------------------------------------------------------------
def generate_customers() -> pd.DataFrame:
    rows = [
        {
            "tenant_id": TENANT_ID, "customer_id": "C000", "customer_name": "Walk-in Retail",
            "segment": "retail", "signup_date": START_DATE.isoformat(),
            "payment_terms_days": 0, "chronic_late_payer": False,
        },
    ]
    wholesale = [
        # (name, signup_date, payment_terms_days, chronic_late_payer)
        ("Riverside Office Park Cafeteria", date(2024, 9, 3), 30, False),
        ("Maple & Co. Coworking", date(2024, 10, 15), 30, True),   # slow payer #1
        ("Oakview Elementary PTA", date(2024, 11, 1), 15, False),
        ("Sunrise Diner", date(2025, 1, 10), 30, False),
        ("Third Street Bookshop Cafe", date(2025, 2, 20), 30, True),  # slow payer #2
        ("Harbor View Hotel", date(2025, 3, 5), 45, False),
        ("Downtown Fitness Studio", date(2025, 4, 12), 30, False),
        ("Green Valley Med Center Cafeteria", date(2025, 5, 22), 30, False),
        ("Lakeside University Faculty Club", date(2025, 6, 30), 45, False),
        ("Northgate Bakery Supply Co-op", date(2025, 9, 2), 30, False),
    ]
    for i, (name, signup, terms, slow) in enumerate(wholesale, start=1):
        rows.append({
            "tenant_id": TENANT_ID, "customer_id": f"C{i:03d}", "customer_name": name,
            "segment": "wholesale", "signup_date": signup.isoformat(),
            "payment_terms_days": terms, "chronic_late_payer": slow,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Sales transactions (retail daily volume + wholesale monthly invoices)
# ---------------------------------------------------------------------------
def generate_sales_transactions(products: pd.DataFrame, customers: pd.DataFrame):
    retail_products = products[products["category"] != "Wholesale"].reset_index(drop=True)
    wholesale_products = products[products["category"] == "Wholesale"].reset_index(drop=True)
    wholesale_customers = customers[customers["segment"] == "wholesale"].reset_index(drop=True)

    tx_rows = []
    tx_id = 1
    payment_methods = ["card", "cash", "mobile_wallet"]
    payment_weights = [0.62, 0.20, 0.18]

    # --- Retail: many small transactions per day, seasonally driven ---
    for d in ALL_DATES:
        base_daily_txns = 45
        mult = day_of_week_multiplier(d) * month_seasonality_multiplier(d) * growth_multiplier(d)
        n_txns = max(5, int(rng.poisson(base_daily_txns * mult)))

        for _ in range(n_txns):
            prod = retail_products.iloc[rng.integers(0, len(retail_products))]
            qty = int(rng.choice([1, 1, 1, 2, 2, 3], p=[0.45, 0.2, 0.15, 0.1, 0.06, 0.04]))
            unit_price = prod["unit_price"]
            total = round(unit_price * qty, 2)
            tx_rows.append({
                "tenant_id": TENANT_ID,
                "transaction_id": f"T{tx_id:07d}",
                "transaction_date": d.isoformat(),
                "customer_id": "C000",
                "product_id": prod["product_id"],
                "quantity": qty,
                "unit_price": unit_price,
                "total_amount": total,
                "payment_method": rng.choice(payment_methods, p=payment_weights),
                "channel": "retail",
            })
            tx_id += 1

    # --- Wholesale: roughly monthly bulk orders per active wholesale customer.
    #     Kept deliberately modest relative to retail (~25-30% of total
    #     revenue) — wholesale is a side channel here, not the main business. ---
    # index order matches the catalog: [5lb bag, 25lb bag, syrup case, cup sleeves]
    wholesale_product_weights = [0.50, 0.12, 0.22, 0.16]
    for _, cust in wholesale_customers.iterrows():
        signup = date.fromisoformat(cust["signup_date"])
        d = signup + timedelta(days=int(rng.integers(3, 15)))
        while d <= END_DATE:
            n_lines = int(rng.integers(1, 3))
            order_mult = month_seasonality_multiplier(d) * growth_multiplier(d)
            for _ in range(n_lines):
                prod = wholesale_products.iloc[
                    rng.choice(len(wholesale_products), p=wholesale_product_weights)
                ]
                qty = int(rng.integers(3, 14) * order_mult)
                qty = max(qty, 2)
                unit_price = prod["unit_price"]
                total = round(unit_price * qty, 2)
                tx_rows.append({
                    "tenant_id": TENANT_ID,
                    "transaction_id": f"T{tx_id:07d}",
                    "transaction_date": d.isoformat(),
                    "customer_id": cust["customer_id"],
                    "product_id": prod["product_id"],
                    "quantity": qty,
                    "unit_price": unit_price,
                    "total_amount": total,
                    "payment_method": "invoice",
                    "channel": "wholesale",
                })
                tx_id += 1
            # next order in ~28-40 days
            d = d + timedelta(days=int(rng.integers(26, 42)))

    tx_df = pd.DataFrame(tx_rows).sort_values("transaction_date").reset_index(drop=True)
    return tx_df


# ---------------------------------------------------------------------------
# 4. Cash ledger (every cash in/out event) + 5. Daily cash position
# ---------------------------------------------------------------------------
def generate_cash_flow(tx_df: pd.DataFrame, customers: pd.DataFrame):
    ledger_rows = []
    ledger_id = 1

    def add_ledger(d, flow_type, category, amount, description, customer_id=None):
        nonlocal ledger_id
        ledger_rows.append({
            "tenant_id": TENANT_ID,
            "ledger_id": f"L{ledger_id:07d}",
            "entry_date": d.isoformat(),
            "flow_type": flow_type,          # inflow | outflow
            "category": category,
            "amount": round(amount, 2),
            "description": description,
            "customer_id": customer_id,
        })
        ledger_id += 1

    customers_idx = customers.set_index("customer_id")

    # --- Retail sales settle same-day (card/cash/wallet all near-instant) ---
    retail_tx = tx_df[tx_df["channel"] == "retail"]
    daily_retail = retail_tx.groupby("transaction_date")["total_amount"].sum()
    for d_str, amount in daily_retail.items():
        d = date.fromisoformat(d_str)
        add_ledger(d, "inflow", "retail_sales", amount, "Daily retail sales receipts")

    # --- Wholesale invoices: paid on a delay driven by payment terms +
    #     chronic-late-payer behavior; the engineered crunch window pushes
    #     two invoices even later so multiple late payments overlap. ---
    wholesale_tx = tx_df[tx_df["channel"] == "wholesale"]
    invoice_groups = wholesale_tx.groupby(["transaction_date", "customer_id"])["total_amount"].sum()
    for (d_str, cust_id), amount in invoice_groups.items():
        d = date.fromisoformat(d_str)
        cust = customers_idx.loc[cust_id]
        terms = int(cust["payment_terms_days"])
        chronic_late = bool(cust["chronic_late_payer"])

        if chronic_late:
            delay = int(rng.integers(terms + 30, terms + 90))
        else:
            # most pay near on-time, a modest right-tail of lateness
            delay = int(max(0, rng.normal(terms - 3, 6)))

        # Engineer the crunch: any wholesale invoice issued in the run-up to
        # the crunch window gets pushed to land (or extend) inside it.
        pay_date = d + timedelta(days=delay)
        if date(2025, 9, 15) <= d <= date(2025, 10, 15) and chronic_late:
            pay_date = max(pay_date, date(2025, 10, 20) + timedelta(days=int(rng.integers(0, 10))))

        if pay_date > END_DATE:
            continue  # invoice still outstanding at end of history window — realistic, excluded from cash-in
        add_ledger(pay_date, "inflow", "wholesale_invoice_payment", amount,
                   f"Invoice payment from {cust['customer_name']}", customer_id=cust_id)

    # --- COGS / inventory restocking: biweekly baseline scaled to roughly
    #     track cost-of-goods on the revenue actually being generated, plus
    #     a large pre-holiday prepay that helps trigger the crunch ---
    # offset from payroll's cadence (below) so the two biweekly outflows
    # don't routinely stack on the same day and manufacture false dips
    d = START_DATE + timedelta(days=7)
    while d <= END_DATE:
        base_restock = 2400 * month_seasonality_multiplier(d) * growth_multiplier(d)
        add_ledger(d, "outflow", "inventory_restock", base_restock * (0.9 + 0.2 * rng.random()),
                   "Routine inventory / COGS restock")
        d += timedelta(days=14)

    # Large pre-holiday bulk prepay landing right before the crunch window —
    # sized to meaningfully dent the operating cash buffer, not just be noise
    add_ledger(date(2025, 10, 8), "outflow", "inventory_restock", 23_000.00,
               "Bulk pre-holiday inventory prepay (beans, merch, packaging)")

    # --- Fixed costs: rent (monthly), payroll (biweekly), utilities (monthly),
    #     loan payment (monthly) ---
    d = date(START_DATE.year, START_DATE.month, 1)
    while d <= END_DATE:
        if d >= START_DATE:
            add_ledger(d, "outflow", "rent", 3200.00, "Monthly rent")
            add_ledger(d, "outflow", "utilities", 380.00 * (1.15 if d.month in (7, 8, 1, 12) else 1.0),
                       "Monthly utilities")
            add_ledger(d, "outflow", "loan_payment", 650.00, "Equipment loan installment")
        # advance to first of next month
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)

    # offset a few days off the 1st-of-month so payroll doesn't routinely
    # stack with rent/utilities/loan on the same day
    d = START_DATE + timedelta(days=4)
    while d <= END_DATE:
        payroll_base = 3800.00 * growth_multiplier(d)
        add_ledger(d, "outflow", "payroll", payroll_base, "Biweekly payroll")
        d += timedelta(days=14)

    ledger_df = pd.DataFrame(ledger_rows).sort_values("entry_date").reset_index(drop=True)

    # --- Daily cash position: running balance from the ledger ---
    daily = ledger_df.copy()
    daily["signed_amount"] = np.where(daily["flow_type"] == "inflow", daily["amount"], -daily["amount"])
    daily_totals = daily.groupby("entry_date")["signed_amount"].sum()
    daily_inflow = daily[daily["flow_type"] == "inflow"].groupby("entry_date")["amount"].sum()
    daily_outflow = daily[daily["flow_type"] == "outflow"].groupby("entry_date")["amount"].sum()

    position_rows = []
    balance = STARTING_CASH_BALANCE
    for d in ALL_DATES:
        d_str = d.isoformat()
        opening = balance
        net = daily_totals.get(d_str, 0.0)
        inflow = daily_inflow.get(d_str, 0.0)
        outflow = daily_outflow.get(d_str, 0.0)
        closing = opening + net
        position_rows.append({
            "tenant_id": TENANT_ID,
            "position_date": d_str,
            "opening_balance": round(opening, 2),
            "total_inflows": round(inflow, 2),
            "total_outflows": round(outflow, 2),
            "closing_balance": round(closing, 2),
        })
        balance = closing

    position_df = pd.DataFrame(position_rows)
    return ledger_df, position_df


# ---------------------------------------------------------------------------
# Sanity check printout
# ---------------------------------------------------------------------------
def sanity_check(products, customers, tx_df, ledger_df, position_df):
    print(f"\n=== Ledgerly synthetic data sanity check — {BUSINESS_NAME} ===")
    print(f"tenant_id: {TENANT_ID}")
    print(f"Date range: {START_DATE} to {END_DATE} ({len(ALL_DATES)} days)")
    print(f"\nproducts.csv: {len(products)} rows")
    print(f"customers.csv: {len(customers)} rows "
          f"({(customers['segment']=='wholesale').sum()} wholesale, "
          f"{(customers['chronic_late_payer']==True).sum()} chronic late payers)")
    print(f"sales_transactions.csv: {len(tx_df)} rows "
          f"({(tx_df['channel']=='retail').sum()} retail, {(tx_df['channel']=='wholesale').sum()} wholesale)")
    print(f"  total retail revenue:    ${tx_df[tx_df.channel=='retail'].total_amount.sum():,.2f}")
    print(f"  total wholesale revenue: ${tx_df[tx_df.channel=='wholesale'].total_amount.sum():,.2f}")
    print(f"cash_ledger.csv: {len(ledger_df)} rows")
    print(f"daily_cash_position.csv: {len(position_df)} rows")

    min_row = position_df.loc[position_df["closing_balance"].idxmin()]
    max_row = position_df.loc[position_df["closing_balance"].idxmax()]
    print(f"\nMin closing balance: ${min_row['closing_balance']:,.2f} on {min_row['position_date']}")
    print(f"Max closing balance: ${max_row['closing_balance']:,.2f} on {max_row['position_date']}")

    crunch = position_df[
        (position_df["position_date"] >= "2025-09-20") & (position_df["position_date"] <= "2025-11-05")
    ]
    print(f"\nEngineered near-cash-crunch window (2025-09-20 to 2025-11-05):")
    print(f"  min balance in window: ${crunch['closing_balance'].min():,.2f} "
          f"on {crunch.loc[crunch['closing_balance'].idxmin(), 'position_date']}")
    below_2k = (position_df["closing_balance"] < 2000).sum()
    negative = (position_df["closing_balance"] < 0).sum()
    print(f"  days overall with balance < $2,000: {below_2k}")
    print(f"  days overall with negative balance: {negative}")

    yoy_summer1 = tx_df[(tx_df.transaction_date >= "2024-08-20") & (tx_df.transaction_date <= "2025-08-19")]
    yoy_summer2 = tx_df[(tx_df.transaction_date >= "2025-08-20") & (tx_df.transaction_date <= "2026-08-19")]
    print(f"\nYear 1 total revenue: ${yoy_summer1.total_amount.sum():,.2f}")
    print(f"Year 2 total revenue: ${yoy_summer2.total_amount.sum():,.2f}  (growth trend check)")
    print("=== end sanity check ===\n")


def main():
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Generating products...")
    products = generate_products()
    print("Generating customers...")
    customers = generate_customers()
    print("Generating sales transactions (this is the slow step)...")
    tx_df = generate_sales_transactions(products, customers)
    print("Generating cash ledger + daily cash position...")
    ledger_df, position_df = generate_cash_flow(tx_df, customers)

    products.to_csv(f"{OUT_DIR}/products.csv", index=False)
    customers.to_csv(f"{OUT_DIR}/customers.csv", index=False)
    tx_df.to_csv(f"{OUT_DIR}/sales_transactions.csv", index=False)
    ledger_df.to_csv(f"{OUT_DIR}/cash_ledger.csv", index=False)
    position_df.to_csv(f"{OUT_DIR}/daily_cash_position.csv", index=False)

    sanity_check(products, customers, tx_df, ledger_df, position_df)
    print(f"Wrote 5 CSVs to {OUT_DIR}/")


if __name__ == "__main__":
    main()
