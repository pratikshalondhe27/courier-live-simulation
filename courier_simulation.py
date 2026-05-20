# courier_simulation.py
# Runs every 5 mins via GitHub Actions to simulate live courier operations

import random
from datetime import datetime, timedelta, timezone
from faker import Faker
from supabase import create_client
from dotenv import load_dotenv
import os
import numpy as np

# =========================================================
# LOAD ENV
# =========================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

fake = Faker("en_IN")

# =========================================================
# MASTER DATA
# =========================================================

locations = [
    ("LOC001", "Mumbai",    "Maharashtra"),
    ("LOC002", "Pune",      "Maharashtra"),
    ("LOC003", "Delhi",     "Delhi"),
    ("LOC004", "Bangalore", "Karnataka"),
    ("LOC005", "Hyderabad", "Telangana"),
    ("LOC006", "Chennai",   "Tamil Nadu"),
    ("LOC007", "Ahmedabad", "Gujarat"),
    ("LOC008", "Kolkata",   "West Bengal"),
    ("LOC009", "Jaipur",    "Rajasthan"),
    ("LOC010", "Lucknow",   "Uttar Pradesh"),
]

service_sla = {
    1: 5,   # Standard
    2: 3,   # Express
    3: 1,   # Same Day
    4: 2,   # Overnight
}

route_distance = {
    ("Mumbai", "Pune"): 160,
    ("Mumbai", "Delhi"): 1400,
    ("Mumbai", "Bangalore"): 980,
    ("Delhi", "Bangalore"): 2150,
    ("Pune", "Hyderabad"): 560,
    ("Delhi", "Kolkata"): 1500,
    ("Chennai", "Bangalore"): 350,
}

# =========================================================
# INDIAN FESTIVAL / SEASONAL MULTIPLIER
# =========================================================
# GitHub Actions runs on cloud — your PC being OFF doesn't stop this!
# Multipliers stack on top of base volume + growth trend.
#
# Pattern for each festival:
#   PRE-SEASON  : +40–80% (gifting orders pile up before the day)
#   FESTIVAL DAY: +20–30% (last-mile deliveries, same-day orders)
#   POST-SEASON : -15–25% (hangover/slowdown after the rush)
# =========================================================

def get_seasonal_multiplier(date):
    """
    Returns a float multiplier based on Indian courier seasonality.
    1.0 = normal day, 1.5 = 50% more, 0.85 = 15% less.
    """
    m  = date.month
    d  = date.day

    # ----------------------------------------------------------
    # DIWALI SEASON (Oct 15 – Nov 5 approx, peak Indian courier)
    # ----------------------------------------------------------
    if (m == 10 and d >= 15) or (m == 11 and d <= 5):
        # Pre-Diwali surge: Oct 15–Oct 30 (gifting, online orders)
        if m == 10 and 15 <= d <= 30:
            return 1.80   # +80% — biggest spike of the year
        # Diwali days: Oct 31 – Nov 2
        elif (m == 10 and d >= 31) or (m == 11 and d <= 2):
            return 1.30   # +30% — same-day deliveries
        # Post-Diwali dip: Nov 3–5
        else:
            return 0.80   # -20% — quiet after festival

    # ----------------------------------------------------------
    # DUSSEHRA / NAVRATRI (Oct 1–14)
    # ----------------------------------------------------------
    elif m == 10 and 1 <= d <= 14:
        if d <= 9:       # Navratri garba shopping rush
            return 1.35  # +35%
        else:            # Dussehra day and after
            return 1.60  # +60% (Dussehra gifting + ecom sale)

    # ----------------------------------------------------------
    # RAKSHA BANDHAN (Aug 7-19 approx, varies by year)
    # Gift hampers, sweets, clothing deliveries
    # ----------------------------------------------------------
    elif m == 8 and 7 <= d <= 19:
        if 7 <= d <= 15:   # Pre-Rakhi rush
            return 1.50    # +50%
        else:              # Post-Rakhi slowdown
            return 0.85    # -15%

    # ----------------------------------------------------------
    # INDEPENDENCE DAY (Aug 15) — minor boost
    # ----------------------------------------------------------
    elif m == 8 and d == 15:
        return 1.25        # +25%

    # ----------------------------------------------------------
    # HOLI (Mar 10-18 approx) — gifting + return season
    # ----------------------------------------------------------
    elif m == 3 and 10 <= d <= 18:
        if d <= 14:
            return 1.40   # +40% pre-Holi
        else:
            return 0.82   # -18% post-Holi

    # ----------------------------------------------------------
    # NEW YEAR SEASON (Dec 26 – Jan 5)
    # ----------------------------------------------------------
    elif (m == 12 and d >= 26) or (m == 1 and d <= 5):
        return 1.30        # +30% festive gifting + returns

    # ----------------------------------------------------------
    # REPUBLIC DAY (Jan 26) — minor spike
    # ----------------------------------------------------------
    elif m == 1 and d == 26:
        return 1.20        # +20%

    # ----------------------------------------------------------
    # EID / RAMZAN SEASON (Mar-Apr, approx 10-day window)
    # Dates shift yearly — use March 25 – April 10 as proxy
    # ----------------------------------------------------------
    elif (m == 3 and d >= 25) or (m == 4 and d <= 10):
        return 1.45        # +45% (gifting, clothing, food hampers)

    # ----------------------------------------------------------
    # VALENTINES WEEK (Feb 7-14) — gifts, flowers
    # ----------------------------------------------------------
    elif m == 2 and 7 <= d <= 14:
        return 1.25        # +25%

    # ----------------------------------------------------------
    # SUMMER SLOWDOWN (May–June) — heat slows retail
    # ----------------------------------------------------------
    elif m in (5, 6):
        return 0.90        # -10% seasonal dip

    # ----------------------------------------------------------
    # MONSOON DIP (Jul–Aug early) — logistics challenges
    # ----------------------------------------------------------
    elif m == 7 or (m == 8 and d < 7):
        return 0.88        # -12% (roads flooded, delays)

    # ----------------------------------------------------------
    # NORMAL MONTHS
    # ----------------------------------------------------------
    else:
        return 1.0


# =========================================================
# FETCH EXISTING CUSTOMERS
# =========================================================

customer_response = supabase.table("dim_customer").select("*").execute()
customers = customer_response.data

# =========================================================
# 1. INSERT NEW SHIPMENTS
# =========================================================

def insert_new_shipments():
    # STATUS FLOW (6 stages for Power BI dashboard):
    # 3=Pending → 5=Arrived at Hub → 2=In Transit → 6=Out for Delivery → 1=Delivered / 4=Failed
    shipment_rows = []
    now_utc = datetime.now(timezone.utc)       # UTC (for scheduling logic)
    now     = now_utc + timedelta(hours=5, minutes=30)  # IST = UTC+5:30 (for storing)

    # --- IST time (GitHub Actions runs on UTC, India = UTC+5:30) ---
    ist_hour    = now.hour          # already IST
    ist_weekday = now.weekday()     # 0=Mon, 6=Sun

    # In India, Saturday is a working day for courier companies
    # Only Sunday is off-peak
    is_sunday   = (ist_weekday == 6)
    day         = now.day

    # --- Calibrated to match history: ~420 shipments/day on weekdays ---
    # 96 runs/day target avg: 4.4/run

    # Night cutoff (9pm–8am IST) — shop closed
    if ist_hour < 8 or ist_hour >= 21:
        base_min, base_max = 0, 1        # avg 0.5 × 44 runs = 22

    # Opening (8am–10am IST)
    elif 8 <= ist_hour < 10:
        base_min, base_max = 3, 5        # avg 4.0 × 8 runs = 32

    # Morning rush (10am–12pm IST) — peak walk-ins
    elif 10 <= ist_hour <= 12:
        base_min, base_max = 9, 11       # avg 10  × 8 runs = 80

    # Afternoon (12pm–4pm IST) — steady flow
    elif 12 < ist_hour <= 16:
        base_min, base_max = 6, 8        # avg 7   × 16 runs = 112

    # Evening rush (4pm–7pm IST) — end of day pickups
    elif 16 < ist_hour <= 19:
        base_min, base_max = 9, 11       # avg 10  × 12 runs = 120

    # Winding down (7pm–9pm IST)
    else:
        base_min, base_max = 3, 5        # avg 4.0 × 8 runs  = 32

    # Sunday — ~30% less (minimal walk-ins)
    if is_sunday and not (ist_hour < 8 or ist_hour >= 21):
        base_min = max(0, base_min - 3)
        base_max = max(1, base_max - 3)

    # Month-end spike (25th+) — billing cycle, e-commerce returns
    if day >= 25 and not (ist_hour < 8 or ist_hour >= 21):
        base_min += 1
        base_max += 2

    # -------------------------------------------------------
    # BUSINESS GROWTH TREND — ~4% per month (realistic startup)
    # Simulation started: May 20, 2026
    # Growth compounds daily: 4% / 30 days = ~0.133% per day
    # After 1 month  → +4%  (~417/day)
    # After 3 months → +12% (~448/day)
    # After 6 months → +27% (~508/day)
    # -------------------------------------------------------
    simulation_start = datetime(2026, 5, 20, tzinfo=timezone.utc)
    days_elapsed     = max(0, (now_utc - simulation_start).days)
    growth_rate      = 0.00133          # 0.133%/day ≈ 4%/month
    growth_factor    = 1.0 + (growth_rate * days_elapsed)
    growth_factor    = min(growth_factor, 2.0)  # cap at 2× (≈ 25 months)

    base_min = int(base_min * growth_factor)
    base_max = int(base_max * growth_factor)

    # -------------------------------------------------------
    # INDIAN FESTIVAL / SEASONAL MULTIPLIER
    # Stacks on top of growth trend and weekday/weekend logic
    # -------------------------------------------------------
    seasonal_factor = get_seasonal_multiplier(now)
    base_min = int(base_min * seasonal_factor)
    base_max = int(base_max * seasonal_factor)
    # Ensure at least 0 during night even after multiplier
    base_min = max(0, base_min)
    base_max = max(1, base_max)

    shipment_count = random.randint(base_min, base_max)
    season_label   = f"(seasonal x{seasonal_factor})" if seasonal_factor != 1.0 else ""

    for i in range(shipment_count):
        customer = random.choice(customers)
        customer_id = customer["customer_id"]

        # Use service type from customer if available, else random
        service_id = customer.get("preferred_service_type") or random.choice([1, 2, 3, 4])
        if not isinstance(service_id, int):
            service_id = random.choice([1, 2, 3, 4])

        origin = random.choice(locations)
        destination = random.choice([x for x in locations if x != origin])

        distance = route_distance.get(
            (origin[1], destination[1]),
            random.randint(200, 2200)
        )

        # Use IST time for order_date — shows proper Indian business hours in Power BI
        order_date = now + timedelta(
            minutes=random.randint(-10, 10),
            seconds=random.randint(0, 59)
        )
        pickup_date = order_date + timedelta(hours=random.randint(1, 6))

        weight = round(np.random.uniform(0.5, 25), 2)
        base_cost = (weight * 9) + (distance * 0.35)
        multiplier = {1: 1, 2: 1.5, 3: 2.5, 4: 2}[service_id]
        cost = round(base_cost * multiplier, 2)
        revenue = round(cost * random.uniform(1.15, 1.4), 2)
        shipment_value = round(np.random.uniform(500, 75000), 2)

        shipment_rows.append({
            "shipment_id":   f"SHP{now.strftime('%Y%m%d%H%M%S')}{i:04}",
            "order_date":    order_date.strftime('%Y-%m-%dT%H:%M:%S'),
            "pickup_date":   pickup_date.strftime('%Y-%m-%dT%H:%M:%S'),
            "delivery_date": None,
            "customer_id":   customer_id,
            # Schema uses location_id, origin_city, destination_city
            "location_id":        origin[0],
            "origin_city":        origin[1],
            "destination_city":   destination[1],
            "service_type_id":    service_id,
            "status_id":          3,        # Pending
            "delay_reason_id":    None,
            "weight_kg":          weight,
            "distance_km":        distance,
            "cost":               cost,
            "revenue":            revenue,
            "days_taken":         None,
            "shipment_value":     shipment_value,
            "courier_agent_id":   f"AG{random.randint(1000, 9999)}",
            "is_delayed":         False,
            "month":              now.month,
            "year":               now.year,
        })

    # Guard: skip insert if no shipments this run (e.g. night hours)
    # This was the cause of GitHub Actions pipeline failures at night!
    if shipment_rows:
        supabase.table("fact_shipments").insert(shipment_rows).execute()
        print(f"✅ {shipment_count} new shipments inserted {season_label}")
    else:
        print("⏭️  0 shipments this run (off-hours). Skipping insert.")


# =========================================================
# 2. UPDATE PENDING → ARRIVED AT HUB
# =========================================================

def update_pending_shipments():
    response = supabase.table("fact_shipments").select("shipment_id").eq("status_id", 3).limit(15).execute()
    rows = response.data

    for row in rows:
        supabase.table("fact_shipments").update({"status_id": 5}).eq(
            "shipment_id", row["shipment_id"]
        ).execute()

    print(f"{len(rows)} Pending -> Arrived at Hub updated")


# =========================================================
# 3. UPDATE ARRIVED AT HUB → IN TRANSIT
# =========================================================

def update_hub_shipments():
    response = supabase.table("fact_shipments").select("shipment_id").eq("status_id", 5).limit(12).execute()
    rows = response.data

    for row in rows:
        supabase.table("fact_shipments").update({"status_id": 2}).eq(
            "shipment_id", row["shipment_id"]
        ).execute()

    print(f"{len(rows)} Arrived at Hub -> In Transit updated")


# =========================================================
# 4. UPDATE IN TRANSIT → OUT FOR DELIVERY
# =========================================================

def update_out_for_delivery():
    response = supabase.table("fact_shipments").select("shipment_id").eq("status_id", 2).limit(10).execute()
    rows = response.data

    for row in rows:
        supabase.table("fact_shipments").update({"status_id": 6}).eq(
            "shipment_id", row["shipment_id"]
        ).execute()

    print(f"{len(rows)} In Transit -> Out for Delivery updated")


# =========================================================
# 5. UPDATE OUT FOR DELIVERY → DELIVERED / FAILED
# (Also handles legacy status_id=2 for backward compat)
# =========================================================

def update_delivered_shipments():
    # Handle Out for Delivery (new flow) + old In-Transit records (backward compat)
    r6 = supabase.table("fact_shipments").select("*").eq("status_id", 6).limit(15).execute()
    r2 = supabase.table("fact_shipments").select("*").eq("status_id", 2).limit(5).execute()
    rows = r6.data + r2.data

    for row in rows:
        delayed      = bool(np.random.choice([True, False], p=[0.18, 0.82]))
        delay_days   = random.randint(1, 3) if delayed else 0
        failed       = bool(np.random.choice([True, False], p=[0.03, 0.97]))

        service_id   = row["service_type_id"]
        sla          = service_sla.get(service_id, 3)

        pickup_date   = datetime.fromisoformat(row["pickup_date"])
        delivery_date = pickup_date + timedelta(days=sla + delay_days)

        supabase.table("fact_shipments").update({
            "status_id":       4 if failed else 1,   # 4=Failed, 1=Delivered
            "delivery_date":   delivery_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "days_taken":      (delivery_date - pickup_date).days,
            "is_delayed":      delayed,
            "delay_reason_id": random.randint(1, 5) if (delayed or failed) else None,
        }).eq("shipment_id", row["shipment_id"]).execute()

    print(f"{len(rows)} Out for Delivery -> Delivered/Failed updated")


# =========================================================
# 4. OCCASIONAL NEW CUSTOMER
# =========================================================

def insert_new_customer():
    if random.random() < 0.15:
        customer_id = f"CUST{random.randint(1001, 9999):06}"
        location = random.choice(locations)
        customer_type = random.choice(["Retail", "Corporate"])

        supabase.table("dim_customer").insert({
            "customer_id":      customer_id,
            "customer_name":    fake.name(),
            "customer_type":    customer_type,
            "city":             location[1],
            "state":            location[2],
            "signup_date":      datetime.now(timezone.utc).date().isoformat(),
            "customer_segment": random.choice(["Premium", "Regular"]),
        }).execute()
        print(f"New customer inserted: {customer_id}")


# =========================================================
# 5. RARE NEW LOCATION
# =========================================================

def insert_new_location():
    if random.random() < 0.03:
        loc_id = f"LOC{random.randint(100, 999)}"

        supabase.table("dim_location").insert({
            "location_id":                   loc_id,
            "city":                          fake.city(),
            "state":                         fake.state(),
            "region":                        random.choice(["North", "South", "East", "West"]),
            "zone":                          random.choice(["A", "B", "C"]),
            "delivery_performance_target":   random.randint(85, 99),
        }).execute()
        print(f"New location inserted: {loc_id}")


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] Starting live simulation...")
    print("Status flow: Pending(3) → Arrived at Hub(5) → In Transit(2) → Out for Delivery(6) → Delivered(1)/Failed(4)")

    insert_new_shipments()       # New orders → Pending
    update_pending_shipments()   # Pending → Arrived at Hub
    update_hub_shipments()       # Arrived at Hub → In Transit
    update_out_for_delivery()    # In Transit → Out for Delivery
    update_delivered_shipments() # Out for Delivery → Delivered/Failed
    insert_new_customer()        # Occasional new customer
    insert_new_location()        # Rare new location

    print("Live simulation completed successfully.")
