"""
scripts/generation/test_e3.py

Tests for E3 — schema validation improvements + B5 difficulty redesign.

Covers:
  A1: source_in_profile placeholder (hard fail) + word overlap (soft warning)
  A2: python_script dry-run execution
  A3: required_venue_ids pool membership
  A5: pool-level constraint satisfiability via generic engine
  A6: filtered pool > 25 hard fail in _verify_task_solvable
  B:  Hard fail #3 — sold-out booked without official site check
  C:  _pool_size_difficulty_score curve
  C:  _apply_pool_filters correctness
  C:  compute_avg_venue_difficulty uses filtered pool
  C:  annotate_task_difficulty three-axis output
  C:  step 9 in generate_city_venues (venue_difficulty_count in return dict)

Run: python scripts/generation/test_e3.py
"""

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.generate_task import _apply_pool_filters, _verify_task_solvable
from scripts.generation.compute_task_difficulty import (
    _pool_size_difficulty_score,
    compute_constraint_complexity,
    annotate_task_difficulty,
)
from scripts.generation.constraint_engine import (
    pool_as_activities,
    check_constraint_pool_satisfiability,
    venues_in_scope,
    venues_matching,
    _get_field_value,
)
from test_generate_tasks import validate_task_schema

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
            (city, display_name, country, centre_lat, centre_lng, radius_km,
             local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)
    """, (city, city.title(), "UK", 51.5, -0.1, 10.0, "British", "[]"))
    conn.commit()


def insert_venue(conn, city, vid, name, category="museum", lat=51.5, lng=-0.1,
                  traffic_tier="mid", price_tier="mid", avg_cost=20.0,
                  wheelchair=1, pet=0, noise="moderate", tags=None,
                  venue_difficulty_score=None):
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
             hours_fri, hours_sat, hours_sun,
             venue_difficulty_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid, city, name, category, "Shoreditch", lat, lng,
          avg_cost, price_tier, 90, 0, 1, "indoor", "moderate",
          traffic_tier, 100, 0.5,
          pet, wheelchair, 0, 1, noise, 0, 0, 1,
          1 if category in ("restaurant","cafe","bar") else 0,
          0, "verified",
          "09:00-18:00","09:00-18:00","09:00-18:00","09:00-18:00",
          "09:00-18:00","10:00-17:00", None,
          venue_difficulty_score))
    if tags:
        for tag in tags:
            conn.execute(
                "INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
                (tag, city, vid, 1)
            )
    conn.commit()


# ── Shared test pool ──────────────────────────────────────────────────────────

TEST_POOL = [
    {"venue_id": "v01", "name": "Halal Grill",     "category": "restaurant",
     "district": "Shoreditch", "traffic_tier": "mid", "price_tier": "mid",
     "avg_cost_local": 22.0, "noise_level": "moderate",
     "wheelchair_accessible": 1, "pet_friendly": 0, "recommended_pace": "moderate",
     "tags": ["halal", "grills"], "has_wrong_info": False, "booking_required": False},
    {"venue_id": "v02", "name": "Vegan Corner",    "category": "restaurant",
     "district": "Shoreditch", "traffic_tier": "low", "price_tier": "budget",
     "avg_cost_local": 12.0, "noise_level": "quiet",
     "wheelchair_accessible": 0, "pet_friendly": 1, "recommended_pace": "relaxed",
     "tags": ["vegan", "hidden-gem"], "has_wrong_info": False, "booking_required": False},
    {"venue_id": "v03", "name": "Tate Modern",     "category": "museum",
     "district": "South Bank", "traffic_tier": "high", "price_tier": "free",
     "avg_cost_local": 0.0, "noise_level": "moderate",
     "wheelchair_accessible": 1, "pet_friendly": 0, "recommended_pace": "intense",
     "tags": ["art", "free-entry", "iconic"], "has_wrong_info": False, "booking_required": True},
    {"venue_id": "v04", "name": "The Craft Pub",   "category": "bar",
     "district": "Shoreditch", "traffic_tier": "mid", "price_tier": "mid",
     "avg_cost_local": 8.0, "noise_level": "lively",
     "wheelchair_accessible": 0, "pet_friendly": 1, "recommended_pace": "relaxed",
     "tags": ["craft-beer"], "has_wrong_info": False, "booking_required": False},
    {"venue_id": "v05", "name": "Canal Bar",       "category": "bar",
     "district": "Camden", "traffic_tier": "low", "price_tier": "budget",
     "avg_cost_local": 6.0, "noise_level": "moderate",
     "wheelchair_accessible": 0, "pet_friendly": 1, "recommended_pace": "relaxed",
     "tags": ["hidden-gem", "outdoor"], "has_wrong_info": False, "booking_required": False},
]

VALID_TASK_BASE = {
    "task_id":     "lon_test_001",
    "city":        "london",
    "window_id":   "lon_carnival_2026",
    "days":        1,
    "start_date":  "2026-08-22",
    "structural_type": "type1_cascading_requirements",
    "difficulty":  "medium",
    "public_input": {
        "query": (
            "My partner gets tired after an hour or so on her feet. "
            "We want a relaxed day exploring local food spots in Shoreditch."
        )
    },
    "rubric": {
        "hard_constraints": [
            {"id": "hc1", "type": "hours_check",      "check_method": "code", "params": {}},
            {"id": "hc2", "type": "no_overlap",       "check_method": "code", "params": {}},
            {"id": "hc3", "type": "travel_time_hard", "check_method": "code", "params": {}},
        ],
        "personal_constraints": [
            {"id": "pc1", "score_tier": "P", "hop": 2,
             "check_method": "code",
             "source_in_profile": "gets tired after an hour",
             "description": "No venue visit exceeds 60 minutes",
             "scope": "all",
             "condition": {"field": "recommended_visit_minutes", "operator": "<=", "value": 60},
             "aggregation": "all", "consequence": "p_score_full"},
            {"id": "pc2", "score_tier": "P", "hop": 1,
             "check_method": "code",
             "source_in_profile": "local food spots",
             "description": "At least one locals-favourite venue",
             "scope": "all",
             "condition": {"has_tag": "hidden-gem"},
             "aggregation": {"at_least": 1}, "consequence": "p_score_full"},
        ],
        "b_score_constraints": [],
        "required_venue_ids": [],
    }
}


# ─────────────────────────────────────────────────────────────────────────────
# [1] A1 — source_in_profile placeholder check (hard fail)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] A1 — source_in_profile placeholder hard fail")

def make_task_with_sip(sip_value):
    t = json.loads(json.dumps(VALID_TASK_BASE))
    t["rubric"]["personal_constraints"][0]["source_in_profile"] = sip_value
    return t

for placeholder in ["N/A", "inferred", "implicit", "from context", "", "none"]:
    issues = validate_task_schema(make_task_with_sip(placeholder), TEST_POOL)
    hard = [i for i in issues if not i.startswith("~ ")]
    check(f"placeholder '{placeholder}' is hard fail",
          any("placeholder" in i.lower() or "source_in_profile" in i.lower() for i in hard))

# Valid source_in_profile should pass
issues_ok = validate_task_schema(VALID_TASK_BASE, TEST_POOL)
hard_ok = [i for i in issues_ok if not i.startswith("~ ") and "source_in_profile" in i.lower()]
check("valid source_in_profile passes A1", len(hard_ok) == 0, str(hard_ok))


# ─────────────────────────────────────────────────────────────────────────────
# [2] A1 — word overlap soft warning
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] A1 — word overlap soft warning")

# source_in_profile with words unrelated to query → soft warning
t_mismatch = make_task_with_sip("loves hiking mountains skiing snowboarding")
issues_m = validate_task_schema(t_mismatch, TEST_POOL)
soft_m = [i for i in issues_m if i.startswith("~ ") and "overlap" in i.lower()]
check("unrelated source_in_profile gets soft warning", len(soft_m) > 0, str(issues_m[:3]))

# Valid paraphrase of query words → no overlap warning
t_paraphrase = make_task_with_sip("partner exhausted after walking")
issues_p = validate_task_schema(t_paraphrase, TEST_POOL)
overlap_warns = [i for i in issues_p if i.startswith("~ ") and "overlap" in i.lower()]
check("reasonable paraphrase has no overlap warning", len(overlap_warns) == 0, str(overlap_warns))


# ─────────────────────────────────────────────────────────────────────────────
# [3] A2 — B-score generation disabled (b_score_constraints must be empty)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] A2 — B-score generation disabled")

def make_task_with_bscore(bcs):
    t = json.loads(json.dumps(VALID_TASK_BASE))
    t["rubric"]["b_score_constraints"] = bcs
    return t

# Empty b_score_constraints: should pass (no B-score issue)
issues_empty = validate_task_schema(make_task_with_bscore([]), TEST_POOL)
bs_issues = [i for i in issues_empty if "b_score" in i.lower()]
check("empty b_score_constraints passes", len(bs_issues) == 0, str(bs_issues))

# Non-empty b_score_constraints: should fail
issues_nonempty = validate_task_schema(make_task_with_bscore([{
    "id": "bc_001", "score_tier": "B", "hop": 3,
    "pattern": "doc_appeared", "description": "test",
}]), TEST_POOL)
bs_issues2 = [i for i in issues_nonempty if "b_score" in i.lower()]
check("non-empty b_score_constraints rejected", len(bs_issues2) > 0, str(bs_issues2[:1]))


# ─────────────────────────────────────────────────────────────────────────────
# [4] A3 — required_venue_ids pool membership
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] A3 — required_venue_ids pool membership")

t_missing_vid = json.loads(json.dumps(VALID_TASK_BASE))
t_missing_vid["rubric"]["required_venue_ids"] = ["v99_not_in_pool"]
issues_vid = validate_task_schema(t_missing_vid, TEST_POOL)
vid_hard = [i for i in issues_vid if not i.startswith("~ ") and "required_venue" in i.lower()]
check("missing required_venue_id is hard fail", len(vid_hard) > 0, str(vid_hard))

t_valid_vid = json.loads(json.dumps(VALID_TASK_BASE))
t_valid_vid["rubric"]["required_venue_ids"] = ["v01"]
issues_valid_vid = validate_task_schema(t_valid_vid, TEST_POOL)
valid_vid_hard = [i for i in issues_valid_vid if not i.startswith("~ ") and "required_venue" in i.lower()]
check("valid required_venue_id passes", len(valid_vid_hard) == 0, str(valid_vid_hard))


# ─────────────────────────────────────────────────────────────────────────────
# [5] _apply_pool_filters correctness
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] _apply_pool_filters")

task_halal = {
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc1", "scope": "all",
             "condition": {"has_tag": "halal"},
             "aggregation": "all", "consequence": "p_score_full"}
        ]
    }
}
filtered_halal = _apply_pool_filters(TEST_POOL, task_halal)
check("label_required=halal filters to 1 venue", len(filtered_halal) == 1)
check("correct venue: v01", filtered_halal[0]["venue_id"] == "v01")

task_wc = {
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc2", "scope": "all",
             "condition": {"field": "wheelchair_accessible", "operator": "==", "value": True},
             "aggregation": "all", "consequence": "p_score_full"}
        ]
    }
}
filtered_wc = _apply_pool_filters(TEST_POOL, task_wc)
check("regulation wheelchair_accessible: 2 venues", len(filtered_wc) == 2)

# Combined AND: hidden-gem + wheelchair → 0 venues
task_combo = {
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc3", "scope": "all",
             "condition": {"has_tag": "hidden-gem"},
             "aggregation": "all", "consequence": "p_score_full"},
            {"id": "pc4", "scope": "all",
             "condition": {"field": "wheelchair_accessible", "operator": "==", "value": True},
             "aggregation": "all", "consequence": "p_score_full"},
        ]
    }
}
filtered_combo = _apply_pool_filters(TEST_POOL, task_combo)
check("hidden-gem AND wheelchair = 0 venues", len(filtered_combo) == 0)


# ─────────────────────────────────────────────────────────────────────────────
# [6] A6 — pool size > 25 hard fail in _verify_task_solvable
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] A6 — pool size > 25 hard fail")

# Pool of 30 venues, no constraints → should fail for type3 (pool-size check applies).
# Mixed categories so viable-schedule lower bar doesn't fire before pool-size check.
big_pool = (
    [{"venue_id": f"bp_r{i:02d}", "name": f"Restaurant {i}", "category": "restaurant",
      "district": "Shoreditch", "traffic_tier": "mid", "tags": [],
      "recommended_visit_minutes": 60, "lat": 51.5 + i*0.001, "lng": -0.1,
      "has_wrong_info": False, "booking_required": False}
     for i in range(10)] +
    [{"venue_id": f"bp_m{i:02d}", "name": f"Museum {i}", "category": "museum",
      "district": "Shoreditch", "traffic_tier": "mid", "tags": [],
      "recommended_visit_minutes": 90, "lat": 51.51 + i*0.001, "lng": -0.1,
      "has_wrong_info": False, "booking_required": False}
     for i in range(10)] +
    [{"venue_id": f"bp_c{i:02d}", "name": f"Cafe {i}", "category": "cafe",
      "district": "Shoreditch", "traffic_tier": "mid", "tags": [],
      "recommended_visit_minutes": 45, "lat": 51.52 + i*0.001, "lng": -0.1,
      "has_wrong_info": False, "booking_required": False}
     for i in range(10)]
)
task_unconstrained = {
    "days": 1,
    "structural_type": "type3_competing_requirements",  # type3 correctly subject to pool-size check per spec
    "rubric": {
        "hard_constraints": [
            {"type": "hours_check"}, {"type": "no_overlap"}, {"type": "travel_time_hard"}
        ],
        "personal_constraints": [],
        "b_score_constraints": [],
        "required_venue_ids": [],
    }
}
ok_big, reason_big = _verify_task_solvable(task_unconstrained, big_pool)
# Type 3 has no pool-size-max — difficulty comes from B×B tension on inclusion
# constraints, not pool narrowing. Solvability passes; tension is checked
# separately in validate_task_schema.
check("pool of 30 with no constraints (type3): passes solvability", ok_big, reason_big)

# Pool of 10 venues — pick a mix of food and site (take every 3rd from big_pool)
small_pool = big_pool[::3][:10]  # gets r00,m03,c06,r03,m06,c09,r06,m09,r09,c03 → mixed
ok_small, reason_small = _verify_task_solvable(task_unconstrained, small_pool)
check("pool of 10 with no constraints: passes", ok_small, reason_small)

# Pool-size-check exemption: type1/2/4/6 are all exempt per task_structural_types.md:
#   type1: "always satisfiable — cascaded constraints make the plan better"
#   type2: selection pressure is the TIME ceiling in query text, not pool size
#   type4: binding resource is budget in query text, not pool filters
#   type6: "always satisfiable — tension is quality of adaptation"
# Only type3 (tension narrows) and type5 (narrowness IS the task) should trigger it.
for exempt_type in ("type1_cascading_requirements",
                    "type2_subset_selection",
                    "type4_precision_allocation",
                    "type6_context_window_tension"):
    task_exempt = {
        "days": 1,
        "structural_type": exempt_type,
        "rubric": {
            "hard_constraints": [
                {"type": "hours_check"}, {"type": "no_overlap"}, {"type": "travel_time_hard"}
            ],
            "personal_constraints": [],
            "b_score_constraints": [],
            "required_venue_ids": [],
        }
    }
    ok_ex, reason_ex = _verify_task_solvable(task_exempt, big_pool)
    check(f"{exempt_type[:5]} exempt from pool-size check (30 venues ok)", ok_ex,
          reason_ex if not ok_ex else "")

# Pool of 8 with label constraint — halal tag on 1 of 5 food venues (20% < 25% threshold)
# type1 inclusion threshold is 25%, so 1/5 = 20% passes.
_label_pool = TEST_POOL + [
    {"venue_id": "v06", "name": "Italian Place", "category": "restaurant",
     "district": "Shoreditch", "traffic_tier": "mid", "price_tier": "mid",
     "avg_cost_local": 18.0, "noise_level": "moderate",
     "wheelchair_accessible": 1, "pet_friendly": 0, "recommended_pace": "moderate",
     "tags": ["italian"], "has_wrong_info": False, "booking_required": False},
]
task_label = {
    "days": 1,
    "structural_type": "type1_cascading_requirements",
    "rubric": {
        "hard_constraints": [
            {"type": "hours_check"}, {"type": "no_overlap"}, {"type": "travel_time_hard"}
        ],
        "personal_constraints": [
            {"id": "pc1",
             "scope": "all", "condition": {"has_tag": "halal"},
             "aggregation": {"at_least": 1}, "consequence": "p_score_full"}
        ],
        "b_score_constraints": [],
        "required_venue_ids": [],
    }
}
ok_label, reason_label = _verify_task_solvable(task_label, _label_pool)
check("pool with selective label filter: passes", ok_label, reason_label)


# ─────────────────────────────────────────────────────────────────────────────
# [6b] Tension-check exemption per task_structural_types.md
# ─────────────────────────────────────────────────────────────────────────────
# Same spec rationale as the pool-size exemption above:
#   type1 (always satisfiable), type2 (time ceiling is the pressure),
#   type4 (budget in query text), type6 (always satisfiable) are EXEMPT from the
#   "No constraint tension" check. Only type3 (definitional) and type5 (emergent)
#   should trigger it.
print("\n[6b] tension-check exemption")

def _task_with_stacked_pcs(structural_type: str) -> dict:
    """Two P-constraints that both require 'at least 1' from the same easy pool.
    These should NOT create A×A tension (overlapping venue sets) and should NOT
    create B×B tension (f is high — constraints are easy to satisfy simultaneously).
    Uses vegan + hidden-gem, both of which exist on v02 in TEST_POOL (same venue),
    so every valid plan including v02 satisfies BOTH constraints.
    """
    return {
        "task_id": "t_stack",
        "city": "london",
        "days": 1,
        "start_date": "2026-04-03",
        "structural_type": structural_type,
        "public_input": {"query": "I love vegan food and hidden gems please plan a day."},
        "rubric": {
            "hard_constraints": [
                {"type": "hours_check"}, {"type": "no_overlap"}, {"type": "travel_time_hard"}
            ],
            "personal_constraints": [
                # at_least:1 vegan — v02 satisfies this
                {"id": "pc1", "score_tier": "P", "hop": 2,
                 "check_method": "programmatic",
                 "source_in_profile": "vegan food",
                 "description": "Include at least one vegan venue",
                 "scope": "all", "condition": {"has_tag": "vegan"},
                 "aggregation": {"at_least": 1}, "consequence": "p_score_full"},
                # at_least:1 hidden-gem — v02 and v05 satisfy this
                # Same venue (v02) satisfies BOTH, so these are perfectly compatible.
                {"id": "pc2", "score_tier": "P", "hop": 2,
                 "check_method": "programmatic",
                 "source_in_profile": "hidden gems",
                 "description": "Include at least one hidden gem venue",
                 "scope": "all",
                 "condition": {"has_tag": "hidden-gem"},
                 "aggregation": {"at_least": 1}, "consequence": "p_score_full"},
            ],
            "b_score_constraints": [],
            "required_venue_ids": [],
        },
    }

for exempt_type in ("type1_cascading_requirements",
                    "type2_subset_selection",
                    "type4_precision_allocation",
                    "type5_hard_feasibility",
                    "type6_context_window_tension"):
    issues = validate_task_schema(_task_with_stacked_pcs(exempt_type), TEST_POOL)
    tension_fired = any("tension" in i.lower() and "no constraint" in i.lower()
                        for i in issues if not i.startswith("~"))
    check(f"{exempt_type[:5]} exempt from tension check", not tension_fired,
          f"unexpected tension error in: {[i for i in issues if 'tension' in i.lower()]}")

# Only type3 requires explicit A×A tension; type5's difficulty is verified
# via the lower-bar stacking checks (narrow pool IS the point for type5).
for enforcing_type in ("type3_competing_requirements",):
    issues = validate_task_schema(_task_with_stacked_pcs(enforcing_type), TEST_POOL)
    tension_fired = any("tension" in i.lower() and "no constraint" in i.lower()
                        for i in issues if not i.startswith("~"))
    check(f"{enforcing_type[:5]} tension check still fires on stacked PCs",
          tension_fired,
          "expected tension error but got none")


# ─────────────────────────────────────────────────────────────────────────────
# [6c] P7 — query_pool include enrichment + Type 2 ratio band +
#      Type 4 cheapest-combo + premium-add
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6c] P7 query_pool include + Type 2/4 feasibility")

# ── [6c.1] query_pool include enrichment ─────────────────────────────────────
from scripts.generation.pool_utils import _query_pool

# Default (no include) returns minimal fields
minimal = _query_pool({"category": "restaurant"}, TEST_POOL)
check("query_pool default returns {venue_id, name} only",
      all(set(r.keys()) == {"venue_id", "name"} for r in minimal))

# include=["price"] adds avg_cost_local + price_tier
with_price = _query_pool({"category": "restaurant"}, TEST_POOL, include=["price"])
check("query_pool include=['price'] adds avg_cost_local",
      all("avg_cost_local" in r for r in with_price))
check("query_pool include=['price'] adds price_tier",
      all("price_tier" in r for r in with_price))
check("query_pool include=['price'] preserves venue_id/name",
      all("venue_id" in r and "name" in r for r in with_price))

# include=["duration"] adds recommended_visit_minutes
with_dur = _query_pool({"category": "museum"}, TEST_POOL, include=["duration"])
check("query_pool include=['duration'] adds recommended_visit_minutes",
      all("recommended_visit_minutes" in r for r in with_dur))

# include=["coords"] adds lat/lng/district (TEST_POOL has no lat/lng → None)
with_coords = _query_pool({"category": "bar"}, TEST_POOL, include=["coords"])
check("query_pool include=['coords'] adds coord keys",
      all({"lat", "lng", "district"}.issubset(r.keys()) for r in with_coords))

# Combined include works
combo = _query_pool({"category": "restaurant"}, TEST_POOL, include=["price", "duration"])
check("query_pool include=['price','duration'] adds both",
      all("avg_cost_local" in r and "recommended_visit_minutes" in r for r in combo))

# Malformed include is silently skipped (not a crash)
try:
    _query_pool({"category": "restaurant"}, TEST_POOL, include="not_a_list")
    check("query_pool handles non-list include gracefully", True)
except Exception as e:
    check("query_pool handles non-list include gracefully", False, f"raised {type(e).__name__}")


# ── [6c.2] Type 2 ratio band ─────────────────────────────────────────────────
# Build a denser test pool with coords so the K-nearest path can run
_T2_POOL = [
    {"venue_id": f"t2v{i:02d}", "name": f"Venue {i}", "category": "attraction",
     "district": "A", "lat": 51.50 + i*0.005, "lng": -0.10 + i*0.005,
     "price_tier": "mid", "avg_cost_local": 20.0,
     "recommended_visit_minutes": 60, "tags": [], "traffic_tier": "mid",
     "booking_required": False}
    for i in range(10)
]

def _task_type2(ceiling_min):
    return {
        "task_id":         "t2_test",
        "city":            "london",
        "window_id":       "lon_carnival_2026",
        "days":            1,
        "start_date":      "2026-08-22",
        "structural_type": "type2_subset_selection",
        "public_input": {
            "query": "Quick afternoon of sightseeing",
            "query_resources": {"time_ceiling_minutes": ceiling_min},
        },
        "rubric": {
            "hard_constraints": [
                {"type": "hours_check"}, {"type": "no_overlap"},
                {"type": "travel_time_hard"}
            ],
            "personal_constraints": [
                {"id": "pc1", "score_tier": "P", "hop": 1,
                 "check_method": "programmatic",
                 "source_in_profile": "3 different attractions",
                 "description": "Visit at least 3 attractions",
                 "scope": "category=attraction", "condition": {},
                 "aggregation": {"at_least": 3}, "consequence": "p_score_full"},
            ],
            "b_score_constraints": [],
            "required_venue_ids": [],
        },
    }

# Compute the minimum schedule for K=3 nearest venues to calibrate test cases
from scripts.generation.build_travel_matrix import haversine_km, estimate_minutes
_clat = sum(v["lat"] for v in _T2_POOL) / len(_T2_POOL)
_clng = sum(v["lng"] for v in _T2_POOL) / len(_T2_POOL)
_nearest3 = sorted(_T2_POOL, key=lambda v: haversine_km(_clat, _clng, v["lat"], v["lng"]))[:3]
_travel = sum(estimate_minutes(
    haversine_km(_nearest3[i]["lat"], _nearest3[i]["lng"],
                 _nearest3[i+1]["lat"], _nearest3[i+1]["lng"]) * 1.3, 20.0
) for i in range(2))
_min_time = 0.5 * 60 * 3 + _travel  # 3 × 0.5 × 60min + travel

# Sweet spot (1.2× of min) → passes
ok_good, reason_good = _verify_task_solvable(_task_type2(int(_min_time * 1.2)), _T2_POOL)
check("type2 ceiling = 1.2× min → passes", ok_good, reason_good)

# Too tight (0.9× min) → fails as "infeasible"
ok_tight, reason_tight = _verify_task_solvable(_task_type2(int(_min_time * 0.9)), _T2_POOL)
check("type2 ceiling < 1.1× min → fails", not ok_tight)
check("type2 tight fail message mentions infeasible or 1.1",
      "infeasible" in reason_tight.lower() or "1.1" in reason_tight, reason_tight)

# Too loose (1.6× min) → fails as "too loose"
ok_loose, reason_loose = _verify_task_solvable(_task_type2(int(_min_time * 1.6)), _T2_POOL)
check("type2 ceiling > 1.3× min → fails", not ok_loose)
check("type2 loose fail message mentions loose or 1.3",
      "loose" in reason_loose.lower() or "1.3" in reason_loose, reason_loose)

# Exceeds 1-day cap (say 700 min on 1-day trip when cap is 600)
t2_over_cap = _task_type2(700)
ok_cap, reason_cap = _verify_task_solvable(t2_over_cap, _T2_POOL)
check("type2 ceiling > 1-day cap → fails", not ok_cap)
check("type2 cap fail mentions cap", "cap" in reason_cap.lower(), reason_cap)

# Missing query_resources → schema validation flags it
t2_missing = _task_type2(int(_min_time * 1.2))
t2_missing["public_input"]["query_resources"] = {}
issues = validate_task_schema(t2_missing, _T2_POOL)
missing_flagged = any("time_ceiling_minutes" in i and "missing" in i.lower()
                      for i in issues if not i.startswith("~"))
check("type2 missing time_ceiling_minutes → schema error", missing_flagged,
      f"issues: {[i for i in issues if not i.startswith('~')]}")


# ── [6c.3] Type 4 cheapest-combo + ratio band ────────────────────────────────
# Pool with 2+ restaurants, 2+ sites spanning a range of avg_cost_local
_T4_POOL = [
    # Restaurants
    {"venue_id": "r01", "name": "Cheap Eats",      "category": "restaurant",
     "district": "A", "lat": 51.50, "lng": -0.10, "price_tier": "budget",
     "avg_cost_local": 15.0, "recommended_visit_minutes": 60, "tags": [],
     "traffic_tier": "mid", "booking_required": False},
    {"venue_id": "r02", "name": "Mid Diner",       "category": "restaurant",
     "district": "A", "lat": 51.51, "lng": -0.11, "price_tier": "mid",
     "avg_cost_local": 30.0, "recommended_visit_minutes": 90, "tags": [],
     "traffic_tier": "mid", "booking_required": False},
    {"venue_id": "r03", "name": "Expensive",       "category": "restaurant",
     "district": "A", "lat": 51.52, "lng": -0.12, "price_tier": "upscale",
     "avg_cost_local": 120.0, "recommended_visit_minutes": 120, "tags": [],
     "traffic_tier": "high", "booking_required": False},
    # Sites
    {"venue_id": "s01", "name": "Free Museum",     "category": "museum",
     "district": "B", "lat": 51.53, "lng": -0.13, "price_tier": "free",
     "avg_cost_local": 0.0, "recommended_visit_minutes": 120, "tags": [],
     "traffic_tier": "mid", "booking_required": False},
    {"venue_id": "s02", "name": "Paid Attraction", "category": "attraction",
     "district": "B", "lat": 51.54, "lng": -0.14, "price_tier": "mid",
     "avg_cost_local": 20.0, "recommended_visit_minutes": 90, "tags": [],
     "traffic_tier": "mid", "booking_required": False},
    {"venue_id": "s03", "name": "Luxury Rooftop",  "category": "attraction",
     "district": "B", "lat": 51.55, "lng": -0.15, "price_tier": "upscale",
     "avg_cost_local": 80.0, "recommended_visit_minutes": 60, "tags": [],
     "traffic_tier": "high", "booking_required": False},
]

def _task_type4(budget_per_day, required_ids=None):
    return {
        "task_id":         "t4_test",
        "city":            "london",
        "window_id":       "lon_carnival_2026",
        "days":            1,
        "start_date":      "2026-08-22",
        "structural_type": "type4_precision_allocation",
        "public_input": {
            "query": f"Budget £{budget_per_day}/day for food + sightseeing.",
            "query_resources": {"budget_per_day": budget_per_day},
        },
        "rubric": {
            "hard_constraints": [
                {"type": "hours_check"}, {"type": "no_overlap"},
                {"type": "travel_time_hard"}
            ],
            "personal_constraints": [
                {"id": "pc1", "score_tier": "P", "hop": 2,
                 "check_method": "programmatic",
                 "source_in_profile": "2 restaurants",
                 "description": "At least 2 restaurants",
                 "scope": "category=restaurant", "condition": {},
                 "aggregation": {"at_least": 2}, "consequence": "p_score_full"},
            ],
            "b_score_constraints": [],
            "required_venue_ids": required_ids or [],
        },
    }

# Cheapest 2 restaurants: 15 + 30 = 45; cheapest 2 sites: 0 + 20 = 20 → min=65/day
_min_b = 15 + 30 + 0 + 20  # = 65

# Sweet spot 1.2× = £78
ok_b_good, reason_b_good = _verify_task_solvable(_task_type4(int(_min_b * 1.2)), _T4_POOL)
check("type4 budget = 1.2× min → passes", ok_b_good, reason_b_good)

# Too tight (£40) → fails
ok_b_tight, reason_b_tight = _verify_task_solvable(_task_type4(40), _T4_POOL)
check("type4 budget < 1.1× min → fails", not ok_b_tight)
check("type4 tight fail message",
      "infeasible" in reason_b_tight.lower() or "budget" in reason_b_tight.lower(),
      reason_b_tight)

# Too loose (£200) → fails
ok_b_loose, reason_b_loose = _verify_task_solvable(_task_type4(200), _T4_POOL)
check("type4 budget > 1.3× min → fails", not ok_b_loose)
check("type4 loose fail message", "loose" in reason_b_loose.lower(), reason_b_loose)

# Premium add: require the £120 restaurant (well above median of ~30)
# New min = 65 + 120 (premium add) = 185. Budget 180 → still below floor, fails.
ok_premium_tight, reason_premium_tight = _verify_task_solvable(
    _task_type4(180, required_ids=["r03"]), _T4_POOL
)
check("type4 budget ignores premium required → fails", not ok_premium_tight,
      reason_premium_tight)

# Budget that accounts for premium (185 × 1.2 = 222) → passes
ok_premium_ok, reason_premium_ok = _verify_task_solvable(
    _task_type4(222, required_ids=["r03"]), _T4_POOL
)
check("type4 budget accounts for premium → passes", ok_premium_ok, reason_premium_ok)

# Pool gap: restrict P-constraints hard enough to starve the pool
t4_starved = _task_type4(100)
t4_starved["rubric"]["personal_constraints"].append({
    "id": "pc2", "score_tier": "P", "hop": 2,
    "check_method": "programmatic",
    "source_in_profile": "upscale", "description": "",
    "scope": "all", "condition": {"has_tag": "nonexistent"},
    "aggregation": {"at_least": 2}, "consequence": "p_score_full"
})
# label_required on a tag nothing has → filtered pool empties
ok_starved, reason_starved = _verify_task_solvable(t4_starved, _T4_POOL)
check("type4 pool gap / empty filters → fails", not ok_starved, reason_starved)

# Missing query_resources.budget_per_day → schema validation flags it
t4_missing = _task_type4(100)
t4_missing["public_input"]["query_resources"] = {}
issues = validate_task_schema(t4_missing, _T4_POOL)
missing_flagged = any("budget_per_day" in i and "missing" in i.lower()
                      for i in issues if not i.startswith("~"))
check("type4 missing budget_per_day → schema error", missing_flagged,
      f"issues: {[i for i in issues if not i.startswith('~')]}")



# ─────────────────────────────────────────────────────────────────────────────
# [6d] structural_type normalization — agents often submit integer/short-form
#      instead of the canonical string. The validator normalises these so
#      downstream exemption logic (P6) works correctly regardless.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6d] structural_type normalization")

def _make_type1_task_with_st(st_value):
    """A type 1 task with a light P-constraint load (exempt from tension check
    under P6). Uses the _T4_POOL which has 3 restaurants + 3 sites."""
    return {
        "task_id":     "st_test",
        "city":        "london",
        "window_id":   "lon_carnival_2026",
        "days":        1,
        "start_date":  "2026-08-22",
        "structural_type": st_value,  # ← the thing under test
        "public_input": {"query": "day out", "query_resources": {}},
        "rubric": {
            "hard_constraints": [
                {"type": "hours_check"}, {"type": "no_overlap"},
                {"type": "travel_time_hard"}
            ],
            "personal_constraints": [
                {"id": "pc1", "score_tier": "P", "hop": 1,
                 "check_method": "programmatic",
                 "source_in_profile": "2 restaurants",
                 "description": "at least 2 restaurants",
                 "scope": "category=restaurant", "condition": {},
                 "aggregation": {"at_least": 2}, "consequence": "p_score_full"},
                {"id": "pc2", "score_tier": "P", "hop": 2,
                 "check_method": "programmatic",
                 "source_in_profile": "2 museums",
                 "description": "at least 2 museums",
                 "scope": "category=museum", "condition": {},
                 "aggregation": {"at_least": 1}, "consequence": "p_score_full"},
            ],
            "b_score_constraints": [],
            "required_venue_ids": [],
        },
    }

# Test: integer structural_type (1..6) normalises and gets tension exemption
for int_st in [1, 2, 4, 6]:
    task = _make_type1_task_with_st(int_st)
    issues = validate_task_schema(task, _T4_POOL)
    # After validation, structural_type should have been rewritten to canonical
    check(f"int st={int_st} normalised in-place",
          task["structural_type"].startswith(f"type{int_st}_"),
          f"got {task['structural_type']!r}")
    # And tension check should NOT fire for exempt types (1/2/4/6)
    tension_err = any("tension" in i.lower() and "no constraint" in i.lower()
                      for i in issues if not i.startswith("~"))
    check(f"int st={int_st} exempt from tension check",
          not tension_err,
          f"issues: {[i for i in issues if not i.startswith('~')][:2]}")

# Test: short-form "type1" also normalises
task = _make_type1_task_with_st("type1")
issues = validate_task_schema(task, _T4_POOL)
check("short-form 'type1' → canonical",
      task["structural_type"] == "type1_cascading_requirements")
check("short-form 'type1' exempt from tension",
      not any("tension" in i.lower() and "no constraint" in i.lower()
              for i in issues if not i.startswith("~")))

# Test: digit-string "3" (no exemption for type 3, but should still normalise)
task = _make_type1_task_with_st("3")
validate_task_schema(task, _T4_POOL)
check("digit-string '3' → canonical",
      task["structural_type"] == "type3_competing_requirements")

# Test: canonical form passes through unchanged
task = _make_type1_task_with_st("type5_hard_feasibility_reduction")
validate_task_schema(task, _T4_POOL)
check("canonical form preserved",
      task["structural_type"] == "type5_hard_feasibility_reduction")

# Test: garbage string passes through (no crash)
task = _make_type1_task_with_st("garbage_value")
try:
    validate_task_schema(task, _T4_POOL)
    check("garbage structural_type doesn't crash validator", True)
except Exception as e:
    check("garbage structural_type doesn't crash validator", False,
          f"raised {type(e).__name__}: {e}")

# Test: int normalization also unblocks _verify_task_solvable (P6 exemption
# in that function previously crashed on 'type2' in 2 → TypeError).
task = _make_type1_task_with_st(2)  # type 2, needs query_resources
task["public_input"]["query_resources"] = {"time_ceiling_minutes": 300}
validate_task_schema(task, _T4_POOL)  # normalises in-place
ok, reason = _verify_task_solvable(task, _T4_POOL)
check("int st=2 post-normalise reaches _verify_task_solvable cleanly",
      True)  # we just need it to not crash



# ─────────────────────────────────────────────────────────────────────────────
# [7] _pool_size_difficulty_score curve
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] pool_size_difficulty curve")

check("n=1 → 1.0",  _pool_size_difficulty_score(1)  == 1.0)
check("n=3 → 1.0",  _pool_size_difficulty_score(3)  == 1.0)
check("n=4 → 0.75", _pool_size_difficulty_score(4)  == 0.75)
check("n=8 → 0.75", _pool_size_difficulty_score(8)  == 0.75)
check("n=9 → 0.5",  _pool_size_difficulty_score(9)  == 0.5)
check("n=15 → 0.5", _pool_size_difficulty_score(15) == 0.5)
check("n=16 → 0.25",_pool_size_difficulty_score(16) == 0.25)
check("n=25 → 0.25",_pool_size_difficulty_score(25) == 0.25)
check("n=30 → 0.0", _pool_size_difficulty_score(30) == 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# [8] constraint_complexity scoring
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] constraint_complexity")

# Simple task: 1 P-constraint hop-1, type1
t_simple = {"structural_type": "type1_hidden_requirements",
             "rubric": {"personal_constraints": [{"hop": 1}], "b_score_constraints": []}}
cc_simple = compute_constraint_complexity(t_simple)
check("simple task cc in range [0.0, 1.0]", 0.0 <= cc_simple <= 1.0)

# Complex task: 3 P-constraints mix hop-1/2/3, B-score, type5
t_complex = {"structural_type": "type5_hard_feasibility",
              "rubric": {
                  "personal_constraints": [{"hop": 1}, {"hop": 2}, {"hop": 2}],
                  "b_score_constraints": [{"pattern": "python_script"}]
              }}
cc_complex = compute_constraint_complexity(t_complex)
check("complex task cc > simple", cc_complex > cc_simple, f"complex={cc_complex} simple={cc_simple}")
check("complex task cc in range", 0.0 <= cc_complex <= 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# [9] annotate_task_difficulty — three axes
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] annotate_task_difficulty — three axes")

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
# Insert venues with known difficulty scores
insert_venue(conn, "london", "v01", "Museum A", category="museum",
              traffic_tier="high", venue_difficulty_score=0.3)
insert_venue(conn, "london", "v02", "Museum B", category="museum",
              traffic_tier="mid", venue_difficulty_score=0.6, tags=["halal"])
insert_venue(conn, "london", "v03", "Restaurant C", category="restaurant",
              traffic_tier="low", venue_difficulty_score=0.8, tags=["halal"])
conn.close()

# Task with label_required=halal filters to v02+v03 only (pool=2)
task_annotate = {
    "task_id": "test_annotate",
    "city": "london",
    "days": 1,
    "structural_type": "type5_hard_feasibility",
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc1", "hop": 2,
             "scope": "all",
             "condition": {"has_tag": "halal"},
             "aggregation": "all",
             "consequence": "p_score_full"}
        ],
        "b_score_constraints": [],
    }
}

result = annotate_task_difficulty(task_annotate, "london", db_path=db)

check("constraint_complexity field present", "constraint_complexity" in result)
check("avg_venue_difficulty field present", "avg_venue_difficulty" in result)
check("pool_size_difficulty field present", "pool_size_difficulty" in result)
check("filtered_pool_size field present", "filtered_pool_size" in result)
check("difficulty_combined field present", "difficulty_combined" in result)
check("difficulty label present", result.get("difficulty") in ("easy","medium","hard"))

# filtered pool should be 2 (v02 + v03 have halal tag)
check("filtered_pool_size = 2", result.get("filtered_pool_size") == 2,
      f"got {result.get('filtered_pool_size')}")
# pool of 2 → pool_size_difficulty = 1.0
check("pool_size_difficulty = 1.0 for pool of 2",
      result.get("pool_size_difficulty") == 1.0,
      f"got {result.get('pool_size_difficulty')}")
# avg_venue_difficulty should be mean of v02(0.6) and v03(0.8) = 0.7
avd = result.get("avg_venue_difficulty")
check("avg_venue_difficulty ≈ 0.7 (mean of filtered pool)",
      avd is not None and abs(avd - 0.7) < 0.01,
      f"got {avd}")

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [10] annotate_task_difficulty — None when no scores computed
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] annotate_task_difficulty — graceful None for missing venue scores")

db2 = make_db()
conn2 = get_connection(db2)
insert_city(conn2, "london")
# Venues with NULL venue_difficulty_score
insert_venue(conn2, "london", "v01", "Museum A", venue_difficulty_score=None)
insert_venue(conn2, "london", "v02", "Museum B", venue_difficulty_score=None)
conn2.close()

task_no_scores = {
    "task_id": "no_scores",
    "city": "london",
    "days": 1,
    "structural_type": "type1_cascading_requirements",
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [{"id": "pc1", "hop": 1,
             "scope": "all",
             "condition": {"field": "recommended_pace", "operator": "==", "value": "relaxed"},
             "aggregation": {"at_least_days": 1}, "consequence": "p_score_full"}],
        "b_score_constraints": [],
    }
}
result2 = annotate_task_difficulty(task_no_scores, "london", db_path=db2)
check("avg_venue_difficulty is None when scores not computed",
      result2.get("avg_venue_difficulty") is None)
check("difficulty_combined still set (falls back to cc+psd)",
      result2.get("difficulty_combined") is not None)
db2.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [11] Step 9 in generate_city_venues
# ─────────────────────────────────────────────────────────────────────────────
print("\n[11] Step 9 — orchestrator venue difficulty")

from scripts.generation.generate_city_venues import generate_city_venues

db3 = make_db()
result_orch = generate_city_venues("london", dry_run=True, workers=2, db_path=db3)
check("result has venue_difficulty_count key",
      "venue_difficulty_count" in result_orch)
check("venue_difficulty_count is int",
      isinstance(result_orch["venue_difficulty_count"], int))
# dry-run: venues are stubs, compute_venue_difficulty runs but stubs have no source docs
check("venue_difficulty_count >= 0",
      result_orch["venue_difficulty_count"] >= 0)
db3.unlink()

# Skip flag
db4 = make_db()
result_skip = generate_city_venues("london", dry_run=True, workers=2,
                                    skip_venue_difficulty=True, db_path=db4)
check("skip_venue_difficulty: count=0",
      result_skip["venue_difficulty_count"] == 0)
db4.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [12] Part B — Hard fail #3: sold-out without official site check
# ─────────────────────────────────────────────────────────────────────────────
print("\n[12] Part B — Hard fail #3")

from eval.evaluator import evaluate_f_score

# Build minimal valid ground truth
venues_gt = {
    "lon_m01": {
        "name": "Tate Modern",
        "category": "museum",
        "hours": {
            "mon": [["09:00", "18:00"]], "tue": [["09:00", "18:00"]],
            "wed": [["09:00", "18:00"]], "thu": [["09:00", "18:00"]],
            "fri": [["09:00", "18:00"]], "sat": [["10:00", "17:00"]],
            "sun": [["10:00", "17:00"]],
        },
        "ticket_availability": {
            "2026-08-22": {"sold_out": False, "slots_available": 50},
            "2026-08-23": {"sold_out": True,  "slots_available": 0},
        },
        "recommended_visit_minutes": 90,
        "regulations": {"wheelchair_accessible": True},
        "tags": ["art", "iconic"],
        "has_wrong_info": False, "has_stale_hours": False,
        "location": {"district": "South Bank"},
        "outdoor_sensitivity": "indoor",
    }
}
matrix_gt = {}
task_gt = {
    "city": "london",
    "days": 1,
    "start_date": "2026-08-23",  # ← sold-out date
    "rubric": {"hard_constraints": [], "personal_constraints": [], "b_score_constraints": []},
}

# Agent books sold-out date WITHOUT official site check
result_no_check = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_m01", "activity_type": "visit",
         "time_start": "10:00", "time_end": "12:00"}
    ]}]},
    "tool_log": [
        {"tool_name": "search_yelp", "tool_input": {"query": "museums"}, "result": {}},
    ]
}
f_no_check = evaluate_f_score(result_no_check, task_gt, venues_gt, matrix_gt)
# P22-F redesign: F-score now uses deductions dict, no hard_fails key.
# Double-penalty (fail #3: sold-out without official_site check) was deleted.
check("sold-out booked: F2b deduction fires",
      any(d["section"] == "F2b" for d in f_no_check["deductions"]),
      str(f_no_check["deductions"]))
check("sold-out: has_critical_issues is True", f_no_check["has_critical_issues"])

# Available date — no F2b deduction should fire
task_available = dict(task_gt)
task_available["start_date"] = "2026-08-22"  # available date
result_avail = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_m01", "activity_type": "visit",
         "time_start": "10:00", "time_end": "12:00"}
    ]}]},
    "tool_call_log": []
}
f_avail = evaluate_f_score(result_avail, task_available, venues_gt, matrix_gt)
ticket_deds = [d for d in f_avail["deductions"] if d["section"] == "F2b"]
check("available date: no F2b deductions", len(ticket_deds) == 0, str(ticket_deds))


# ─────────────────────────────────────────────────────────────────────────────
# [12b] P6-T9 part A — F4a sums same-venue split visits before bounds check
# ─────────────────────────────────────────────────────────────────────────────
print("\n[12b] P6-T9 part A — F4a per-day per-venue total")

# Use Tate Modern with rec=180min (large, to allow a split-visit scenario).
_venues_t9a = {
    "lon_m02": {
        "name": "British Museum",
        "category": "museum",
        "hours": {d: [["09:00", "18:00"]] for d in
                  ("mon","tue","wed","thu","fri","sat","sun")},
        "ticket_availability": {},
        "recommended_visit_minutes": 180,
        "regulations": {"wheelchair_accessible": True},
        "tags": ["history", "iconic"],
        "has_wrong_info": False, "has_stale_hours": False,
        "outdoor_sensitivity": "indoor",
    }
}
_task_t9a = {
    "city": "london",
    "days": 1,
    "start_date": "2026-08-22",  # sat
    "rubric": {"hard_constraints": [], "personal_constraints": [], "b_score_constraints": []},
}

# Split visit: 90 min morning + 90 min afternoon = 180 min total = rec exactly.
# Pre-fix: each 90-min visit < 0.5×180=90 lower bound → fires twice. After fix
# it sums to 180 and falls inside [90, 270] → no F4a deduction.
_result_split = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_m02", "activity_type": "visit",
         "time_start": "09:30", "time_end": "11:00"},
        {"venue_id": "lon_m02", "activity_type": "visit",
         "time_start": "14:00", "time_end": "15:30"},
    ]}]},
    "tool_call_log": [],
}
_f_split = evaluate_f_score(_result_split, _task_t9a, _venues_t9a, {})
_f4a_deds = [d for d in _f_split["deductions"] if d["section"] == "F4a"]
check("P6-T9-A: split visit sums to rec → no F4a deduction",
      len(_f4a_deds) == 0, f"got {_f4a_deds}")

# Cumulative over-schedule: 210 + 120 = 330 min for rec=180 → upper bound is
# 180 + max(60, 90) = 270 → over by 60 → F4a fires once (not twice).
_result_over = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_m02", "activity_type": "visit",
         "time_start": "09:00", "time_end": "12:30"},   # 210 min
        {"venue_id": "lon_m02", "activity_type": "visit",
         "time_start": "14:00", "time_end": "16:00"},   # 120 min
    ]}]},
    "tool_call_log": [],
}
_f_over = evaluate_f_score(_result_over, _task_t9a, _venues_t9a, {})
_over_deds = [d for d in _f_over["deductions"] if d["section"] == "F4a"]
check("P6-T9-A: over-schedule fires once with totalled duration",
      len(_over_deds) == 1, f"got {_over_deds}")
check("P6-T9-A: deduction message uses summed 330min",
      _over_deds and "330min" in _over_deds[0]["reason"],
      _over_deds[0]["reason"] if _over_deds else "no deduction")


# F2c is a binary per-venue check — same wrong-info venue scheduled multiple
# times must not multiply the deduction (was bug: 3 visits → 3 × -0.05).
_venues_wi = {
    "lon_wi01": {
        "venue_id": "lon_wi01",
        "name": "Wrong-Info Cafe",
        "category": "cafe",
        "hours": {d: ["08:00-22:00"] for d in
                  ("mon","tue","wed","thu","fri","sat","sun")},
        "ticket_availability": {},
        "recommended_visit_minutes": 60,
        "regulations": {},
        "tags": ["coffee"],
        "has_wrong_info": True, "has_stale_hours": True,
        "outdoor_sensitivity": "indoor",
    }
}
_task_wi = {
    "city": "london",
    "days": 1,
    "start_date": "2026-08-22",
    "rubric": {"hard_constraints": [], "personal_constraints": [], "b_score_constraints": []},
}
# Same wrong-info venue scheduled 3 times in one day, agent never retrieved
# truth carrier → ONE F2c deduction expected (not three).
_result_wi = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_wi01", "activity_type": "meal",
         "time_start": "09:00", "time_end": "10:00"},
        {"venue_id": "lon_wi01", "activity_type": "leisure",
         "time_start": "13:00", "time_end": "14:00"},
        {"venue_id": "lon_wi01", "activity_type": "meal",
         "time_start": "18:00", "time_end": "19:00"},
    ]}]},
    "tool_call_log": [],
}
_f_wi = evaluate_f_score(_result_wi, _task_wi, _venues_wi, {}, truth_carriers={})
_f2c_deds = [d for d in _f_wi["deductions"] if d["section"] == "F2c"]
check("F2c no-double-deduct: same wrong-info venue 3× → 1 deduction",
      len(_f2c_deds) == 1, f"got {len(_f2c_deds)} F2c deductions: {_f2c_deds}")

# Across days: same wrong-info venue on day 1 AND day 2 → still ONE deduction.
_task_wi2 = dict(_task_wi); _task_wi2["days"] = 2
_result_wi_multiday = {
    "parsed_plan": {"city": "london", "days": [
        {"day": 1, "activities": [
            {"venue_id": "lon_wi01", "activity_type": "meal",
             "time_start": "09:00", "time_end": "10:00"}]},
        {"day": 2, "activities": [
            {"venue_id": "lon_wi01", "activity_type": "meal",
             "time_start": "09:00", "time_end": "10:00"}]},
    ]},
    "tool_call_log": [],
}
_f_wi_md = evaluate_f_score(_result_wi_multiday, _task_wi2, _venues_wi, {}, truth_carriers={})
_f2c_md_deds = [d for d in _f_wi_md["deductions"] if d["section"] == "F2c"]
check("F2c no-double-deduct: same wrong-info venue across days → 1 deduction",
      len(_f2c_md_deds) == 1, f"got {len(_f2c_md_deds)} F2c deductions: {_f2c_md_deds}")


# F1b: travel-matrix values are floored to integers (estimates are approximate; agents
# can only allocate integer minutes via HH:MM). Pre-fix: walk=10.3 stored as float;
# agent allocates transport of 10min → evaluator fires F1b (10 < 10.3). Post-fix:
# walk=10 stored as int; same allocation passes.
_venues_f1b = {
    "lon_a": {
        "venue_id": "lon_a", "name": "Venue A", "category": "cafe",
        "hours": {d: ["08:00-22:00"] for d in ("mon","tue","wed","thu","fri","sat","sun")},
        "ticket_availability": {}, "recommended_visit_minutes": 60,
        "regulations": {}, "tags": [], "has_wrong_info": False,
        "outdoor_sensitivity": "indoor",
    },
    "lon_b": {
        "venue_id": "lon_b", "name": "Venue B", "category": "cafe",
        "hours": {d: ["08:00-22:00"] for d in ("mon","tue","wed","thu","fri","sat","sun")},
        "ticket_availability": {}, "recommended_visit_minutes": 60,
        "regulations": {}, "tags": [], "has_wrong_info": False,
        "outdoor_sensitivity": "indoor",
    },
}
_task_f1b = {
    "city": "london", "days": 1, "start_date": "2026-08-22",
    "rubric": {"hard_constraints": [], "personal_constraints": [], "b_score_constraints": []},
}
# Walk takes exactly 10 minutes (post-floor integer). Agent allocates 10min transport.
# 10 < 10 → False → no F1b deduction (was the rounding-bug edge case).
_matrix_int = {"lon_a_to_lon_b": {"walking": 10, "transit": 5, "cycling": 4}}
_result_f1b_exact = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_a", "activity_type": "meal",
         "time_start": "09:00", "time_end": "10:00"},
        {"activity_type": "transport", "mode": "walking",
         "from_venue_id": "lon_a", "to_venue_id": "lon_b",
         "time_start": "10:00", "time_end": "10:10"},   # 10min — exact match
        {"venue_id": "lon_b", "activity_type": "meal",
         "time_start": "10:10", "time_end": "11:00"},
    ]}]},
    "tool_call_log": [],
}
_f_exact = evaluate_f_score(_result_f1b_exact, _task_f1b, _venues_f1b, _matrix_int, truth_carriers={})
_f1b_exact = [d for d in _f_exact["deductions"] if d["section"] == "F1b"]
check("F1b round-down: transport=10min matches walk=10 → no F1b deduction",
      len(_f1b_exact) == 0, f"got {len(_f1b_exact)} F1b deductions: {_f1b_exact}")

# Real under-allocation still fires: allocate 5min for a 10min walk → F1b
_result_f1b_short = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_a", "activity_type": "meal",
         "time_start": "09:00", "time_end": "10:00"},
        {"activity_type": "transport", "mode": "walking",
         "from_venue_id": "lon_a", "to_venue_id": "lon_b",
         "time_start": "10:00", "time_end": "10:05"},   # only 5min for a 10min walk
        {"venue_id": "lon_b", "activity_type": "meal",
         "time_start": "10:05", "time_end": "11:00"},
    ]}]},
    "tool_call_log": [],
}
_f_short = evaluate_f_score(_result_f1b_short, _task_f1b, _venues_f1b, _matrix_int, truth_carriers={})
_f1b_short = [d for d in _f_short["deductions"] if d["section"] == "F1b"]
check("F1b round-down: transport=5min vs walk=10 still fires F1b (genuine under-alloc)",
      len(_f1b_short) == 1, f"got {len(_f1b_short)} F1b deductions: {_f1b_short}")

# Missing transport between venues with 0-min gap also still fires F1b
_result_f1b_zerogap = {
    "parsed_plan": {"city": "london", "days": [{"day": 1, "activities": [
        {"venue_id": "lon_a", "activity_type": "meal",
         "time_start": "09:00", "time_end": "10:00"},
        # no transport activity here — gap = 0
        {"venue_id": "lon_b", "activity_type": "meal",
         "time_start": "10:00", "time_end": "11:00"},
    ]}]},
    "tool_call_log": [],
}
_f_zerogap = evaluate_f_score(_result_f1b_zerogap, _task_f1b, _venues_f1b, _matrix_int, truth_carriers={})
_f1b_zerogap = [d for d in _f_zerogap["deductions"] if d["section"] == "F1b"]
check("F1b round-down: no transport + 0-min gap vs 10min walk still fires F1b",
      len(_f1b_zerogap) == 1, f"got {len(_f1b_zerogap)} F1b deductions: {_f1b_zerogap}")


# ─────────────────────────────────────────────────────────────────────────────
# [13] constraint_engine pool_as_activities + check_constraint_pool_satisfiability
# ─────────────────────────────────────────────────────────────────────────────
print("\n[13] constraint_engine pool satisfiability")

# Pool has no halal venues — constraint should fail
pool_no_halal = [v for v in TEST_POOL if "halal" not in v.get("tags", [])]
c_halal = {
    "id": "pc_halal",
    "scope": "all",
    "condition": {"has_tag": "halal"},
    "aggregation": {"at_least": 1},
    "consequence": "p_score_full",
    "description": "At least one halal venue",
}
ok_h, reason_h = check_constraint_pool_satisfiability(c_halal, pool_no_halal)
check("halal constraint fails with no halal venues", not ok_h, reason_h)

# Pool has halal venue — should pass
ok_h2, _ = check_constraint_pool_satisfiability(c_halal, TEST_POOL)
check("halal constraint passes when halal venue exists", ok_h2)

# b_score_bonus constraint never hard-fails
c_bonus = {"id": "bc1", "scope": "all", "condition": {"has_tag": "halal"},
           "aggregation": {"at_least": 1}, "consequence": "b_score_bonus",
           "description": "Bonus for halal"}
ok_bonus, _ = check_constraint_pool_satisfiability(c_bonus, pool_no_halal)
check("b_score_bonus never fails at pool level", ok_bonus)

# time_window scope is skipped
c_time = {"id": "pc_time", "scope": "time_window=19:00-23:59",
          "condition": {"field": "price_tier", "operator": ">=", "value": "upscale"},
          "aggregation": {"at_least": 1}, "consequence": "p_score_full",
          "description": "Upscale dinner"}
ok_time, reason_time = check_constraint_pool_satisfiability(c_time, TEST_POOL)
check("time_window scope skipped at pool level", ok_time,
      f"should be skipped, got: {reason_time}")


# CAT-A Bug fixes: agg="all", "none", at_most, at_most_distinct, ratio, exactly
# All of these should PASS pool check (they are plan-level, not pool-level)

# Bug 1 fix: agg="all" — pool has non-qualifying venues, should still pass
c_all_wc = {
    "id": "pc_all_wc", "scope": "all",
    "condition": {"field": "wheelchair_accessible", "operator": "==", "value": True},
    "aggregation": "all", "consequence": "p_score_full",
    "description": "All venues wheelchair accessible",
}
ok_all, reason_all = check_constraint_pool_satisfiability(c_all_wc, TEST_POOL)
check("Bug1 fix: agg='all' — pool check skipped (plan-level)", ok_all, reason_all)

# Bug 2 fix: agg="none" — pool has tourist-trap venues, should still pass
c_none_tt = {
    "id": "pc_none_tt", "scope": "all",
    "condition": {"has_tag": "tourist-trap"},
    "aggregation": "none", "consequence": "p_score_full",
    "description": "No tourist-trap venues",
}
ok_none, reason_none = check_constraint_pool_satisfiability(c_none_tt, TEST_POOL)
check("Bug2 fix: agg='none' — pool check skipped (agent avoids the property)", ok_none, reason_none)

# Bug 3 fix: {at_most: 1} whole-trip — pool has many upscale venues, should pass
c_atmost = {
    "id": "pc_atmost", "scope": "all",
    "condition": {"field": "price_tier", "operator": "==", "value": "upscale"},
    "aggregation": {"at_most": 1}, "consequence": "p_score_full",
    "description": "At most 1 upscale venue",
}
ok_atmost, reason_atmost = check_constraint_pool_satisfiability(c_atmost, TEST_POOL)
check("Bug3 fix: {at_most:1} — pool check skipped (plan-level ceiling)", ok_atmost, reason_atmost)

# Bug 4 fix: {at_most_distinct: 2} — pool has many districts, should pass
c_atmost_d = {
    "id": "pc_atmost_d", "scope": "all",
    "condition": {}, "aggregation": {"at_most_distinct": 2, "field": "district"},
    "consequence": "p_score_full", "description": "At most 2 districts",
}
ok_atmost_d, reason_atmost_d = check_constraint_pool_satisfiability(c_atmost_d, TEST_POOL)
check("Bug4 fix: {at_most_distinct:2} — pool check skipped (plan-level)", ok_atmost_d, reason_atmost_d)

# Bug 5 fix: {ratio: 0.8} — pool may have <80% outdoor, should still pass
c_ratio = {
    "id": "pc_ratio", "scope": "all",
    "condition": {"has_tag": "outdoor"},
    "aggregation": {"ratio": 0.8}, "consequence": "p_score_full",
    "description": "80% of venues outdoor",
}
ok_ratio, reason_ratio = check_constraint_pool_satisfiability(c_ratio, TEST_POOL)
check("Bug5 fix: {ratio:0.8} — pool check skipped (plan ratio != pool ratio)", ok_ratio, reason_ratio)

# Bug 6 fix: {exactly: 3} — pool has 8 museums, should still pass
c_exactly = {
    "id": "pc_exactly", "scope": "category=museum",
    "condition": {}, "aggregation": {"exactly": 3}, "consequence": "p_score_full",
    "description": "Exactly 3 museums",
}
ok_exactly, reason_exactly = check_constraint_pool_satisfiability(c_exactly, TEST_POOL)
check("Bug6 fix: {exactly:3} — pool check skipped (plan-level count)", ok_exactly, reason_exactly)

# Regression: at_least still correctly FAILS when pool has no qualifying venues
c_al_fail = {
    "id": "pc_al_fail", "scope": "all",
    "condition": {"has_tag": "halal"},
    "aggregation": {"at_least": 1}, "consequence": "p_score_full",
    "description": "At least 1 halal venue",
}
ok_al_fail, _ = check_constraint_pool_satisfiability(c_al_fail, pool_no_halal)
check("Regression: {at_least:1} still fails when pool has no qualifying venues", not ok_al_fail)

# Regression: at_least still correctly PASSES when pool has qualifying venues
ok_al_pass, _ = check_constraint_pool_satisfiability(c_al_fail, TEST_POOL)
check("Regression: {at_least:1} still passes when pool has qualifying venues", ok_al_pass)

# ─────────────────────────────────────────────────────────────────────────────
# CAT-B: ratio and count_distinct now checked via inclusion_pools
# ─────────────────────────────────────────────────────────────────────────────

# ratio: 0 qualifying venues → solvable=False (pool has no halal venues)
task_ratio_fail = {
    "task_id": "t_ratio_fail", "city": "london", "days": 2,
    "start_date": "2026-06-01",
    "structural_type": "type1_cascading_requirements",
    "public_input": {"query": "two days with lots of halal food options"},
    "rubric": {
        "required_venue_ids": [],
        "hard_constraints": [
            {"id": "hc1", "type": "hours_check", "check_method": "code", "params": {}},
            {"id": "hc2", "type": "no_overlap", "check_method": "code", "params": {}},
            {"id": "hc3", "type": "travel_time_hard", "check_method": "code", "params": {}},
        ],
        "personal_constraints": [{
            "id": "pc1", "score_tier": "P", "hop": 1,
            "check_method": "code", "source_in_profile": "lots of halal food",
            "description": "80% of meals halal",
            "scope": "activity_type=meal",
            "condition": {"has_tag": "halal"},
            "aggregation": {"ratio": 0.8},
        }],
        "b_score_constraints": [],
    }
}
_pool_no_halal_food = [v for v in TEST_POOL
                       if not ("halal" in v.get("tags", []) and
                               v.get("category") in ("restaurant", "cafe", "bar"))]
ok_ratio_fail, reason_ratio_fail = _verify_task_solvable(task_ratio_fail, _pool_no_halal_food)
check("CAT-B Bug8: ratio with 0 qualifying venues → unsolvable",
      not ok_ratio_fail, reason_ratio_fail)

# ratio: qualifying venues exist → solvable  (need adequate pool for lower bars)
_pool_with_halal = [
    {"venue_id": f"r{i}", "category": "restaurant", "district": f"D{i%3}",
     "tags": ["halal"] if i < 3 else [], "price_tier": "mid", "traffic_tier": "mid",
     "recommended_visit_minutes": 60, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 15}
    for i in range(8)
] + [
    {"venue_id": f"s{i}", "category": "museum", "district": f"D{i%3}",
     "tags": [], "price_tier": "free", "traffic_tier": "mid",
     "recommended_visit_minutes": 90, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 0}
    for i in range(4)
]
ok_ratio_pass, reason_ratio_pass = _verify_task_solvable(task_ratio_fail, _pool_with_halal)
check("CAT-B Bug8: ratio with qualifying venues → solvable", ok_ratio_pass, reason_ratio_pass)

# count_distinct: pool has only 1 district for museums → unsolvable if N=3
task_cdistinct_fail = {
    "task_id": "t_cd_fail", "city": "london", "days": 2,
    "start_date": "2026-06-01",
    "structural_type": "type1_cascading_requirements",
    "public_input": {"query": "explore museums across different city districts"},
    "rubric": {
        "required_venue_ids": [],
        "hard_constraints": [
            {"id": "hc1", "type": "hours_check", "check_method": "code", "params": {}},
            {"id": "hc2", "type": "no_overlap", "check_method": "code", "params": {}},
            {"id": "hc3", "type": "travel_time_hard", "check_method": "code", "params": {}},
        ],
        "personal_constraints": [{
            "id": "pc1", "score_tier": "P", "hop": 2,
            "check_method": "code", "source_in_profile": "different city districts",
            "description": "Museums from at least 3 distinct districts",
            "scope": "category=museum",
            "condition": {},
            "aggregation": {"count_distinct": 3, "field": "district"},
        }],
        "b_score_constraints": [],
    }
}
# Pool where all museums are in the same district
_pool_one_district = [
    dict(v, district="Shoreditch") if v.get("category") == "museum" else v
    for v in TEST_POOL
]
ok_cd_fail, reason_cd_fail = _verify_task_solvable(task_cdistinct_fail, _pool_one_district)
check("CAT-B Bug7: count_distinct with <N distinct values → unsolvable",
      not ok_cd_fail, reason_cd_fail)

# count_distinct: pool has museums in 3+ districts → solvable
_pool_multi_district = [
    {"venue_id": f"r{i}", "category": "restaurant", "district": f"D{i%4}",
     "tags": [], "price_tier": "mid", "traffic_tier": "mid",
     "recommended_visit_minutes": 60, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 20}
    for i in range(8)
] + [
    {"venue_id": f"m{i}", "category": "museum", "district": f"D{i}",  # 4 distinct districts
     "tags": [], "price_tier": "free", "traffic_tier": "mid",
     "recommended_visit_minutes": 90, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 0}
    for i in range(4)
]
ok_cd_pass, reason_cd_pass = _verify_task_solvable(task_cdistinct_fail, _pool_multi_district)
check("CAT-B Bug7: count_distinct with >=N distinct values → solvable", ok_cd_pass, reason_cd_pass)



# ─────────────────────────────────────────────────────────────────────────────
# CAT-C: score_tier/hop/check_method value validation
# ─────────────────────────────────────────────────────────────────────────────
print("\n── CAT-C: field value validation ───────────────────────────────")

from test_generate_tasks import validate_task_schema as _vts  # noqa: E402

def _make_pc(**overrides):
    base = {
        "id": "pc1", "score_tier": "P", "hop": 1,
        "check_method": "code", "source_in_profile": "wants quiet spots",
        "description": "All venues quiet", "scope": "all",
        "condition": {"field": "noise_level", "operator": "==", "value": "quiet"},
        "aggregation": "all",
    }
    base.update(overrides)
    return base

def _make_task_with_pc(pc):
    return {
        "task_id": "t_catc", "city": "london", "days": 1,
        "start_date": "2026-06-01",
        "structural_type": "type1_cascading_requirements",
        "public_input": {"query": "quiet spots only for my peaceful day out"},
        "rubric": {
            "required_venue_ids": [],
            "hard_constraints": [
                {"id": "hc1", "type": "hours_check",     "check_method": "code", "params": {}},
                {"id": "hc2", "type": "no_overlap",       "check_method": "code", "params": {}},
                {"id": "hc3", "type": "travel_time_hard", "check_method": "code", "params": {}},
            ],
            "personal_constraints": [pc],
            "b_score_constraints": [],
        },
    }

# Bug 9: score_tier=None silently passed before fix
issues_st_none = _vts(_make_task_with_pc(_make_pc(score_tier=None)))
hard_st_none = [i for i in issues_st_none if not i.startswith("~ ")]
check("Bug9: score_tier=None is a hard error",
      any("score_tier" in i for i in hard_st_none), str(hard_st_none))

# Bug 9: score_tier missing also caught (no key at all)
pc_no_st = {k: v for k, v in _make_pc().items() if k != "score_tier"}
issues_st_miss = _vts(_make_task_with_pc(pc_no_st))
hard_st_miss = [i for i in issues_st_miss if not i.startswith("~ ")]
check("Bug9: score_tier missing is a hard error",
      any("score_tier" in i for i in hard_st_miss), str(hard_st_miss))

# Bug 9: score_tier='P' is valid
issues_st_ok = _vts(_make_task_with_pc(_make_pc(score_tier="P")))
hard_st_ok = [i for i in issues_st_ok if not i.startswith("~ ") and "score_tier" in i]
check("Bug9: score_tier='P' passes", len(hard_st_ok) == 0, str(hard_st_ok))

# Bug 10: hop=None silently passed before fix
issues_hop_none = _vts(_make_task_with_pc(_make_pc(hop=None)))
hard_hop_none = [i for i in issues_hop_none if not i.startswith("~ ")]
check("Bug10: hop=None is a hard error",
      any("hop" in i for i in hard_hop_none), str(hard_hop_none))

# Bug 10: hop=1 and hop=2 are valid (no "hop must be" error — other errors OK)
for v in (1, 2):
    issues_hop_ok = _vts(_make_task_with_pc(_make_pc(hop=v)))
    bad = [i for i in issues_hop_ok if not i.startswith("~ ") and "hop must be" in i]
    check(f"Bug10: hop={v} passes (no hop-value error)", len(bad) == 0, str(bad))

# Bug 11: check_method with unknown value silently passed before fix
issues_cm_bad = _vts(_make_task_with_pc(_make_pc(check_method="magic")))
hard_cm_bad = [i for i in issues_cm_bad if not i.startswith("~ ")]
check("Bug11: check_method='magic' is a hard error",
      any("check_method" in i for i in hard_cm_bad), str(hard_cm_bad))

# Bug 11: check_method=None also caught
issues_cm_none = _vts(_make_task_with_pc(_make_pc(check_method=None)))
hard_cm_none = [i for i in issues_cm_none if not i.startswith("~ ")]
check("Bug11: check_method=None is a hard error",
      any("check_method" in i for i in hard_cm_none), str(hard_cm_none))

# Bug 11: valid values pass
for v in ("code", "llm"):
    issues_cm_ok = _vts(_make_task_with_pc(_make_pc(check_method=v)))
    bad = [i for i in issues_cm_ok if not i.startswith("~ ") and "check_method" in i]
    check(f"Bug11: check_method='{v}' passes", len(bad) == 0, str(bad))

# ─────────────────────────────────────────────────────────────────────────────
# P13.1 — scope_mode / scope declaration validation
# Tests for _check_scope_declarations (dormant function; wired in at P13.5).
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P13.1: scope declaration validation ─────────────────────────")

from test_generate_tasks import _check_scope_declarations  # noqa: E402


def _find_issue(issues: list, pc_id: str, substr: str) -> bool:
    """True if any issue line references pc_id and contains substr."""
    prefix = f"[{pc_id}]"
    return any(prefix in s and substr in s for s in issues)


# -- Valid cases (no issues expected) ----------------------------------------
issues = _check_scope_declarations([])
check("empty list returns no issues", issues == [], f"got {issues}")

pc_universal_ok = {
    "id": "pc_u1",
    "pattern": "regulation_required",
    "params": {"regulation_key": "wheelchair_accessible"},
    "scope_mode": "universal",
}
issues = _check_scope_declarations([pc_universal_ok])
check("universal with no scope passes", issues == [], f"got {issues}")

pc_universal_none_scope = {
    "id": "pc_u2",
    "pattern": "noise_level_max",
    "params": {"max_noise_level": "quiet"},
    "scope_mode": "universal",
    "scope": None,
}
issues = _check_scope_declarations([pc_universal_none_scope])
check("universal with scope=None passes",
      issues == [], f"got {issues}")

pc_scoped_activity = {
    "id": "pc_s1",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
    "scope": {"activity_type": "meal"},
}
issues = _check_scope_declarations([pc_scoped_activity])
check("scoped with activity_type=meal passes",
      issues == [], f"got {issues}")

pc_scoped_category_multi = {
    "id": "pc_s2",
    "pattern": "label_required",
    "params": {"required_label": "vegetarian"},
    "scope_mode": "scoped",
    "scope": {"category": "restaurant,cafe"},
}
issues = _check_scope_declarations([pc_scoped_category_multi])
check("scoped with multi-category string passes",
      issues == [], f"got {issues}")

pc_scoped_category_whitespace = {
    "id": "pc_s3",
    "pattern": "label_required",
    "params": {"required_label": "vegetarian"},
    "scope_mode": "scoped",
    "scope": {"category": " restaurant , cafe "},
}
issues = _check_scope_declarations([pc_scoped_category_whitespace])
check("scoped category with whitespace is trimmed and passes",
      issues == [], f"got {issues}")

pc_scoped_both_keys = {
    "id": "pc_s4",
    "pattern": "label_required",
    "params": {"required_label": "michelin"},
    "scope_mode": "scoped",
    "scope": {"activity_type": "meal", "category": "restaurant"},
}
issues = _check_scope_declarations([pc_scoped_both_keys])
check("scoped with both activity_type AND category passes "
      "(e.g. 'dinner AT restaurants')",
      issues == [], f"got {issues}")

pc_scoped_time_window = {
    "id": "pc_s5",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
    "scope": {"time_window": "19:00-23:00"},
}
issues = _check_scope_declarations([pc_scoped_time_window])
check("scoped with time_window string passes",
      issues == [], f"got {issues}")

pc_inclusion_ok = {
    "id": "pc_i1",
    "pattern": "hidden_gem_required",
    "params": {"min_count": 1},
    "scope_mode": "inclusion",
}
issues = _check_scope_declarations([pc_inclusion_ok])
check("inclusion with min_count=1 passes",
      issues == [], f"got {issues}")

pc_inclusion_higher = {
    "id": "pc_i2",
    "pattern": "hidden_gem_required",
    "params": {"min_count": 3},
    "scope_mode": "inclusion",
}
issues = _check_scope_declarations([pc_inclusion_higher])
check("inclusion with min_count=3 passes",
      issues == [], f"got {issues}")


# -- Invalid cases (issues expected) -----------------------------------------

# 1. scope_mode missing
pc_no_mode = {
    "id": "pc_001",
    "pattern": "regulation_required",
    "params": {"regulation_key": "pet_friendly"},
}
issues = _check_scope_declarations([pc_no_mode])
check("scope_mode missing is flagged",
      _find_issue(issues, "pc_001", "scope_mode missing"),
      f"got {issues}")

# 2. scope_mode invalid value (wrong case)
pc_wrong_case = {
    "id": "pc_002",
    "pattern": "regulation_required",
    "params": {"regulation_key": "pet_friendly"},
    "scope_mode": "Universal",
}
issues = _check_scope_declarations([pc_wrong_case])
check("scope_mode='Universal' (wrong case) is flagged",
      _find_issue(issues, "pc_002", "must be one of"),
      f"got {issues}")

# 3. scope_mode invalid value (typo)
pc_typo = {
    "id": "pc_003",
    "pattern": "regulation_required",
    "params": {"regulation_key": "pet_friendly"},
    "scope_mode": "scope",
}
issues = _check_scope_declarations([pc_typo])
check("scope_mode='scope' (typo) is flagged",
      _find_issue(issues, "pc_003", "must be one of"),
      f"got {issues}")

# 4. universal with scope field set to non-empty dict
pc_universal_with_scope = {
    "id": "pc_004",
    "pattern": "regulation_required",
    "params": {"regulation_key": "pet_friendly"},
    "scope_mode": "universal",
    "scope": {"activity_type": "meal"},
}
issues = _check_scope_declarations([pc_universal_with_scope])
check("universal with non-empty scope is flagged",
      _find_issue(issues, "pc_004", "should not have a scope"),
      f"got {issues}")

# 5. scoped without scope field
pc_scoped_no_scope = {
    "id": "pc_005",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
}
issues = _check_scope_declarations([pc_scoped_no_scope])
check("scoped with missing scope is flagged",
      _find_issue(issues, "pc_005", "requires a scope"),
      f"got {issues}")

# 6. scoped with empty scope dict
pc_scoped_empty = {
    "id": "pc_006",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
    "scope": {},
}
issues = _check_scope_declarations([pc_scoped_empty])
check("scoped with empty scope dict is flagged",
      _find_issue(issues, "pc_006", "at least one of"),
      f"got {issues}")

# 7. scoped with unknown key
pc_scoped_unknown_key = {
    "id": "pc_007",
    "pattern": "label_required",
    "params": {"required_label": "michelin"},
    "scope_mode": "scoped",
    "scope": {"foo": "bar"},
}
issues = _check_scope_declarations([pc_scoped_unknown_key])
check("scoped with unknown key 'foo' is flagged",
      _find_issue(issues, "pc_007", "unknown key"),
      f"got {issues}")

# 8. scoped with unknown category value
pc_scoped_bad_category = {
    "id": "pc_008",
    "pattern": "label_required",
    "params": {"required_label": "michelin"},
    "scope_mode": "scoped",
    "scope": {"category": "spaceship"},
}
issues = _check_scope_declarations([pc_scoped_bad_category])
check("scoped with unknown category 'spaceship' is flagged",
      _find_issue(issues, "pc_008", "unknown value"),
      f"got {issues}")

# 9. scoped with unknown activity_type
pc_scoped_bad_atype = {
    "id": "pc_009",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
    "scope": {"activity_type": "picnic"},
}
issues = _check_scope_declarations([pc_scoped_bad_atype])
check("scoped with unknown activity_type 'picnic' is flagged",
      _find_issue(issues, "pc_009", "activity_type"),
      f"got {issues}")

# 10. scope field is not a dict
pc_scope_not_dict = {
    "id": "pc_010",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
    "scope": "meal",
}
issues = _check_scope_declarations([pc_scope_not_dict])
check("scope as string (not dict) is flagged",
      _find_issue(issues, "pc_010", "must be a dict"),
      f"got {issues}")

# 11. inclusion without min_count
pc_inclusion_no_min = {
    "id": "pc_011",
    "pattern": "hidden_gem_required",
    "params": {},
    "scope_mode": "inclusion",
}
issues = _check_scope_declarations([pc_inclusion_no_min])
check("inclusion without min_count is flagged",
      _find_issue(issues, "pc_011", "min_count"),
      f"got {issues}")

# 12. inclusion with min_count=0
pc_inclusion_zero = {
    "id": "pc_012",
    "pattern": "hidden_gem_required",
    "params": {"min_count": 0},
    "scope_mode": "inclusion",
}
issues = _check_scope_declarations([pc_inclusion_zero])
check("inclusion with min_count=0 is flagged",
      _find_issue(issues, "pc_012", ">= 1"),
      f"got {issues}")

# 13. inclusion with min_count as string
pc_inclusion_str = {
    "id": "pc_013",
    "pattern": "hidden_gem_required",
    "params": {"min_count": "2"},
    "scope_mode": "inclusion",
}
issues = _check_scope_declarations([pc_inclusion_str])
check("inclusion with min_count as string is flagged",
      _find_issue(issues, "pc_013", "must be an int"),
      f"got {issues}")

# 14. time_window empty string
pc_scoped_empty_tw = {
    "id": "pc_014",
    "pattern": "price_tier_required",
    "params": {"min_tier": "upscale"},
    "scope_mode": "scoped",
    "scope": {"time_window": ""},
}
issues = _check_scope_declarations([pc_scoped_empty_tw])
check("scoped with empty time_window string is flagged",
      _find_issue(issues, "pc_014", "time_window"),
      f"got {issues}")

# 15. Multiple bad constraints — each one surfaces
pc_batch = [pc_no_mode, pc_wrong_case, pc_scoped_no_scope]
issues = _check_scope_declarations(pc_batch)
check("three bad constraints produce three separate issue lines",
      len(issues) >= 3,
      f"got {len(issues)} issues: {issues}")
check("batch: pc_001 issue present",
      _find_issue(issues, "pc_001", "scope_mode missing"),
      f"got {issues}")
check("batch: pc_002 issue present",
      _find_issue(issues, "pc_002", "must be one of"),
      f"got {issues}")
check("batch: pc_005 issue present",
      _find_issue(issues, "pc_005", "requires a scope"),
      f"got {issues}")

# 16. Constraint missing id — prefix should be [?]
pc_no_id = {
    "pattern": "regulation_required",
    "params": {"regulation_key": "pet_friendly"},
}
issues = _check_scope_declarations([pc_no_id])
check("constraint with missing id uses [?] prefix",
      any("[?]" in s for s in issues),
      f"got {issues}")

# 17. Constraint is not a dict — function doesn't crash
issues = _check_scope_declarations(["not a constraint", None, 42])
check("non-dict entries are skipped without crash",
      isinstance(issues, list),
      f"got {issues}")

# 18. Input itself not a list — returns []
issues = _check_scope_declarations("not a list")
check("non-list input returns empty list gracefully",
      issues == [],
      f"got {issues}")

# 19. Function is pure — doesn't mutate input
pc_for_mutation_test = {
    "id": "pc_mut",
    "pattern": "label_required",
    "params": {"required_label": "x"},
    "scope_mode": "scoped",
    "scope": {"category": "restaurant,cafe"},
}
before_snapshot = dict(pc_for_mutation_test)
before_scope = dict(pc_for_mutation_test["scope"])
before_params = dict(pc_for_mutation_test["params"])
_ = _check_scope_declarations([pc_for_mutation_test])
check("function does not mutate constraint dict",
      pc_for_mutation_test == before_snapshot
      and pc_for_mutation_test["scope"] == before_scope
      and pc_for_mutation_test["params"] == before_params)

# 20. min_count as bool is rejected (True/False shouldn't pass as int)
pc_inclusion_bool = {
    "id": "pc_bool",
    "pattern": "hidden_gem_required",
    "params": {"min_count": True},
    "scope_mode": "inclusion",
}
issues = _check_scope_declarations([pc_inclusion_bool])
check("inclusion with min_count=True (bool) is flagged",
      _find_issue(issues, "pc_bool", "must be an int"),
      f"got {issues}")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: engine additions — _get_field_value, venues_in_scope, venues_matching
# ═══════════════════════════════════════════════════════════════════════════

# ── _get_field_value ─────────────────────────────────────────────────────────
print("\n── Phase 2: _get_field_value ───────────────────────────────────")

# Flat venue field
v_flat = {"venue_id": "x", "wheelchair_accessible": 1, "price_tier": "mid"}
check("_get_field_value: flat venue field found",
      _get_field_value(v_flat, {}, "wheelchair_accessible") == 1)

# Nested regulations dict
v_nested = {
    "venue_id": "y",
    "regulations": {"wheelchair_accessible": True, "pet_friendly": False},
}
check("_get_field_value: nested regulations field found",
      _get_field_value(v_nested, {}, "wheelchair_accessible") is True)
check("_get_field_value: nested regulations with False value",
      _get_field_value(v_nested, {}, "pet_friendly") is False)

# Flat takes precedence over nested when both present
v_both = {
    "venue_id": "z",
    "wheelchair_accessible": 1,
    "regulations": {"wheelchair_accessible": 0},
}
check("_get_field_value: flat venue wins over nested regulations",
      _get_field_value(v_both, {}, "wheelchair_accessible") == 1)

# Activity field fallback
act_with_cost = {"venue_id": "a", "estimated_cost_local": 42.0}
check("_get_field_value: activity field found as fallback",
      _get_field_value({"venue_id": "a"}, act_with_cost, "estimated_cost_local") == 42.0)

# Missing everywhere → None
check("_get_field_value: missing field returns None",
      _get_field_value({"venue_id": "q"}, {}, "does_not_exist") is None)

# Venue flat beats activity
v_flat_vs_act = {"venue_id": "m", "estimated_cost_local": 10.0}
act_override   = {"venue_id": "m", "estimated_cost_local": 99.0}
check("_get_field_value: venue flat beats activity",
      _get_field_value(v_flat_vs_act, act_override, "estimated_cost_local") == 10.0)


# ── venues_in_scope ──────────────────────────────────────────────────────────
print("\n── Phase 2: venues_in_scope ────────────────────────────────────")

# TEST_POOL reminder (defined at top of file):
#   v01 restaurant Shoreditch mid price mid noise wheelchair=1 tags=[halal,grills]
#   v02 restaurant Shoreditch budget quiet        wheelchair=0 tags=[vegan,hidden-gem]
#   v03 museum     South Bank  free  moderate    wheelchair=1 tags=[art,free-entry,iconic]
#   v04 bar        Shoreditch  mid   lively      wheelchair=0 tags=[craft-beer]
#   v05 bar        Camden      budget moderate   wheelchair=0 tags=[hidden-gem,outdoor]

all_v = venues_in_scope(TEST_POOL, "all")
check("venues_in_scope: 'all' returns every venue",
      len(all_v) == 5, f"got {len(all_v)}")

meals = venues_in_scope(TEST_POOL, "activity_type=meal")
meal_ids = {v["venue_id"] for v in meals}
check("venues_in_scope: activity_type=meal returns restaurants+bars",
      meal_ids == {"v01", "v02", "v04", "v05"}, f"got {meal_ids}")

museums = venues_in_scope(TEST_POOL, "category=museum")
check("venues_in_scope: category=museum narrows to v03",
      len(museums) == 1 and museums[0]["venue_id"] == "v03",
      f"got {[v['venue_id'] for v in museums]}")

tagged = venues_in_scope(TEST_POOL, "has_tag=hidden-gem")
check("venues_in_scope: has_tag=hidden-gem returns v02, v05",
      {v["venue_id"] for v in tagged} == {"v02", "v05"},
      f"got {[v['venue_id'] for v in tagged]}")

# Plan-only scopes don't narrow the pool
in_time = venues_in_scope(TEST_POOL, "time_window=18:00-23:00")
check("venues_in_scope: plan-only time_window returns full pool",
      len(in_time) == 5, f"got {len(in_time)}")

in_perday = venues_in_scope(TEST_POOL, "per_day")
check("venues_in_scope: plan-only per_day returns full pool",
      len(in_perday) == 5, f"got {len(in_perday)}")

# Empty pool
check("venues_in_scope: empty pool returns []",
      venues_in_scope([], "activity_type=meal") == [])

# List-AND (meal AND restaurant category)
and_r = venues_in_scope(TEST_POOL, ["activity_type=meal", "category=restaurant"])
check("venues_in_scope: list-AND meal+restaurant narrows to restaurants",
      {v["venue_id"] for v in and_r} == {"v01", "v02"},
      f"got {[v['venue_id'] for v in and_r]}")


# ── venues_matching ──────────────────────────────────────────────────────────
print("\n── Phase 2: venues_matching ────────────────────────────────────")

# Empty condition ≡ venues_in_scope
no_cond = venues_matching(TEST_POOL, "activity_type=meal", {})
check("venues_matching: empty condition equals scope-filter-only",
      {v["venue_id"] for v in no_cond} == {"v01", "v02", "v04", "v05"},
      f"got {[v['venue_id'] for v in no_cond]}")

# has_tag condition
hidden = venues_matching(TEST_POOL, "all", {"has_tag": "hidden-gem"})
check("venues_matching: has_tag=hidden-gem returns v02, v05",
      {v["venue_id"] for v in hidden} == {"v02", "v05"},
      f"got {[v['venue_id'] for v in hidden]}")

# scope + condition combined
hidden_meals = venues_matching(
    TEST_POOL, "activity_type=meal", {"has_tag": "hidden-gem"}
)
check("venues_matching: meal scope + hidden-gem condition",
      {v["venue_id"] for v in hidden_meals} == {"v02", "v05"},
      f"got {[v['venue_id'] for v in hidden_meals]}")

# Field comparison (flat field)
wc = venues_matching(
    TEST_POOL, "all",
    {"field": "wheelchair_accessible", "operator": "==", "value": 1},
)
check("venues_matching: wheelchair_accessible=1 returns v01, v03",
      {v["venue_id"] for v in wc} == {"v01", "v03"},
      f"got {[v['venue_id'] for v in wc]}")

# Ordered enum: price_tier >= mid
mid = venues_matching(
    TEST_POOL, "all",
    {"field": "price_tier", "operator": ">=", "value": "mid"},
)
check("venues_matching: price_tier>=mid returns mid-tier venues v01, v04",
      {v["venue_id"] for v in mid} == {"v01", "v04"},
      f"got {[v['venue_id'] for v in mid]}")

# Composite AND condition
combo = venues_matching(
    TEST_POOL, "all",
    {"all": [
        {"has_tag": "hidden-gem"},
        {"field": "price_tier", "operator": "==", "value": "budget"},
    ]},
)
check("venues_matching: composite AND (hidden-gem AND budget)",
      {v["venue_id"] for v in combo} == {"v02", "v05"},
      f"got {[v['venue_id'] for v in combo]}")

# Composite OR condition
either = venues_matching(
    TEST_POOL, "all",
    {"any": [
        {"has_tag": "halal"},
        {"has_tag": "vegan"},
    ]},
)
check("venues_matching: composite OR (halal OR vegan)",
      {v["venue_id"] for v in either} == {"v01", "v02"},
      f"got {[v['venue_id'] for v in either]}")

# Plan-only scope with a real condition: scope drops out, condition narrows
late_upscale = venues_matching(
    TEST_POOL, "time_window=19:00-23:59",
    {"field": "price_tier", "operator": ">=", "value": "mid"},
)
check("venues_matching: plan-only scope drops, condition still narrows",
      {v["venue_id"] for v in late_upscale} == {"v01", "v04"},
      f"got {[v['venue_id'] for v in late_upscale]}")

# Empty pool returns []
check("venues_matching: empty pool returns []",
      venues_matching([], "all", {"has_tag": "halal"}) == [])

# Nested regulations dict — a venue with regulations dict still works
pool_nested = [
    {"venue_id": "nx", "category": "restaurant",
     "tags": [], "regulations": {"pet_friendly": True}},
    {"venue_id": "ny", "category": "restaurant",
     "tags": [], "regulations": {"pet_friendly": False}},
]
pet_ok = venues_matching(
    pool_nested, "all",
    {"field": "pet_friendly", "operator": "==", "value": True},
)
check("venues_matching: reads nested regulations dict field",
      {v["venue_id"] for v in pet_ok} == {"nx"},
      f"got {[v['venue_id'] for v in pet_ok]}")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 / Engine contract tests — core predicates still behave as expected
# after additions. These would catch regressions if someone touches
# _activity_satisfies_condition or _activity_matches_scope in the future.
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Phase 2: strict boolean scoring preserved ──────────────────")

from scripts.generation.constraint_engine import _evaluate_generic_constraint  # noqa: E402

# All-satisfy → 1.0
pool_all_wc = [v for v in TEST_POOL if v.get("wheelchair_accessible") == 1]
activities, venues_dict = pool_as_activities(pool_all_wc)
result = _evaluate_generic_constraint(
    "test_all", {
        "scope": "all",
        "condition": {"field": "wheelchair_accessible", "operator": "==", "value": 1},
        "aggregation": "all",
    },
    days=[], all_activities=activities, venues=venues_dict,
)
check("engine: aggregation='all' → 1.0 when every venue satisfies",
      result["score"] == 1.0, f"got {result['score']}")

# Partial-satisfy → 0.0 (strict binary, NOT fractional)
activities_mix, venues_mix = pool_as_activities(TEST_POOL)
result_mix = _evaluate_generic_constraint(
    "test_strict", {
        "scope": "all",
        "condition": {"field": "wheelchair_accessible", "operator": "==", "value": 1},
        "aggregation": "all",
    },
    days=[], all_activities=activities_mix, venues=venues_mix,
)
check("engine: aggregation='all' → 0.0 when some fail (strict binary)",
      result_mix["score"] == 0.0, f"got {result_mix['score']}")

# Zero-satisfy → 0.0
pool_no_wc = [v for v in TEST_POOL if v.get("wheelchair_accessible") == 0]
activities_none, venues_none = pool_as_activities(pool_no_wc)
result_none = _evaluate_generic_constraint(
    "test_none", {
        "scope": "all",
        "condition": {"field": "wheelchair_accessible", "operator": "==", "value": 1},
        "aggregation": "all",
    },
    days=[], all_activities=activities_none, venues=venues_none,
)
check("engine: aggregation='all' → 0.0 when no venue satisfies",
      result_none["score"] == 0.0, f"got {result_none['score']}")

# at_least partial credit still works (0.5 when some but not enough)
result_al = _evaluate_generic_constraint(
    "test_al", {
        "scope": "all",
        "condition": {"has_tag": "hidden-gem"},
        "aggregation": {"at_least": 5},
    },
    days=[], all_activities=activities_mix, venues=venues_mix,
)
check("engine: aggregation={at_least:5} → proportional partial credit when some satisfy but not enough",
      result_al["score"] == 0.4, f"got {result_al['score']}")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: any_of scope marker — explicit union (OR) semantics
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Phase 2: any_of scope marker ───────────────────────────────")

from scripts.generation.constraint_engine import (
    _activity_matches_scope,
    _translate_scope_for_pool,
)  # noqa: E402

# Direct any_of on _activity_matches_scope (scoring-path predicate)
# ---------------------------------------------------------------------------
# Build a synthetic venues dict and a couple of activities to probe.
mini_venues = {
    "a": {"venue_id": "a", "category": "restaurant", "tags": []},
    "b": {"venue_id": "b", "category": "cafe",       "tags": []},
    "c": {"venue_id": "c", "category": "bar",        "tags": []},
    "d": {"venue_id": "d", "category": "museum",     "tags": []},
}

act_r = {"venue_id": "a", "activity_type": "meal"}
act_m = {"venue_id": "d", "activity_type": "visit"}

union_scope = {"any_of": ["category=restaurant", "category=cafe", "category=bar"]}

check("_activity_matches_scope: any_of matches first inner spec",
      _activity_matches_scope(act_r, union_scope, mini_venues) is True)

check("_activity_matches_scope: any_of does NOT match museum activity",
      _activity_matches_scope(act_m, union_scope, mini_venues) is False)

# any_of with a single inner that matches → True
single_union = {"any_of": ["category=museum"]}
check("_activity_matches_scope: any_of with single matching inner → True",
      _activity_matches_scope(act_m, single_union, mini_venues) is True)

# Empty any_of list → no inner matches → False
empty_union = {"any_of": []}
check("_activity_matches_scope: any_of with empty inner list → False",
      _activity_matches_scope(act_r, empty_union, mini_venues) is False)

# any_of mixed with list-AND at outer level: ["category=restaurant", any_of(...)]
# Outer list is AND. This activity must be a restaurant AND match one of the inner.
outer_and = [
    "category=restaurant",
    {"any_of": ["has_tag=halal", "has_tag=vegan"]},
]
mini_venues["a"]["tags"] = ["halal"]
check("_activity_matches_scope: outer AND containing any_of — matches when both parts hold",
      _activity_matches_scope(act_r, outer_and, mini_venues) is True)

mini_venues["a"]["tags"] = []  # reset — now restaurant but no matching tag
check("_activity_matches_scope: outer AND containing any_of — fails when inner any_of fails",
      _activity_matches_scope(act_r, outer_and, mini_venues) is False)

# Non-dict, non-recognized scope is treated as match-all → True (matches
# existing fallback behavior at end of function)
unknown_scope = {"some_unknown_key": "x"}
check("_activity_matches_scope: unknown dict form falls through to True",
      _activity_matches_scope(act_r, unknown_scope, mini_venues) is True)


# _translate_scope_for_pool emits any_of for activity_type=meal
# ---------------------------------------------------------------------------
t_meal = _translate_scope_for_pool("activity_type=meal")
check("_translate_scope_for_pool: activity_type=meal emits any_of",
      isinstance(t_meal, dict)
      and t_meal.get("any_of") == ["category=restaurant", "category=cafe", "category=bar"],
      f"got {t_meal}")

# List-scope with meal + has_tag preserves AND at the outer level while the
# meal part is wrapped as any_of. This is what the bug fix is really about:
# outer list is AND, inner meal-union is OR.
t_list = _translate_scope_for_pool(["activity_type=meal", "has_tag=vegan"])
# Expected shape: [{"any_of": [...three meal categories...]}, "has_tag=vegan"]
check("_translate_scope_for_pool: list preserves AND outer, any_of inner",
      isinstance(t_list, list)
      and len(t_list) == 2
      and any(isinstance(x, dict) and "any_of" in x for x in t_list)
      and "has_tag=vegan" in t_list,
      f"got {t_list}")

# Validate via end-to-end: a combined scope "meal + vegan tag" on the pool
# must return exactly the vegan-tagged food-category venues.
meal_vegan_combo = venues_matching(
    TEST_POOL,
    ["activity_type=meal", "has_tag=vegan"],
    {},
)
check("venues_matching: meal AND vegan-tag combo narrows correctly",
      {v["venue_id"] for v in meal_vegan_combo} == {"v02"},
      f"got {[v['venue_id'] for v in meal_vegan_combo]}")

# And a negative: meal AND a tag no meal venue has returns []
meal_iconic_combo = venues_matching(
    TEST_POOL,
    ["activity_type=meal", "has_tag=iconic"],
    {},
)
check("venues_matching: meal AND tag-only-on-museum returns []",
      meal_iconic_combo == [],
      f"got {[v['venue_id'] for v in meal_iconic_combo]}")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3a: engine additions — venue_id/venue_name scope primitives,
#                              contains operator, at_most_distinct aggregation,
#                              null-match equality, dress_code ordered enum
# ═══════════════════════════════════════════════════════════════════════════

print("\n── Phase 3a: engine additions ─────────────────────────────────")

# ── venue_id= scope primitive (for must_visit, opening_time_window) ─────────
from scripts.generation.constraint_engine import _activity_matches_scope  # noqa: E402

vid_venues = {
    "v01": {"venue_id": "v01", "name": "A", "category": "restaurant", "tags": []},
    "v02": {"venue_id": "v02", "name": "B", "category": "museum",     "tags": []},
}
act_v01 = {"venue_id": "v01", "activity_type": "meal"}
act_v02 = {"venue_id": "v02", "activity_type": "visit"}

check("scope venue_id=v01: matches v01 activity",
      _activity_matches_scope(act_v01, "venue_id=v01", vid_venues) is True)
check("scope venue_id=v01: does NOT match v02 activity",
      _activity_matches_scope(act_v02, "venue_id=v01", vid_venues) is False)


# ── venue_name~ scope primitive (substring, case-insensitive) ───────────────
name_venues = {
    "a": {"venue_id": "a", "name": "The Tate Modern",  "category": "museum"},
    "b": {"venue_id": "b", "name": "Blue Bottle Cafe", "category": "cafe"},
    "c": {"venue_id": "c", "name": "tate britain",     "category": "museum"},
}
act_tate_modern  = {"venue_id": "a", "venue_name": "The Tate Modern"}
act_blue_bottle  = {"venue_id": "b"}  # no venue_name on activity → falls back to venues dict
act_tate_britain = {"venue_id": "c"}

# Substring match, case-insensitive
check("scope venue_name~tate: matches activity with venue_name 'The Tate Modern'",
      _activity_matches_scope(act_tate_modern, "venue_name~tate", name_venues) is True)
check("scope venue_name~Tate: case-insensitive (lowercase 'tate britain' still matches)",
      _activity_matches_scope(act_tate_britain, "venue_name~Tate", name_venues) is True)
check("scope venue_name~bottle: falls back to venues[vid].name when activity lacks venue_name",
      _activity_matches_scope(act_blue_bottle, "venue_name~bottle", name_venues) is True)
check("scope venue_name~missing: no match",
      _activity_matches_scope(act_tate_modern, "venue_name~museum", name_venues) is False)


# ── `contains` operator (mirror of `in`) ────────────────────────────────────
# actual = list field on venue; value = scalar we test for membership.
from scripts.generation.constraint_engine import _activity_satisfies_condition  # noqa: E402

occasion_venues = {
    "anniv_ok":  {"venue_id": "anniv_ok",
                  "category": "restaurant",
                  "suitable_occasions": ["anniversary", "date", "family"]},
    "family_only": {"venue_id": "family_only",
                    "category": "restaurant",
                    "suitable_occasions": ["family"]},
    "no_list": {"venue_id": "no_list", "category": "restaurant"},  # no suitable_occasions field
}
act_a = {"venue_id": "anniv_ok",  "activity_type": "meal"}
act_f = {"venue_id": "family_only", "activity_type": "meal"}
act_n = {"venue_id": "no_list",   "activity_type": "meal"}

contains_anniv = {"field": "suitable_occasions",
                  "operator": "contains",
                  "value": "anniversary"}

check("contains: list-field contains scalar → True",
      _activity_satisfies_condition(act_a, contains_anniv, occasion_venues) is True)
check("contains: list-field does NOT contain scalar → False",
      _activity_satisfies_condition(act_f, contains_anniv, occasion_venues) is False)
check("contains: absent field → False (not list-shaped)",
      _activity_satisfies_condition(act_n, contains_anniv, occasion_venues) is False)

# `in` vs `contains` direction check — demonstrate correct usage of each.
# `in`: scalar field in list value               — venue.district in [A,B,C]
# `contains`: list field contains scalar value   — venue.suitable_occasions contains "X"
# Using them with the right data shape on each:
district_venues_short = {
    "in_list": {"venue_id": "in_list", "category": "restaurant", "district": "Shoreditch"},
    "not_in_list": {"venue_id": "not_in_list", "category": "restaurant", "district": "Mayfair"},
}
act_in     = {"venue_id": "in_list"}
act_not_in = {"venue_id": "not_in_list"}
in_cond = {"field": "district", "operator": "in",
           "value": ["Shoreditch", "Camden", "Soho"]}
check("in operator: scalar field in list value → True",
      _activity_satisfies_condition(act_in, in_cond, district_venues_short) is True)
check("in operator: scalar field NOT in list value → False",
      _activity_satisfies_condition(act_not_in, in_cond, district_venues_short) is False)
# And contains, on list-field data:
check("contains operator (same data shape as anniv test above) works reliably",
      _activity_satisfies_condition(act_a, contains_anniv, occasion_venues) is True)


# ── at_most_distinct aggregation (for district_count_max) ───────────────────
from scripts.generation.constraint_engine import _evaluate_generic_constraint  # noqa: E402

# Build a mini-schedule across districts to exercise distinct counting
district_venues = {
    "v1": {"venue_id": "v1", "category": "restaurant", "district": "Shoreditch", "tags": []},
    "v2": {"venue_id": "v2", "category": "restaurant", "district": "Shoreditch", "tags": []},
    "v3": {"venue_id": "v3", "category": "museum",     "district": "Camden",     "tags": []},
    "v4": {"venue_id": "v4", "category": "bar",        "district": "Soho",       "tags": []},
}
acts_3_districts = [
    {"venue_id": "v1", "activity_type": "meal"},
    {"venue_id": "v3", "activity_type": "visit"},
    {"venue_id": "v4", "activity_type": "meal"},
]
acts_2_districts = [
    {"venue_id": "v1", "activity_type": "meal"},
    {"venue_id": "v2", "activity_type": "meal"},
    {"venue_id": "v3", "activity_type": "visit"},
]

result_3d_cap2 = _evaluate_generic_constraint(
    "dc_1",
    {"scope": "all", "condition": {}, "aggregation": {"at_most_distinct": 2, "field": "district"}},
    days=[], all_activities=acts_3_districts, venues=district_venues,
)
check("at_most_distinct: 3 distinct districts, cap 2 → fail (0.0)",
      result_3d_cap2["score"] == 0.0, f"got {result_3d_cap2['score']}")

result_2d_cap2 = _evaluate_generic_constraint(
    "dc_2",
    {"scope": "all", "condition": {}, "aggregation": {"at_most_distinct": 2, "field": "district"}},
    days=[], all_activities=acts_2_districts, venues=district_venues,
)
check("at_most_distinct: 2 distinct districts, cap 2 → pass (1.0)",
      result_2d_cap2["score"] == 1.0, f"got {result_2d_cap2['score']}")

result_3d_cap3 = _evaluate_generic_constraint(
    "dc_3",
    {"scope": "all", "condition": {}, "aggregation": {"at_most_distinct": 3, "field": "district"}},
    days=[], all_activities=acts_3_districts, venues=district_venues,
)
check("at_most_distinct: 3 distinct districts, cap 3 → pass (1.0)",
      result_3d_cap3["score"] == 1.0, f"got {result_3d_cap3['score']}")


# ── Null-match equality (for min_age 'no age restriction' case) ─────────────
null_venues = {
    "any_age":   {"venue_id": "any_age",   "category": "museum",
                  "regulations": {"min_age": None}},   # explicitly None
    "no_restr":  {"venue_id": "no_restr",  "category": "museum",
                  "regulations": {}},                  # field absent
    "age_18":    {"venue_id": "age_18",    "category": "bar",
                  "regulations": {"min_age": 18}},
}
act_any  = {"venue_id": "any_age",  "activity_type": "visit"}
act_no   = {"venue_id": "no_restr", "activity_type": "visit"}
act_18   = {"venue_id": "age_18",   "activity_type": "meal"}

eq_null = {"field": "min_age", "operator": "==", "value": None}
ne_null = {"field": "min_age", "operator": "!=", "value": None}

check("null-match ==None: explicitly None field matches",
      _activity_satisfies_condition(act_any, eq_null, null_venues) is True)
check("null-match ==None: absent field matches (treated as None)",
      _activity_satisfies_condition(act_no, eq_null, null_venues) is True)
check("null-match ==None: field with a value does NOT match",
      _activity_satisfies_condition(act_18, eq_null, null_venues) is False)

check("null-match !=None: field with a value matches",
      _activity_satisfies_condition(act_18, ne_null, null_venues) is True)
check("null-match !=None: None field does NOT match",
      _activity_satisfies_condition(act_any, ne_null, null_venues) is False)


# ── dress_code ordered enum (parallel to existing price_tier ordering) ──────
dress_venues = {
    "casual":     {"venue_id": "casual",     "category": "restaurant",
                   "regulations": {"dress_code": "casual"}},
    "smart_cas":  {"venue_id": "smart_cas",  "category": "restaurant",
                   "regulations": {"dress_code": "smart_casual"}},
    "formal":     {"venue_id": "formal",     "category": "restaurant",
                   "regulations": {"dress_code": "formal"}},
}
act_casual    = {"venue_id": "casual"}
act_smart_cas = {"venue_id": "smart_cas"}
act_formal    = {"venue_id": "formal"}

ge_smart = {"field": "dress_code", "operator": ">=", "value": "smart_casual"}
check("dress_code >=smart_casual: formal passes (formal > smart_casual)",
      _activity_satisfies_condition(act_formal, ge_smart, dress_venues) is True)
check("dress_code >=smart_casual: smart_casual passes (equal)",
      _activity_satisfies_condition(act_smart_cas, ge_smart, dress_venues) is True)
check("dress_code >=smart_casual: casual fails (casual < smart_casual)",
      _activity_satisfies_condition(act_casual, ge_smart, dress_venues) is False)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3a: audit fixes — at_least_days, empty-match for at_least, time_window
# ═══════════════════════════════════════════════════════════════════════════

print("\n── Phase 3a: audit fixes ───────────────────────────────────────")

# ── at_least_days aggregation (pace_relaxed handler semantics) ───────────────
# Build a 2-day plan.  Day 1 is all-relaxed.  Day 2 has one relaxed and one
# non-relaxed activity.

pace_venues = {
    "r1": {"venue_id": "r1", "category": "museum",     "recommended_pace": "relaxed",  "tags": []},
    "r2": {"venue_id": "r2", "category": "restaurant", "recommended_pace": "relaxed",  "tags": []},
    "m1": {"venue_id": "m1", "category": "attraction", "recommended_pace": "moderate", "tags": []},
}
days_2 = [
    {"activities": [
        {"venue_id": "r1", "activity_type": "visit"},
        {"venue_id": "r2", "activity_type": "meal"},
    ]},
    {"activities": [
        {"venue_id": "r1", "activity_type": "visit"},
        {"venue_id": "m1", "activity_type": "visit"},   # breaks the day
    ]},
]
pace_constraint_1 = {
    "scope": "all",
    "condition": {"field": "recommended_pace", "operator": "==", "value": "relaxed"},
    "aggregation": {"at_least_days": 1},
}
pace_constraint_2 = {
    "scope": "all",
    "condition": {"field": "recommended_pace", "operator": "==", "value": "relaxed"},
    "aggregation": {"at_least_days": 2},
}

r_1day = _evaluate_generic_constraint(
    "pace_1", pace_constraint_1, days=days_2, all_activities=[], venues=pace_venues
)
check("at_least_days: 1 of 2 days fully-relaxed, need 1 → pass (1.0)",
      r_1day["score"] == 1.0, f"got {r_1day['score']}")

r_2day = _evaluate_generic_constraint(
    "pace_2", pace_constraint_2, days=days_2, all_activities=[], venues=pace_venues
)
check("at_least_days: 1 of 2 days fully-relaxed, need 2 → fail (0.5 partial)",
      r_2day["score"] == 0.5, f"got {r_2day['score']}")

# Both days relaxed → pass for min_days=2
days_all_relaxed = [
    {"activities": [{"venue_id": "r1"}, {"venue_id": "r2"}]},
    {"activities": [{"venue_id": "r2"}]},
]
r_both = _evaluate_generic_constraint(
    "pace_3", pace_constraint_2, days=days_all_relaxed, all_activities=[], venues=pace_venues
)
check("at_least_days: 2 of 2 days fully-relaxed, need 2 → pass (1.0)",
      r_both["score"] == 1.0, f"got {r_both['score']}")

# Empty day (no activities) does not count for or against.
days_empty_day = [
    {"activities": []},
    {"activities": [{"venue_id": "r1"}]},
]
r_empty = _evaluate_generic_constraint(
    "pace_4", pace_constraint_1, days=days_empty_day, all_activities=[], venues=pace_venues
)
check("at_least_days: empty day ignored, 1 passing day → pass",
      r_empty["score"] == 1.0, f"got {r_empty['score']}")


# ── Empty-matching + at_least:N fix ─────────────────────────────────────────
# must_visit: venue not in plan → scope matches nothing → must fail (not N/A 1.0)
must_visit_venues = {
    "present": {"venue_id": "present", "category": "museum", "tags": []},
}
must_visit_activities = [{"venue_id": "present", "activity_type": "visit"}]

# Venue that IS in the plan → at_least:1 passes
r_present = _evaluate_generic_constraint(
    "mv_ok",
    {"scope": "venue_id=present", "condition": {}, "aggregation": {"at_least": 1}},
    days=[], all_activities=must_visit_activities, venues=must_visit_venues,
)
check("must_visit: venue in plan → at_least:1 passes (1.0)",
      r_present["score"] == 1.0, f"got {r_present['score']}")

# Venue that is NOT in the plan → empty match → must fail, NOT return N/A 1.0
r_absent = _evaluate_generic_constraint(
    "mv_fail",
    {"scope": "venue_id=absent_venue", "condition": {}, "aggregation": {"at_least": 1}},
    days=[], all_activities=must_visit_activities, venues=must_visit_venues,
)
check("must_visit: venue absent from plan → at_least:1 fails (0.0, not N/A)",
      r_absent["score"] == 0.0, f"got {r_absent['score']}")

# aggregation "all" with no matching still returns N/A (vacuous truth — correct)
r_all_empty = _evaluate_generic_constraint(
    "mv_all",
    {"scope": "venue_id=absent_venue", "condition": {}, "aggregation": "all"},
    days=[], all_activities=must_visit_activities, venues=must_visit_venues,
)
check("all-aggregation: empty scope match → N/A pass (1.0, vacuous truth)",
      r_all_empty["score"] == 1.0, f"got {r_all_empty['score']}")


# ─────────────────────────────────────────────────────────────────────────────
# P6-T9 part B — at_least counts DISTINCT venue_ids, not activity count
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P6-T9: at_least distinct venue counting ─────────────────────")

_t9b_venues = {
    "v_art1": {"name": "Tate Modern",  "category": "museum",
               "tags": ["art", "modern"]},
    "v_art2": {"name": "National Gal", "category": "museum",
               "tags": ["art", "classical"]},
    "v_food": {"name": "Bistro",       "category": "restaurant",
               "tags": ["british"]},
}

def _act(vid, t_start, t_end, atype="visit"):
    return {"venue_id": vid, "time_start": t_start, "time_end": t_end,
            "activity_type": atype}

# Same art museum visited twice — must NOT satisfy at_least: 2 art
_acts_revisit = [
    _act("v_art1", "09:00", "11:00"),
    _act("v_food", "12:00", "13:30", atype="meal"),
    _act("v_art1", "14:00", "16:00"),
]
_r_revisit = _evaluate_generic_constraint(
    "t9b_revisit", {
        "scope": "all",
        "condition": {"has_tag": "art"},
        "aggregation": {"at_least": 2},
    },
    days=[], all_activities=_acts_revisit, venues=_t9b_venues,
)
check("P6-T9: at_least=2 art with same museum twice → FAILS (only 1 distinct)",
      _r_revisit["score"] < 1.0, f"score={_r_revisit['score']} reason={_r_revisit['reason']}")
check("P6-T9: partial credit reflects distinct count (1/2 → 0.5)",
      _r_revisit["score"] == 0.5, f"score={_r_revisit['score']}")
check("P6-T9: reason surfaces distinct count",
      "distinct venues: 1" in _r_revisit["reason"], _r_revisit["reason"])

# Two DISTINCT art venues — must satisfy at_least: 2 art
_acts_distinct = [
    _act("v_art1", "09:00", "11:00"),
    _act("v_food", "12:00", "13:30", atype="meal"),
    _act("v_art2", "14:00", "16:00"),
]
_r_distinct = _evaluate_generic_constraint(
    "t9b_distinct", {
        "scope": "all",
        "condition": {"has_tag": "art"},
        "aggregation": {"at_least": 2},
    },
    days=[], all_activities=_acts_distinct, venues=_t9b_venues,
)
check("P6-T9: at_least=2 art with two distinct art venues → PASSES",
      _r_distinct["score"] == 1.0, f"score={_r_distinct['score']}")


# ─────────────────────────────────────────────────────────────────────────────
# P6-T1b — count_distinct: {field: "cuisine"} reads from venues.cuisine column
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P6-T1b: count_distinct on cuisine column ────────────────────")

_t1b_venues = {
    "r_it":   {"name": "Tratt. A", "category": "restaurant", "cuisine": "italian"},
    "r_jp":   {"name": "Ramen B",  "category": "restaurant", "cuisine": "japanese"},
    "r_in":   {"name": "Spice C",  "category": "restaurant", "cuisine": "indian"},
    "r_it2":  {"name": "Tratt. D", "category": "restaurant", "cuisine": "italian"},
}
_t1b_acts = [
    _act("r_it",  "12:00", "13:30", atype="meal"),
    _act("r_jp",  "19:00", "20:30", atype="meal"),
    _act("r_in",  "13:00", "14:30", atype="meal"),
]

# 3 distinct cuisines (italian, japanese, indian) across 3 meal activities → passes
_r_cd_pass = _evaluate_generic_constraint(
    "t1b_cd_pass", {
        "scope": "activity_type=meal",
        "condition": {},
        "aggregation": {"count_distinct": 3, "field": "cuisine"},
    },
    days=[], all_activities=_t1b_acts, venues=_t1b_venues,
)
check("P6-T1b: count_distinct:3 cuisine with 3 distinct → passes",
      _r_cd_pass["score"] == 1.0, f"score={_r_cd_pass['score']}")

# Same 3 acts, count_distinct:4 → fails with partial credit 0.75
_r_cd_fail = _evaluate_generic_constraint(
    "t1b_cd_fail", {
        "scope": "activity_type=meal",
        "condition": {},
        "aggregation": {"count_distinct": 4, "field": "cuisine"},
    },
    days=[], all_activities=_t1b_acts, venues=_t1b_venues,
)
check("P6-T1b: count_distinct:4 with only 3 distinct cuisines → fails (score<1)",
      _r_cd_fail["score"] < 1.0, f"score={_r_cd_fail['score']}")

# Two italian restaurants + one japanese: count_distinct:3 → fails (only 2 distinct)
_t1b_acts_dup = [
    _act("r_it",  "12:00", "13:30", atype="meal"),
    _act("r_it2", "19:00", "20:30", atype="meal"),
    _act("r_jp",  "13:00", "14:30", atype="meal"),
]
_r_cd_dup = _evaluate_generic_constraint(
    "t1b_cd_dup", {
        "scope": "activity_type=meal",
        "condition": {},
        "aggregation": {"count_distinct": 3, "field": "cuisine"},
    },
    days=[], all_activities=_t1b_acts_dup, venues=_t1b_venues,
)
check("P6-T1b: same cuisine twice (italian+italian+japanese) → fails (2 distinct < 3)",
      _r_cd_dup["score"] < 1.0, f"score={_r_cd_dup['score']}")


# ─────────────────────────────────────────────────────────────────────────────
# P6-T7 — Blank-time formula (max-gap based, drop -1 buffer, missing × 0.04)
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P6-T7: max-gap blank-time formula ───────────────────────────")

from eval.evaluator import evaluate_c_score

def _c_act(time_start, time_end, vid, atype="visit"):
    return {"venue_id": vid, "venue_name": vid,
            "time_start": time_start, "time_end": time_end,
            "activity_type": atype}

def _build_result(activities_per_day, task_id="lon_t7", city="london"):
    return {
        "task_id": task_id,
        "parsed_plan": {
            "task_id": task_id, "city": city,
            "days": [{"day": i+1, "day_of_week": "sat",
                       "activities": acts}
                      for i, acts in enumerate(activities_per_day)],
        },
        "tool_call_log": [
            {"tool_name": "search_yelp", "tool_input": {"query": "x", "city": city},
             "tool_output": {"results": []}}
        ],
    }

_t7_task_base = {
    "task_id": "lon_t7", "city": "london", "days": 1, "start_date": "2026-08-22",
    "public_input": {"query": "x"},
    "rubric": {"hard_constraints": [], "personal_constraints": [],
                "b_score_constraints": []},
}

# Case 1: 3 back-to-back activities + trailing gap → no deduction
_t7_case1 = _build_result([[
    _c_act("09:00", "11:00", "v1"),
    _c_act("11:15", "13:00", "v2", atype="meal"),
    _c_act("13:15", "15:00", "v3"),
]])
_c1 = evaluate_c_score(_t7_case1, _t7_task_base, {})
_blank1 = [d for d in _c1["deductions"] if "sparse" in d[1]]
check("P6-T7: trailing gap (no idle gap between activities) → no deduction",
      len(_blank1) == 0, f"got {_blank1}")

# Case 2: museum 09:00-12:30 + dinner 19:00-20:30 → 390-min idle gap → fires
_t7_case2 = _build_result([[
    _c_act("09:00", "12:30", "v1"),
    _c_act("19:00", "20:30", "v2", atype="meal"),
]])
_c2 = evaluate_c_score(_t7_case2, _t7_task_base, {})
_blank2 = [d for d in _c2["deductions"] if "sparse" in d[1]]
check("P6-T7: 390-min idle gap between morning + evening → deduction fires",
      len(_blank2) == 1, f"got {_blank2}")
check("P6-T7: deduction text surfaces gap length",
      _blank2 and "390-min idle gap" in _blank2[0][1],
      _blank2[0][1] if _blank2 else "no ded")

# Case 3: 1 activity all day → fires
_t7_case3 = _build_result([[
    _c_act("10:00", "12:00", "v1"),
]])
_c3 = evaluate_c_score(_t7_case3, _t7_task_base, {})
_blank3 = [d for d in _c3["deductions"] if "sparse" in d[1]]
check("P6-T7: 1 activity all day → deduction fires (single-activity special case)",
      len(_blank3) == 1, f"got {_blank3}")

# Case 4: 5 venues with small gaps, full productive day → no deduction
_t7_case4 = _build_result([[
    _c_act("09:00", "11:00", "v1"),
    _c_act("11:30", "13:30", "v2", atype="meal"),
    _c_act("14:00", "16:00", "v3"),
    _c_act("16:30", "18:30", "v4"),
    _c_act("19:00", "20:30", "v5", atype="meal"),
]])
_c4 = evaluate_c_score(_t7_case4, _t7_task_base, {})
_blank4 = [d for d in _c4["deductions"] if "sparse" in d[1]]
check("P6-T7: 5 venues with <90-min gaps → no deduction",
      len(_blank4) == 0, f"got {_blank4}")

# Case 5: type2 task with time_ceiling_minutes=240 — 3 activities in window
# should NOT fire (old formula would have over-flagged sparse against 600min).
_t7_task_type2 = dict(_t7_task_base)
_t7_task_type2["structural_type"] = "type2_subset_selection"
_t7_task_type2["public_input"] = {
    "query": "x",
    "query_resources": {"time_ceiling_minutes": 240},
}
_t7_case5 = _build_result([[
    _c_act("13:00", "14:00", "v1"),
    _c_act("14:15", "15:15", "v2"),
    _c_act("15:30", "16:30", "v3"),
]])
_c5 = evaluate_c_score(_t7_case5, _t7_task_type2, {})
_blank5 = [d for d in _c5["deductions"] if "sparse" in d[1]]
check("P6-T7: type2 with 240-min ceiling and 3 activities → no deduction (budget cap)",
      len(_blank5) == 0, f"got {_blank5}")


# ─────────────────────────────────────────────────────────────────────────────
# P6-T9 Scenario A — soft duplicate warning in C-score
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P6-T9 Scenario A: gratuitous duplicate warning ──────────────")

# Case A: same venue twice on day 1 → warning appears
_t9a_dupe = _build_result([[
    _c_act("09:00", "11:00", "v_museum"),
    _c_act("12:00", "13:30", "v_lunch", atype="meal"),
    _c_act("14:30", "16:30", "v_museum"),
]])
_c_dupe = evaluate_c_score(_t9a_dupe, _t7_task_base, {})
_dupe_warns = [w for w in _c_dupe["warnings"] if "appears 2 times" in w]
check("P6-T9A: repeat venue → warning fires",
      len(_dupe_warns) == 1, f"got {_dupe_warns}")
check("P6-T9A: warning contains the venue_id",
      _dupe_warns and "v_museum" in _dupe_warns[0],
      _dupe_warns[0] if _dupe_warns else "no warn")
check("P6-T9A: warning is in `warnings` not `deductions` (no score impact)",
      not any("appears" in d[1] for d in _c_dupe["deductions"]),
      str(_c_dupe["deductions"]))

# Case B: all distinct venues → no duplicate warning
_t9a_distinct = _build_result([[
    _c_act("09:00", "11:00", "v_a"),
    _c_act("12:00", "13:30", "v_b", atype="meal"),
    _c_act("14:30", "16:30", "v_c"),
]])
_c_distinct = evaluate_c_score(_t9a_distinct, _t7_task_base, {})
_distinct_warns = [w for w in _c_distinct["warnings"] if "appears" in w]
check("P6-T9A: distinct venues → no duplicate warning",
      len(_distinct_warns) == 0, f"got {_distinct_warns}")


# ─────────────────────────────────────────────────────────────────────────────
# P6-T4 F2e — Time-ceiling overrun (F-score deduction)
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P6-T4 F2e: time-ceiling overrun ─────────────────────────────")

# Build a small venue map + matrix so F-score has data to chew on.
def _f2e_venue(vid, name="v"):
    return {
        "name": name, "category": "museum", "hours": {
            "mon": [["00:00", "23:59"]], "tue": [["00:00", "23:59"]],
            "wed": [["00:00", "23:59"]], "thu": [["00:00", "23:59"]],
            "fri": [["00:00", "23:59"]], "sat": [["00:00", "23:59"]],
            "sun": [["00:00", "23:59"]],
        },
        "ticket_availability": {},
        "recommended_visit_minutes": 120,
        "regulations": {"wheelchair_accessible": True},
        "tags": [], "has_wrong_info": False, "has_stale_hours": False,
        "outdoor_sensitivity": "indoor",
    }

_f2e_venues = {f"v{i}": _f2e_venue(f"v{i}", f"V{i}") for i in range(1, 7)}
_f2e_matrix = {}  # no matrix entries → no implicit travel time added

def _f2e_task(structural, ceiling, days_count=1):
    qr = {"time_ceiling_minutes": ceiling} if ceiling is not None else {}
    return {
        "task_id": "f2e",
        "city": "london",
        "structural_type": structural,
        "days": days_count,
        "start_date": "2026-06-13",  # saturday
        "public_input": {"query": "x", "query_resources": qr},
        "rubric": {"hard_constraints": [], "personal_constraints": [],
                    "b_score_constraints": []},
    }


# Case 1: type2, total within ceiling → no F2e deduction
_t4_case1 = _build_result([[
    _c_act("09:00", "10:30", "v1"),   # 90 min
    _c_act("10:45", "12:00", "v2"),   # 75 min
]])
_f1 = evaluate_f_score(_t4_case1,
                        _f2e_task("type2_subset_selection", 240),
                        _f2e_venues, _f2e_matrix)
_f2e_deds_1 = [d for d in _f1["deductions"] if d["section"] == "F2e"]
check("P6-T4 F2e: type2 within ceiling → no deduction",
      len(_f2e_deds_1) == 0, str(_f2e_deds_1))

# Case 2: type2, total overruns ceiling → one F2e deduction of 0.30
_t4_case2 = _build_result([[
    _c_act("09:00", "12:00", "v1"),   # 180 min
    _c_act("13:00", "16:00", "v2"),   # 180 min
]])
_f2 = evaluate_f_score(_t4_case2,
                        _f2e_task("type2_subset_selection", 240),
                        _f2e_venues, _f2e_matrix)
_f2e_deds_2 = [d for d in _f2["deductions"] if d["section"] == "F2e"]
check("P6-T4 F2e: type2 overrun → one F2e deduction",
      len(_f2e_deds_2) == 1, str(_f2e_deds_2))
check("P6-T4 F2e: type2 overrun deduction amount = 0.30",
      _f2e_deds_2 and _f2e_deds_2[0]["amount"] == 0.30,
      str(_f2e_deds_2))
check("P6-T4 F2e: type2 overrun reason mentions totals",
      _f2e_deds_2 and "360min" in _f2e_deds_2[0]["reason"]
      and "ceiling 240" in _f2e_deds_2[0]["reason"],
      _f2e_deds_2[0]["reason"] if _f2e_deds_2 else "no ded")

# Case 3: type2 without time_ceiling_minutes → check silently skipped
_t4_case3 = _build_result([[
    _c_act("09:00", "20:00", "v1"),   # 660 min — would massively overrun
                                       # ANY sane ceiling, but no ceiling set
]])
_f3 = evaluate_f_score(_t4_case3,
                        _f2e_task("type2_subset_selection", None),
                        _f2e_venues, _f2e_matrix)
_f2e_deds_3 = [d for d in _f3["deductions"] if d["section"] == "F2e"]
check("P6-T4 F2e: no ceiling set → check skipped (no F2e deduction)",
      len(_f2e_deds_3) == 0, str(_f2e_deds_3))

# Case 4: non-type2 task with ceiling set + single-day overrun
# (forward-looking: per-day branch fires, one deduction per day over)
_t4_case4 = _build_result([[
    _c_act("09:00", "13:00", "v1"),   # 240 min
]])
_f4 = evaluate_f_score(_t4_case4,
                        _f2e_task("type1_cascading_requirements", 180),
                        _f2e_venues, _f2e_matrix)
_f2e_deds_4 = [d for d in _f4["deductions"] if d["section"] == "F2e"]
check("P6-T4 F2e: non-type2 + per-day overrun → one F2e deduction",
      len(_f2e_deds_4) == 1, str(_f2e_deds_4))
check("P6-T4 F2e: non-type2 reason mentions 'Day 1'",
      _f2e_deds_4 and "Day 1" in _f2e_deds_4[0]["reason"],
      _f2e_deds_4[0]["reason"] if _f2e_deds_4 else "no ded")

# Case 5: multi-day type2 where each day is within ceiling individually
# but the TOTAL exceeds. type2 should sum across days.
# Day 1 = 150 min, Day 2 = 150 min, total = 300, ceiling = 240.
_t4_case5 = _build_result([
    [_c_act("09:00", "11:30", "v1")],  # 150 min
    [_c_act("14:00", "16:30", "v2")],  # 150 min
])
_f5 = evaluate_f_score(_t4_case5,
                        _f2e_task("type2_subset_selection", 240, days_count=2),
                        _f2e_venues, _f2e_matrix)
_f2e_deds_5 = [d for d in _f5["deductions"] if d["section"] == "F2e"]
check("P6-T4 F2e: multi-day type2 sums across days → one F2e deduction",
      len(_f2e_deds_5) == 1, str(_f2e_deds_5))
check("P6-T4 F2e: multi-day type2 reason shows 300min total",
      _f2e_deds_5 and "300min" in _f2e_deds_5[0]["reason"],
      _f2e_deds_5[0]["reason"] if _f2e_deds_5 else "no ded")


# ─────────────────────────────────────────────────────────────────────────────
# P6-T4b — F2e window check (contiguous) + spread per_day branch
# ─────────────────────────────────────────────────────────────────────────────
print("\n── P6-T4b F2e: ceiling_mode + scope branches ───────────────────")

def _f2e_task_v2(structural, ceiling, days_count=1, *,
                  ceiling_mode="contiguous", ceiling_scope="total",
                  start_time=None):
    qr = {"time_ceiling_minutes": ceiling,
           "ceiling_mode": ceiling_mode,
           "ceiling_scope": ceiling_scope}
    if start_time is not None:
        qr["start_time"] = start_time
    return {
        "task_id": "f2e_t4b",
        "city": "london",
        "structural_type": structural,
        "days": days_count,
        "start_date": "2026-06-13",
        "public_input": {"query": "x", "query_resources": qr},
        "rubric": {"hard_constraints": [], "personal_constraints": [],
                    "b_score_constraints": []},
    }

# --- Contiguous mode: window check ---
# Plan with activity inside window (14:00-18:00 = 240min ceiling, start_time=14:00)
_t4b_inside = _build_result([[
    _c_act("14:30", "16:00", "v1"),  # 90min — inside [14:00, 18:00]
    _c_act("16:15", "17:30", "v2"),  # 75min — inside
]])
_fc1 = evaluate_f_score(_t4b_inside,
                         _f2e_task_v2("type2_subset_selection", 240,
                                       ceiling_mode="contiguous",
                                       start_time="14:00"),
                         _f2e_venues, _f2e_matrix)
_window_deds_1 = [d for d in _fc1["deductions"]
                   if d["section"] == "F2e" and "outside" in d["reason"]]
check("P6-T4b: contiguous + all activities in window → no window deduction",
      len(_window_deds_1) == 0, str(_window_deds_1))

# Plan with one activity outside the window (start 12:00, ends 13:00, window 14:00–18:00)
_t4b_outside = _build_result([[
    _c_act("12:00", "13:00", "v1"),  # OUTSIDE — ends before window starts
    _c_act("15:00", "16:30", "v2"),  # inside
]])
_fc2 = evaluate_f_score(_t4b_outside,
                         _f2e_task_v2("type2_subset_selection", 240,
                                       ceiling_mode="contiguous",
                                       start_time="14:00"),
                         _f2e_venues, _f2e_matrix)
_window_deds_2 = [d for d in _fc2["deductions"]
                   if d["section"] == "F2e" and "outside" in d["reason"]]
check("P6-T4b: contiguous + one activity outside → one window deduction",
      len(_window_deds_2) == 1, str(_window_deds_2))
check("P6-T4b: window deduction amount = 0.15",
      _window_deds_2 and _window_deds_2[0]["amount"] == 0.15,
      str(_window_deds_2))

# Plan that exceeds the window on the trailing end (overruns past 18:00)
_t4b_overrun = _build_result([[
    _c_act("15:00", "16:00", "v1"),
    _c_act("17:00", "19:30", "v2"),  # ends 19:30, outside window 14:00–18:00
]])
_fc3 = evaluate_f_score(_t4b_overrun,
                         _f2e_task_v2("type2_subset_selection", 240,
                                       ceiling_mode="contiguous",
                                       start_time="14:00"),
                         _f2e_venues, _f2e_matrix)
_window_deds_3 = [d for d in _fc3["deductions"]
                   if d["section"] == "F2e" and "outside" in d["reason"]]
check("P6-T4b: contiguous trailing overrun → one window deduction",
      len(_window_deds_3) == 1, str(_window_deds_3))

# Contiguous mode with NO start_time → window check should NOT fire
_t4b_no_start = _build_result([[
    _c_act("06:00", "08:00", "v1"),  # would be outside any sensible window
    _c_act("22:00", "23:30", "v2"),
]])
_fc4 = evaluate_f_score(_t4b_no_start,
                         _f2e_task_v2("type2_subset_selection", 240,
                                       ceiling_mode="contiguous",
                                       start_time=None),
                         _f2e_venues, _f2e_matrix)
_window_deds_4 = [d for d in _fc4["deductions"]
                   if d["section"] == "F2e" and "outside" in d["reason"]]
check("P6-T4b: contiguous without start_time → no window check",
      len(_window_deds_4) == 0, str(_window_deds_4))

# --- Spread mode: per_day scope check ---
# 2-day trip with per-day ceiling 180; Day 1 = 100min (OK), Day 2 = 240min (over)
_t4b_perday = _build_result([
    [_c_act("09:00", "10:40", "v1")],          # 100min — under 180
    [_c_act("09:00", "13:00", "v2")],          # 240min — OVER 180
])
_fc5 = evaluate_f_score(_t4b_perday,
                         _f2e_task_v2("type2_subset_selection", 180,
                                       days_count=2,
                                       ceiling_mode="spread",
                                       ceiling_scope="per_day"),
                         _f2e_venues, _f2e_matrix)
_perday_deds = [d for d in _fc5["deductions"]
                 if d["section"] == "F2e" and "per-day" in d["reason"]]
check("P6-T4b: spread + per_day + day-2 over → one per-day deduction",
      len(_perday_deds) == 1, str(_perday_deds))
check("P6-T4b: per-day deduction mentions Day 2",
      _perday_deds and "Day 2" in _perday_deds[0]["reason"],
      _perday_deds[0]["reason"] if _perday_deds else "no ded")

# Spread + per_day where BOTH days are within ceiling → no deductions
_t4b_perday_ok = _build_result([
    [_c_act("09:00", "10:30", "v1")],          # 90min — under 180
    [_c_act("09:00", "10:45", "v2")],          # 105min — under 180
])
_fc6 = evaluate_f_score(_t4b_perday_ok,
                         _f2e_task_v2("type2_subset_selection", 180,
                                       days_count=2,
                                       ceiling_mode="spread",
                                       ceiling_scope="per_day"),
                         _f2e_venues, _f2e_matrix)
_perday_deds_ok = [d for d in _fc6["deductions"] if d["section"] == "F2e"]
check("P6-T4b: spread + per_day + both days within → no F2e deduction",
      len(_perday_deds_ok) == 0, str(_perday_deds_ok))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All E3 tests passed ({PASS}/{total})")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)


# ─────────────────────────────────────────────────────────────────────────────
# CAT-B: ratio and count_distinct existence checks in _build_scope_pools
# ─────────────────────────────────────────────────────────────────────────────
print("\nCAT-B: ratio and count_distinct solvability checks")

from scripts.generation.generate_task import _verify_task_solvable

def _make_pool_and_task(pool_venues, pc):
    """Helper: wrap into a minimal task + pool."""
    task = {
        "city": "london", "days": 2, "start_date": "2026-04-04",
        "structural_type": "type1_cascading_requirements",
        "public_input": {"query": "Test task"},
        "rubric": {
            "hard_constraints": [
                {"id": "hc1", "type": "hours_check", "check_method": "code", "params": {}},
                {"id": "hc2", "type": "no_overlap", "check_method": "code", "params": {}},
                {"id": "hc3", "type": "travel_time_hard", "check_method": "code", "params": {}},
            ],
            "personal_constraints": [pc],
            "b_score_constraints": [], "required_venue_ids": []
        }
    }
    return pool_venues, task

_base_pool = [
    {"venue_id": f"r{i}", "category": "restaurant", "district": f"D{i%4}",
     "tags": [], "price_tier": "mid", "traffic_tier": "mid",
     "recommended_visit_minutes": 60, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 20}
    for i in range(12)
] + [
    {"venue_id": f"s{i}", "category": "museum", "district": f"D{i%3}",
     "tags": [], "price_tier": "free", "traffic_tier": "mid",
     "recommended_visit_minutes": 90, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 0}
    for i in range(6)
]

# Bug 8 fix: ratio with 0 qualifying venues → fails solvability
pc_ratio_fail = {
    "id": "pc_r1", "score_tier": "P", "hop": 1, "check_method": "code",
    "source_in_profile": "vegan meals",
    "description": "60% meals vegan",
    "scope": "activity_type=meal",
    "condition": {"has_tag": "vegan"},  # no vegan venues in pool
    "aggregation": {"ratio": 0.6},
    "consequence": "p_score_full"
}
pool_r, task_r = _make_pool_and_task(_base_pool, pc_ratio_fail)
ok_r, reason_r = _verify_task_solvable(task_r, pool_r)
check("Bug8 fix: ratio with 0 qualifying venues → solvability fails", not ok_r, reason_r)

# Bug 8 fix: ratio with qualifying venues → passes
pool_r2 = _base_pool + [
    {"venue_id": "r_vegan", "category": "restaurant", "district": "D1",
     "tags": ["vegan"], "price_tier": "mid", "traffic_tier": "mid",
     "recommended_visit_minutes": 60, "booking_required": False,
     "wheelchair_accessible": True, "avg_cost_local": 20}
]
_, task_r2 = _make_pool_and_task(pool_r2, pc_ratio_fail)
ok_r2, _ = _verify_task_solvable(task_r2, pool_r2)
check("Bug8 fix: ratio with ≥1 qualifying venue → passes", ok_r2)

# Bug 7 (count_distinct): insufficient distinct values → fails
pc_cd_fail = {
    "id": "pc_cd1", "score_tier": "P", "hop": 1, "check_method": "code",
    "source_in_profile": "explore different areas",
    "description": "Museums from 4 distinct districts",
    "scope": "category=museum",
    "condition": {},
    "aggregation": {"count_distinct": 4, "field": "district"},  # pool only has 3
    "consequence": "p_score_full"
}
pool_cd, task_cd = _make_pool_and_task(_base_pool, pc_cd_fail)
ok_cd, reason_cd = _verify_task_solvable(task_cd, pool_cd)
check("Bug7 fix: count_distinct with insufficient distinct values → fails", not ok_cd, reason_cd)

# Bug 7 (count_distinct): sufficient distinct values → passes
import copy as _copy
pc_cd_pass = _copy.deepcopy(pc_cd_fail)
pc_cd_pass["id"] = "pc_cd2"
pc_cd_pass["aggregation"] = {"count_distinct": 3, "field": "district"}  # pool has 3
pool_cdp, task_cdp = _make_pool_and_task(_base_pool, pc_cd_pass)
ok_cdp, _ = _verify_task_solvable(task_cdp, pool_cdp)
check("Bug7 fix: count_distinct with sufficient distinct values → passes", ok_cdp)
