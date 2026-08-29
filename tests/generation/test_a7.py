"""
scripts/generation/test_a7.py

Tests for A7: bounding box computation, enrich_venues (dry-run),
PLAN_VENUES real-venue prompt, generate_venue with overpass_ref.

Run: python scripts/generation/test_a7.py
"""

import sys
import json
import tempfile
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.research_city import research_city, _compute_bbox, STUB_CONFIGS
from scripts.generation.enrich_venues import (
    enrich_briefs, _name_similarity, _find_best_match
)
from scripts.generation.generate_city_venues import (
    plan_venues, generate_city_venues, _generate_stub_briefs
)
from scripts.generation.generate_venue import run_venue_agent, _build_assignment

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

# ─── Test 1: Bounding box computation ─────────────────────────────────────────
print("\n[1] Bounding box computation")
bbox = _compute_bbox(51.5074, -0.1278, 6.0)
check("bbox is a tuple of 4", len(bbox) == 4)
check("min_lat < centre_lat", bbox[0] < 51.5074)
check("max_lat > centre_lat", bbox[2] > 51.5074)
check("min_lng < centre_lng", bbox[1] < -0.1278)
check("max_lng > centre_lng", bbox[3] > -0.1278)
# For 6km radius: ~0.054 degrees lat, ~0.086 degrees lng at London
lat_spread = bbox[2] - bbox[0]
lng_spread = bbox[3] - bbox[1]
check("lat spread ~0.1 degrees for 6km", 0.08 < lat_spread < 0.14, f"{lat_spread:.4f}")
check("lng spread reasonable", 0.10 < lng_spread < 0.20, f"{lng_spread:.4f}")

# ─── Test 2: Bounding box stored in city_config ────────────────────────────────
print("\n[2] Bounding box saved to DB via research_city")
config = research_city("london", dry_run=True, **KW)
conn = get_connection(TEST_DB)
row = conn.execute("SELECT * FROM city_config WHERE city='london'").fetchone()
check("bbox_min_lat stored", row["bbox_min_lat"] is not None)
check("bbox_max_lat stored", row["bbox_max_lat"] is not None)
check("bbox_min_lat < centre_lat", row["bbox_min_lat"] < row["centre_lat"])
check("bbox_max_lat > centre_lat", row["bbox_max_lat"] > row["centre_lat"])
conn.close()

# ─── Test 3: Name similarity function ─────────────────────────────────────────
print("\n[3] Name similarity")
check("exact match = 1.0", _name_similarity("Tate Modern", "Tate Modern") == 1.0)
check("partial match > 0", _name_similarity("Tate Modern", "Tate Modern Gallery") > 0.5)
check("no overlap = 0", _name_similarity("Borough Market", "Shinjuku Gyoen") == 0.0)
check("stop words ignored", _name_similarity("The British Museum", "British Museum") > 0.8)
check("case insensitive", _name_similarity("borough market", "Borough Market") == 1.0)
# Fuzzy: "Tate Modern" vs "Tate Modern (Museum of Modern Art)" should still match
check("name substring match", _name_similarity("Tate Modern", "Tate Modern Museum") > 0.5)

# ─── Test 4: _find_best_match ─────────────────────────────────────────────────
print("\n[4] _find_best_match")
candidates = [
    {"tags": {"name": "Tate Modern"}, "type": "way", "center": {"lat": 51.507, "lon": -0.099}},
    {"tags": {"name": "Borough Market"}, "type": "node", "lat": 51.505, "lon": -0.091},
    {"tags": {"name": "Random Venue"}, "type": "node", "lat": 51.510, "lon": -0.100},
]
match = _find_best_match("Tate Modern", candidates)
check("finds Tate Modern", match is not None)
check("correct match name", match["tags"]["name"] == "Tate Modern")

no_match = _find_best_match("Completely Unknown Place XYZ", candidates)
check("returns None for no match", no_match is None)

# ─── Test 5: enrich_briefs dry-run ────────────────────────────────────────────
print("\n[5] enrich_briefs dry-run")
city_cfg = dict(STUB_CONFIGS["london"])
city_cfg["bbox_min_lat"] = 51.45
city_cfg["bbox_min_lng"] = -0.25
city_cfg["bbox_max_lat"] = 51.57
city_cfg["bbox_max_lng"] = -0.02

sample_briefs = [
    {"name": "The British Museum", "category": "museum", "district": "Camden",
     "traffic_tier": "high", "has_wrong_info": False, "character": "World-famous museum"},
    {"name": "Local Pub", "category": "bar", "district": "Shoreditch",
     "traffic_tier": "mid", "has_wrong_info": False, "character": "Neighbourhood pub"},
    {"name": "Hidden Gem Cafe", "category": "cafe", "district": "Brixton",
     "traffic_tier": "low", "has_wrong_info": True, "character": "Tiny local cafe"},
]
enriched = enrich_briefs(sample_briefs, city_cfg, dry_run=True)
check("returns 3 briefs", len(enriched) == 3)
check("low traffic has overpass_match=None", enriched[2]["overpass_match"] is None)
check("low traffic has no overpass_ref", enriched[2]["overpass_ref"] is None)
check("briefs preserve original fields", enriched[0]["name"] == "The British Museum")
check("briefs preserve has_wrong_info", enriched[2]["has_wrong_info"] is True)
# In dry-run: first high venue gets a simulated match
check("first high venue has overpass_ref or False", enriched[0]["overpass_match"] in (True, False))

# ─── Test 6: plan_venues stub produces real-venue-first briefs ─────────────────
print("\n[6] plan_venues stub — pool balance")
briefs, _ = plan_venues(city_cfg, dry_run=True)
check("40 briefs total", len(briefs) == 40)

food_cats = {"restaurant", "cafe", "bar"}
sight_cats = {"museum", "attraction", "park", "neighbourhood"}
food_count = sum(1 for b in briefs if b["category"] in food_cats)
sight_count = sum(1 for b in briefs if b["category"] in sight_cats)
check("20 food/drink venues", food_count == 20, f"got {food_count}")
check("20 sight venues", sight_count == 20, f"got {sight_count}")
check("all tiers valid", all(b["traffic_tier"] in ("high","mid","low") for b in briefs))
check("all categories valid", all(b["category"] in food_cats | sight_cats for b in briefs))

# ─── Test 7: generate_venue with overpass_ref ─────────────────────────────────
print("\n[7] generate_venue — overpass_ref injected into assignment")
assignment = _build_assignment(
    city="london", category="museum", district="South Bank",
    traffic_tier="high", name="Tate Modern", archetype="World-famous modern art gallery",
    has_wrong_info=False,
    overpass_ref={
        "lat": 51.5076,
        "lng": -0.0994,
        "address": "Bankside, London SE1 9TG",
        "policies": {"wheelchair": "yes", "fee": "no"},
    }
)
check("assignment contains coordinates", "51.5076" in assignment)
check("assignment contains address", "Bankside" in assignment)
check("assignment contains wheelchair policy", "wheelchair" in assignment)
check("assignment warns about hours", "invent opening hours" in assignment.lower() or "do not use any hours" in assignment.lower())

# Without overpass_ref — should not have reference section
assignment_no_ref = _build_assignment(
    city="london", category="cafe", district="Shoreditch",
    traffic_tier="low", name="Hidden Gem Cafe", has_wrong_info=False, overpass_ref=None
)
check("no reference section without overpass_ref", "Reference data" not in assignment_no_ref)

# ─── Test 8: generate_venue dry-run uses overpass coords ──────────────────────
print("\n[8] generate_venue dry-run — overpass coords used")
research_city("london", dry_run=True, **KW)  # already saved, will skip
result = run_venue_agent(
    city="london", category="museum", district="South Bank",
    name="Tate Modern", traffic_tier="high",
    overpass_ref={"lat": 51.5076, "lng": -0.0994,
                  "address": "Bankside, London SE1 9TG", "policies": {}},
    **KW
)
check("dry run succeeds", result["status"] == "success")
conn = get_connection(TEST_DB)
venue = conn.execute(
    "SELECT lat, lng FROM venues WHERE venue_id=?", (result["venue_id"],)
).fetchone()
check("overpass lat used", abs(venue["lat"] - 51.5076) < 0.001, f"got {venue['lat']}")
check("overpass lng used", abs(venue["lng"] - (-0.0994)) < 0.001, f"got {venue['lng']}")
conn.close()

# ─── Test 9: full orchestration dry-run with enrichment ──────────────────────
print("\n[9] Full orchestration dry-run with enrichment")
result = generate_city_venues("london", dry_run=True, workers=3, **KW)
check("40 briefs attempted", result["total_briefs"] == 40)
# 1 already inserted in test 8 + 40 new = 41, but london city already had venues
conn = get_connection(TEST_DB)
total = conn.execute("SELECT COUNT(*) FROM venues WHERE city='london'").fetchone()[0]
check("venues in DB >= 40", total >= 40, f"found {total}")
conn.close()

# ─── Cleanup ─────────────────────────────────────────────────────────────────
TEST_DB.unlink()

passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("✅ All A7 tests passed — Real venue integration ready")
else:
    print("❌ Some tests failed")
    sys.exit(1)
