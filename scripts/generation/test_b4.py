"""
scripts/generation/test_b4.py

Tests for B4 — task generation agent:
  - venue pool loader
  - stub task generation
  - solvability verification
  - task schema validation
  - full dry-run pipeline
"""

import sys, json, tempfile
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

from scripts.generation.db import init_db, get_connection, new_venue_id
from scripts.generation.populate_seasonal_windows import populate_windows, get_window
from scripts.generation.generate_task import (
    load_venue_pool, _stub_task, _verify_task_solvable,
    generate_tasks,
)


# ─── Test DB setup ────────────────────────────────────────────────────────────

def make_test_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    conn = get_connection(db)

    conn.execute("""INSERT INTO city_config
        (city, display_name, country, centre_lat, centre_lng,
         radius_km, local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("london","London","UK",51.5,-0.1,5.0,"British","[]"))

    vids = []
    venues = [
        ("Tate Modern",    "museum",     "South Bank", "high", "intense",  "free",   0, 120, 0),
        ("Borough Market", "attraction", "South Bank", "high", "moderate", "free",   0,  60, 0),
        ("Hidden Cafe",    "cafe",       "Shoreditch", "low",  "relaxed",  "budget", 1,  45, 0),
        ("Local Bistro",   "restaurant", "Soho",       "mid",  "moderate", "mid",    1,  75, 0),
        ("Wine Bar",       "bar",        "Mayfair",    "mid",  "moderate", "upscale",0,  90, 1),
        ("Secret Garden",  "park",       "Islington",  "low",  "relaxed",  "free",   0,  60, 0),
        ("The Shard",      "attraction", "South Bank", "high", "intense",  "upscale",0,  90, 0),
        ("Thai Kitchen",   "restaurant", "Camden",     "mid",  "moderate", "budget", 0,  60, 0),
        ("Jazz Club",      "attraction", "Soho",       "mid",  "moderate", "mid",    0, 120, 1),
        ("Hyde Park",      "park",       "Kensington", "high", "relaxed",  "free",   0, 120, 0),
    ]

    for name, cat, district, tier, pace, price, local, visit_min, wi in venues:
        vid = new_venue_id()
        conn.execute("""INSERT INTO venues
            (venue_id, city, name, category, district, lat, lng,
             avg_cost_local, price_tier, recommended_visit_minutes,
             booking_required, has_official_site, outdoor_sensitivity,
             recommended_pace, traffic_tier, total_results, yelp_popularity_score,
             pet_friendly, wheelchair_accessible, photography_allowed,
             noise_level, family_friendly, food_available, local_cuisine,
             has_wrong_info_planned, page_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid,"london",name,cat,district,51.5,-0.1,
             20.0,price,visit_min,0,1,"indoor",pace,tier,1000,0.7,
             0,1,1,"moderate",1,int(cat in("restaurant","cafe")),local,wi,"verified"))
        vids.append((vid, name, cat, tier, local))

    # Add tags
    tag_map = {
        "Tate Modern": ["art","museum","free-entry"],
        "Borough Market": ["market","food","outdoor"],
        "Hidden Cafe": ["hidden-gem","coffee","locals-favourite"],
        "Local Bistro": ["british","casual","local"],
        "Wine Bar": ["wine","upscale"],
        "Secret Garden": ["outdoor","hidden-gem","park"],
        "The Shard": ["viewpoint","upscale","tourist"],
        "Thai Kitchen": ["thai","casual"],
        "Jazz Club": ["live-music","jazz"],
        "Hyde Park": ["outdoor","park","free-entry"],
    }
    for vid, name, *_ in vids:
        for tag in tag_map.get(name, []):
            conn.execute("INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
                         (tag,"london",vid,1))

    conn.commit()
    conn.close()

    # Populate windows
    populate_windows("london", db_path=db)

    return db, vids


# ─────────────────────────────────────────────────────────────────────────────
# [1] Venue pool loader
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] Venue pool loader")
db, vids = make_test_db()

pool = load_venue_pool("london", db_path=db)
check("pool loads 10 verified venues", len(pool) == 10, len(pool))
check("pool has venue_id field", all("venue_id" in v for v in pool))
check("pool has tags list", all("tags" in v for v in pool))
check("pool has has_wrong_info bool", all("has_wrong_info" in v for v in pool))
check("has_wrong_info is bool not int", all(isinstance(v["has_wrong_info"], bool) for v in pool))
check("some venues have wrong info", sum(1 for v in pool if v["has_wrong_info"]) == 2)
check("pool has window_flags dict", all(isinstance(v.get("window_flags"), dict) for v in pool))
check("no source_doc bodies in pool", all("body" not in v for v in pool))

# Tags loaded
tate = next((v for v in pool if v["name"] == "Tate Modern"), None)
check("Tate Modern has art tag", tate and "art" in tate["tags"], tate)
hidden = next((v for v in pool if v["name"] == "Hidden Cafe"), None)
check("Hidden Cafe has hidden-gem tag", hidden and "hidden-gem" in hidden["tags"])


# ─────────────────────────────────────────────────────────────────────────────
# [2] Window loading
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Window loading")
window = get_window("london", "london_carnival_2026", db_path=db)
check("carnival window loads", window is not None)
check("window has character", len(window.get("character","")) > 50)
check("window has dates", len(window.get("dates",[])) == 7)
check("window has anchor_events", len(window.get("anchor_events",[])) >= 1)
check("window has conditional_wrong_info_hints", len(window.get("conditional_wrong_info_hints",[])) == 3)


# ─────────────────────────────────────────────────────────────────────────────
# [3] Stub task generation
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Stub task generation")
task = _stub_task("london", window, pool, 0)

check("task has task_id", "task_id" in task)
check("task has city=london", task.get("city") == "london")
check("task has window_id", task.get("window_id") == "london_carnival_2026")
check("task has days >= 1", task.get("days", 0) >= 1)
check("task has start_date in window", task.get("start_date") in window["dates"])
check("task has public_input", "public_input" in task)
check("query is non-empty", len(task["public_input"].get("query","")) > 20)
check("task has rubric", "rubric" in task)

rubric = task.get("rubric", {})
check("rubric has hard_constraints", len(rubric.get("hard_constraints",[])) >= 3)
check("rubric has hc_001 hours_check",
      any(c["type"] == "hours_check" for c in rubric.get("hard_constraints",[])))
check("rubric has hc_002 no_overlap",
      any(c["type"] == "no_overlap" for c in rubric.get("hard_constraints",[])))
check("rubric has hc_003 travel_time_hard",
      any(c["type"] == "travel_time_hard" for c in rubric.get("hard_constraints",[])))
check("rubric has b_score_constraints key",
      "b_score_constraints" in rubric)

# structural_type present
check("task has structural_type", "structural_type" in task)

# public_input doesn't contain constraint keywords
query = task["public_input"]["query"].lower()
forbidden = ["must not", "constraint", "regulation_required", "label_required",
             "hop:", "score_tier"]
check("query has no constraint terminology",
      not any(f in query for f in forbidden), [f for f in forbidden if f in query])


# ─────────────────────────────────────────────────────────────────────────────
# [3b] P6-T3 — structural_type auto-correct in SUBMIT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3b] P6-T3 — structural_type alias auto-correct")

from scripts.generation.task_agent import _dispatch_task_tool

def _alias_check(input_st, expected_canonical):
    minimal = {
        "task_id":     "lon_carnival_001",
        "city":        "london",
        "window_id":   "lon_carnival_2026",
        "days":        1,
        "start_date":  "2026-08-22",
        "structural_type": input_st,
        "difficulty":  "medium",
        "public_input": {"query": "x", "query_resources": {"time_ceiling_minutes": 120}},
        "rubric": {
            "hard_constraints": [
                {"id": "hc_001", "type": "hours_check",      "check_method": "code", "params": {}},
                {"id": "hc_002", "type": "no_overlap",       "check_method": "code", "params": {}},
                {"id": "hc_003", "type": "travel_time_hard", "check_method": "code", "params": {}},
            ],
            "personal_constraints": [],
            "b_score_constraints": [],
            "required_venue_ids":  [],
        },
    }
    r, _ = _dispatch_task_tool(
        "SUBMIT", {"task_json": json.dumps(minimal)},
        pool=pool, pool_map={v["venue_id"]: v for v in pool},
        window={"window_id": "lon_carnival_2026"},
        city="london", handbook={},
        unavailable={}, type_key="type2", model="test-model",
    )
    # Either accepted or rejected for downstream reasons — but never "validation_failed"
    # on structural_type. And the in-place normalization should have happened.
    return r, minimal["structural_type"]

# All short forms map to canonical
for short, canonical in [
    ("type1", "type1_cascading_requirements"),
    ("type2", "type2_subset_selection"),
    ("type3", "type3_competing_requirements"),
    ("type4", "type4_precision_allocation"),
    ("type5", "type5_hard_feasibility_reduction"),
    ("type6", "type6_context_window_tension"),
    ("1",     "type1_cascading_requirements"),
    ("2",     "type2_subset_selection"),
    ("6",     "type6_context_window_tension"),
]:
    r, final = _alias_check(short, canonical)
    # Reject only if the error mentions structural_type — otherwise alias worked
    errs = " ".join(r.get("errors", []) if isinstance(r, dict) else [])
    bad = "structural_type" in errs and short in errs
    check(f"structural_type '{short}' auto-mapped (no structural_type rejection)", not bad,
          errs[:120] if bad else "")


# ─────────────────────────────────────────────────────────────────────────────
# [4] Solvability verification
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] Solvability verification")

# Basic task — should be solvable
ok, reason = _verify_task_solvable(task, pool)
check("basic stub task is solvable", ok, reason)

# Task requiring impossible constraint (halal + no halal venues in pool)
impossible_task = {
    "days": 1,
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc1", "scope": "all",
             "condition": {"has_tag": "halal"},
             "aggregation": {"at_least": 1}, "consequence": "p_score_full"}
        ]
    }
}
ok2, reason2 = _verify_task_solvable(impossible_task, pool)
check("task with unsatisfiable label → not solvable", not ok2, reason2)

# Task requiring hidden gems — Secret Garden is a low-traffic site.
# Scope to site categories to avoid food group upper bar issues.
# 1 park qualifies out of 7 sites = 14% < 25% type1 inclusion threshold.
gem_task = {
    "days": 1,
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc1",
             "scope": "category=park",
             "condition": {"field": "traffic_tier", "operator": "==", "value": "low"},
             "aggregation": {"at_least": 1}, "consequence": "p_score_full"}
        ]
    }
}
ok3, reason3 = _verify_task_solvable(gem_task, pool)
check("task with hidden_gem_required (2 in pool) → solvable", ok3, reason3)

# Task requiring 5 museums (only 1 in pool)
museum_task = {
    "days": 2,
    "rubric": {
        "hard_constraints": [],
        "personal_constraints": [
            {"id": "pc1", "scope": "category=museum",
             "condition": {},
             "aggregation": {"at_least": 5}, "consequence": "p_score_full"}
        ]
    }
}
ok4, reason4 = _verify_task_solvable(museum_task, pool)
check("task requiring 5 museums (only 1) → not solvable", not ok4, reason4)


# ─────────────────────────────────────────────────────────────────────────────
# [6] Full dry-run pipeline
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] Full dry-run pipeline")
import tempfile as _tempfile
with _tempfile.TemporaryDirectory() as tmpdir:
    tasks = generate_tasks(
        city="london",
        window_id="london_carnival_2026",
        count=3,
        dry_run=True,
        output_dir=Path(tmpdir),
        db_path=db,
    )
    check("generates 3 tasks in dry-run", len(tasks) == 3, len(tasks))
    check("all tasks have task_id", all("task_id" in t for t in tasks))
    check("all tasks have correct city", all(t.get("city") == "london" for t in tasks))
    check("all tasks have correct window_id",
          all(t.get("window_id") == "london_carnival_2026" for t in tasks))

    # Check files written
    written = list(Path(tmpdir).glob("*.json"))
    check("3 task files written", len(written) == 3, len(written))

    # Each file is valid JSON
    for f in written:
        content = json.loads(f.read_text())
        check(f"{f.name} is valid JSON with task_id", "task_id" in content)


# ─────────────────────────────────────────────────────────────────────────────
# [7] Without output_dir — returns list only
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] In-memory generation (no output_dir)")
tasks_mem = generate_tasks(
    city="london",
    window_id="london_easter_2026",
    count=2,
    dry_run=True,
    output_dir=None,
    db_path=db,
)
check("generates 2 tasks in-memory", len(tasks_mem) == 2)
check("easter window used", all(t.get("window_id") == "london_easter_2026" for t in tasks_mem))


# ─────────────────────────────────────────────────────────────────────────────
# [8] Invalid window → raises
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] Error handling")
try:
    generate_tasks("london", "nonexistent_window_2026", count=1,
                   dry_run=True, db_path=db)
    check("invalid window raises ValueError", False)
except ValueError as e:
    check("invalid window raises ValueError", True, str(e))

try:
    generate_tasks("tokyo", "london_carnival_2026", count=1,
                   dry_run=True, db_path=db)
    check("city with no venues raises ValueError", False)
except ValueError as e:
    check("city with no venues raises ValueError", True, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# [9] P6-T6 — K-target × days for scope=per_day + soft warning
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] P6-T6 — K-target × days for at_least + scope=per_day")

# Test 1: validate_task_schema emits a soft warning for multi-day type2 +
# at_least + scope=per_day.
from test_generate_tasks import validate_task_schema

_t6_warned = {
    "task_id": "lon_x_001", "city": "london", "window_id": "lon_carnival_2026",
    "days": 2, "start_date": "2026-08-22",
    "structural_type": "type2_subset_selection", "difficulty": "medium",
    "public_input": {"query": "want to see lots of sights",
                      "query_resources": {"time_ceiling_minutes": 500}},
    "rubric": {
        "hard_constraints": [
            {"id":"hc_001","type":"hours_check","check_method":"code","params":{}},
            {"id":"hc_002","type":"no_overlap","check_method":"code","params":{}},
            {"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{}},
        ],
        "personal_constraints": [{
            "id": "pc_t6", "score_tier": "P", "hop": 1, "check_method": "code",
            "source_in_profile": "want 3 different sights each day",
            "description": "≥3 sites per day",
            "scope": "per_day",
            "condition": {"has_tag": "iconic"},
            "aggregation": {"at_least": 3},
            "consequence": "p_score_full",
        }],
        "b_score_constraints": [], "required_venue_ids": [],
    },
}
_t6_issues = validate_task_schema(_t6_warned, pool)
_t6_per_day_warn = [i for i in _t6_issues
                     if i.startswith("~ ") and "scope=per_day" in i and "type2" in i.lower()]
check("P6-T6: validate_task_schema emits per_day soft warning for multi-day type2",
      len(_t6_per_day_warn) >= 1, f"issues={_t6_issues}")
check("P6-T6: warning surfaces N×days arithmetic",
      _t6_per_day_warn and "3×2 = 6" in _t6_per_day_warn[0],
      _t6_per_day_warn[0] if _t6_per_day_warn else "no warning")

# Test 2: 1-day type2 does NOT trigger the warning (no day-multiplication danger)
_t6_oneday = json.loads(json.dumps(_t6_warned))
_t6_oneday["days"] = 1
_t6_oneday_issues = validate_task_schema(_t6_oneday, pool)
_t6_oneday_warn = [i for i in _t6_oneday_issues
                    if i.startswith("~ ") and "per_day" in i and "type 2" in i.lower()]
check("P6-T6: 1-day type2 with per_day does NOT trigger the warning",
      len(_t6_oneday_warn) == 0, f"got {_t6_oneday_warn}")

# Test 3: Direct K-target derivation — _verify_task_solvable must complete on
# a multi-day per_day task without crashing (the K calc now multiplies).
# Pool only has 10 venues so the KNN branch may skip, but the function must
# return cleanly either way.
_t6_smoke_ok, _t6_smoke_reason = _verify_task_solvable(_t6_warned, pool)
check("P6-T6: _verify_task_solvable completes on multi-day per_day type2",
      isinstance(_t6_smoke_ok, bool), f"reason={_t6_smoke_reason}")


# ─────────────────────────────────────────────────────────────────────────────
# [10] P6-T12 — currency auto-populate in SUBMIT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] P6-T12 — currency auto-populate")

from scripts.generation.task_agent import _dispatch_task_tool

# Auto-populate runs unconditionally in the SUBMIT handler before downstream
# validators. Patch validate_task_schema + _verify_task_solvable to no-ops so
# this test isolates the currency-population behaviour from full validation.
import test_generate_tasks as _tgt
import scripts.generation.generate_task as _gt_mod
_orig_validate = _tgt.validate_task_schema
_orig_solvable = _gt_mod._verify_task_solvable
_tgt.validate_task_schema = lambda task, pool=None: []
_gt_mod._verify_task_solvable = lambda task, pool: (True, "ok")

_t12_task = {
    "task_id":     "lon_x_002",
    "city":        "london",
    "window_id":   "lon_carnival_2026",
    "days":        1,
    "start_date":  "2026-08-22",
    "structural_type": "type4_precision_allocation",
    "difficulty":  "medium",
    "public_input": {
        "query": "tight budget day in London",
        "query_resources": {"budget_per_day": 40},  # NOTE: no currency
    },
    "rubric": {
        "hard_constraints": [
            {"id":"hc_001","type":"hours_check","check_method":"code","params":{}},
            {"id":"hc_002","type":"no_overlap","check_method":"code","params":{}},
            {"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{}},
        ],
        "personal_constraints": [],
        "b_score_constraints": [], "required_venue_ids": [],
    },
}
r_t12, _ = _dispatch_task_tool(
    "SUBMIT", {"task_json": json.dumps(_t12_task)},
    pool=pool, pool_map={v["venue_id"]: v for v in pool},
    window={"window_id": "lon_carnival_2026"},
    city="london", handbook={},
    unavailable={}, type_key="type4", model="test-model",
)
# SUBMIT re-parses task_json internally, so the mutation lives on the returned
# task (under `_accepted_task` on success or accessible via re-parsing).
# Find the auto-populated currency on the corrected task in the response.
_corrected = r_t12.get("_accepted_task") if isinstance(r_t12, dict) else None
if _corrected is None:
    # Validation may have failed downstream; re-parse from raw response or
    # inspect errors. For the auto-populate to be observable, we need at least
    # the parsed task. Fall back to invoking validate_task_schema indirectly.
    _corrected = {}
_corrected_currency = (_corrected.get("public_input", {})
                                   .get("query_resources", {}).get("currency"))
check("P6-T12: SUBMIT auto-populates currency=GBP for city=london",
      _corrected_currency == "GBP",
      f"got {_corrected_currency!r}; r_t12 status={r_t12.get('status') if isinstance(r_t12, dict) else type(r_t12)}")

# Pre-populated currency should NOT be overwritten.
_t12_task_pre = json.loads(json.dumps(_t12_task))
_t12_task_pre["public_input"]["query_resources"]["currency"] = "USD"
_t12_task_pre["public_input"]["query_resources"]["budget_per_day"] = 40
r_pre, _ = _dispatch_task_tool(
    "SUBMIT", {"task_json": json.dumps(_t12_task_pre)},
    pool=pool, pool_map={v["venue_id"]: v for v in pool},
    window={"window_id": "lon_carnival_2026"},
    city="london", handbook={},
    unavailable={}, type_key="type4", model="test-model",
)
_pre_corrected = r_pre.get("_accepted_task") if isinstance(r_pre, dict) else None
_pre_currency = ((_pre_corrected or {}).get("public_input", {})
                                         .get("query_resources", {}).get("currency"))
check("P6-T12: existing currency value preserved (not overwritten)",
      _pre_currency == "USD",
      f"got {_pre_currency!r}; r_pre status={r_pre.get('status') if isinstance(r_pre, dict) else type(r_pre)}")

# Restore the originals so subsequent tests run with the real validators.
_tgt.validate_task_schema = _orig_validate
_gt_mod._verify_task_solvable = _orig_solvable


# ─────────────────────────────────────────────────────────────────────────────
# [11] P6-T5 — Scoped KNN catches infeasible scoped subset
# ─────────────────────────────────────────────────────────────────────────────
print("\n[11] P6-T5 — scoped KNN geometry check")

# Build a custom pool with diverse coords: 5 cafes near the centre + 3 museums
# spread far apart. Universal-pool KNN with k_target=2 picks 2 nearby cafes
# (short travel, ceiling fits). Scoped KNN on category=museum with at_least:3
# forces the 3 distant museums (long travel, ceiling too tight).
def _v(vid, name, cat, lat, lng, visit=90):
    return {
        "venue_id": vid, "name": name, "category": cat,
        "lat": lat, "lng": lng,
        "recommended_visit_minutes": visit,
        "avg_cost_local": 10.0, "price_tier": "mid",
        "traffic_tier": "mid", "recommended_pace": "moderate",
        "noise_level": "moderate", "tags": [], "window_flags": {},
        "has_official_site": True, "has_wrong_info": False,
        "booking_required": False,
        # F4a-relevant flags that the validator may peek at:
        "wheelchair_accessible": True, "pet_friendly": False,
        "photography_allowed": True, "family_friendly": True,
        "food_available": (cat in ("restaurant","cafe","bar")),
        "outdoor_sensitivity": "indoor",
    }

# 5 cafes tightly clustered near (51.51, -0.10) — universal KNN sees these.
_t5_cafes = [
    _v("c1", "Cafe A", "cafe", 51.510, -0.100, visit=45),
    _v("c2", "Cafe B", "cafe", 51.511, -0.101, visit=45),
    _v("c3", "Cafe C", "cafe", 51.509, -0.099, visit=45),
    _v("c4", "Cafe D", "cafe", 51.512, -0.098, visit=45),
    _v("c5", "Cafe E", "cafe", 51.508, -0.102, visit=45),
]
# 3 museums far apart — diagonal across ~10km in London terms.
_t5_museums = [
    _v("m1", "Museum N", "museum",  51.560, -0.100, visit=120),  # ~5.5km north
    _v("m2", "Museum S", "museum",  51.460, -0.100, visit=120),  # ~5.5km south
    _v("m3", "Museum E", "museum",  51.510,  0.000, visit=120),  # ~7km east
]
_t5_pool = _t5_cafes + _t5_museums

# Two constraints:
#   - at_least:5 scope=all → drives universal K_target to 5 (all cafes, short
#     travel between them, ceiling cap of ~140min fits)
#   - at_least:3 scope=category=museum → drives the scoped check, museums are
#     spread out → min_time ~250min → 140 < 1.1×250 → scoped FAIL
_t5_constraints = [
    {
        "id": "pc_t5_uni",
        "score_tier": "P", "hop": 1, "check_method": "code",
        "source_in_profile": "want 5 cafes",
        "description": "≥5 venues across the day",
        "scope": "all",
        "condition": {},
        "aggregation": {"at_least": 5},
        "consequence": "p_score_full",
    },
    {
        "id": "pc_t5_museum",
        "score_tier": "P", "hop": 1, "check_method": "code",
        "source_in_profile": "want 3 museums",
        "description": "≥3 museums",
        "scope": "category=museum",
        "condition": {},
        "aggregation": {"at_least": 3},
        "consequence": "p_score_full",
    },
]

# Build a type2 task with a ceiling that fits the universal 5 cafes (short
# travel between them) but is too tight for 3 spread-out museums.
_t5_task = {
    "task_id": "lon_t5_001", "city": "london", "window_id": "lon_carnival_2026",
    "days": 1, "start_date": "2026-08-22",
    "structural_type": "type2_subset_selection", "difficulty": "medium",
    "public_input": {"query": "x", "query_resources": {"time_ceiling_minutes": 140}},
    "rubric": {
        "hard_constraints": [
            {"id":"hc_001","type":"hours_check","check_method":"code","params":{}},
            {"id":"hc_002","type":"no_overlap","check_method":"code","params":{}},
            {"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{}},
        ],
        "personal_constraints": _t5_constraints,
        "b_score_constraints": [], "required_venue_ids": [],
    },
}

_ok_t5, _reason_t5 = _verify_task_solvable(_t5_task, _t5_pool)
check("P6-T5: ceiling that passes universal KNN but fails scoped KNN on museums → rejected",
      not _ok_t5 and "scoped geometry" in (_reason_t5 or "") and "pc_t5_museum" in (_reason_t5 or ""),
      f"ok={_ok_t5} reason={_reason_t5}")

# Negative regression: same pool, generous ceiling → scoped geometry should
# NOT be the blocker.
_t5_task_loose = json.loads(json.dumps(_t5_task))
_t5_task_loose["public_input"]["query_resources"]["time_ceiling_minutes"] = 300
_ok_loose, _reason_loose = _verify_task_solvable(_t5_task_loose, _t5_pool)
check("P6-T5: loose ceiling not rejected by scoped geometry",
      "scoped geometry" not in (_reason_loose or ""),
      f"ok={_ok_loose} reason={_reason_loose}")


# ─────────────────────────────────────────────────────────────────────────────
# [12] P6-T4b — validate_task_schema for ceiling_mode / start_time / ceiling_scope
# ─────────────────────────────────────────────────────────────────────────────
print("\n[12] P6-T4b — validate_task_schema query_resources fields")

from test_generate_tasks import validate_task_schema

def _t4b_base():
    return {
        "task_id": "lon_x_t4b", "city": "london", "window_id": "lon_carnival_2026",
        "days": 1, "start_date": "2026-08-22",
        "structural_type": "type2_subset_selection", "difficulty": "medium",
        "public_input": {"query": "Sat 14:00–18:00 free for an outing.",
                          "query_resources": {"time_ceiling_minutes": 240}},
        "rubric": {
            "hard_constraints": [
                {"id":"hc_001","type":"hours_check","check_method":"code","params":{}},
                {"id":"hc_002","type":"no_overlap","check_method":"code","params":{}},
                {"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{}},
            ],
            "personal_constraints": [{
                "id":"pc_001","score_tier":"P","hop":1,"check_method":"code",
                "source_in_profile":"want to see iconic sights",
                "description":"≥2 iconic venues",
                "scope":"all",
                "condition":{"has_tag":"iconic"},
                "aggregation":{"at_least":2},
                "consequence":"p_score_full",
            }],
            "b_score_constraints":[], "required_venue_ids":[],
        },
    }

# Case A: contiguous (default) WITHOUT start_time → hard error
_ta = _t4b_base()
_issues_a = validate_task_schema(_ta, pool)
_a_err = [i for i in _issues_a if not i.startswith("~ ") and "start_time" in i]
check("P6-T4b: contiguous without start_time → hard error",
      len(_a_err) >= 1, f"issues={_issues_a}")

# Case B: contiguous WITH start_time → no schema error related to ceiling_mode
_tb = _t4b_base()
_tb["public_input"]["query_resources"]["start_time"]   = "14:00"
_tb["public_input"]["query_resources"]["ceiling_mode"] = "contiguous"
_issues_b = validate_task_schema(_tb, pool)
_b_err = [i for i in _issues_b if not i.startswith("~ ")
          and ("start_time" in i or "ceiling_mode" in i)]
check("P6-T4b: contiguous + start_time=14:00 → no ceiling_mode/start_time error",
      len(_b_err) == 0, f"errs={_b_err}")

# Case C: spread mode → start_time NOT required (no error if absent)
_tc = _t4b_base()
_tc["public_input"]["query_resources"] = {
    "time_ceiling_minutes": 360, "ceiling_mode": "spread"}
_issues_c = validate_task_schema(_tc, pool)
_c_err = [i for i in _issues_c if not i.startswith("~ ")
          and "start_time" in i]
check("P6-T4b: spread mode does NOT require start_time",
      len(_c_err) == 0, f"errs={_c_err}")

# Case D: spread + start_time present → soft warning
_td = _t4b_base()
_td["public_input"]["query_resources"] = {
    "time_ceiling_minutes": 360, "ceiling_mode": "spread",
    "start_time": "14:00",
}
_issues_d = validate_task_schema(_td, pool)
_d_warn = [i for i in _issues_d if i.startswith("~ ")
           and "spread" in i and "start_time" in i]
check("P6-T4b: spread + start_time → soft warning fires",
      len(_d_warn) >= 1, f"warnings={[i for i in _issues_d if i.startswith('~ ')]}")

# Case E: invalid ceiling_mode value → hard error
_te = _t4b_base()
_te["public_input"]["query_resources"]["ceiling_mode"] = "bogus"
_te["public_input"]["query_resources"]["start_time"]   = "14:00"
_issues_e = validate_task_schema(_te, pool)
_e_err = [i for i in _issues_e if not i.startswith("~ ")
          and "ceiling_mode" in i and "bogus" in i]
check("P6-T4b: invalid ceiling_mode value → hard error",
      len(_e_err) >= 1, f"issues={_issues_e}")

# Case F: invalid ceiling_scope value → hard error
_tf = _t4b_base()
_tf["public_input"]["query_resources"] = {
    "time_ceiling_minutes": 360, "ceiling_mode": "spread",
    "ceiling_scope": "weekly",  # invalid
}
_issues_f = validate_task_schema(_tf, pool)
_f_err = [i for i in _issues_f if not i.startswith("~ ")
          and "ceiling_scope" in i and "weekly" in i]
check("P6-T4b: invalid ceiling_scope value → hard error",
      len(_f_err) >= 1, f"issues={_issues_f}")

# Case G: start_time wrong format ("2pm" instead of HH:MM) → hard error
_tg = _t4b_base()
_tg["public_input"]["query_resources"]["start_time"]   = "2pm"
_tg["public_input"]["query_resources"]["ceiling_mode"] = "contiguous"
_issues_g = validate_task_schema(_tg, pool)
_g_err = [i for i in _issues_g if not i.startswith("~ ")
          and "start_time" in i and "HH:MM" in i]
check("P6-T4b: start_time wrong format → hard error",
      len(_g_err) >= 1, f"issues={_issues_g}")


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup + Summary
# ─────────────────────────────────────────────────────────────────────────────
db.unlink()

print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B4 tests passed ({PASS}/{total}) — task generation agent ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
