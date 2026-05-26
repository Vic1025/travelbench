"""
scripts/generation/test_a65_a76.py

Tests for:
  A6.5 — coordinate spread check, tag diversity check, annotate_venues_for_window
  A7.6 — travel matrix DB schema, computation, retrieval
"""

import sys, tempfile, json, math
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection, new_venue_id, new_page_id
from scripts.generation.validate_city import validate_city
from scripts.generation.annotate_venues_for_window import (
    annotate_city, get_venue_window_flags, ZONES, _in_zone
)
from scripts.generation.build_travel_matrix import (
    build_travel_matrix, get_travel_time, compute_fallback, haversine_km
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}{(' — ' + detail) if detail else ''}")
        FAIL += 1


def make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    return db


def insert_venue(conn, city, name, lat, lng, traffic_tier="mid",
                 category="restaurant", district="Test"):
    vid = new_venue_id()
    conn.execute("""
        INSERT INTO venues (venue_id, city, name, category, district, lat, lng,
            avg_cost_local, price_tier, recommended_visit_minutes, booking_required,
            has_official_site, outdoor_sensitivity, recommended_pace, traffic_tier,
            total_results, yelp_popularity_score, food_available, page_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid, city, name, category, district, lat, lng,
          20.0, "mid", 60, 0, 0, "indoor", "moderate",
          traffic_tier, 100, 0.5, 1, "committed"))
    conn.commit()
    return vid


def insert_tag(conn, venue_id, city, tag):
    conn.execute(
        "INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
        (tag, city, venue_id, 1)
    )
    conn.commit()


def insert_city(conn, city):
    conn.execute("""
        INSERT OR IGNORE INTO city_config
            (city, display_name, country, centre_lat, centre_lng, radius_km,
             local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)
    """, (city, city.title(), "XX", 0.0, 0.0, 5.0, "local", "[]"))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# [1] A6.5 — Coordinate spread check
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] A6.5 — Coordinate spread check")
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")

# Insert 5 high-traffic venues all clustered within 100m
base_lat, base_lng = 51.5080, -0.1281
for i in range(5):
    insert_venue(conn, "london", f"Clustered Venue {i}",
                 base_lat + i * 0.0002, base_lng + i * 0.0002, "high")

result = validate_city("london", db_path=db)
warnings = result.get("warnings", [])
spread_warned = any("cluster" in w.lower() or "spread" in w.lower() for w in warnings)
check("clustered high-traffic venues triggers spread warning", spread_warned,
      f"warnings={warnings}")

db.unlink()

# Spread out venues — no warning
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
positions = [
    (51.500, -0.200), (51.515, -0.100), (51.530, -0.050),
    (51.480, -0.150), (51.545, -0.180),
]
for i, (lat, lng) in enumerate(positions):
    insert_venue(conn, "london", f"Spread Venue {i}", lat, lng, "high")

result = validate_city("london", db_path=db)
warnings = result.get("warnings", [])
spread_warned = any("cluster" in w.lower() or "spread" in w.lower() for w in warnings)
check("spread high-traffic venues does not trigger warning", not spread_warned,
      f"warnings={warnings}")
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [2] A6.5 — Tag diversity check
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] A6.5 — Tag diversity check")
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")

v1 = insert_venue(conn, "london", "Dog Venue", 51.5, -0.1)
insert_tag(conn, v1, "london", "dog-friendly")

result = validate_city("london", db_path=db)
warnings = result.get("warnings", [])
tag_warned = any("tag" in w.lower() or "coverage" in w.lower() for w in warnings)
check("sparse tags trigger diversity warning", tag_warned, f"warnings={warnings}")

# Check tag_diversity in stats
stats = result.get("stats", {})
check("tag_diversity in stats", "tag_diversity" in stats)
check("dog-friendly count = 1", stats.get("tag_diversity", {}).get("dog-friendly") == 1)
check("halal count = 0", stats.get("tag_diversity", {}).get("halal") == 0)

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [3] A6.5 — annotate_venues_for_window
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] A6.5 — annotate_venues_for_window")

# Test zone detection
notting_hill_lat, notting_hill_lng = 51.512, -0.200  # inside carnival closure
check("Notting Hill in carnival closure zone",
      _in_zone(notting_hill_lat, notting_hill_lng, "london_carnival_closure"))

city_centre_lat, city_centre_lng = 51.515, -0.120  # outside carnival closure
check("City centre not in carnival closure zone",
      not _in_zone(city_centre_lat, city_centre_lng, "london_carnival_closure"))

odori_lat, odori_lng = 43.060, 141.353  # Odori Park Sapporo
check("Odori Park in snow festival zone",
      _in_zone(odori_lat, odori_lng, "sapporo_odori"))

sambodromo_lat, sambodromo_lng = -22.908, -43.195  # Rio Sambódromo
check("Sambódromo in carnival zone",
      _in_zone(sambodromo_lat, sambodromo_lng, "rio_sambodromo"))

# Test full annotation pass
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
v_notting = insert_venue(conn, "london", "Notting Hill Bar",
                         notting_hill_lat, notting_hill_lng, district="Notting Hill")
v_city = insert_venue(conn, "london", "City Bar",
                      city_centre_lat, city_centre_lng, district="City")
conn.close()

result = annotate_city("london", db_path=db)
check("annotation runs ok", result["status"] == "ok", str(result))
check("both venues annotated", result["annotated"] == 2)

flags_notting = get_venue_window_flags(v_notting, db_path=db)
flags_city = get_venue_window_flags(v_city, db_path=db)
check("Notting Hill venue has carnival flags",
      "london_carnival_2026" in flags_notting)
check("Notting Hill venue in closure zone",
      flags_notting.get("london_carnival_2026", {}).get("in_closure_zone") is True)
check("City centre venue not in closure zone",
      flags_city.get("london_carnival_2026", {}).get("in_closure_zone") is False)
db.unlink()

# Unsupported city returns skipped
db = make_db()
conn = get_connection(db)
insert_city(conn, "paris")
conn.close()
result = annotate_city("paris", db_path=db)
check("unsupported city returns skipped status", result["status"] == "skipped")
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [4] A7.6 — travel_matrix DB schema
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] A7.6 — travel_matrix DB schema")
db = make_db()
conn = get_connection(db)
# Verify table exists and has correct columns
cols = {r[1] for r in conn.execute("PRAGMA table_info(travel_matrix)").fetchall()}
check("travel_matrix table exists", len(cols) > 0)
for expected_col in ["venue_id_a", "venue_id_b", "walk_minutes",
                     "transit_minutes", "cycling_minutes", "distance_km", "city"]:
    check(f"column {expected_col} exists", expected_col in cols)
conn.close()
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [5] A7.6 — haversine calculation
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] A7.6 — haversine distance")
# Borough Market to Tate Modern — approx 0.7km straight line
dist = haversine_km(51.5055, -0.0910, 51.5076, -0.0994)
check("Borough Market → Tate Modern ~0.5-1.0km", 0.4 < dist < 1.2,
      f"got {dist:.3f}km")

# London to Paris — approx 340km
dist_lp = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
check("London → Paris ~340km", 300 < dist_lp < 380, f"got {dist_lp:.1f}km")


# ─────────────────────────────────────────────────────────────────────────────
# [6] A7.6 — compute_fallback and DB write
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] A7.6 — compute and store matrix")
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")

# Insert 5 venues at known London coordinates
known_venues = [
    ("Borough Market",  51.5055, -0.0910),
    ("Tate Modern",     51.5076, -0.0994),
    ("London Bridge",   51.5079, -0.0877),
    ("Southwark Cath.", 51.5062, -0.0956),
    ("The Shard",       51.5045, -0.0865),
]
vid_map = {}
for name, lat, lng in known_venues:
    vid = insert_venue(conn, "london", name, lat, lng, "high")
    vid_map[name] = vid
conn.close()

result = build_travel_matrix("london", api_key=None, dry_run=False, db_path=db)
check("matrix build succeeds", result["status"] == "ok", str(result))
check("correct pair count (5 venues → 10 pairs)", result["pairs"] == 10,
      f"got {result['pairs']}")
check("method is haversine (no API key)", result["method"] == "haversine")

# Verify retrieval
bm_id = vid_map["Borough Market"]
tate_id = vid_map["Tate Modern"]
walk = get_travel_time(bm_id, tate_id, "walk", db)
transit = get_travel_time(bm_id, tate_id, "transit", db)
cycling = get_travel_time(bm_id, tate_id, "cycling", db)
check("walk time retrieved", walk is not None)
check("transit time retrieved", transit is not None)
check("cycling time retrieved", cycling is not None)
check("Borough Market → Tate Modern walk ~8-20min", walk and 5 < walk < 25,
      f"got {walk}min")
check("walk > cycling (walking slower)", walk and cycling and walk > cycling)
check("transit ≤ walk (transit faster)", walk and transit and transit <= walk)

# Verify reverse lookup works
walk_rev = get_travel_time(tate_id, bm_id, "walk", db)
check("reverse pair lookup works", walk_rev == walk)

# Verify all 10 pairs stored
conn = get_connection(db)
count = conn.execute("SELECT COUNT(*) FROM travel_matrix WHERE city='london'").fetchone()[0]
check("all 10 pairs in DB", count == 10, f"got {count}")
conn.close()
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [7] A7.6 — dry-run mode
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] A7.6 — dry-run mode")
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
for name, lat, lng in known_venues[:3]:
    insert_venue(conn, "london", name, lat, lng)
conn.close()

result = build_travel_matrix("london", api_key=None, dry_run=True, db_path=db)
check("dry-run returns dry_run status", result["status"] == "dry_run")
check("dry-run computes correct pairs (3 venues → 3 pairs)", result["pairs"] == 3,
      f"got {result['pairs']}")

conn = get_connection(db)
count = conn.execute("SELECT COUNT(*) FROM travel_matrix").fetchone()[0]
check("dry-run writes nothing to DB", count == 0, f"got {count} rows")
conn.close()
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [8] A7.6 — no venues case
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] A7.6 — edge cases")
db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
conn.close()

result = build_travel_matrix("london", db_path=db)
check("no venues returns no_venues status", result["status"] == "no_venues")

# Unknown city
result2 = build_travel_matrix("atlantis", db_path=db)
check("unknown city returns no_venues", result2["status"] == "no_venues")
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All A6.5+A7.6 tests passed ({PASS}/{total}) — ready for Sprint B")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
