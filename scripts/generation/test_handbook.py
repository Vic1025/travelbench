"""
scripts/generation/test_handbook.py

Tests for handbook.py — verifies all valid queries return non-empty content
and unknown queries return a helpful error.

Run: python scripts/generation/test_handbook.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.handbook import query_handbook

PASS = "✅"
FAIL = "❌"
results = []

def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))

def check_contains(name: str, result: str, *expected_strings: str):
    for s in expected_strings:
        ok = s.lower() in result.lower()
        check(f"{name} contains '{s}'", ok)

# ─── Test 1: function index ───────────────────────────────────────────────────
print("\n[1] -h function (index)")
r = query_handbook("-h function")
check("returns non-empty", len(r) > 0)
check_contains("function index", r, "HELP", "CREATE_PAGE", "FILL", "COMMIT",
               "VERIFY", "GET_STATUS", "THINK", "SUBMIT")

# ─── Test 2: individual function specs ───────────────────────────────────────
print("\n[2] -h function <name>")
tools = ["HELP", "CREATE_PAGE", "FILL", "COMMIT", "VERIFY", "GET_STATUS", "THINK", "SUBMIT"]
for tool in tools:
    r = query_handbook(f"-h function {tool}")
    check(f"{tool} spec non-empty", len(r) > 100)
    check_contains(f"{tool} spec", r, "Parameters", "Returns")

# ─── Test 3: format index ─────────────────────────────────────────────────────
print("\n[3] -h format (index)")
r = query_handbook("-h format")
check("returns non-empty", len(r) > 0)
check_contains("format index", r, "venue", "yelp_listing", "source_doc",
               "official_site_doc", "wrong_info_rules", "traffic_tier", "tags", "hours")

# ─── Test 4: individual format specs ─────────────────────────────────────────
print("\n[4] -h format <name>")
formats = ["venue", "yelp_listing", "source_doc", "official_site_doc",
           "wrong_info_rules", "traffic_tier", "tags", "hours"]
for fmt in formats:
    r = query_handbook(f"-h format {fmt}")
    check(f"{fmt} spec non-empty", len(r) > 100)

# ─── Test 5: wrong_info_rules content ────────────────────────────────────────
print("\n[5] wrong_info_rules content")
r = query_handbook("-h format wrong_info_rules")
check_contains("wrong_info_rules", r,
    "temporal decay", "propagation", "conditional",
    "origin story", "source-content fit", "truth carrier", "incorrect")

# ─── Test 6: traffic_tier content ────────────────────────────────────────────
print("\n[6] traffic_tier content")
r = query_handbook("-h format traffic_tier")
check_contains("traffic_tier", r, "high", "mid", "low",
               "total_results", "source docs", "wrong info")

# ─── Test 7: query normalisation ─────────────────────────────────────────────
print("\n[7] Query normalisation")
# Should work with and without -h prefix, mixed case
variants = [
    "-h function FILL",
    "-h function fill",
    "-h function Fill",
    "function FILL",
]
for v in variants:
    r = query_handbook(v)
    check(f"normalised: '{v}'", "Parameters" in r)

# ─── Test 8: unknown queries ──────────────────────────────────────────────────
print("\n[8] Unknown queries")
unknowns = [
    "-h function UNKNOWN_TOOL",
    "-h format unknown_format",
    "-h blah",
    "",
]
for q in unknowns:
    r = query_handbook(q)
    check(f"unknown '{q}' returns error message", len(r) > 0)
    # Should not return empty string or crash
    check(f"unknown '{q}' not empty", len(r.strip()) > 0)

# ─── Test 9: VERIFY spec mentions all checks ─────────────────────────────────
print("\n[9] VERIFY spec completeness")
r = query_handbook("-h function VERIFY")
expected_checks = [
    "incorrect_hours_diff", "regulation_visibility", "label_subset",
    "official_site_exists", "truth_carrier_registered",
    "recommended_pace_assigned", "hours_override_coverage"
]
for c in expected_checks:
    check(f"VERIFY mentions {c}", c in r)

# ─── Test 10: venue format mentions all required fields ──────────────────────
print("\n[10] Venue format completeness")
r = query_handbook("-h format venue")
required = [
    "venue_id", "category", "price_tier", "recommended_visit_minutes",
    "outdoor_sensitivity", "recommended_pace", "traffic_tier",
    "noise_level", "food_available"
]
for f in required:
    check(f"venue format mentions {f}", f in r)


# ─── Test 11: P6-T1 — Canonical tag vocabulary constants & helpers ───────────
print("\n[11] P6-T1 vocab constants and helpers")
from scripts.generation.handbook import (
    UNIVERSAL_CORE_TAGS, UNIVERSAL_CUISINES, get_combined_vocab, format_vocab_by_axis,
)
check("UNIVERSAL_CORE_TAGS is non-empty frozenset",
      isinstance(UNIVERSAL_CORE_TAGS, frozenset) and len(UNIVERSAL_CORE_TAGS) == 53)
check("UNIVERSAL_CUISINES has 18 entries", len(UNIVERSAL_CUISINES) == 18)

# Sentinel entries — load-bearing tags that downstream code depends on
sentinels_core = ["free-entry", "step-free", "hidden-gem", "rooftop", "instagrammable"]
for s in sentinels_core:
    check(f"core vocab contains '{s}'", s in UNIVERSAL_CORE_TAGS)
sentinels_cuisine = ["italian", "japanese", "british", "fusion"]
for s in sentinels_cuisine:
    check(f"cuisine vocab contains '{s}'", s in UNIVERSAL_CUISINES)

# P6-T2-B vocab cleanup — regulation-mirror tags must be ABSENT from the vocab.
# Each of these has a dedicated structured field; agents filter via query_pool's
# regulation/price_tier/etc. branches directly.
removed_in_t2b = [
    "photography-allowed", "pet-friendly", "family-friendly",
    "wheelchair-accessible", "booking-required", "no-reservations",
    "outdoor", "dog-friendly", "quiet", "formal", "budget-friendly",
]
for s in removed_in_t2b:
    check(f"P6-T2-B cleanup: '{s}' removed from core vocab",
          s not in UNIVERSAL_CORE_TAGS)

# Slice 3 vocab amendment — survivors of the T2-B cleanup
slice3_survivors = ["coffee", "cocktails", "wine",
                    "street-food", "small-plates", "park"]
for s in slice3_survivors:
    check(f"Slice 3 amendment: '{s}' still in core vocab", s in UNIVERSAL_CORE_TAGS)

# Combined vocab semantics
v_no_ext = get_combined_vocab(None)
v_with_ext = get_combined_vocab(["samba-spot", "bloco-route"])
check("get_combined_vocab(None) returns universal",
      "free-entry" in v_no_ext and "italian" in v_no_ext)
check("get_combined_vocab() merges city extension",
      "samba-spot" in v_with_ext and "free-entry" in v_with_ext)
check("get_combined_vocab() does NOT mutate universal core",
      "samba-spot" not in UNIVERSAL_CORE_TAGS)

# format_vocab_by_axis is a string with all axis headers
axis_text = format_vocab_by_axis(["samba-spot"])
for axis in ["Cuisine:", "Practical:", "Audience:", "Vibe:",
             "Quality signal:", "Characteristic:", "City-specific:"]:
    check(f"format_vocab_by_axis contains '{axis}'", axis in axis_text)
check("format_vocab_by_axis shows city extension",
      "samba-spot" in axis_text)

# Handbook "tags" entry references the new vocabulary
tags_help = query_handbook("-h format tags")
check("handbook tags section mentions 'VOCABULARY'", "VOCABULARY" in tags_help)
check("handbook tags section lists italian", "italian" in tags_help)
check("handbook tags section no longer mentions CITY_TAG_CAP",
      "CITY_TAG_CAP" not in tags_help)


# ─── Summary ─────────────────────────────────────────────────────────────────
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("✅ All A2 tests passed — Handbook ready")
else:
    print("❌ Some tests failed — fix before proceeding to A3")
    sys.exit(1)
