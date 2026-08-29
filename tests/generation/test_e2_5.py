"""
scripts/generation/test_e2_5.py

Tests for E2.5 — task generation agent loop.

Tests all non-LLM components:
  - _query_pool AND-logic filter engine
  - _dispatch_task_tool for each tool (HELP, THINK, query_pool, get_venue,
    estimate_travel, SUBMIT pass, SUBMIT fail)
  - _build_task_handbook dynamic content
  - _city_centre_from_pool
  - run_task_agent dry-run path (no API key → None return)

Run: python scripts/generation/test_e2_5.py
"""

import sys
import json
import math
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.task_agent import (
    _query_pool,
    _get_venue_detail,
    _dispatch_task_tool,
    _build_task_handbook,
    _city_centre_from_pool,
    _query_handbook,
    run_task_agent,
)

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


# ── Shared fixtures ───────────────────────────────────────────────────────────

TEST_POOL = [
    {
        "venue_id": "lon_r01", "name": "Halal Grill", "category": "restaurant",
        "district": "Shoreditch", "traffic_tier": "mid", "recommended_pace": "moderate",
        "price_tier": "mid", "avg_cost_local": 22.0, "lat": 51.522, "lng": -0.077,
        "recommended_visit_minutes": 75, "tags": ["halal", "grills", "locals-favourite"],
        "wheelchair_accessible": 1, "pet_friendly": 0, "photography_allowed": 1,
        "family_friendly": 1, "age_restriction": None, "dress_code": None,
        "noise_level": "moderate", "reservation_required": 0, "outside_food_allowed": 0,
        "booking_required": 0, "has_official_site": 1, "window_flags": {},
        "has_wrong_info": False,
    },
    {
        "venue_id": "lon_r02", "name": "Veggie Corner", "category": "restaurant",
        "district": "Shoreditch", "traffic_tier": "low", "recommended_pace": "relaxed",
        "price_tier": "budget", "avg_cost_local": 12.0, "lat": 51.524, "lng": -0.079,
        "recommended_visit_minutes": 60, "tags": ["vegetarian-options", "vegan", "hidden-gem"],
        "wheelchair_accessible": 0, "pet_friendly": 1, "photography_allowed": 1,
        "family_friendly": 1, "age_restriction": None, "dress_code": None,
        "noise_level": "quiet", "reservation_required": 0, "outside_food_allowed": 0,
        "booking_required": 0, "has_official_site": 0, "window_flags": {},
        "has_wrong_info": False,
    },
    {
        "venue_id": "lon_m01", "name": "Tate Modern", "category": "museum",
        "district": "South Bank", "traffic_tier": "high", "recommended_pace": "intense",
        "price_tier": "free", "avg_cost_local": 0.0, "lat": 51.507, "lng": -0.099,
        "recommended_visit_minutes": 120, "tags": ["art", "free-entry", "iconic"],
        "wheelchair_accessible": 1, "pet_friendly": 0, "photography_allowed": 1,
        "family_friendly": 1, "age_restriction": None, "dress_code": None,
        "noise_level": "moderate", "reservation_required": 0, "outside_food_allowed": 0,
        "booking_required": 1, "has_official_site": 1, "window_flags": {},
        "has_wrong_info": False,
    },
    {
        "venue_id": "lon_b01", "name": "The Craft Pub", "category": "bar",
        "district": "Shoreditch", "traffic_tier": "mid", "recommended_pace": "relaxed",
        "price_tier": "mid", "avg_cost_local": 8.0, "lat": 51.521, "lng": -0.075,
        "recommended_visit_minutes": 60, "tags": ["craft-beer", "live-music"],
        "wheelchair_accessible": 0, "pet_friendly": 1, "photography_allowed": 1,
        "family_friendly": 0, "age_restriction": 18, "dress_code": None,
        "noise_level": "lively", "reservation_required": 0, "outside_food_allowed": 1,
        "booking_required": 0, "has_official_site": 0, "window_flags": {},
        "has_wrong_info": False,
    },
    {
        "venue_id": "lon_b02", "name": "Canal Bar", "category": "bar",
        "district": "Camden", "traffic_tier": "low", "recommended_pace": "relaxed",
        "price_tier": "budget", "avg_cost_local": 6.0, "lat": 51.536, "lng": -0.143,
        "recommended_visit_minutes": 60, "tags": ["hidden-gem", "outdoor"],
        "wheelchair_accessible": 0, "pet_friendly": 1, "photography_allowed": 1,
        "family_friendly": 0, "age_restriction": 18, "dress_code": None,
        "noise_level": "moderate", "reservation_required": 0, "outside_food_allowed": 0,
        "booking_required": 0, "has_official_site": 0, "window_flags": {},
        "has_wrong_info": False,
    },
    {
        "venue_id": "lon_p01", "name": "Hidden Garden", "category": "park",
        "district": "Islington", "traffic_tier": "low", "recommended_pace": "relaxed",
        "price_tier": "free", "avg_cost_local": 0.0, "lat": 51.538, "lng": -0.101,
        "recommended_visit_minutes": 45, "tags": ["hidden-gem", "outdoor", "artisan"],
        "wheelchair_accessible": 1, "pet_friendly": 1, "photography_allowed": 1,
        "family_friendly": 1, "age_restriction": None, "dress_code": None,
        "noise_level": "quiet", "reservation_required": 0, "outside_food_allowed": 1,
        "booking_required": 0, "has_official_site": 0, "window_flags": {},
        "has_wrong_info": False,
    },
]

POOL_MAP   = {v["venue_id"]: v for v in TEST_POOL}
UNAVAIL    = {"lon_m01": ["2026-08-23"]}
TEST_WINDOW = {
    "window_id": "lon_carnival_2026",
    "label": "Carnival Week",
    "dates": ["2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26"],
    "anchor_events": [{"date": "2026-08-23", "name": "Notting Hill Carnival", "type": "festival"}],
    "character": "London Carnival week. Notting Hill closed to traffic on Sunday.",
    "conditional_wrong_info_hints": [],
}


def make_dispatch(tool_name, tool_input):
    return _dispatch_task_tool(
        tool_name, tool_input,
        pool=TEST_POOL, pool_map=POOL_MAP,
        window=TEST_WINDOW, city="london",
        handbook=_build_task_handbook(TEST_POOL, TEST_WINDOW, "london"),
        unavailable=UNAVAIL,
    )


# ─────────────────────────────────────────────────────────────────────────────
# [1] _city_centre_from_pool
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] _city_centre_from_pool")

lat, lng = _city_centre_from_pool(TEST_POOL)
check("derives centre from high-traffic venues", True)
# Tate Modern is the only high-traffic venue: lat=51.507, lng=-0.099
check("centre lat ≈ 51.507 (Tate Modern)", abs(lat - 51.507) < 0.001, f"lat={lat:.3f}")
check("centre lng ≈ -0.099", abs(lng - (-0.099)) < 0.001, f"lng={lng:.3f}")

# Pool with no high-traffic → falls back to all coords
pool_no_high = [{**v, "traffic_tier": "mid"} for v in TEST_POOL]
lat2, lng2 = _city_centre_from_pool(pool_no_high)
check("falls back to all venues when no high-traffic", True)
check("fallback lat is mean of all lats",
      abs(lat2 - sum(v["lat"] for v in TEST_POOL) / len(TEST_POOL)) < 0.001)

# Empty pool → Paris fallback
lat3, lng3 = _city_centre_from_pool([])
check("empty pool returns Paris fallback (48.8)", abs(lat3 - 48.8) < 0.1)


# ─────────────────────────────────────────────────────────────────────────────
# [2] _query_pool AND logic
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] _query_pool — AND logic filter engine")

# Single filter
restaurants = _query_pool({"category": "restaurant"}, TEST_POOL)
check("category filter returns 2 restaurants", len(restaurants) == 2)
check("result contains venue_id and name", all("venue_id" in r and "name" in r for r in restaurants))

# Tag filter
halal = _query_pool({"tag": "halal"}, TEST_POOL)
check("tag=halal returns 1 venue", len(halal) == 1)
check("correct venue returned", halal[0]["venue_id"] == "lon_r01")

# Regulation filter — lon_r01, lon_m01, lon_p01 all have wheelchair_accessible=1
wheelchair = _query_pool({"regulation": "wheelchair_accessible"}, TEST_POOL)
check("regulation=wheelchair_accessible returns venues with it=1", len(wheelchair) == 3)
wids = {v["venue_id"] for v in wheelchair}
check("correct venues: lon_r01, lon_m01, lon_p01", wids == {"lon_r01", "lon_m01", "lon_p01"})

# District filter
shoreditch = _query_pool({"district": "Shoreditch"}, TEST_POOL)
check("district=Shoreditch returns 3 venues", len(shoreditch) == 3)

# Traffic tier — lon_r02, lon_b02, lon_p01 all have traffic_tier=low
low_traffic = _query_pool({"traffic_tier": "low"}, TEST_POOL)
check("traffic_tier=low returns 3 venues", len(low_traffic) == 3)

# Multi-filter AND: halal AND wheelchair_accessible
halal_wc = _query_pool({"tag": "halal", "regulation": "wheelchair_accessible"}, TEST_POOL)
check("halal AND wheelchair returns 1 venue", len(halal_wc) == 1)
check("correct venue: lon_r01", halal_wc[0]["venue_id"] == "lon_r01")

# Multi-filter AND: impossible intersection
impossible = _query_pool({"tag": "halal", "regulation": "pet_friendly"}, TEST_POOL)
check("halal AND pet_friendly returns 0 (no overlap)", len(impossible) == 0)

# booking_required filter
booked = _query_pool({"booking_required": True}, TEST_POOL)
check("booking_required=True returns 1 venue", len(booked) == 1)
check("correct venue: lon_m01", booked[0]["venue_id"] == "lon_m01")

# Empty filters → all venues
all_venues = _query_pool({}, TEST_POOL)
check("empty filters returns all 6 venues", len(all_venues) == 6)


# ─────────────────────────────────────────────────────────────────────────────
# [3] _get_venue_detail
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] _get_venue_detail")

detail = _get_venue_detail("lon_r01", POOL_MAP, UNAVAIL)
check("returns detail for known venue", detail is not None)
check("has venue_id", detail["venue_id"] == "lon_r01")
check("has name", detail["name"] == "Halal Grill")
check("has lat/lng", detail.get("lat") == 51.522)
check("has tags list", "halal" in detail.get("tags", []))
check("has regulations dict", isinstance(detail.get("regulations"), dict))
check("wheelchair_accessible in regulations", "wheelchair_accessible" in detail["regulations"])
check("has avg_cost_local", detail.get("avg_cost_local") == 22.0)
check("has recommended_visit_minutes", detail.get("recommended_visit_minutes") == 75)

# Sold-out venue gets unavailable_dates
detail_m = _get_venue_detail("lon_m01", POOL_MAP, UNAVAIL)
check("sold-out venue has unavailable_dates", "2026-08-23" in detail_m.get("unavailable_dates", []))

# Non-sold-out venue has empty unavailable_dates
check("non-sold-out venue has empty unavailable_dates",
      detail.get("unavailable_dates", []) == [])

# Unknown venue returns None
check("unknown venue_id returns None",
      _get_venue_detail("does_not_exist", POOL_MAP, UNAVAIL) is None)


# ─────────────────────────────────────────────────────────────────────────────
# [4] _build_task_handbook — dynamic content
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] _build_task_handbook — dynamic content")

hb = _build_task_handbook(TEST_POOL, TEST_WINDOW, "london")

check("has 'tools' section", "tools" in hb)
check("has 'tags' section", "tags" in hb)
check("has 'pool_fields' section", "pool_fields" in hb)
check("has 'regulations' section", "regulations" in hb)
check("has 'venue_sample' section", "venue_sample" in hb)
check("has 'window' section", "window" in hb)
check("has 'validate' section", "validate" in hb)

# Tags content is derived from actual pool
tags_content = hb["tags"]
check("halal tag appears in tags section", "halal" in tags_content)
check("hidden-gem tag appears in tags section", "hidden-gem" in tags_content)
check("free-entry tag appears in tags section", "free-entry" in tags_content)

# Pool fields lists actual categories
fields_content = hb["pool_fields"]
check("restaurant in pool_fields", "restaurant" in fields_content)
check("museum in pool_fields", "museum" in fields_content)
check("bar in pool_fields", "bar" in fields_content)

# Window content has anchor event
window_content = hb["window"]
check("anchor event in window section", "Notting Hill Carnival" in window_content)
check("window dates in window section", "2026-08-22" in window_content)


# ─────────────────────────────────────────────────────────────────────────────
# [5] HELP tool dispatch
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] HELP tool dispatch")

hb = _build_task_handbook(TEST_POOL, TEST_WINDOW, "london")

r, term = make_dispatch("HELP", {"query": "-h tools"})
check("HELP tools: status ok", r["status"] == "ok")
check("HELP tools: not terminating", not term)
check("HELP tools: content has SUBMIT", "SUBMIT" in r["content"])

r, _ = make_dispatch("HELP", {"query": "-h tags"})
check("HELP tags: halal in content", "halal" in r["content"])

r, _ = make_dispatch("HELP", {"query": "-h pool_fields"})
check("HELP pool_fields: category in content", "category" in r["content"])

r, _ = make_dispatch("HELP", {"query": "-h regulations"})
check("HELP regulations: wheelchair in content", "wheelchair" in r["content"])

r, _ = make_dispatch("HELP", {"query": "-h venue_sample"})
check("HELP venue_sample: avg_cost_local in content", "avg_cost_local" in r["content"])

r, _ = make_dispatch("HELP", {"query": "-h validate"})
check("HELP validate: validate_task_schema in content", "validate_task_schema" in r["content"])


# ─────────────────────────────────────────────────────────────────────────────
# [6] THINK tool dispatch
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] THINK tool dispatch")

r, term = make_dispatch("THINK", {"thought": "I will use Type 2 with pub-hop subgroup."})
check("THINK: status ok", r["status"] == "ok")
check("THINK: not terminating", not term)
check("THINK: logged field present", "logged" in r)


# ─────────────────────────────────────────────────────────────────────────────
# [7] query_pool tool dispatch
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] query_pool tool dispatch")

r, term = make_dispatch("query_pool", {"filters": {"category": "bar"}})
check("query_pool bars: status ok", r["status"] == "ok")
check("query_pool bars: count=2", r["count"] == 2)
check("query_pool bars: not terminating", not term)
check("query_pool bars: note suggests get_venue", "get_venue" in r.get("note", ""))

r, _ = make_dispatch("query_pool", {"filters": {"tag": "halal", "regulation": "wheelchair_accessible"}})
check("query_pool AND: 1 result", r["count"] == 1)

r, _ = make_dispatch("query_pool", {"filters": {"tag": "nonexistent_tag_xyz"}})
check("query_pool no match: count=0, not error", r["count"] == 0 and r["status"] == "ok")


# ─────────────────────────────────────────────────────────────────────────────
# [8] get_venue tool dispatch
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] get_venue tool dispatch")

r, term = make_dispatch("get_venue", {"venue_id": "lon_m01"})
check("get_venue Tate Modern: status ok", r["status"] == "ok")
check("get_venue: not terminating", not term)
check("get_venue: venue dict present", "venue" in r)
check("get_venue: name correct", r["venue"]["name"] == "Tate Modern")
check("get_venue: tags include 'art'", "art" in r["venue"].get("tags", []))
check("get_venue: regulations dict present", isinstance(r["venue"].get("regulations"), dict))
check("get_venue: unavailable_dates has carnival date",
      "2026-08-23" in r["venue"].get("unavailable_dates", []))

r, _ = make_dispatch("get_venue", {"venue_id": "nonexistent_xxx"})
check("get_venue unknown: status error", r["status"] == "error")


# ─────────────────────────────────────────────────────────────────────────────
# [9] estimate_travel tool dispatch
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] estimate_travel tool dispatch")

# Shoreditch pub (51.521, -0.075) to Tate Modern (51.507, -0.099) ≈ 2.1km
r, term = make_dispatch("estimate_travel", {
    "venue_id_a": "lon_b01",
    "venue_id_b": "lon_m01",
    "mode": "walking"
})
check("estimate_travel: status ok", r["status"] == "ok")
check("estimate_travel: not terminating", not term)
check("estimate_travel: distance_km 1.5-3.0km", 1.0 < r["distance_km"] < 3.5,
      f"{r['distance_km']:.2f}km")
check("estimate_travel: walk_minutes 15-35min", 10 < r["walk_minutes"] < 40,
      f"{r['walk_minutes']:.1f}min")
check("estimate_travel: result_minutes matches walking",
      abs(r["result_minutes"] - r["walk_minutes"]) < 0.1)
check("estimate_travel: note mentions buffer", "buffer" in r.get("note", "").lower())

# Transit mode
r2, _ = make_dispatch("estimate_travel", {
    "venue_id_a": "lon_b01",
    "venue_id_b": "lon_m01",
    "mode": "transit"
})
check("estimate_travel transit: result_minutes = transit_minutes",
      abs(r2["result_minutes"] - r2["transit_minutes"]) < 0.1)

# Unknown venue
r3, _ = make_dispatch("estimate_travel", {"venue_id_a": "lon_b01", "venue_id_b": "bad_id"})
check("estimate_travel unknown venue: status error", r3["status"] == "error")

# Venue without coords (add fake one)
pool_no_coord = TEST_POOL + [{
    "venue_id": "nc01", "name": "No Coords", "category": "bar",
    "district": "Soho", "traffic_tier": "mid", "lat": None, "lng": None,
    "tags": [], "has_wrong_info": False,
}]
pool_map_nc = {v["venue_id"]: v for v in pool_no_coord}
r4, _ = _dispatch_task_tool(
    "estimate_travel", {"venue_id_a": "lon_b01", "venue_id_b": "nc01"},
    pool=pool_no_coord, pool_map=pool_map_nc, window=TEST_WINDOW,
    city="london", handbook={}, unavailable={}
)
check("estimate_travel no coords: status error", r4["status"] == "error")


# ─────────────────────────────────────────────────────────────────────────────
# [10] SUBMIT — validation fail → loop continues
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] SUBMIT — validation fail")

# Bad JSON string
r, term = make_dispatch("SUBMIT", {"task_json": "this is not json"})
check("SUBMIT bad JSON: status validation_failed", r["status"] == "validation_failed")
check("SUBMIT bad JSON: not terminating", not term)
check("SUBMIT bad JSON: errors list non-empty", len(r.get("errors", [])) > 0)
check("SUBMIT bad JSON: message guides agent to fix", "fix" in r.get("message", "").lower()
      or "json" in r.get("message", "").lower())

# Structurally invalid task (missing required fields)
bad_task = {"task_id": "test_001", "city": "london"}
r, term = make_dispatch("SUBMIT", {"task_json": json.dumps(bad_task)})
check("SUBMIT missing fields: validation_failed", r["status"] == "validation_failed")
check("SUBMIT missing fields: not terminating", not term)
check("SUBMIT missing fields: errors mention missing fields",
      any("missing" in e.lower() or "rubric" in e.lower() or "days" in e.lower()
          for e in r.get("errors", [])))


# ─────────────────────────────────────────────────────────────────────────────
# [11] SUBMIT — validation pass → loop terminates
# ─────────────────────────────────────────────────────────────────────────────
print("\n[11] SUBMIT — validation pass")

# Build a minimal valid task that should pass validate_task_schema.
# Use type2 (subset selection under ceiling) — exempt from upper/lower bar checks.
# The difficulty comes from the time ceiling, not pool narrowing.
valid_task = {
    "task_id":     "lon_carnival_001",
    "city":        "london",
    "window_id":   "lon_carnival_2026",
    "days":        1,
    "start_date":  "2026-08-22",
    "structural_type": "type2_subset_selection",
    "difficulty":  "medium",
    "public_input": {
        "query": (
            "We only have about 90 minutes to explore — "
            "fit as much as possible into a quick afternoon."
        ),
        "query_resources": {"time_ceiling_minutes": 82},
    },
    "rubric": {
        "hard_constraints": [
            {"id": "hc_001", "type": "hours_check",       "check_method": "code", "params": {}},
            {"id": "hc_002", "type": "no_overlap",        "check_method": "code", "params": {}},
            {"id": "hc_003", "type": "travel_time_hard",  "check_method": "code", "params": {}},
        ],
        "personal_constraints": [
            {
                "id":               "pc_001",
                "score_tier":       "P",
                "hop":              2,
                "check_method":     "code",
                "source_in_profile":"about 90 minutes to explore",
                "description":      "Visit at least 2 venues within the time ceiling",
                "scope":            "category=museum,attraction,park,neighbourhood,restaurant,cafe,bar",
                "condition":        {},
                "aggregation":      {"at_least": 2},
                "consequence":      "p_score_full",
            },
        ],
        "b_score_constraints": [],
        "required_venue_ids": [],
    }
}

r, term = make_dispatch("SUBMIT", {"task_json": json.dumps(valid_task)})
if r["status"] == "accepted":
    check("SUBMIT valid task: accepted", True)
    check("SUBMIT valid task: terminates loop", term)
    check("SUBMIT valid task: task_id in response", r.get("task_id") == "lon_carnival_001")
else:
    # Print errors to help debug
    print(f"    SUBMIT errors: {r.get('errors', [])}")
    print(f"    SUBMIT warnings: {r.get('warnings', [])}")
    check("SUBMIT valid task: accepted", False,
          f"status={r['status']}, errors={r.get('errors', [])[:2]}")
    check("SUBMIT valid task: terminates loop", False)
    check("SUBMIT valid task: task_id in response", False)


# ─────────────────────────────────────────────────────────────────────────────
# [12] run_task_agent dry-run (no API key → returns None)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[12] run_task_agent — no API key")

result, stop_info = run_task_agent(
    city="london",
    window=TEST_WINDOW,
    pool=TEST_POOL,
    type_key="type1",
    model="claude-sonnet-4-20250514",
    api_key=None,   # ← no key
    verbose=False,
)
check("no API key returns None (not crash)", result is None)
check("no API key stop_reason is no_api_key", stop_info.get("stop_reason") == "no_api_key")


# ─────────────────────────────────────────────────────────────────────────────
# [13] Unknown tool graceful error
# ─────────────────────────────────────────────────────────────────────────────
print("\n[13] Unknown tool handling")

r, term = make_dispatch("UNKNOWN_TOOL_XYZ", {})
check("unknown tool: status error", r["status"] == "error")
check("unknown tool: not terminating", not term)
check("unknown tool: message mentions tool name", "UNKNOWN_TOOL_XYZ" in r.get("message", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All E2.5 tests passed ({PASS}/{total})")
else:
    print(f"❌ {FAIL}/{total} tests failed")
import sys as _sys
_sys.exit(0 if FAIL == 0 else 1)
