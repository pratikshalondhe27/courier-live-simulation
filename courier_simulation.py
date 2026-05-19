# courier_simulation.py
# Runs every 5 mins via GitHub Actions to simulate live courier operations

import random
from datetime import datetime, timedelta
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
    now = datetime.now()

    # --- Realistic business patterns (mirrors history data logic) ---
    hour    = now.hour
    weekday = now.weekday()   # 0=Mon, 6=Sun
    day     = now.day

    # Base: 3-6 per run  (~430 shipments/day on weekdays)
    base_min, base_max = 3, 6

    # Weekend dip (Sat/Sun) — ~30% fewer, same as history
    if weekday >= 5:
        base_min, base_max = 1, 3

    # Month-end spike (day 25+) — ~33% more, same as history
    if day >= 25:
        base_min += 1
        base_max += 2

    # Night slowdown (10pm - 6am) — minimal courier activity
    if hour < 6 or hour >= 22:
        base_min, base_max = 0, 1

    # Business hours peak (9am - 7pm) — slight boost
    elif 9 <= hour <= 19:
        base_max += 1

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
            "order_date":    order_date.isoformat(),
            "pickup_date":   pickup_date.isoformat(),
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

    supabase.table("fact_shipments").insert(shipment_rows).execute()
    print(f"{shipment_count} new shipments inserted")


# =========================================================
# 2. UPDATE PENDING → IN TRANSIT
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
# 3. UPDATE IN TRANSIT → DELIVERED
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
            "delivery_date":  delivery_date.isoformat(),
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
            "signup_date":      datetime.now().date().isoformat(),
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
    print(f"[{datetime.now().isoformat()}] Starting live simulation...")

    insert_new_shipments()
    update_pending_shipments()
    update_delivered_shipments()
    insert_new_customer()
    insert_new_location()

    print("Live simulation completed successfully.")
