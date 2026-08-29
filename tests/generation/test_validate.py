"""
scripts/generation/test_validate.py

Tests for validate_city.py — passes and failures for each check.

Run: python scripts/generation/test_validate.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection, new_venue_id, new_doc_id, new_wrong_info_id
from scripts.generation.research_city import research_city
from scripts.generation.generate_venue import run_venue_agent
from scripts.generation.validate_city import validate_city

PASS = "✅"
FAIL = "❌"
results = []

def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    TEST_DB = Path(f.name)

init_db(TEST_DB)
KW = {"db_path": TEST_DB}
research_city("london", dry_run=True, **KW)

# ─── Test 1: Empty city fails ─────────────────────────────────────────────────
print("\n[1] Unknown city fails")
r = validate_city("unknowncity", **KW)
check("unknown city fails", not r["passed"])
check("has issue message", len(r["issues"]) > 0)

# ─── Test 2: City with no venues fails ────────────────────────────────────────
print("\n[2] City with no venues")
research_city("emptytown", dry_run=True, **KW)  # will use placeholder
# Override with a real city_config entry
conn = get_connection(TEST_DB)
conn.execute("""
    INSERT OR REPLACE INTO city_config (city, display_name, country, centre_lat,
        centre_lng, radius_km, local_cuisine_label, task_dates)
    VALUES (?,?,?,?,?,?,?,?)
""", ("emptytown", "Empty Town", "Nowhere", 0.0, 0.0, 5.0, "local", '["2026-06-06"]'))
conn.commit()
conn.close()

r = validate_city("emptytown", **KW)
check("empty city fails", not r["passed"])

# ─── Test 3: Insufficient venues — warnings but not hard fail ─────────────────
print("\n[3] Insufficient venue pool — warnings")
# Generate a few venues only
for cat in ["restaurant", "cafe", "museum"]:
    run_venue_agent("london", cat, "Soho", "mid", **KW)

r = validate_city("london", **KW)
check("partial pool has warnings", len(r["warnings"]) > 0)
# Should warn about food/sight counts being below target
pool_warnings = [w for w in r["warnings"] if "pool" in w.lower() or "target" in w.lower() or "food" in w.lower() or "sight" in w.lower()]
check("pool size warnings present", len(pool_warnings) > 0)

# ─── Test 4: District concentration check ─────────────────────────────────────
print("\n[4] District concentration")
# Add many venues all in same district to trigger check
conn = get_connection(TEST_DB)
for i in range(15):
    vid = new_venue_id()
    conn.execute("""
        INSERT INTO venues (venue_id, city, name, category, district, lat, lng,
            avg_cost_local, price_tier, recommended_visit_minutes, booking_required,
            has_official_site, outdoor_sensitivity, recommended_pace, traffic_tier,
            total_results, yelp_popularity_score, pet_friendly, wheelchair_accessible,
            parking_nearby, photography_allowed, noise_level, reservation_required,
            outside_food_allowed, family_friendly, food_available, page_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid, "london", f"Soho Venue {i}", "restaurant", "Soho",
          51.51, -0.13, 20.0, "mid", 60, 0, 0, "indoor", "moderate",
          "mid", 50, 0.5, 1, 1, 0, 1, "moderate", 0, 0, 1, 1, "verified"))
conn.commit()
conn.close()

r_concentrated = validate_city("london", **KW)
soho_issues = [i for i in r_concentrated["issues"] if "Soho" in i]
check("district concentration detected", len(soho_issues) > 0)

# ─── Test 5: Missing required fields ─────────────────────────────────────────
print("\n[5] Missing required fields")
conn = get_connection(TEST_DB)
bad_vid = new_venue_id()
conn.execute("""
    INSERT INTO venues (venue_id, city, name, category, district, page_status)
    VALUES (?,?,?,?,?,?)
""", (bad_vid, "london", "Incomplete Venue", "cafe", "Camden", "verified"))
conn.commit()
conn.close()

r_missing = validate_city("london", **KW)
missing_issues = [i for i in r_missing["issues"] if "missing required fields" in i.lower()]
check("missing fields detected", len(missing_issues) > 0)
check("bad venue ID mentioned", bad_vid in str(missing_issues))

# ─── Test 6: Wrong info count check ──────────────────────────────────────────
print("\n[6] Wrong info count")
# Right now london has 0 wrong_info entries
r_wi = validate_city("london", **KW)
wi_issues = [i for i in r_wi["issues"] if "wrong_info" in i.lower()]
check("insufficient wrong info detected", len(wi_issues) > 0)

# Add 3 wrong_info entries
conn = get_connection(TEST_DB)
all_venues = conn.execute("SELECT venue_id FROM venues WHERE city='london' LIMIT 3").fetchall()
for row in all_venues:
    wi_id = new_wrong_info_id()
    conn.execute("""
        INSERT INTO wrong_info (wrong_info_id, venue_id, affected_field,
            incorrect_value, correct_value, source_type, wrong_info_category, origin_story)
        VALUES (?,?,?,?,?,?,?,?)
    """, (wi_id, row["venue_id"], "hours_fri", "20:00", "22:00", "yelp",
          "temporal_decay", "Owner didn't update Yelp after extending hours."))
conn.commit()
conn.close()

r_wi2 = validate_city("london", **KW)
wi_issues2 = [i for i in r_wi2["issues"] if "wrong_info" in i.lower() and "minimum" in i.lower()]
check("wrong info count passes after adding entries", len(wi_issues2) == 0)

# ─── Test 7: Full mode — venues missing source docs ───────────────────────────
print("\n[7] Full mode — venues missing source docs")
r_full = validate_city("london", full=True, **KW)
missing_docs = [i for i in r_full["issues"] if "no source docs" in i.lower()]
check("missing source docs detected in full mode", len(missing_docs) > 0)

# ─── Test 8: Full mode — unregistered truth carriers ─────────────────────────
print("\n[8] Full mode — unregistered truth carriers")
unregistered = [i for i in r_full["issues"] if "truth_carrier" in i.lower() or "no truth" in i.lower()]
check("unregistered truth carriers detected", len(unregistered) > 0)

# ─── Test 9: Stats are populated ─────────────────────────────────────────────
print("\n[9] Stats populated correctly")
r_stats = validate_city("london", **KW)
check("stats has total_venues", r_stats["stats"]["total_venues"] > 0)
check("stats has food_venues", "food_venues" in r_stats["stats"])
check("stats has districts", len(r_stats["stats"]["districts"]) > 0)
check("stats has wrong_info_count", r_stats["stats"]["wrong_info_count"] >= 3)

# ─── Cleanup ─────────────────────────────────────────────────────────────────
TEST_DB.unlink()

passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("✅ All A6 tests passed — validate_city ready")
else:
    print("❌ Some tests failed")
    sys.exit(1)
