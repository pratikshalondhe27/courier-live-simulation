// live-simulation/index.ts
// Supabase Edge Function — GitHub ki zarurat NAHI!
// Supabase pg_cron se har 15 min mein automatically chalega — KABHI BAND NAHI HOGA!
// STATUS FLOW: Pending(3) → Arrived at Hub(5) → In Transit(2) → Out for Delivery(6) → Delivered(1)/Failed(4)

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const supabase     = createClient(SUPABASE_URL, SUPABASE_KEY);

// ─── Master Data ─────────────────────────────────────────────────────────────
const locations = [
  { id: "LOC001", city: "Mumbai",    state: "Maharashtra" },
  { id: "LOC002", city: "Pune",      state: "Maharashtra" },
  { id: "LOC003", city: "Delhi",     state: "Delhi" },
  { id: "LOC004", city: "Bangalore", state: "Karnataka" },
  { id: "LOC005", city: "Hyderabad", state: "Telangana" },
  { id: "LOC006", city: "Chennai",   state: "Tamil Nadu" },
  { id: "LOC007", city: "Ahmedabad", state: "Gujarat" },
  { id: "LOC008", city: "Kolkata",   state: "West Bengal" },
  { id: "LOC009", city: "Jaipur",    state: "Rajasthan" },
  { id: "LOC010", city: "Lucknow",   state: "Uttar Pradesh" },
];

const serviceSLA: Record<number, number> = { 1: 5, 2: 3, 3: 1, 4: 2 };

const routeDistance: Record<string, number> = {
  "Mumbai-Pune": 160,    "Mumbai-Delhi": 1400,  "Mumbai-Bangalore": 980,
  "Delhi-Bangalore": 2150, "Pune-Hyderabad": 560,
  "Delhi-Kolkata": 1500, "Chennai-Bangalore": 350,
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
const randInt   = (a: number, b: number) => Math.floor(Math.random() * (b - a + 1)) + a;
const randFloat = (a: number, b: number) => Math.random() * (b - a) + a;
const choice    = <T>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];
const nowIST    = () => new Date(Date.now() + 5.5 * 60 * 60 * 1000);
const fmtISO    = (d: Date) => d.toISOString().replace("Z", "");

// ─── GROWTH TREND — ~4% per month from May 20 2026 ───────────────────────────
function getGrowthFactor(): number {
  const start      = new Date("2026-05-20T00:00:00Z").getTime();
  const daysElapsed = Math.max(0, (Date.now() - start) / 86400000);
  const factor      = 1.0 + (0.00133 * daysElapsed); // 0.133%/day = 4%/month
  return Math.min(factor, 2.0); // cap at 2x
}

// ─── INDIAN FESTIVAL / SEASONAL MULTIPLIER ───────────────────────────────────
function getSeasonalMultiplier(date: Date): number {
  const m = date.getUTCMonth() + 1; // 1-12
  const d = date.getUTCDate();

  // Diwali Pre-Season (Oct 15-30) — BIGGEST SPIKE
  if (m === 10 && d >= 15 && d <= 30) return 1.80;
  // Diwali Days (Oct 31 - Nov 2)
  if ((m === 10 && d >= 31) || (m === 11 && d <= 2)) return 1.30;
  // Post-Diwali Dip (Nov 3-5)
  if (m === 11 && d >= 3 && d <= 5) return 0.80;
  // Dussehra/Navratri (Oct 1-14)
  if (m === 10 && d >= 1 && d <= 9)  return 1.35;
  if (m === 10 && d >= 10 && d <= 14) return 1.60;
  // Raksha Bandhan (Aug 7-19)
  if (m === 8 && d >= 7 && d <= 15) return 1.50;
  if (m === 8 && d >= 16 && d <= 19) return 0.85;
  // Independence Day
  if (m === 8 && d === 15) return 1.25;
  // Holi (Mar 10-18)
  if (m === 3 && d >= 10 && d <= 14) return 1.40;
  if (m === 3 && d >= 15 && d <= 18) return 0.82;
  // Eid/Ramzan (Mar 25 - Apr 10)
  if ((m === 3 && d >= 25) || (m === 4 && d <= 10)) return 1.45;
  // New Year Season (Dec 26 - Jan 5)
  if ((m === 12 && d >= 26) || (m === 1 && d <= 5)) return 1.30;
  // Republic Day
  if (m === 1 && d === 26) return 1.20;
  // Valentine Week
  if (m === 2 && d >= 7 && d <= 14) return 1.25;
  // Summer Slowdown
  if (m === 5 || m === 6) return 0.90;
  // Monsoon Dip
  if (m === 7 || (m === 8 && d < 7)) return 0.88;

  return 1.0;
}

// ─── 1. INSERT NEW SHIPMENTS (Pending = status 3) ────────────────────────────
async function insertNewShipments(customers: any[]): Promise<number> {
  const now  = nowIST();
  const hour = now.getUTCHours(); // IST hour
  const day  = now.getUTCDate();
  const wday = now.getUTCDay();   // 0=Sun
  const isSun = wday === 0;
  const isNight = hour < 8 || hour >= 21;

  let baseMin = 0, baseMax = 1;
  if      (isNight)         { baseMin = 0;  baseMax = 1;  }
  else if (hour < 10)       { baseMin = 3;  baseMax = 5;  }
  else if (hour <= 12)      { baseMin = 9;  baseMax = 11; }
  else if (hour <= 16)      { baseMin = 6;  baseMax = 8;  }
  else if (hour <= 19)      { baseMin = 9;  baseMax = 11; }
  else                      { baseMin = 3;  baseMax = 5;  }

  if (isSun && !isNight) { baseMin = Math.max(0, baseMin - 3); baseMax = Math.max(1, baseMax - 3); }
  if (day >= 25 && !isNight) { baseMin += 1; baseMax += 2; }

  // Apply growth + seasonal multipliers
  const growth   = getGrowthFactor();
  const seasonal = getSeasonalMultiplier(now);
  const combined = growth * seasonal;

  baseMin = Math.max(0, Math.floor(baseMin * combined));
  baseMax = Math.max(1, Math.floor(baseMax * combined));

  const count = randInt(baseMin, baseMax);
  if (count === 0) return 0; // Night hours — skip insert safely

  const ts   = now.toISOString().replace(/[-:T.Z]/g, "").slice(0, 14);
  const rows = [];

  for (let i = 0; i < count; i++) {
    const customer  = choice(customers);
    const serviceId = customer.preferred_service_type || randInt(1, 4);
    const origin    = choice(locations);
    const dest      = choice(locations.filter(l => l.id !== origin.id));
    const distKey   = `${origin.city}-${dest.city}`;
    const distance  = routeDistance[distKey] || randInt(200, 2200);
    const orderDate = new Date(now.getTime() + randInt(-10, 10) * 60000);
    const pickupDate = new Date(orderDate.getTime() + randInt(1, 6) * 3600000);
    const weight    = Math.round(randFloat(0.5, 25) * 100) / 100;
    const mult      = [0, 1, 1.5, 2.5, 2][serviceId] || 1;
    const cost      = Math.round(((weight * 9) + (distance * 0.35)) * mult * 100) / 100;
    const revenue   = Math.round(cost * randFloat(1.15, 1.4) * 100) / 100;

    rows.push({
      shipment_id:      `SHP${ts}${String(i).padStart(4, "0")}`,
      order_date:       fmtISO(orderDate),
      pickup_date:      fmtISO(pickupDate),
      delivery_date:    null,
      customer_id:      customer.customer_id,
      location_id:      origin.id,
      origin_city:      origin.city,
      destination_city: dest.city,
      service_type_id:  serviceId,
      status_id:        3, // Pending
      delay_reason_id:  null,
      weight_kg:        weight,
      distance_km:      distance,
      cost,
      revenue,
      days_taken:       null,
      shipment_value:   Math.round(randFloat(500, 75000) * 100) / 100,
      courier_agent_id: `AG${randInt(1000, 9999)}`,
      is_delayed:       false,
      month:            now.getUTCMonth() + 1,
      year:             now.getUTCFullYear(),
    });
  }

  const { error } = await supabase.from("fact_shipments").insert(rows);
  if (error) throw new Error(`Insert: ${error.message}`);
  return count;
}

// ─── 2. Pending → Arrived at Hub (status 5) ──────────────────────────────────
async function updatePendingToHub(): Promise<number> {
  const { data, error } = await supabase
    .from("fact_shipments").select("shipment_id").eq("status_id", 3).limit(15);
  if (error) throw new Error(`Pending→Hub: ${error.message}`);
  for (const row of data || []) {
    await supabase.from("fact_shipments").update({ status_id: 5 }).eq("shipment_id", row.shipment_id);
  }
  return data?.length || 0;
}

// ─── 3. Arrived at Hub → In Transit (status 2) ───────────────────────────────
async function updateHubToTransit(): Promise<number> {
  const { data, error } = await supabase
    .from("fact_shipments").select("shipment_id").eq("status_id", 5).limit(12);
  if (error) throw new Error(`Hub→Transit: ${error.message}`);
  for (const row of data || []) {
    await supabase.from("fact_shipments").update({ status_id: 2 }).eq("shipment_id", row.shipment_id);
  }
  return data?.length || 0;
}

// ─── 4. In Transit → Out for Delivery (status 6) ─────────────────────────────
async function updateTransitToOutForDelivery(): Promise<number> {
  const { data, error } = await supabase
    .from("fact_shipments").select("shipment_id").eq("status_id", 2).limit(10);
  if (error) throw new Error(`Transit→OFD: ${error.message}`);
  for (const row of data || []) {
    await supabase.from("fact_shipments").update({ status_id: 6 }).eq("shipment_id", row.shipment_id);
  }
  return data?.length || 0;
}

// ─── 5. Out for Delivery → Delivered(1) / Failed(4) ──────────────────────────
async function updateToDelivered(): Promise<number> {
  // Handle both Out for Delivery (6) + legacy In Transit (2) for backward compat
  const { data: d6 } = await supabase.from("fact_shipments").select("*").eq("status_id", 6).limit(15);
  const { data: d2 } = await supabase.from("fact_shipments").select("*").eq("status_id", 2).limit(5);
  const rows = [...(d6 || []), ...(d2 || [])];

  for (const row of rows) {
    const delayed    = Math.random() < 0.18;
    const failed     = Math.random() < 0.03;
    const delayDays  = delayed ? randInt(1, 3) : 0;
    const sla        = serviceSLA[row.service_type_id] || 3;
    const pickup     = new Date(row.pickup_date);
    const delivery   = new Date(pickup.getTime() + (sla + delayDays) * 86400000);

    await supabase.from("fact_shipments").update({
      status_id:       failed ? 4 : 1,
      delivery_date:   delivery.toISOString(),
      days_taken:      sla + delayDays,
      is_delayed:      delayed,
      delay_reason_id: (delayed || failed) ? randInt(1, 5) : null,
    }).eq("shipment_id", row.shipment_id);
  }
  return rows.length;
}

// ─── 6. Occasional New Customer (15% chance) ─────────────────────────────────
async function insertNewCustomer(): Promise<boolean> {
  if (Math.random() >= 0.15) return false;
  const loc = choice(locations);
  const names = ["Raj Kumar","Priya Sharma","Amit Singh","Sunita Patel","Vikram Mehta",
                  "Anjali Gupta","Rohit Verma","Deepa Nair","Suresh Reddy","Kavita Joshi"];
  const { error } = await supabase.from("dim_customer").insert({
    customer_id:      `CUST${randInt(100000, 999999)}`,
    customer_name:    choice(names),
    customer_type:    choice(["Retail", "Corporate"]),
    city:             loc.city,
    state:            loc.state,
    signup_date:      nowIST().toISOString().split("T")[0],
    customer_segment: choice(["Premium", "Regular"]),
  });
  if (error) console.warn("New customer:", error.message);
  return !error;
}

// ─── MAIN HANDLER ─────────────────────────────────────────────────────────────
Deno.serve(async (_req) => {
  const t0 = Date.now();
  console.log(`[${nowIST().toISOString()}] Simulation started | Growth: x${getGrowthFactor().toFixed(3)} | Season: x${getSeasonalMultiplier(nowIST())}`);

  try {
    const { data: customers, error: custErr } = await supabase.from("dim_customer").select("*");
    if (custErr) throw new Error(`Customers: ${custErr.message}`);

    const [s1, s2, s3, s4, s5, s6] = await Promise.all([
      insertNewShipments(customers || []),
      updatePendingToHub(),
      updateHubToTransit(),
      updateTransitToOutForDelivery(),
      updateToDelivered(),
      insertNewCustomer(),
    ]);

    const result = {
      success:              true,
      timestamp_ist:        nowIST().toISOString(),
      growth_factor:        getGrowthFactor().toFixed(3),
      seasonal_factor:      getSeasonalMultiplier(nowIST()),
      new_shipments:        s1,
      pending_to_hub:       s2,
      hub_to_transit:       s3,
      transit_to_ofd:       s4,
      ofd_to_delivered:     s5,
      new_customer:         s6,
      elapsed_ms:           Date.now() - t0,
    };

    console.log("Done:", result);
    return new Response(JSON.stringify(result), { headers: { "Content-Type": "application/json" } });
  } catch (err) {
    console.error("Error:", err);
    return new Response(JSON.stringify({ success: false, error: String(err) }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});
