#!/usr/bin/env python3
"""
test_v55_validation.py — Unit tests for Phase 5.5 validation rules.

Tests V1–V7 changes to validate_task_schema and V2 tension axis tracking.
Run: python test_v55_validation.py
"""
import sys, copy
sys.path.insert(0, ".")
from test_generate_tasks import validate_task_schema

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_pass_count = 0
_fail_count = 0

def check(label, condition, detail=""):
    global _pass_count, _fail_count
    if condition:
        _pass_count += 1
        print(f"  ✅ {label}")
    else:
        _fail_count += 1
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))


def _find_issue(issues, fragment):
    """Return True if any issue string contains the fragment."""
    return any(fragment.lower() in i.lower() for i in issues)


def _pc(pid, hop=1, scope="all", condition=None, aggregation=None, desc="test", source="test phrase"):
    """Build a minimal valid personal constraint."""
    return {
        "id": pid, "score_tier": "P", "hop": hop, "check_method": "code",
        "source_in_profile": source,
        "description": desc,
        "scope": scope,
        "condition": condition or {},
        "aggregation": aggregation or {"at_least": 1},
        "consequence": "p_score_full",
    }


def _task(pcs, structural_type="type1_cascading_requirements", difficulty="medium",
          days=2, query="My partner and I are visiting London for a quiet romantic weekend"):
    """Build a minimal valid task with given personal constraints."""
    return {
        "task_id": "test_001", "city": "london", "days": days,
        "start_date": "2026-04-04",
        "structural_type": structural_type,
        "difficulty": difficulty,
        "public_input": {"query": query},
        "rubric": {
            "hard_constraints": [
                {"id": "hc_001", "type": "hours_check", "check_method": "code", "params": {}},
                {"id": "hc_002", "type": "no_overlap", "check_method": "code", "params": {}},
                {"id": "hc_003", "type": "travel_time_hard", "check_method": "code", "params": {}},
            ],
            "personal_constraints": pcs,
            "b_score_constraints": [],
            "required_venue_ids": [],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# V1 — Aggregation diversity
# ─────────────────────────────────────────────────────────────────────────────
print("\n── V1: Aggregation diversity ──────────────────────────────────")

# All at_least → should fail
v1_bad = _task([
    _pc("pc_001", hop=1, aggregation={"at_least": 2}, source="museums",
        condition={"has_tag": "art"}, scope="category=museum"),
    _pc("pc_002", hop=2, aggregation={"at_least": 1}, source="special dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"},
        scope=["activity_type=meal", "time_window=18:00-23:59"]),
    _pc("pc_003", hop=1, aggregation={"at_least": 3}, source="hidden gems",
        condition={"field": "traffic_tier", "operator": "==", "value": "low"}),
])
issues = validate_task_schema(v1_bad, pool=None)
check("all at_least with ≥3 constraints → rejected",
      _find_issue(issues, "at_least"), f"issues={issues[:2]}")

# Mixed aggregation → should pass (at_least + none + ratio)
v1_good = _task([
    _pc("pc_001", hop=1, aggregation={"at_least": 2}, source="museums",
        condition={"has_tag": "art"}, scope="category=museum"),
    _pc("pc_002", hop=2, aggregation="none", source="tourist traps",
        condition={"has_tag": "tourist-trap"}),
    _pc("pc_003", hop=2, aggregation={"ratio": 0.5}, source="outdoor lover",
        condition={"has_tag": "outdoor"}),
])
issues = validate_task_schema(v1_good, pool=None)
check("mixed aggregation (at_least + none + ratio) → no aggregation issue",
      not _find_issue(issues, "at_least.*aggregation") and not _find_issue(issues, "too uniform"),
      f"issues={[i for i in issues if 'aggregat' in i.lower()]}")

# Only 2 constraints → V1 doesn't apply (< 3)
v1_two = _task([
    _pc("pc_001", hop=1, aggregation={"at_least": 2}, source="museums",
        condition={}, scope="category=museum"),
    _pc("pc_002", hop=2, aggregation={"at_least": 1}, source="special dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"},
        scope="activity_type=meal"),
])
issues = validate_task_schema(v1_two, pool=None)
check("only 2 constraints → V1 aggregation check not triggered",
      not _find_issue(issues, "too uniform"),
      f"issues={[i for i in issues if 'aggregat' in i.lower() or 'uniform' in i.lower()]}")


# ─────────────────────────────────────────────────────────────────────────────
# V5 — Scope diversity
# ─────────────────────────────────────────────────────────────────────────────
print("\n── V5: Scope diversity ────────────────────────────────────────")

# All scopes are "all" or "activity_type=meal" → should fail
v5_bad = _task([
    _pc("pc_001", hop=1, scope="all", source="quiet places",
        condition={"field": "noise_level", "operator": "<=", "value": "moderate"}),
    _pc("pc_002", hop=2, scope="activity_type=meal", source="special dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="all", source="hidden gems", aggregation="none",
        condition={"has_tag": "tourist-trap"}),
])
issues = validate_task_schema(v5_bad, pool=None)
check("all trivial scopes (all + activity_type=meal) → rejected",
      _find_issue(issues, "too uniform") and _find_issue(issues, "scope"))

# One diverse scope → should pass
v5_good = _task([
    _pc("pc_001", hop=1, scope="all", source="quiet places",
        condition={"field": "noise_level", "operator": "<=", "value": "moderate"}),
    _pc("pc_002", hop=2, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}),
    _pc("pc_003", hop=2, scope="per_day", source="don't rush",
        condition={"field": "category", "operator": "==", "value": "museum"},
        aggregation={"at_most": 1}),
])
issues = validate_task_schema(v5_good, pool=None)
check("mixed scopes (all + category=museum + per_day) → no scope issue",
      not _find_issue(issues, "scope.*uniform"),
      f"issues={[i for i in issues if 'scope' in i.lower() and 'uniform' in i.lower()]}")


# ─────────────────────────────────────────────────────────────────────────────
# V6 — Type 6 time_window grounding
# ─────────────────────────────────────────────────────────────────────────────
print("\n── V6: Type 6 time_window grounding ───────────────────────────")

# Type 6 with only venue_id scope → should fail
v6_bad = _task([
    _pc("pc_001", hop=1, scope="venue_id=lon_abc", source="must visit Hawksmoor"),
    _pc("pc_002", hop=2, scope="activity_type=meal", source="special dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="category=museum", source="art lovers",
        condition={"has_tag": "art"}, aggregation={"at_least": 2}),
], structural_type="type6_context_window_tension")
issues = validate_task_schema(v6_bad, pool=None)
check("type 6 with only venue_id scope → rejected (no time_window OR venue_id grounding... wait, venue_id IS valid)",
      not _find_issue(issues, "not grounded"),
      "venue_id= scope IS valid grounding under reverted V6 OR logic")

# Type 6 with time_window scope → should pass
v6_good = _task([
    _pc("pc_001", hop=1, scope="venue_id=lon_abc", source="must visit Hawksmoor"),
    _pc("pc_002", hop=2, scope=["activity_type=meal", "time_window=18:00-23:59"],
        source="special evening dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="category=museum", source="art lovers",
        condition={"has_tag": "art"}, aggregation={"at_least": 2}),
], structural_type="type6_context_window_tension")
issues = validate_task_schema(v6_good, pool=None)
check("type 6 with time_window scope → no grounding issue",
      not _find_issue(issues, "time_window") or not _find_issue(issues, "not grounded"),
      f"issues={[i for i in issues if 'time_window' in i.lower() or 'grounded' in i.lower()]}")

# Non-type-6 without time_window → should NOT trigger V6
v6_other = _task([
    _pc("pc_001", hop=1, scope="all", source="quiet places",
        condition={"field": "noise_level", "operator": "<=", "value": "moderate"}),
    _pc("pc_002", hop=2, scope="activity_type=meal", source="special dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}, aggregation={"at_least": 2}),
], structural_type="type1_cascading_requirements")
issues = validate_task_schema(v6_other, pool=None)
check("type 1 without time_window → V6 not triggered",
      not _find_issue(issues, "type 6"))


# ─────────────────────────────────────────────────────────────────────────────
# V7 — Type 4 sum constraint requirement
# ─────────────────────────────────────────────────────────────────────────────
print("\n── V7: Type 4 sum constraint requirement ──────────────────────")

# Type 4 without sum → should fail
v7_bad = _task([
    _pc("pc_001", hop=1, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}, aggregation={"at_least": 1}),
    _pc("pc_002", hop=2, scope=["activity_type=meal", "time_window=18:00-23:59"],
        source="special evening",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="all", source="hidden gems", aggregation="none",
        condition={"has_tag": "tourist-trap"}),
], structural_type="type4_precision_allocation",
   query="My partner and I have a budget of £80 per day for our London trip")
issues = validate_task_schema(v7_bad, pool=None)
check("type 4 without sum aggregation → rejected",
      _find_issue(issues, "sum aggregation"))

# Type 4 with sum → should pass
v7_good = _task([
    _pc("pc_001", hop=1, scope="per_day", source="budget £80",
        condition={},
        aggregation={"sum": "estimated_cost_local", "operator": "<=", "value": 80}),
    _pc("pc_002", hop=2, scope=["activity_type=meal", "time_window=18:00-23:59"],
        source="special evening",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}, aggregation={"at_least": 1}),
], structural_type="type4_precision_allocation",
   query="My partner and I have a budget of £80 per day for our London trip")
issues = validate_task_schema(v7_good, pool=None)
check("type 4 with sum aggregation → no sum issue",
      not _find_issue(issues, "sum aggregation"),
      f"issues={[i for i in issues if 'sum' in i.lower()]}")

# Non-type-4 without sum → should NOT trigger V7
v7_other = _task([
    _pc("pc_001", hop=1, scope="all", source="quiet places",
        condition={"field": "noise_level", "operator": "<=", "value": "moderate"}),
    _pc("pc_002", hop=2, scope="activity_type=meal", source="special dinner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=1, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}, aggregation={"at_least": 2}),
], structural_type="type1_cascading_requirements")
issues = validate_task_schema(v7_other, pool=None)
check("type 1 without sum → V7 not triggered",
      not _find_issue(issues, "sum aggregation"))


# ─────────────────────────────────────────────────────────────────────────────
# V2 — Type 3 tension axis extraction (unit logic, not full dispatch)
# ─────────────────────────────────────────────────────────────────────────────
print("\n── V2: Type 3 tension axis extraction ─────────────────────────")

def _extract_tension_axes(task):
    """Extract tension axes from a Type 3 task (mirrors dispatch logic)."""
    axes = set()
    for pc in task.get("rubric", {}).get("personal_constraints", []):
        cond = pc.get("condition", {})
        if isinstance(cond, dict):
            axis = cond.get("field") or cond.get("has_tag") or cond.get("not_tag")
            if axis:
                axes.add(axis)
    return axes

# Classic iconic vs hidden → extracts traffic_tier
v2_iconic = _task([
    _pc("pc_001", hop=1, condition={"field": "traffic_tier", "operator": "==", "value": "high"},
        aggregation={"at_least": 4}, source="famous landmarks"),
    _pc("pc_002", hop=2, condition={"field": "traffic_tier", "operator": "==", "value": "low"},
        aggregation={"at_least": 4}, source="hidden gems"),
    _pc("pc_003", hop=1, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}, aggregation={"at_least": 1}),
], structural_type="type3_competing_requirements")
axes = _extract_tension_axes(v2_iconic)
check("iconic vs hidden → extracts traffic_tier",
      "traffic_tier" in axes, f"axes={axes}")

# Tag-based tension → extracts outdoor + indoor
v2_tags = _task([
    _pc("pc_001", hop=1, condition={"has_tag": "outdoor"},
        aggregation={"at_least": 4}, source="love outdoors"),
    _pc("pc_002", hop=2, condition={"has_tag": "indoor-activity"},
        aggregation={"at_least": 4}, source="partner prefers indoors"),
    _pc("pc_003", hop=1, scope="activity_type=meal", source="local food",
        condition={"has_tag": "local-cuisine"}, aggregation={"at_least": 2}),
], structural_type="type3_competing_requirements")
axes = _extract_tension_axes(v2_tags)
check("outdoor vs indoor → extracts both tags",
      "outdoor" in axes and "indoor-activity" in axes, f"axes={axes}")

# Cross-task reuse detection
used = {"traffic_tier"}  # simulate previous type3 used traffic_tier
new_axes = _extract_tension_axes(v2_iconic)
reused = new_axes & used
check("traffic_tier already used → detected as reused",
      "traffic_tier" in reused, f"reused={reused}")

new_axes2 = _extract_tension_axes(v2_tags)
reused2 = new_axes2 & used
check("outdoor/indoor with traffic_tier used → no overlap",
      len(reused2) == 0, f"reused={reused2}")


# ─────────────────────────────────────────────────────────────────────────────
# COMBINED: a fully compliant task should pass all V1–V7 checks
# ─────────────────────────────────────────────────────────────────────────────
print("\n── Combined: fully compliant task ─────────────────────────────")

golden = _task([
    _pc("pc_001", hop=1, scope="category=museum", source="art museums",
        condition={"has_tag": "art"}, aggregation={"at_least": 2}),
    _pc("pc_002", hop=2, scope=["activity_type=meal", "time_window=18:00-23:59"],
        source="celebrating something special with my partner",
        condition={"field": "price_tier", "operator": ">=", "value": "upscale"}),
    _pc("pc_003", hop=2, scope="all", source="avoid tourist traps",
        condition={"has_tag": "tourist-trap"}, aggregation="none"),
    _pc("pc_004", hop=1, scope="per_day", source="we love being outside",
        condition={"has_tag": "outdoor"}, aggregation={"ratio": 0.4}),
], days=3, difficulty="hard",
   query="My partner and I are celebrating something special with a 3-day London trip. "
         "We love art museums and being outside. I want to avoid tourist traps. "
         "The evenings should feel truly memorable.")
issues = validate_task_schema(golden, pool=None)

# Filter out issues unrelated to V1-V7 (e.g. pool-dependent checks)
v55_keywords = ["at_least.*aggregation", "too uniform", "too few personal",
                "hard tasks require", "scope.*uniform", "time_window", "sum aggregation"]
v55_issues = [i for i in issues
              if any(kw.lower() in i.lower()
                     for kw in ["at_least", "uniform", "too few", "hard tasks",
                                "scope", "time_window", "sum aggregation", "type 6",
                                "type 4"])]
check("golden task passes all V1-V7 checks",
      len(v55_issues) == 0, f"V55-related issues: {v55_issues}")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
if _fail_count == 0:
    print(f"✅ All V5.5 validation tests passed ({_pass_count}/{_pass_count})")
else:
    print(f"❌ {_fail_count}/{_pass_count + _fail_count} tests failed")
sys.exit(1 if _fail_count else 0)
