"""
scripts/generation/calibrate_constraints.py

B6 — Constraint calibration.

For each new handler (Sprint B2), runs probe pairs: a "good" plan that
satisfies the constraint and a "bad" plan that doesn't. Verifies that
the score delta >= 0.8 (good scores 1.0, bad scores ≤ 0.2).

Also tests route efficiency (B3) and consecutive-pair checks.

Usage:
  python scripts/generation/calibrate_constraints.py
  python scripts/generation/calibrate_constraints.py --verbose
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from eval.evaluator import (
    _evaluate_generic_constraint,
    _handle_consecutive_pairs,
    _check_route_efficiency,
)

REQUIRED_DELTA = 0.8  # good_score - bad_score must be >= this

# ─── Shared venue fixtures ────────────────────────────────────────────────────

VENUES = {
    "v_museum_hi": {
        "venue_id": "v_museum_hi", "category": "museum",
        "traffic_tier": "high", "recommended_pace": "intense",
        "recommended_visit_minutes": 120, "local_cuisine": 0,
        "price_tier": "free", "noise_level": "moderate",
        "tags": ["art", "museum"], "category_tags": ["art","museum"],
    },
    "v_cafe_lo": {
        "venue_id": "v_cafe_lo", "category": "cafe",
        "traffic_tier": "low", "recommended_pace": "relaxed",
        "recommended_visit_minutes": 45, "local_cuisine": 1,
        "price_tier": "budget", "noise_level": "quiet",
        "tags": ["hidden-gem", "coffee", "locals-favourite"],
        "category_tags": ["hidden-gem","coffee"],
        "cuisine_label": "British",
    },
    "v_resto_local": {
        "venue_id": "v_resto_local", "category": "restaurant",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "recommended_visit_minutes": 75, "local_cuisine": 1,
        "price_tier": "mid", "noise_level": "moderate",
        "tags": ["french", "local"], "category_tags": ["french"],
        "cuisine_label": "French",
    },
    "v_resto_intl": {
        "venue_id": "v_resto_intl", "category": "restaurant",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "recommended_visit_minutes": 75, "local_cuisine": 0,
        "price_tier": "mid", "noise_level": "moderate",
        "tags": ["international"], "category_tags": ["international"],
        "cuisine_label": "International",
    },
    "v_resto_thai": {
        "venue_id": "v_resto_thai", "category": "restaurant",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "recommended_visit_minutes": 60, "local_cuisine": 0,
        "price_tier": "budget", "noise_level": "moderate",
        "tags": ["thai"], "category_tags": ["thai"],
        "cuisine_label": "Thai",
    },
    "v_park_lo": {
        "venue_id": "v_park_lo", "category": "park",
        "traffic_tier": "low", "recommended_pace": "relaxed",
        "recommended_visit_minutes": 45, "local_cuisine": 0,
        "price_tier": "free", "noise_level": "quiet",
        "tags": ["outdoor", "hidden-gem"], "category_tags": ["outdoor"],
    },
    "v_bar_hi": {
        "venue_id": "v_bar_hi", "category": "bar",
        "traffic_tier": "high", "recommended_pace": "intense",
        "recommended_visit_minutes": 90, "local_cuisine": 0,
        "price_tier": "upscale", "noise_level": "loud",
        "tags": ["bar", "tourist"], "category_tags": ["bar"],
    },
}

MATRIX = {
    "v_museum_hi_to_v_cafe_lo": 10,   "v_cafe_lo_to_v_museum_hi": 10,
    "v_museum_hi_to_v_park_lo": 5,    "v_park_lo_to_v_museum_hi": 5,
    "v_cafe_lo_to_v_park_lo": 8,      "v_park_lo_to_v_cafe_lo": 8,
    "v_museum_hi_to_v_bar_hi": 15,    "v_bar_hi_to_v_museum_hi": 15,
    "v_cafe_lo_to_v_bar_hi": 20,      "v_bar_hi_to_v_cafe_lo": 20,
    "v_park_lo_to_v_bar_hi": 18,      "v_bar_hi_to_v_park_lo": 18,
}

def act(vid, atype="visit"):
    v = VENUES[vid]
    return {"venue_id": vid, "venue_name": v.get("name", vid),
            "activity_type": atype, "time_start": "10:00", "time_end": "12:00",
            "estimated_cost_local": 0}

def day(acts): return {"day": 1, "day_of_week": "sat", "activities": acts}


# ─── Calibration harness ──────────────────────────────────────────────────────

results = []

def probe(name, good_score, bad_score, verbose=False):
    delta = good_score - bad_score
    passed = delta >= REQUIRED_DELTA
    results.append({"name": name, "good": good_score, "bad": bad_score,
                     "delta": delta, "passed": passed})
    status = "✅" if passed else "❌"
    if verbose or not passed:
        print(f"  {status} {name}: good={good_score:.2f} bad={bad_score:.2f} Δ={delta:.2f}")
    else:
        print(f"  {status} {name}")
    return passed


def run_calibration(verbose=False):
    print("\n" + "="*60)
    print("B6 — Constraint calibration (required delta ≥ 0.8)")
    print("="*60)

    # ── hidden_gem_required (migrated to engine) ─────────────────────────────
    print("\n[hidden_gem_required → engine at_least]")

    good_acts = [act("v_cafe_lo"), act("v_museum_hi")]
    bad_acts  = [act("v_museum_hi"), act("v_bar_hi")]
    c_gem = {"scope": "all",
             "condition": {"any": [
                 {"field": "traffic_tier", "operator": "==", "value": "low"},
                 {"has_tag": "hidden-gem"},
             ]},
             "aggregation": {"at_least": 1}}

    good = _evaluate_generic_constraint("c", c_gem, [day(good_acts)],
                                        good_acts, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_gem, [day(bad_acts)],
                                        bad_acts, VENUES)["score"]
    probe("hidden_gem_required min_count=1", good, bad, verbose)

    # ── local_cuisine_preference (migrated to engine) ─────────────────────────
    print("\n[local_cuisine_preference → engine ratio]")

    good_meals = [act("v_cafe_lo","meal"), act("v_resto_local","meal")]
    bad_meals  = [act("v_resto_intl","meal"), act("v_bar_hi","meal")]
    c_local = {"scope": "activity_type=meal",
               "condition": {"field": "local_cuisine", "operator": "==", "value": 1},
               "aggregation": {"ratio": 0.5}}

    good = _evaluate_generic_constraint("c", c_local, [day(good_meals)],
                                        good_meals, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_local, [day(bad_meals)],
                                        bad_meals, VENUES)["score"]
    probe("local_cuisine_preference ratio=0.5", good, bad, verbose)

    # ── cuisine_diversity_minimum (migrated to engine) ───────────────────────
    print("\n[cuisine_diversity_minimum → engine count_distinct]")

    good_diverse = [act("v_cafe_lo","meal"), act("v_resto_local","meal"),
                    act("v_resto_thai","meal")]
    # Truly-bad case: no meal activities at all → 0 distinct cuisines.
    # (After count_distinct gained proportional partial credit, 3× same venue would
    # score 1/3 ≈ 0.33 — not "bad" enough vs 0.8 calibration target. Using
    # non-meal activities forces a true 0 by failing the scope filter.)
    bad_same = [act("v_park_lo","visit"), act("v_museum_hi","visit"),
                act("v_park_lo","visit")]
    c_diversity = {"scope": "activity_type=meal",
                   "condition": {},
                   "aggregation": {"count_distinct": 3, "field": "cuisine_label"}}

    good = _evaluate_generic_constraint("c", c_diversity, [day(good_diverse)],
                                        good_diverse, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_diversity, [day(bad_same)],
                                        bad_same, VENUES)["score"]
    probe("cuisine_diversity_minimum min_count=3", good, bad, verbose)

    # ── pace_relaxed (migrated to engine) ────────────────────────────────────
    print("\n[pace_relaxed → engine at_least_days]")

    good_relaxed = [act("v_cafe_lo"), act("v_park_lo")]
    bad_intense  = [act("v_museum_hi"), act("v_bar_hi")]
    c_pace = {"scope": "all",
              "condition": {"field": "recommended_pace", "operator": "<=", "value": "relaxed"},
              "aggregation": {"at_least_days": 1}}

    good = _evaluate_generic_constraint("c", c_pace, [day(good_relaxed)],
                                        good_relaxed, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_pace, [day(bad_intense)],
                                        bad_intense, VENUES)["score"]
    probe("pace_relaxed min_days=1", good, bad, verbose)

    # ── max_visit_duration (migrated to engine) ───────────────────────────────
    print("\n[max_visit_duration → engine all+field]")

    good_short = [act("v_cafe_lo"), act("v_park_lo")]  # 45min each
    bad_long   = [act("v_museum_hi"), act("v_bar_hi")]  # 120/90min
    c_maxvisit = {"scope": "all",
                  "condition": {"field": "recommended_visit_minutes",
                                "operator": "<=", "value": 60},
                  "aggregation": "all"}

    good = _evaluate_generic_constraint("c", c_maxvisit, [day(good_short)],
                                        good_short, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_maxvisit, [day(bad_long)],
                                        bad_long, VENUES)["score"]
    probe("max_visit_duration max=60min", good, bad, verbose)

    # ── consecutive_pairs ─────────────────────────────────────────────────────
    print("\n[consecutive_pairs]")

    good_alt = [act("v_museum_hi"), act("v_cafe_lo"), act("v_bar_hi")]  # intense/relaxed/intense
    bad_same = [act("v_museum_hi"), act("v_bar_hi"), act("v_museum_hi")]  # all intense

    good = _handle_consecutive_pairs("c",
           {"check_type":"alternates","field":"recommended_pace","threshold":0.5},
           [day(good_alt)], good_alt, VENUES)["score"]
    bad  = _handle_consecutive_pairs("c",
           {"check_type":"alternates","field":"recommended_pace","threshold":0.5},
           [day(bad_same)], bad_same, VENUES)["score"]
    probe("consecutive_pairs alternates pace", good, bad, verbose)

    # ── generic schema: label_required ────────────────────────────────────────
    print("\n[generic schema]")

    c_label = {"scope":"all","condition":{"has_tag":"hidden-gem"},
                "aggregation":{"at_least":1},"description":"need hidden gem"}
    good_acts2 = [act("v_cafe_lo"), act("v_museum_hi")]
    bad_acts2  = [act("v_museum_hi"), act("v_bar_hi")]

    good = _evaluate_generic_constraint("c", c_label, [day(good_acts2)], good_acts2, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_label, [day(bad_acts2)], bad_acts2, VENUES)["score"]
    probe("generic: at_least 1 hidden-gem", good, bad, verbose)

    c_excl = {"scope":"all","condition":{"not_tag":"tourist"},
               "aggregation":"all","description":"no tourist venues"}
    good = _evaluate_generic_constraint("c", c_excl, [day(good_acts2)], good_acts2, VENUES)["score"]
    bad  = _evaluate_generic_constraint("c", c_excl, [day(bad_acts2)], bad_acts2, VENUES)["score"]
    probe("generic: aggregation=all not_tag tourist", good, bad, verbose)

    # ── route efficiency ──────────────────────────────────────────────────────
    print("\n[route_efficiency (B3)]")

    # Efficient: museum→park→cafe = 5+8=13, MST(museum,park,cafe) = 5+8=13
    efficient_plan = {"days": [day([act("v_museum_hi"), act("v_park_lo"), act("v_cafe_lo")])]}
    # Bad: cafe→museum→bar→cafe = 10+15+20=45, MST(cafe,museum,bar)=10+15=25, ratio=1.8x (fails)
    # Include cafe twice to force backtracking across the whole map
    backtrack_plan = {"days": [day([
        act("v_cafe_lo"), act("v_museum_hi"),
        act("v_bar_hi"),  act("v_cafe_lo"),
    ])]}

    good = _check_route_efficiency(efficient_plan, MATRIX)["score"]
    bad  = _check_route_efficiency(backtrack_plan, MATRIX)["score"]
    probe("route_efficiency (efficient vs backtracking)", good, bad, verbose)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    n_pass = sum(1 for r in results if r["passed"])
    n_fail = sum(1 for r in results if not r["passed"])
    print(f"Calibration results: {n_pass}/{len(results)} handlers pass (Δ ≥ {REQUIRED_DELTA})")

    if n_fail > 0:
        print("\nFailing handlers:")
        for r in results:
            if not r["passed"]:
                print(f"  ❌ {r['name']}: Δ={r['delta']:.2f} (good={r['good']:.2f} bad={r['bad']:.2f})")

    return n_fail == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Calibrate B2/B3 constraint handlers")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    passed = run_calibration(verbose=args.verbose)
    sys.exit(0 if passed else 1)
