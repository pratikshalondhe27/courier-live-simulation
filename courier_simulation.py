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
# FETCH EXISTING CUSTOMERS
# =========================================================

customer_response = supabase.table("dim_customer").select("*").execute()
customers = customer_response.data

# =========================================================
# 1. INSERT NEW SHIPMENTS
# =========================================================

def insert_new_shipments():
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

    # Night cutoff (9pm-8am IST) - shop closed
    if ist_hour < 8 or ist_hour >= 21:
        base_min, base_max = 0, 1        # avg 0.5 x 44 runs = 22

    # Opening (8am-10am IST)
    elif 8 <= ist_hour < 10:
        base_min, base_max = 3, 5        # avg 4.0 x 8 runs = 32

    # Morning rush (10am-12pm IST) - peak walk-ins
    elif 10 <= ist_hour <= 12:
        base_min, base_max = 9, 11       # avg 10  x 8 runs = 80

    # Afternoon (12pm-4pm IST) - steady flow
    elif 12 < ist_hour <= 16:
        base_min, base_max = 6, 8        # avg 7   x 16 runs = 112

    # Evening rush (4pm-7pm IST) - end of day pickups
    elif 16 < ist_hour <= 19:
        base_min, base_max = 9, 11       # avg 10  x 12 runs = 120

    # Winding down (7pm-9pm IST)
    else:
        base_min, base_max = 3, 5        # avg 4.0 x 8 runs  = 32

    # Sunday - ~30% less (minimal walk-ins)
    if is_sunday and not (ist_hour < 8 or ist_hour >= 21):
        base_min = max(0, base_min - 3)
        base_max = max(1, base_max - 3)

    # Month-end spike (25th+) - billing cycle, e-commerce returns
    if day >= 25 and not (ist_hour < 8 or ist_hour >= 21):
        base_min += 1
        base_max += 2

    # -------------------------------------------------------
    # BUSINESS GROWTH TREND - ~4% per month (realistic startup)
    # Simulation started: May 20, 2026
    # Growth compounds daily: 4% / 30 days = ~0.133% per day
    # After 1 month  -> +4%  (~417/day)
    # After 3 months -> +12% (~448/day)
    # After 6 months -> +27% (~508/day)
    # -------------------------------------------------------
    simulation_start = datetime(2026, 5, 20, tzinfo=timezone.utc)
    days_elapsed     = max(0, (now_utc - simulation_start).days)
    growth_rate      = 0.00133          # 0.133%/day = 4%/month
    growth_factor    = 1.0 + (growth_rate * days_elapsed)
    growth_factor    = min(growth_factor, 2.0)  # cap at 2x (approx 25 months)

    base_min = int(base_min * growth_factor)
    base_max = int(base_max * growth_factor)

    shipment_count = random.randint(base_min, base_max)

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

        # Use IST time for order_date - shows proper Indian business hours in Power BI
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
        print(f"OK {shipment_count} new shipments inserted")
    else:
        print("SKIP 0 shipments this run (off-hours). Skipping insert.")


# =========================================================
# 2. UPDATE PENDING -> IN TRANSIT
# =========================================================

def update_pending_shipments():
    response = supabase.table("fact_shipments").select("shipment_id").eq("status_id", 3).limit(15).execute()
    rows = response.data

    for row in rows:
        supabase.table("fact_shipments").update({"status_id": 2}).eq(
            "shipment_id", row["shipment_id"]
        ).execute()

    print(f"{len(rows)} Pending -> In Transit updated")


# =========================================================
# 3. UPDATE IN TRANSIT -> DELIVERED
# =========================================================

def update_delivered_shipments():
    response = supabase.table("fact_shipments").select("*").eq("status_id", 2).limit(20).execute()
    rows = response.data

    for row in rows:
        delayed = bool(np.random.choice([True, False], p=[0.18, 0.82]))
        delay_days = random.randint(1, 3) if delayed else 0

        service_id = row["service_type_id"]
        sla = service_sla.get(service_id, 3)

        pickup_date = datetime.fromisoformat(row["pickup_date"])
        delivery_date = pickup_date + timedelta(days=sla + delay_days)

        supabase.table("fact_shipments").update({
            "status_id":      1,
            "delivery_date":  delivery_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "days_taken":     (delivery_date - pickup_date).days,
            "is_delayed":     delayed,
            "delay_reason_id": random.randint(1, 5) if delayed else None,
        }).eq("shipment_id", row["shipment_id"]).execute()

    print(f"{len(rows)} In Transit -> Delivered updated")


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

    insert_new_shipments()
    update_pending_shipments()
    update_delivered_shipments()
    insert_new_customer()
    insert_new_location()

    print("Live simulation completed successfully.")
