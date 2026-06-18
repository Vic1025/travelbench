"""Tests for the corruption validator (validate_corruption.py).

Run: python3 scripts/generation/test_validate_corruption.py

Uses a /tmp COPY of the real test_70 corpus DB. NEVER mutates the real corpus.
No API calls; fully deterministic.

Two scenarios:
  1. HEALTHY  — a freshly-generated load-bearing corpus passes ALL hard
                invariants (1–5), GT is immutable & reproducible, and the
                per-task live-wedge count is > 0.
  2. BROKEN   — a deliberately corrupted flaw (repairability=0 / served==GT)
                is FLAGGED by the validator (nonzero exit / failure recorded).
"""

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.generation.inject_flaws import inject, DEFAULT_PROFILE
from scripts.generation.validate_corruption import validate_corruption

_HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(_HERE, "..", "..", "data", "cities", "New_York",
                      "runs", "test_70", "travelbench.db")
SEED = "b1-seed-001"
CREATED_AT = "2026-01-01T00:00:00Z"

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))
        FAIL += 1


def _make_corpus(tmp):
    dst = os.path.join(tmp, "dst.db")
    plan = inject(SRC_DB, dst, SEED, DEFAULT_PROFILE, created_at=CREATED_AT)
    return dst, plan


# ─────────────────────────────────────────────────────────────────────────────
# 1. HEALTHY corpus passes all hard invariants + has live wedges
# ─────────────────────────────────────────────────────────────────────────────

def test_healthy_corpus_passes(tmp):
    print("\n[1] Healthy corpus — all hard invariants pass, live wedges > 0")
    dst, plan = _make_corpus(tmp)
    res = validate_corruption(dst, SRC_DB)

    check("validator reports PASSED", res["passed"] is True,
          f"issues={res['issues'][:3]}")
    check("no hard issues recorded", len(res["issues"]) == 0,
          f"issues={res['issues'][:3]}")

    ipc = res["stats"]["invariant_pass_counts"]
    n = ipc["total_flaws"]
    check("operator flaws discovered (== plan kept)", n == plan["n_kept"],
          f"validator={n} plan={plan['n_kept']}")
    check("1. RECOVERABLE all pass", ipc["recoverable"] == n)
    check("2. HAS TEETH all pass", ipc["has_teeth"] == n)
    check("3. PLAUSIBLE all pass", ipc["plausible"] == n)
    check("5. RECOVERY PATH all pass", ipc["recovery_path"] == n)

    # 6. GT immutable (hard via --src)
    check("6. GT IMMUTABLE (dst venues hash == src)",
          res["stats"]["gt_hash_dst"] == res["stats"]["gt_hash_src"])

    # 8. reproducible
    check("8. REPRODUCIBLE overlay hash matches",
          res["stats"]["reproducible"]["status"] == "match",
          str(res["stats"].get("reproducible")))

    # live wedges
    lw = res["stats"]["live_wedge"]
    check("live-wedge count > 0",
          lw["n_tasks_with_live_wedge"] > 0,
          f"{lw['n_tasks_with_live_wedge']}/{lw['n_tasks']}")
    print(f"       live wedges: {lw['n_tasks_with_live_wedge']}/{lw['n_tasks']} tasks; "
          f"{n} operator flaws validated")


# ─────────────────────────────────────────────────────────────────────────────
# 2a. BROKEN: repairability=0 (no recovery) is flagged
# ─────────────────────────────────────────────────────────────────────────────

def test_broken_repairability_flagged(tmp):
    print("\n[2a] Broken flaw — repairability=0 / truth_carrier stripped is FLAGGED")
    dst, plan = _make_corpus(tmp)

    # Pick a kept flaw and destroy its recovery path: drop every truth_carrier
    # doc role tied to it, and overwrite stored repairability to 0. (The certifier
    # will recompute repairability=0 since only the lie remains.)
    victim = plan["kept"][0]["wrong_info_id"]
    conn = sqlite3.connect(dst)
    conn.execute(
        "UPDATE doc_venue_roles SET role='neutral', wrong_info_id=NULL "
        "WHERE wrong_info_id=? AND role='truth_carrier'", (victim,))
    conn.execute(
        "UPDATE wrong_info SET repairability=0.0 WHERE wrong_info_id=?", (victim,))
    # Also suppress official truth so there is genuinely no path back to GT.
    conn.execute(
        "UPDATE wrong_info SET suppress_authority=1 WHERE wrong_info_id=?", (victim,))
    conn.commit()
    conn.close()

    res = validate_corruption(dst, SRC_DB)
    check("validator reports FAILED", res["passed"] is False)
    flagged = any(victim in i for i in res["issues"])
    check("the broken flaw is named in an issue", flagged,
          f"issues={res['issues'][:5]}")
    # The failure should be a RECOVERABLE and/or RECOVERY PATH issue.
    relevant = [i for i in res["issues"]
                if victim in i and ("RECOVERABLE" in i or "RECOVERY PATH" in i)]
    check("failure is a RECOVERABLE / RECOVERY PATH issue", len(relevant) >= 1,
          f"got: {[i for i in res['issues'] if victim in i]}")


# ─────────────────────────────────────────────────────────────────────────────
# 2b. BROKEN: served == GT (no teeth) is flagged
# ─────────────────────────────────────────────────────────────────────────────

def test_broken_no_teeth_flagged(tmp):
    print("\n[2b] Broken flaw — served value reset to GT (no teeth) is FLAGGED")
    dst, plan = _make_corpus(tmp)

    # Pick an hours flaw and restore the served yelp value to GT, so the
    # agent-visible value == GT (the lie is no longer visible -> no teeth).
    victim = next(k for k in plan["kept"] if k["field"].startswith("hours_"))
    wid = victim["wrong_info_id"]
    vid = victim["venue_id"]
    field = victim["field"]
    conn = sqlite3.connect(dst)
    conn.row_factory = sqlite3.Row
    gt = conn.execute(
        f"SELECT {field} AS v FROM venues WHERE venue_id=?", (vid,)).fetchone()["v"]
    conn.execute(
        f"UPDATE yelp_listings SET yelp_{field}=? WHERE venue_id=?", (gt, vid))
    conn.commit()
    conn.close()

    res = validate_corruption(dst, SRC_DB)
    check("validator reports FAILED", res["passed"] is False)
    teeth_issue = any(wid in i and "HAS TEETH" in i for i in res["issues"])
    check("the no-teeth flaw raises a HAS TEETH issue", teeth_issue,
          f"issues={[i for i in res['issues'] if wid in i]}")


# ─────────────────────────────────────────────────────────────────────────────
# 2c. BROKEN: implausible corrupted value is flagged
# ─────────────────────────────────────────────────────────────────────────────

def test_broken_implausible_flagged(tmp):
    print("\n[2c] Broken flaw — implausible hours value is FLAGGED")
    dst, plan = _make_corpus(tmp)
    victim = next(k for k in plan["kept"] if k["field"].startswith("hours_"))
    wid = victim["wrong_info_id"]
    conn = sqlite3.connect(dst)
    # 26:00 is out of the 00:00–24:00 domain.
    conn.execute(
        "UPDATE wrong_info SET incorrect_value='10:00-26:00' WHERE wrong_info_id=?",
        (wid,))
    conn.commit()
    conn.close()

    res = validate_corruption(dst, SRC_DB)
    check("validator reports FAILED", res["passed"] is False)
    plaus_issue = any(wid in i and "PLAUSIBLE" in i for i in res["issues"])
    check("the implausible flaw raises a PLAUSIBLE issue", plaus_issue,
          f"issues={[i for i in res['issues'] if wid in i]}")


def main():
    if not os.path.exists(SRC_DB):
        print(f"[skip] source DB not found: {SRC_DB}")
        return 0

    tmp = tempfile.mkdtemp(prefix="valcorrtest_")
    try:
        test_healthy_corpus_passes(tmp)
        test_broken_repairability_flagged(tmp)
        test_broken_no_teeth_flagged(tmp)
        test_broken_implausible_flagged(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*60}")
    print(f"  {PASS} passed, {FAIL} failed")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
