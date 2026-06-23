"""
eval/test_f2c_three_tier.py — unit tests for the 3-tier value-based F2c check.

F2c grades whether the agent's PLAN reflects the truth or the served lie for a
corrupted field, rather than just whether it retrieved the truth-carrier doc:

  recovered (plan reflects GT)             ->  0.0   (no deduction)
  verified but plan reflects the lie       -> -0.025 (partial)
  not verified AND plan reflects the lie   -> -0.05  (misled)

Plus a legacy fallback test: with no served-lie values on the venue, F2c keeps
its original binary behavior.

Run:
  python eval/test_f2c_three_tier.py
  (or)  python -m pytest eval/test_f2c_three_tier.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.evaluator import evaluate_f_score, _f2c_plan_reflects_truth


# ── Synthetic fixtures ────────────────────────────────────────────────────────

VID = "vtest1"
# A wrong-info venue: served lie says avg_cost_local = 0 (free); truth = 28.
BASE_VENUE = {
    "venue_id": VID,
    "name": "Test Museum",
    "has_wrong_info": True,
    # open all week 09:00-18:00 so F2a does not fire
    "hours": {d: ["09:00-18:00"] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
    "regulations": {},
    "tags": [],
    "location": {"district": ""},
    "ticket_availability": {},
    "recommended_visit_minutes": 60,
    "wrong_info_values": {"avg_cost_local": {"lie": "0", "gt": "28.0"}},
}


def _venue(**overrides):
    v = {k: (dict(val) if isinstance(val, dict) else val) for k, val in BASE_VENUE.items()}
    v.update(overrides)
    return v


TASK = {
    "task_id": "t",
    "city": "new_york",
    "start_date": "2026-07-06",  # Monday
    "days": 1,
}


def _make_result(est_cost, flags, with_official_site):
    """Build a result whose plan schedules VID once."""
    tool_log = []
    if with_official_site:
        tool_log.append({"tool_name": "get_official_site", "tool_input": {"venue_id": VID}})
    return {
        "tool_call_log": tool_log,
        "parsed_plan": {
            "city": "new_york",
            "days": [
                {
                    "activities": [
                        {
                            "time_start": "10:00",
                            "time_end": "11:00",
                            "venue_id": VID,
                            "venue_name": "Test Museum",
                            "activity_type": "visit",
                            "estimated_cost_local": est_cost,
                            "flags": flags,
                        }
                    ]
                }
            ],
        },
    }


def _f2c_deductions(result, venues):
    f = evaluate_f_score(result, TASK, venues, matrix={}, truth_carriers={})
    return [d for d in f["deductions"] if d["section"] == "F2c"]


# ── Tier tests via full evaluate_f_score ──────────────────────────────────────

def test_tier_recovered_no_deduction_via_cost():
    """Plan cost (28) tracks GT, not the lie (0) -> 0.0 deduction."""
    venues = {VID: _venue()}
    res = _make_result(est_cost=28, flags=[], with_official_site=False)
    deds = _f2c_deductions(res, venues)
    assert deds == [], f"expected no F2c deduction, got {deds}"


def test_tier_recovered_no_deduction_via_flag():
    """Corrective flag present -> recovered even if cost looks lie-like."""
    venues = {VID: _venue()}
    res = _make_result(
        est_cost=0,
        flags=["One blog claimed free but museum actually requires $28 admission"],
        with_official_site=False,
    )
    deds = _f2c_deductions(res, venues)
    assert deds == [], f"expected no F2c deduction (flag recovery), got {deds}"


def test_tier_partial_verified_but_lie():
    """Verified (official site) but plan reflects the lie (cost 0, no flag) -> -0.025."""
    venues = {VID: _venue()}
    res = _make_result(est_cost=0, flags=[], with_official_site=True)
    deds = _f2c_deductions(res, venues)
    assert len(deds) == 1, f"expected one F2c deduction, got {deds}"
    assert abs(deds[0]["amount"] - 0.025) < 1e-9, deds


def test_tier_misled_unverified_and_lie():
    """Not verified AND plan reflects the lie -> -0.05."""
    venues = {VID: _venue()}
    res = _make_result(est_cost=0, flags=[], with_official_site=False)
    deds = _f2c_deductions(res, venues)
    assert len(deds) == 1, f"expected one F2c deduction, got {deds}"
    assert abs(deds[0]["amount"] - 0.05) < 1e-9, deds


def test_legacy_fallback_binary():
    """No wrong_info_values on venue -> original binary behavior (-0.05 if unverified)."""
    venues = {VID: _venue(wrong_info_values={})}
    res = _make_result(est_cost=0, flags=[], with_official_site=False)
    deds = _f2c_deductions(res, venues)
    assert len(deds) == 1 and abs(deds[0]["amount"] - 0.05) < 1e-9, deds

    # verified -> no deduction in legacy mode
    res2 = _make_result(est_cost=0, flags=[], with_official_site=True)
    deds2 = _f2c_deductions(res2, venues)
    assert deds2 == [], f"legacy verified should not deduct, got {deds2}"


# ── Direct helper tests ───────────────────────────────────────────────────────

def test_helper_cost_near_gt_recovered():
    v = _venue()
    assert _f2c_plan_reflects_truth(v, VID, [{"estimated_cost_local": 28, "flags": []}]) is True


def test_helper_cost_near_lie_not_recovered():
    v = _venue()
    assert _f2c_plan_reflects_truth(v, VID, [{"estimated_cost_local": 0, "flags": []}]) is False


def test_helper_no_values_not_recovered():
    v = _venue(wrong_info_values={})
    assert _f2c_plan_reflects_truth(v, VID, [{"estimated_cost_local": 28, "flags": []}]) is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
