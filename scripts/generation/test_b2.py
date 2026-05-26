"""
scripts/generation/test_b2.py

Tests for B2 — generic constraint schema engine and five new handlers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}{(' — ' + str(detail)) if detail else ''}")
        FAIL += 1

from eval.evaluator import (
    _evaluate_generic_constraint,
    _handle_consecutive_pairs,
    P_SCORE_HANDLERS,
)

# ─── Test fixtures ────────────────────────────────────────────────────────────

VENUES = {
    "v_museum_high": {
        "venue_id": "v_museum_high", "name": "Big Museum", "category": "museum",
        "traffic_tier": "high", "recommended_pace": "intense",
        "recommended_visit_minutes": 120, "local_cuisine": 0,
        "price_tier": "free", "noise_level": "moderate",
        "tags": ["art", "museum"], "category_tags": ["art", "museum"],
        "wheelchair_accessible": 1,
    },
    "v_cafe_low": {
        "venue_id": "v_cafe_low", "name": "Hidden Cafe", "category": "cafe",
        "traffic_tier": "low", "recommended_pace": "relaxed",
        "recommended_visit_minutes": 60, "local_cuisine": 1,
        "price_tier": "budget", "noise_level": "quiet",
        "tags": ["hidden-gem", "coffee", "locals-favourite"],
        "category_tags": ["hidden-gem", "coffee"],
        "cuisine_label": "British",
    },
    "v_restaurant_mid": {
        "venue_id": "v_restaurant_mid", "name": "Local Bistro", "category": "restaurant",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "recommended_visit_minutes": 75, "local_cuisine": 1,
        "price_tier": "mid", "noise_level": "moderate",
        "tags": ["french", "casual"], "category_tags": ["french", "casual"],
        "cuisine_label": "French",
    },
    "v_bar_mid": {
        "venue_id": "v_bar_mid", "name": "Wine Bar", "category": "bar",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "recommended_visit_minutes": 90, "local_cuisine": 0,
        "price_tier": "upscale", "noise_level": "loud",
        "tags": ["wine", "bar"], "category_tags": ["wine"],
        "cuisine_label": "International",
    },
    "v_park_low": {
        "venue_id": "v_park_low", "name": "Secret Garden", "category": "park",
        "traffic_tier": "low", "recommended_pace": "relaxed",
        "recommended_visit_minutes": 45, "local_cuisine": 0,
        "price_tier": "free", "noise_level": "quiet",
        "tags": ["outdoor", "hidden-gem"], "category_tags": ["outdoor"],
    },
    "v_restaurant_thai": {
        "venue_id": "v_restaurant_thai", "name": "Thai Place", "category": "restaurant",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "recommended_visit_minutes": 60, "local_cuisine": 0,
        "price_tier": "budget", "noise_level": "moderate",
        "tags": ["thai"], "category_tags": ["thai"],
        "cuisine_label": "Thai",
    },
}

def make_day(activities):
    return {"day": 1, "day_of_week": "sat", "activities": activities}

def make_act(vid, atype="visit", time_start="10:00", time_end="12:00", cost=0):
    v = VENUES.get(vid, {})
    return {
        "venue_id": vid,
        "venue_name": v.get("name", vid),
        "activity_type": atype,
        "time_start": time_start,
        "time_end": time_end,
        "estimated_cost_local": cost,
    }

ALL_ACTS = [
    make_act("v_museum_high", "visit"),
    make_act("v_cafe_low", "meal"),
    make_act("v_restaurant_mid", "meal"),
    make_act("v_bar_mid", "leisure"),
    make_act("v_park_low", "visit"),
]

DAYS = [make_day(ALL_ACTS)]


# ─────────────────────────────────────────────────────────────────────────────
# [1] P_SCORE_HANDLERS registry contains only Bucket C irreducibles
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] P_SCORE_HANDLERS — only irreducible Bucket C patterns")

for h in ["weather_aware", "dependency_chain",
          "consecutive_pairs", "opening_time_required"]:
    check(f"'{h}' still registered", h in P_SCORE_HANDLERS)

for h in ["local_cuisine_preference", "cuisine_diversity_minimum",
          "temporal_cross_day"]:
    check(f"'{h}' removed (migrated to engine)", h not in P_SCORE_HANDLERS)


# ─────────────────────────────────────────────────────────────────────────────
# [2] hidden_gem_required (engine)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] hidden_gem_required → engine")

c_gem = {"scope": "all",
          "condition": {"any": [
              {"field": "traffic_tier", "operator": "==", "value": "low"},
              {"has_tag": "hidden-gem"},
          ]},
          "aggregation": {"at_least": 1}}

r = _evaluate_generic_constraint("c1", c_gem, DAYS, ALL_ACTS, VENUES)
check("found hidden gems → score=1.0", r["score"] == 1.0, r)

c_gem3 = {**c_gem, "aggregation": {"at_least": 3}}
r = _evaluate_generic_constraint("c1", c_gem3, DAYS, ALL_ACTS, VENUES)
check("insufficient hidden gems → score<1.0", r["score"] < 1.0, r)

no_hidden = [make_act("v_museum_high","visit"), make_act("v_bar_mid","leisure")]
r = _evaluate_generic_constraint("c1", c_gem, [make_day(no_hidden)], no_hidden, VENUES)
check("no hidden gems → score=0.0", r["score"] == 0.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [3] local_cuisine_preference → engine (ratio aggregation)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] local_cuisine_preference → engine ratio")

c_local = {"scope": "activity_type=meal",
            "condition": {"field": "local_cuisine", "operator": "==", "value": 1},
            "aggregation": {"ratio": 0.6}}

meals = [make_act("v_cafe_low","meal"), make_act("v_restaurant_mid","meal")]
r = _evaluate_generic_constraint("c1", c_local, [make_day(meals)], meals, VENUES)
check("2/2 local meals → score=1.0", r["score"] == 1.0, r)

mixed = [make_act("v_cafe_low","meal"), make_act("v_bar_mid","meal")]
c_local_strict = {**c_local, "aggregation": {"ratio": 0.9}}
r = _evaluate_generic_constraint("c1", c_local_strict, [make_day(mixed)], mixed, VENUES)
check("1/2 local below threshold → score<1.0", r["score"] < 1.0, r)

no_meals = [make_act("v_museum_high","visit")]
r = _evaluate_generic_constraint("c1", c_local, [make_day(no_meals)], no_meals, VENUES)
check("no meals → N/A → score=1.0", r["score"] == 1.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [4] cuisine_diversity_minimum → engine (count_distinct aggregation)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] cuisine_diversity_minimum → engine count_distinct")

c_diversity = {"scope": "activity_type=meal",
               "condition": {},
               "aggregation": {"count_distinct": 3, "field": "cuisine_label"}}

diverse_meals = [
    make_act("v_cafe_low","meal"),
    make_act("v_restaurant_mid","meal"),
    make_act("v_restaurant_thai","meal"),
]
r = _evaluate_generic_constraint("c1", c_diversity, [make_day(diverse_meals)], diverse_meals, VENUES)
check("3 distinct cuisines → score=1.0", r["score"] == 1.0, r)

two_meals = [make_act("v_cafe_low","meal"), make_act("v_restaurant_mid","meal")]
r = _evaluate_generic_constraint("c1", c_diversity, [make_day(two_meals)], two_meals, VENUES)
check("2 distinct cuisines, need 3 → score<1.0", r["score"] < 1.0, r)

same_meals = [make_act("v_cafe_low","meal"), make_act("v_cafe_low","meal")]
c_diversity2 = {**c_diversity, "aggregation": {"count_distinct": 2, "field": "cuisine_label"}}
r = _evaluate_generic_constraint("c1", c_diversity2, [make_day(same_meals)], same_meals, VENUES)
check("1 distinct cuisine, need 2 → score=0.0", r["score"] == 0.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [5] pace_relaxed → engine (at_least_days aggregation)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] pace_relaxed → engine at_least_days")

c_pace = {"scope": "all",
           "condition": {"field": "recommended_pace", "operator": "<=", "value": "relaxed"},
           "aggregation": {"at_least_days": 1}}

relaxed_acts = [make_act("v_cafe_low","visit"), make_act("v_park_low","visit")]
relaxed_day  = [make_day(relaxed_acts)]
r = _evaluate_generic_constraint("c1", c_pace, relaxed_day, relaxed_acts, VENUES)
check("full relaxed day → score=1.0", r["score"] == 1.0, r)

mixed_acts = [make_act("v_cafe_low","visit"), make_act("v_museum_high","visit")]
mixed_day  = [make_day(mixed_acts)]
r = _evaluate_generic_constraint("c1", c_pace, mixed_day, mixed_acts, VENUES)
check("mixed pace day → no fully-relaxed day → score<1.0", r["score"] < 1.0, r)

two_relaxed = [make_day(relaxed_acts), make_day(relaxed_acts)]
c_pace2 = {**c_pace, "aggregation": {"at_least_days": 2}}
r = _evaluate_generic_constraint("c1", c_pace2, two_relaxed, relaxed_acts * 2, VENUES)
check("2 relaxed days, need 2 → score=1.0", r["score"] == 1.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [6] max_visit_duration → engine (all + field comparison)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] max_visit_duration → engine")

c_dur = {"scope": "all",
          "condition": {"field": "recommended_visit_minutes", "operator": "<=", "value": 90},
          "aggregation": "all"}

short_acts = [make_act("v_cafe_low","visit"), make_act("v_park_low","visit")]
r = _evaluate_generic_constraint("c1", c_dur, DAYS, short_acts, VENUES)
check("all venues ≤ 90min → score=1.0", r["score"] == 1.0, r)

long_acts = [make_act("v_museum_high","visit")]  # museum=120min
r = _evaluate_generic_constraint("c1", c_dur, [make_day(long_acts)], long_acts, VENUES)
check("museum 120min > 90 limit → score<1.0", r["score"] < 1.0, r)

c_dur150 = {**c_dur, "condition": {"field": "recommended_visit_minutes", "operator": "<=", "value": 150}}
r = _evaluate_generic_constraint("c1", c_dur150, DAYS, ALL_ACTS, VENUES)
check("all venues ≤ 150min → score=1.0", r["score"] == 1.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [7] consecutive_pairs
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] consecutive_pairs")

# Museum(intense) → Cafe(relaxed): alternates — good
alternating = [make_act("v_museum_high","visit"), make_act("v_cafe_low","visit")]
r = _handle_consecutive_pairs("c1", {"check_type": "alternates", "field": "recommended_pace",
                                      "threshold": 0.5},
                               [make_day(alternating)], alternating, VENUES)
check("alternating pace → passes", r["score"] >= 0.5, r)

# Museum(intense) → Museum(intense): no consecutive intense
same_pace = [make_act("v_museum_high","visit"), make_act("v_museum_high","visit")]
r = _handle_consecutive_pairs("c1", {"check_type": "no_consecutive", "field": "recommended_pace",
                                      "values": ["intense"], "threshold": 1.0},
                               [make_day(same_pace)], same_pace, VENUES)
check("consecutive intense venues → fails no_consecutive", r["score"] < 1.0, r)

# Single activity: no pairs to check
single = [make_act("v_museum_high","visit")]
r = _handle_consecutive_pairs("c1", {"check_type": "alternates", "field": "recommended_pace"},
                               [make_day(single)], single, VENUES)
check("single activity: no pairs → score=1.0", r["score"] == 1.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [8] Generic schema engine — scope
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] Generic schema — scope")

# scope="all"
c = {"scope": "all", "condition": {"has_tag": "hidden-gem"},
     "aggregation": {"at_least": 1}, "description": "need hidden gem"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("scope=all, at_least 1 hidden-gem → pass", r["score"] == 1.0, r)

# scope="activity_type=meal"
c = {"scope": "activity_type=meal", "condition": {"has_tag": "hidden-gem"},
     "aggregation": "all", "description": "all meals hidden gem"}
meal_acts = [make_act("v_cafe_low","meal"), make_act("v_restaurant_mid","meal")]
r = _evaluate_generic_constraint("c1", c, [make_day(meal_acts)], meal_acts, VENUES)
check("scope=meal, all hidden-gem: cafe yes, bistro no → fail", r["score"] < 1.0, r)

# scope="category=museum"
c = {"scope": "category=museum", "condition": {"field": "traffic_tier", "operator": "==", "value": "high"},
     "aggregation": "all", "description": "museums are high traffic"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("scope=museum, traffic_tier=high → pass", r["score"] == 1.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [9] Generic schema — condition types
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] Generic schema — condition types")

# not_tag
c = {"scope": "all", "condition": {"not_tag": "tourist-trap"},
     "aggregation": "all", "description": "no tourist traps"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("not_tag=tourist-trap, none have it → pass", r["score"] == 1.0, r)

# field comparison — price_tier >= upscale
c = {"scope": "all", "condition": {"field": "price_tier", "operator": ">=", "value": "upscale"},
     "aggregation": {"at_least": 1}, "description": "at least one upscale"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("at_least 1 upscale (bar is upscale) → pass", r["score"] == 1.0, r)

# count_distinct cuisines
c = {"scope": "activity_type=meal",
     "condition": {"field": "cuisine_label"},
     "aggregation": {"count_distinct": 2, "field": "cuisine_label"},
     "description": "2 distinct cuisines"}
meal_acts2 = [make_act("v_cafe_low","meal"), make_act("v_restaurant_mid","meal")]
r = _evaluate_generic_constraint("c1", c, [make_day(meal_acts2)], meal_acts2, VENUES)
check("count_distinct 2 cuisines (British+French) → pass", r["score"] == 1.0, r)

# ratio
c = {"scope": "activity_type=meal",
     "condition": {"field": "local_cuisine", "operator": "==", "value": 1},
     "aggregation": {"ratio": 0.5, "of": "activity_type=meal"},
     "description": "50% local meals"}
r = _evaluate_generic_constraint("c1", c, [make_day(meal_acts2)], meal_acts2, VENUES)
check("ratio 2/2 local meals ≥ 0.5 → pass", r["score"] == 1.0, r)


# ─────────────────────────────────────────────────────────────────────────────
# [10] Generic schema — aggregation types
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] Generic schema — aggregation")

loud_acts = [make_act("v_bar_mid","leisure")]

# aggregation="none" — no loud venues
c = {"scope": "all", "condition": {"field": "noise_level", "operator": "==", "value": "loud"},
     "aggregation": "none", "description": "no loud venues"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("aggregation=none, bar is loud → fail", r["score"] < 1.0, r)

# aggregation "at_most"
c = {"scope": "all", "condition": {"field": "noise_level", "operator": "==", "value": "loud"},
     "aggregation": {"at_most": 1}, "description": "at most 1 loud venue"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("at_most 1 loud (1 bar = 1) → pass", r["score"] == 1.0, r)

# aggregation per_day
c = {"scope": "per_day",
     "condition": {"field": "recommended_pace", "operator": "==", "value": "relaxed"},
     "aggregation": {"at_least": 1}, "description": "at least 1 relaxed per day"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("per_day: 2 relaxed venues in day → pass", r["score"] == 1.0, r)

# per_day + sum aggregation (travel_time_budget pattern)
# Simulate travel_minutes enrichment on activities
_travel_acts = []
for i, a in enumerate(ALL_ACTS):
    _a = dict(a)
    _a["travel_minutes"] = 12.0 if i < len(ALL_ACTS) - 1 else 0.0
    _travel_acts.append(_a)
_travel_days = [make_day(_travel_acts)]

c = {"scope": "per_day",
     "condition": {},
     "aggregation": {"sum": "travel_minutes", "operator": "<=", "value": 60},
     "description": "daily travel ≤ 60 min"}
r = _evaluate_generic_constraint("c1", c, _travel_days, _travel_acts, VENUES)
check("per_day sum: 4×12=48 ≤ 60 → pass", r["score"] == 1.0, r)

c2 = {"scope": "per_day",
      "condition": {},
      "aggregation": {"sum": "travel_minutes", "operator": "<=", "value": 30},
      "description": "daily travel ≤ 30 min"}
r2 = _evaluate_generic_constraint("c1", c2, _travel_days, _travel_acts, VENUES)
check("per_day sum: 48 > 30 → fail", r2["score"] == 0.0, r2)

# indoor/outdoor balance via ratio + has_tag
# v_park_low has "outdoor" tag; 1 of 5 activities → 20%
c = {"scope": "all",
     "condition": {"has_tag": "outdoor"},
     "aggregation": {"ratio": 0.5},
     "description": "≥50% outdoor"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("ratio outdoor: 1/5 = 20% < 50% → fail", r["score"] == 0.0, r)

c = {"scope": "all",
     "condition": {"has_tag": "outdoor"},
     "aggregation": {"ratio": 0.15},
     "description": "≥15% outdoor"}
r = _evaluate_generic_constraint("c1", c, DAYS, ALL_ACTS, VENUES)
check("ratio outdoor: 1/5 = 20% ≥ 15% → pass", r["score"] == 1.0, r)


# [11] Paris regression — retired with the Paris-JSON pipeline.


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B2 tests passed ({PASS}/{total}) — generic schema engine ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
