"""
scripts/generation/test_b3.py

Tests for B3 — B-score engine:
  route efficiency (MST), doc-appearance, Python script runner, aggregation
"""

import sys, json
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
    _compute_mst_minutes,
    _check_route_efficiency,
    _check_doc_appeared,
    _check_blog_mentioned,
    _run_b_script,
    evaluate_b_score,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────

# Matrix: distances in minutes between 4 London venues (approximate)
# Borough Market (bm), Tate Modern (tm), London Bridge (lb), Southwark (sw)
MATRIX = {
    "bm_to_tm": 12,  "tm_to_bm": 12,
    "bm_to_lb": 5,   "lb_to_bm": 5,
    "bm_to_sw": 8,   "sw_to_bm": 8,
    "tm_to_lb": 14,  "lb_to_tm": 14,
    "tm_to_sw": 10,  "sw_to_tm": 10,
    "lb_to_sw": 6,   "sw_to_lb": 6,
}

VENUES = {
    "bm": {"venue_id":"bm","name":"Borough Market","category":"attraction",
           "traffic_tier":"high","recommended_pace":"moderate"},
    "tm": {"venue_id":"tm","name":"Tate Modern","category":"museum",
           "traffic_tier":"high","recommended_pace":"intense"},
    "lb": {"venue_id":"lb","name":"London Bridge","category":"attraction",
           "traffic_tier":"high","recommended_pace":"relaxed"},
    "sw": {"venue_id":"sw","name":"Southwark Cathedral","category":"attraction",
           "traffic_tier":"mid","recommended_pace":"relaxed"},
}

def make_plan(day_venue_ids: list):
    """Make a minimal plan with one day."""
    acts = [{"venue_id":v,"venue_name":VENUES.get(v,{}).get("name",v),
             "activity_type":"visit","time_start":"10:00","time_end":"11:00",
             "estimated_cost_local":0} for v in day_venue_ids]
    return {
        "task_id":"test","city":"london",
        "days":[{"day":1,"day_of_week":"sat","activities":acts}]
    }

BASE_TASK = {"task_id":"test","city":"london","days":1,"start_date":"2026-08-22",
             "rubric":{"hard_constraints":[],"partial_constraints":[],
                       "personal_constraints":[],"b_score_constraints":[]}}


# ─────────────────────────────────────────────────────────────────────────────
# [1] MST computation
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] MST computation")

# Single venue — no travel needed
mst = _compute_mst_minutes(["bm"], MATRIX)
check("single venue MST=0", mst == 0.0, mst)

# Two venues: bm→lb = 5min
mst = _compute_mst_minutes(["bm","lb"], MATRIX)
check("bm+lb MST=5", mst == 5.0, mst)

# Four venues: MST should be bm-lb(5) + lb-sw(6) + sw-tm(10) = 21
mst = _compute_mst_minutes(["bm","tm","lb","sw"], MATRIX)
check("4 venues MST=21", mst == 21.0, mst)

# MST ≤ any actual route (by definition)
# Route bm→tm→lb→sw: 12+14+6=32 — MST is 21
check("MST ≤ sequential route", mst <= 32, mst)


# ─────────────────────────────────────────────────────────────────────────────
# [2] Route efficiency
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Route efficiency")

# Efficient route: bm→lb→sw→tm = 5+6+10=21 (= MST, perfect)
plan_efficient = make_plan(["bm","lb","sw","tm"])
r = _check_route_efficiency(plan_efficient, MATRIX, threshold=1.5)
check("efficient route → score=1.0", r["score"] == 1.0, r)
check("actual=21, MST=21", r["day_results"][0]["actual_min"] == 21.0,
      r["day_results"][0])

# Inefficient route: bm→tm→lb→sw = 12+14+6=32, MST=21, ratio=1.52
plan_bad = make_plan(["bm","tm","lb","sw"])
r = _check_route_efficiency(plan_bad, MATRIX, threshold=1.5)
check("backtracking route (1.52x) → score=0.0 at 1.5x threshold",
      r["score"] == 0.0, r["day_results"][0])

# Single venue day — always passes
plan_single = make_plan(["bm"])
r = _check_route_efficiency(plan_single, MATRIX, threshold=1.5)
check("single venue → score=1.0", r["score"] == 1.0, r)

# Empty plan
r = _check_route_efficiency({"days":[]}, MATRIX)
check("empty plan → score=None", r["score"] is None, r)


# ─────────────────────────────────────────────────────────────────────────────
# [3] Doc-appearance awareness
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Doc-appearance awareness")

tool_log_with = [
    {"tool_name":"get_official_site",
     "tool_input":{"venue_id":"bm","city":"london","date":"2026-08-23"},
     "tool_output":{}},
]
tool_log_without = [
    {"tool_name":"search_yelp","tool_input":{"query":"market","city":"london"},"tool_output":{}},
]

check("agent called official_site for bm → True",
      _check_doc_appeared("bm", tool_log_with))
check("agent did not call for bm → False",
      not _check_doc_appeared("bm", tool_log_without))
check("called with matching date → True",
      _check_doc_appeared("bm", tool_log_with, date="2026-08-23"))
check("called but different date → False",
      not _check_doc_appeared("bm", tool_log_with, date="2026-12-25"))
check("blog mention check works",
      not _check_blog_mentioned("bm","Borough Market", tool_log_without))


# ─────────────────────────────────────────────────────────────────────────────
# [4] Python script runner
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] Python script runner")

# Score 1.0 unconditionally
script_pass = "def evaluate(plan, task, venues): return 1.0"
score = _run_b_script(script_pass, {}, {}, {})
check("script returning 1.0 → 1.0", score == 1.0, score)

# Score based on number of days
script_days = """
def evaluate(plan, task, venues):
    days = plan.get('days', [])
    return 1.0 if len(days) >= 2 else 0.0
"""
score = _run_b_script(script_days, make_plan(["bm","tm"]), {}, {})
check("1-day plan → 0.0", score == 0.0, score)

two_day_plan = {"task_id":"t","city":"london","days":[
    {"day":1,"day_of_week":"sat","activities":[]},
    {"day":2,"day_of_week":"sun","activities":[]},
]}
score = _run_b_script(script_days, two_day_plan, {}, {})
check("2-day plan → 1.0", score == 1.0, score)

# Timeout safety (fast enough)
import time
t0 = time.time()
score = _run_b_script("def evaluate(plan,task,venues): return 0.5", {}, {}, {})
elapsed = time.time() - t0
check("script runs fast (< 3s)", elapsed < 3.0, f"{elapsed:.1f}s")
check("script returns 0.5", abs(score - 0.5) < 0.01, score)

# Malformed script → 0.0
score = _run_b_script("def evaluate(plan, task, venues): raise ValueError('oops')", {}, {}, {})
check("script with exception → 0.0", score == 0.0, score)

# Empty script → 0.0
score = _run_b_script("", {}, {}, {})
check("empty script → 0.0", score == 0.0, score)

# Score clamped to [0, 1]
score = _run_b_script("def evaluate(plan,task,venues): return 999.0", {}, {}, {})
check("score > 1.0 clamped to 1.0", score == 1.0, score)


# ─────────────────────────────────────────────────────────────────────────────
# [5] Full evaluate_b_score
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] Full evaluate_b_score")

# No b_score_constraints — only route efficiency
result = {
    "parsed_plan": plan_efficient,
    "tool_call_log": [],
}
b = evaluate_b_score(result, BASE_TASK, VENUES, MATRIX)
check("no constraints: score based on route efficiency", b["score"] is not None, b)
check("components has route_efficiency", "route_efficiency" in b["components"])
check("efficient route: score=1.0", b["score"] == 1.0, b["score"])

# With doc_appeared constraint
task_with_doc = {**BASE_TASK, "rubric": {
    **BASE_TASK["rubric"],
    "b_score_constraints": [
        {"id":"b_001","pattern":"doc_appeared","description":"agent checked bm official site",
         "params":{"venue_id":"bm","date":"2026-08-23"}}
    ]
}}
result_with_log = {
    "parsed_plan": plan_efficient,
    "tool_call_log": tool_log_with,
}
b = evaluate_b_score(result_with_log, task_with_doc, VENUES, MATRIX)
check("doc_appeared: agent called → constraint score=1.0",
      any(r["score"] == 1.0 for r in b["constraint_results"]), b["constraint_results"])

result_no_log = {"parsed_plan": plan_efficient, "tool_call_log": []}
b = evaluate_b_score(result_no_log, task_with_doc, VENUES, MATRIX)
check("doc_appeared: agent did not call → constraint score=0.0",
      any(r["score"] == 0.0 for r in b["constraint_results"]), b["constraint_results"])

# With python_script constraint
task_with_script = {**BASE_TASK, "rubric": {
    **BASE_TASK["rubric"],
    "b_score_constraints": [
        {"id":"b_002","pattern":"python_script",
         "description":"plan has at least 2 venues",
         "script_code":"def evaluate(plan,task,venues): return 1.0 if sum(len(d.get('activities',[])) for d in plan.get('days',[])) >= 2 else 0.0"}
    ]
}}
b = evaluate_b_score({"parsed_plan": plan_efficient, "tool_call_log": []},
                      task_with_script, VENUES, MATRIX)
check("python_script: 4 venues → score=1.0",
      any(r["score"] == 1.0 for r in b["constraint_results"]), b["constraint_results"])

# llm_semantic without api_key → skipped
task_llm = {**BASE_TASK, "rubric": {
    **BASE_TASK["rubric"],
    "b_score_constraints": [
        {"id":"b_003","pattern":"llm_semantic",
         "description":"schedule builds toward dinner as peak",
         "rubric_prompt":"Does the plan build toward a dinner as its emotional peak?",
         "scoring_guide":"1.0=yes clearly, 0.5=somewhat, 0.0=no"}
    ]
}}
b = evaluate_b_score({"parsed_plan": plan_efficient, "tool_call_log": []},
                      task_llm, VENUES, MATRIX)
check("llm_semantic without api_key → score=None (pending)",
      any(r["score"] is None for r in b["constraint_results"]), b["constraint_results"])
check("llm_semantic result has id b_003",
      any(r.get("id") == "b_003" for r in b["constraint_results"]), b)

# No plan → score=None
b = evaluate_b_score({"parsed_plan": None, "tool_call_log": []}, BASE_TASK, VENUES, MATRIX)
check("no plan → score=None", b["score"] is None, b)


# [6] Paris regression — retired with the Paris-JSON pipeline.


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B3 tests passed ({PASS}/{total}) — B-score engine ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
