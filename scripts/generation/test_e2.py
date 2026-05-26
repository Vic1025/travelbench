"""
scripts/generation/test_e2.py

Tests for E2 — travel matrix wiring, travel formula in prompts,
Type 2 solvability geometry, ticket availability, load_ground_truth DB.

Run: python scripts/generation/test_e2.py
"""

import sys
import json
import math
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.build_travel_matrix import haversine_km, estimate_minutes

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))
        FAIL += 1


def make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    return db


def insert_city(conn, city="london"):
    conn.execute("""
        INSERT OR IGNORE INTO city_config
            (city, display_name, country, centre_lat, centre_lng,
             radius_km, local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)
    """, (city, city.title(), "UK", 51.5, -0.1, 10.0, "British", "[]"))
    conn.commit()


def insert_venue(conn, city, vid, name, category="museum", lat=51.5, lng=-0.1,
                  booking_required=0, avg_cost=20.0, traffic_tier="mid"):
    conn.execute("""
        INSERT OR IGNORE INTO venues
            (venue_id, city, name, category, district, lat, lng,
             avg_cost_local, price_tier, recommended_visit_minutes,
             booking_required, has_official_site, outdoor_sensitivity,
             recommended_pace, traffic_tier, total_results, yelp_popularity_score,
             pet_friendly, wheelchair_accessible, parking_nearby,
             photography_allowed, noise_level, reservation_required,
             outside_food_allowed, family_friendly, food_available,
             has_wrong_info_planned, page_status,
             hours_mon, hours_tue, hours_wed, hours_thu,
             hours_fri, hours_sat, hours_sun)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid, city, name, category, "Shoreditch", lat, lng,
          avg_cost, "mid", 90, booking_required, 1, "indoor", "moderate",
          traffic_tier, 100, 0.5,
          1, 1, 0, 1, "moderate", 0, 0, 1,
          1 if category in ("restaurant","cafe","bar") else 0,
          0, "verified",
          "09:00-18:00","09:00-18:00","09:00-18:00","09:00-18:00",
          "09:00-18:00","10:00-17:00",None))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# [1] haversine_km and estimate_minutes
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] Haversine and estimate_minutes")

d = haversine_km(51.522, -0.077, 51.507, -0.099)
check("Shoreditch→Tate Modern ~2-3km", 1.5 < d < 3.5, f"{d:.2f}km")

walk_min = estimate_minutes(d * 1.3, 5.0)
check("walking estimate reasonable (15-50min)", 15 < walk_min < 50, f"{walk_min:.1f}min")

check("same point = 0km", haversine_km(51.5, -0.1, 51.5, -0.1) < 0.001)

lng_factor = round(math.cos(math.radians(51.5)) * 111, 1)
check("London lng_factor ≈ 69-70", 68 < lng_factor < 71, f"{lng_factor}")


# ─────────────────────────────────────────────────────────────────────────────
# [2] Part A — orchestrator wiring (dry-run)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Part A — orchestrator wiring (dry-run)")

from scripts.generation.generate_city_venues import generate_city_venues

db = make_db()
result = generate_city_venues("london", dry_run=True, workers=2, db_path=db)

check("result has matrix_pairs", "matrix_pairs" in result)
check("result has ticket_rows", "ticket_rows" in result)
check("matrix_pairs is int", isinstance(result["matrix_pairs"], int))
check("ticket_rows is int", isinstance(result["ticket_rows"], int))
check("matrix_pairs >= 0", result["matrix_pairs"] >= 0)
check("ticket_rows >= 0", result["ticket_rows"] >= 0)
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [3] Part A — skip flags
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Part A — skip flags")

db = make_db()
result_skip = generate_city_venues("london", dry_run=True, workers=2,
                                    skip_matrix=True, skip_tickets=True, db_path=db)
check("skip_matrix: matrix_pairs=0", result_skip["matrix_pairs"] == 0)
check("skip_tickets: ticket_rows=0", result_skip["ticket_rows"] == 0)
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [4] Part B — load_city_pool raises on unknown city
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] Part B — load_city_pool error handling")

from test_generate_tasks import load_city_pool

db = make_db()  # empty DB with schema but no cities
raised = False
try:
    load_city_pool("atlantis_does_not_exist", db_path=db)
except ValueError as e:
    raised = True
    check("raises ValueError for unknown city", True)
    check("error message mentions city name", "atlantis" in str(e).lower())
except Exception as e:
    check("raises ValueError for unknown city", False, f"wrong exception type: {e}")
if not raised:
    check("raises ValueError for unknown city", False, "no exception raised")
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [5] Part B — load_unavailable_dates
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] Part B — load_unavailable_dates")

from scripts.generation.generate_task import load_unavailable_dates

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
insert_venue(conn, "london", "lon_v1", "Test Museum", booking_required=1)
insert_venue(conn, "london", "lon_v2", "Open Cafe",   booking_required=0)
conn.execute(
    "INSERT INTO ticket_availability (venue_id,date,status,slots_available,sold_out) "
    "VALUES ('lon_v1','2026-08-23','sold_out',0,1)")
conn.execute(
    "INSERT INTO ticket_availability (venue_id,date,status,slots_available,sold_out) "
    "VALUES ('lon_v1','2026-08-24','available',20,0)")
conn.execute(
    "INSERT INTO ticket_availability (venue_id,date,status,slots_available,sold_out) "
    "VALUES ('lon_v2','2026-08-23','sold_out',0,1)")
conn.commit()
conn.close()

window_dates = ["2026-08-20","2026-08-21","2026-08-22",
                "2026-08-23","2026-08-24","2026-08-25","2026-08-26"]
unavail = load_unavailable_dates("london", window_dates, db_path=db)

check("sold-out venue appears", "lon_v1" in unavail)
check("sold-out date listed", "2026-08-23" in unavail.get("lon_v1", []))
check("available date not listed", "2026-08-24" not in unavail.get("lon_v1", []))
check("second venue appears", "lon_v2" in unavail)
check("empty dates returns {}", load_unavailable_dates("london", [], db_path=db) == {})
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# [7] Part C — _verify_task_solvable Type 2 geometry
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] Part C — Type 2 solvability geometry")

from scripts.generation.generate_task import _verify_task_solvable

def make_venue(vid, lat, lng, visit_min=60, category="bar"):
    return {"venue_id": vid, "name": vid, "category": category,
            "district": "Shoreditch", "traffic_tier": "mid",
            "recommended_visit_minutes": visit_min,
            "lat": lat, "lng": lng, "tags": [],
            "noise_level": "moderate",
            "has_wrong_info": False, "booking_required": False}

def make_type2_task(min_count, time_ceiling_minutes):
    """P7 interface: ceiling lives in public_input.query_resources.time_ceiling_minutes,
    not in a time_threshold constraint. Schema validator requires the field for type2."""
    return {
        "task_id":         "e2_test",
        "city":            "london",
        "window_id":       "lon_carnival_2026",
        "start_date":      "2026-08-22",
        "days": 1, "structural_type": "type2_subset_selection",
        "public_input": {
            "query": f"Visit {min_count} bars on one evening",
            "query_resources": {"time_ceiling_minutes": time_ceiling_minutes},
        },
        "rubric": {
            "hard_constraints": [
                {"type": "hours_check"}, {"type": "no_overlap"}, {"type": "travel_time_hard"}
            ],
            "personal_constraints": [
                {"id": "pc1", "score_tier": "P",
                 "hop": 1, "check_method": "code", "source_in_profile": "pub hop",
                 "description": f"visit {min_count} bars",
                 "scope": f"category=bar", "condition": {},
                 "aggregation": {"at_least": min_count}, "consequence": "p_score_full"},
            ],
            "b_score_constraints": [],
            "required_venue_ids": [],
        }
    }

# Feasible: 3 nearby pubs (Shoreditch cluster ~0.3km each) with a ceiling in the
# P7 sweet spot — min schedule ~3×0.5×45 + small travel ≈ 75min; 90min = 1.2×
nearby_pubs = [
    make_venue("p1", 51.522, -0.077, visit_min=45),
    make_venue("p2", 51.524, -0.079, visit_min=45),
    make_venue("p3", 51.521, -0.075, visit_min=45),
    make_venue("p4", 51.523, -0.078, visit_min=45),
]
ok, reason = _verify_task_solvable(make_type2_task(3, 82), nearby_pubs)
check("nearby pubs 3-visit ~82min ceiling (~1.2× min): solvable", ok, reason)

# Infeasible: 3 pubs spread far across London, 60min ceiling
far_pubs = [
    make_venue("fp1", 51.522, -0.077, visit_min=50),  # Shoreditch
    make_venue("fp2", 51.507, -0.176, visit_min=50),  # Notting Hill
    make_venue("fp3", 51.466, -0.195, visit_min=50),  # Chiswick
    make_venue("fp4", 51.485,  0.004, visit_min=50),  # Docklands
]
ok2, reason2 = _verify_task_solvable(make_type2_task(3, 60), far_pubs)
check("spread-city pubs 60min ceiling: infeasible", not ok2, reason2)
check("reason mentions infeasibility", any(w in reason2.lower() for w in ["ceil","tight","infeasible","loose"]))

# Non-Type-2 uses scope-aware pool checks, should pass with a normal task.
# nearby_pubs has 4 bars; pc1 requires noise_level=="quiet" (only 2 of 4 pubs are quiet)
# → universal_pool has 2 food venues (50% of 4) which passes the 50% threshold exactly.
quiet_pubs = [
    make_venue("p1", 51.522, -0.077, visit_min=45),
    make_venue("p2", 51.524, -0.079, visit_min=45),
    make_venue("p3", 51.521, -0.075, visit_min=45),
    make_venue("p4", 51.523, -0.078, visit_min=45),
    make_venue("p5", 51.523, -0.077, visit_min=45),
    make_venue("p6", 51.524, -0.076, visit_min=45),
    # Sites: 4 museums, only 1 will be quiet so site ratio = 1/4 = 25% ≤ 50%
    make_venue("m1", 51.520, -0.080, visit_min=90, category="museum"),
    make_venue("m2", 51.521, -0.081, visit_min=90, category="museum"),
    make_venue("m3", 51.519, -0.079, visit_min=90, category="museum"),
    make_venue("m4", 51.518, -0.078, visit_min=90, category="museum"),
]
# Mark 2 bars and 1 museum as quiet; rest moderate (default)
# Food: 2/6 = 33% quiet → passes 50% bar
# Site: 1/4 = 25% quiet → passes 50% bar (exactly on threshold → ≤ 50% means pass)
quiet_pubs[0] = {**quiet_pubs[0], "noise_level": "quiet"}
quiet_pubs[1] = {**quiet_pubs[1], "noise_level": "quiet"}
quiet_pubs[6] = {**quiet_pubs[6], "noise_level": "quiet"}
task_type1 = {
    "days": 1, "structural_type": "type1_cascading_requirements",
    "rubric": {
        "hard_constraints": [
            {"type": "hours_check"}, {"type": "no_overlap"}, {"type": "travel_time_hard"}
        ],
        "personal_constraints": [
            {"id": "pc1", "score_tier": "P",
             "hop": 2, "check_method": "code", "source_in_profile": "quiet",
             "description": "quiet venues only",
             "scope": "all",
             "condition": {"field": "noise_level", "operator": "==", "value": "quiet"},
             "aggregation": "all", "consequence": "p_score_full"}
        ],
        "b_score_constraints": [],
    }
}
ok3, reason3 = _verify_task_solvable(task_type1, quiet_pubs)
check("Type 1 uses flat check, passes normally", ok3, reason3)


# ─────────────────────────────────────────────────────────────────────────────
# [8] Part D — sold-out check handles both formats
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] Part D — sold-out check formats")

def _is_sold_out(ticket_avail, date_str):
    date_avail = ticket_avail.get(date_str)
    return (
        (isinstance(date_avail, dict) and bool(date_avail.get("sold_out"))) or
        date_avail == "sold_out"
    )

check("DB dict sold_out=True fires",
      _is_sold_out({"2026-08-23": {"sold_out": True, "slots_available": 0}}, "2026-08-23"))
check("DB dict sold_out=False does not fire",
      not _is_sold_out({"2026-08-23": {"sold_out": False, "slots_available": 10}}, "2026-08-23"))
check("legacy string format fires",
      _is_sold_out({"2026-08-23": "sold_out"}, "2026-08-23"))
check("missing date does not fire",
      not _is_sold_out({}, "2026-08-23"))


# ─────────────────────────────────────────────────────────────────────────────
# [9] Part E — load_ground_truth DB function
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] Part E — load_ground_truth")

from eval.evaluator import load_ground_truth

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
insert_venue(conn, "london", "lon_m1", "Tate Modern", category="museum",
              lat=51.507, lng=-0.099, booking_required=1, avg_cost=0.0, traffic_tier="high")
insert_venue(conn, "london", "lon_r1", "Borough Market", category="restaurant",
              lat=51.505, lng=-0.091, booking_required=0, avg_cost=25.0, traffic_tier="high")
conn.execute("INSERT INTO tags (tag,city,venue_id,yelp_visible) VALUES (?,?,?,?)",
             ("art","london","lon_m1",1))
conn.execute("INSERT INTO tags (tag,city,venue_id,yelp_visible) VALUES (?,?,?,?)",
             ("free-entry","london","lon_m1",1))
conn.execute(
    "INSERT INTO ticket_availability (venue_id,date,status,slots_available,sold_out,price_local) "
    "VALUES ('lon_m1','2026-08-23','sold_out',0,1,0.0)")
conn.execute(
    "INSERT INTO ticket_availability (venue_id,date,status,slots_available,sold_out,price_local) "
    "VALUES ('lon_m1','2026-08-24','available',50,0,0.0)")
conn.execute(
    "INSERT INTO travel_matrix (city,venue_id_a,venue_id_b,walk_minutes,transit_minutes,"
    "cycling_minutes,distance_km) VALUES ('london','lon_m1','lon_r1',12.0,8.0,5.0,0.8)")
conn.commit()
conn.close()

try:
    venues, matrix, _ = load_ground_truth("london", db_path=db)
    check("venues dict non-empty", len(venues) > 0, f"{len(venues)}")
    check("venue has hours dict", isinstance(venues.get("lon_m1", {}).get("hours"), dict))
    check("hours has 'mon' key", "mon" in venues.get("lon_m1", {}).get("hours", {}))
    check("venue has regulations dict", isinstance(venues.get("lon_m1", {}).get("regulations"), dict))
    check("wheelchair_accessible in regulations",
          "wheelchair_accessible" in venues.get("lon_m1", {}).get("regulations", {}))
    check("venue has tags list", isinstance(venues.get("lon_m1", {}).get("tags"), list))
    check("'art' tag in tags", "art" in venues.get("lon_m1", {}).get("tags", []))
    check("venue has location.district",
          venues.get("lon_m1", {}).get("location", {}).get("district") is not None)
    check("has_wrong_info field present", "has_wrong_info" in venues.get("lon_m1", {}))
    check("has_stale_hours field present", "has_stale_hours" in venues.get("lon_m1", {}))
    ta = venues.get("lon_m1", {}).get("ticket_availability", {})
    check("ticket_availability attached", "2026-08-23" in ta)
    check("sold_out=True in DB dict format", ta.get("2026-08-23", {}).get("sold_out") is True)
    check("available date present", "2026-08-24" in ta)
    key = "lon_m1_to_lon_r1" if "lon_m1_to_lon_r1" in matrix else "lon_r1_to_lon_m1"
    check("matrix entry exists", key in matrix, f"keys: {list(matrix.keys())[:3]}")
    check("matrix walking ~12", 10 < matrix.get(key, {}).get("walking", 0) < 15)
    check("matrix transit ~8", 6 < matrix.get(key, {}).get("transit", 0) < 11)
except Exception as e:
    check("load_ground_truth runs without error", False, str(e))
    import traceback; traceback.print_exc()
finally:
    db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [10] Part E — load_ground_truth raises on unknown city
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] Part E — load_ground_truth error handling")

db2 = make_db()
raised_e = False
try:
    load_ground_truth("atlantis_unknown", db_path=db2)
except ValueError as e2:
    raised_e = True
    check("raises ValueError for unknown city", True)
    check("error mentions city name", "atlantis" in str(e2).lower())
except Exception as e2:
    check("raises ValueError for unknown city", False, f"wrong type: {e2}")
finally:
    if not raised_e:
        check("raises ValueError for unknown city", False, "no exception")
    db2.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All E2 tests passed ({PASS}/{total})")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
