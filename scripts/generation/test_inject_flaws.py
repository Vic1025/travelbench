"""Tests for the b1 flaw injector (inject_flaws.py) and masks (flaw_masks.py).

Run: python3 scripts/generation/test_inject_flaws.py

Uses a /tmp COPY of the real test_70 corpus DB. Skips gracefully if missing.
No API calls; fully deterministic.
"""

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.generation import flaw_masks as fm
from scripts.generation.inject_flaws import (
    inject, DEFAULT_PROFILE, _constraint_fields, _sub_seed,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(_HERE, "..", "..", "data", "cities", "New_York",
                      "runs", "test_70", "travelbench.db")
SEED = "b1-seed-001"
CREATED_AT = "2026-01-01T00:00:00Z"


# ─────────────────────────────────────────────────────────────────────────────
# MASK UNIT TESTS (no DB)
# ─────────────────────────────────────────────────────────────────────────────

def test_mask_cost_down():
    assert fm.cost_halve(40) == "20.0"          # halved, rounded
    assert fm.cost_halve(41) == "20.0"          # round(20.5)=20
    assert fm.cost_halve(0) is None             # free -> skip
    assert fm.cost_halve(None) is None
    # always lower than the true value
    for c in (5, 12, 22, 44, 100):
        w = fm.cost_halve(c)
        assert w is not None and float(w) < c, (c, w)
    print("  ✓ cost_down value_fn lowers cost")


def test_mask_price_tier_down():
    assert fm.price_tier_down("mid") == "budget"
    assert fm.price_tier_down("upscale") == "mid"
    assert fm.price_tier_down("luxury") == "fine-dining"
    assert fm.price_tier_down("free") is None   # already cheapest
    assert fm.price_tier_down("nonsense") is None
    print("  ✓ price_tier_down moves one rung cheaper")


def test_mask_hours_widen():
    # close later: 10:00-21:00 -> 10:00-23:00 (close +2h)
    assert fm.hours_close_later("10:00-21:00") == "10:00-23:00"
    # open earlier: 10:00-21:00 -> 08:00-21:00 (open -2h)
    assert fm.hours_open_earlier("10:00-21:00") == "08:00-21:00"
    # widening must produce a strictly larger open window
    def _span(rng):
        o, c = rng.split("-")
        def m(s):
            h, mm = s.split(":")
            return int(h) * 60 + int(mm)
        return m(c) - m(o)
    true = "12:00-18:00"
    assert _span(fm.hours_close_later(true)) > _span(true)
    assert _span(fm.hours_open_earlier(true)) > _span(true)
    # non-canonical / closed -> skip
    assert fm.hours_close_later("closed") is None
    assert fm.hours_open_earlier("6:00 AM - 1:00 AM") is None
    assert fm.hours_close_later("10:00-12:00,14:00-18:00") is None  # split service
    # clamp: can't close later than 24:00
    assert fm.hours_close_later("06:00-23:30") in ("06:00-24:00",)
    # clamp: can't open earlier than 00:00
    assert fm.hours_open_earlier("01:00-10:00") in ("00:00-10:00",)
    print("  ✓ hours widen (open earlier / close later) within 24h")


def test_mask_booking_off():
    assert fm.booking_to_false(1) == "0"
    assert fm.booking_to_false("1") == "0"
    assert fm.booking_to_false(0) is None
    assert fm.booking_to_false(None) is None
    print("  ✓ booking_required True->False flip")


def test_masks_library_shape():
    masks = fm.build_masks()
    ids = [m["id"] for m in masks]
    assert len(ids) == len(set(ids)), "mask ids must be unique"
    assert len(masks) >= 4
    for m in masks:
        assert m["direction"] == "false_positive"
        assert m["structure"] in ("minority_truth", "stale_authority")
        assert callable(m["predicate"]) and callable(m["value_fn"])
        lo, hi = m["target_repairability_band"]
        assert 0.0 <= lo < hi <= 1.0
    # coverage of the required directions
    fields = {m["target_field"] for m in masks}
    assert "avg_cost_local" in fields
    assert "__hours__" in fields
    assert "booking_required" in fields
    print(f"  ✓ mask library shape OK ({len(masks)} masks: {ids})")


def test_masks_for_field():
    assert all(m["target_field"] in ("avg_cost_local",)
               for m in fm.masks_for_field("avg_cost_local"))
    # hours masks resolve to any hours_<dow>
    assert len(fm.masks_for_field("hours_fri")) >= 2
    assert fm.masks_for_field("recommended_visit_minutes") == []  # no mask yet
    print("  ✓ masks_for_field routing")


def test_sub_seed_order_independent():
    a = _sub_seed("S", "v1", "hours_fri")
    b = _sub_seed("S", "v1", "hours_fri")
    assert a == b
    assert _sub_seed("S", "v1", "hours_fri") != _sub_seed("S", "v2", "hours_fri")
    print("  ✓ sub-seed deterministic & per-target distinct")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRAINT-FIELD EXTRACTION (no DB)
# ─────────────────────────────────────────────────────────────────────────────

def test_constraint_field_extraction():
    task = {
        "rubric": {
            "hard_constraints": [{"id": "hc_001", "type": "hours_check"}],
            "personal_constraints": [
                {"id": "pc_001", "scope": "per_day",
                 "aggregation": {"sum": "estimated_cost_local", "operator": "<=", "value": 26}},
                {"id": "pc_002", "scope": "activity_type=meal",
                 "condition": {"field": "price_tier", "operator": "<=", "value": "mid"},
                 "aggregation": "all"},
            ],
        }
    }
    fields = _constraint_fields(task)
    assert "avg_cost_local" in fields, fields   # from estimated_cost_local
    assert "price_tier" in fields, fields       # from condition.field
    assert "hours_fri" in fields, fields        # hours_check makes hours load-bearing
    print(f"  ✓ binding-constraint fields read: cost+price_tier+hours ({len(fields)} fields)")


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION (DB copy)
# ─────────────────────────────────────────────────────────────────────────────

def _venues_hash(conn):
    rows = conn.execute("SELECT * FROM venues ORDER BY venue_id").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM venues LIMIT 1").description]
    h = hashlib.blake2b(digest_size=16)
    for r in rows:
        h.update("|".join(str(r[c]) for c in cols).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def run_integration():
    tmp = tempfile.mkdtemp(prefix="b1test_")
    dst = os.path.join(tmp, "dst.db")

    plan = inject(SRC_DB, dst, SEED, DEFAULT_PROFILE, created_at=CREATED_AT)

    # (a) >=1 load-bearing flaw placed on a binding-constraint field
    assert plan["n_kept"] >= 1, "expected >=1 kept flaw"
    assert plan["n_targets"] >= 1, "expected >=1 load-bearing target"
    print(f"  ✓ (a) {plan['n_kept']} flaws kept on binding-constraint fields "
          f"({plan['n_targets']} targets, {plan['n_rejected']} rejected)")

    src = sqlite3.connect(SRC_DB); src.row_factory = sqlite3.Row
    dconn = sqlite3.connect(dst); dconn.row_factory = sqlite3.Row

    # (b) venues table truth UNCHANGED (whole-table hash identical)
    assert _venues_hash(src) == _venues_hash(dconn), "venues GT must be immutable"
    print("  ✓ (b) venues GT table unchanged (hash identical src vs dst)")

    # (c) yelp served value == wrong value for a placed hours flaw
    hours_kept = [k for k in plan["kept"] if k["field"].startswith("hours_")]
    assert hours_kept, "expected >=1 hours flaw"
    k = hours_kept[0]
    served = dconn.execute(
        f"SELECT yelp_{k['field']} AS v FROM yelp_listings WHERE venue_id = ?",
        (k["venue_id"],),
    ).fetchone()["v"]
    assert str(served) == k["incorrect_value"], (served, k["incorrect_value"])
    # and the dst venues GT still holds the correct value
    gt = dconn.execute(
        f"SELECT {k['field']} AS v FROM venues WHERE venue_id = ?",
        (k["venue_id"],),
    ).fetchone()["v"]
    assert str(gt) == k["correct_value"], (gt, k["correct_value"])
    print(f"  ✓ (c) yelp serves the lie ({k['field']}={served}) while venues GT={gt}")

    # (d) every kept flaw passes the certifier gate
    from scripts.generation import flaw_certifier as fc
    for k in plan["kept"]:
        row = dconn.execute(
            "SELECT * FROM wrong_info WHERE wrong_info_id = ?", (k["wrong_info_id"],)
        ).fetchone()
        cert = fc.certify(fc.load_flaw_evidence(dconn, row))
        assert cert["detectability"] >= 1 and 0.0 < cert["repairability"] < 1.0, \
            (k["wrong_info_id"], cert)
        # persisted signals match
        assert row["detectability"] == k["detectability"]
    print(f"  ✓ (d) all {plan['n_kept']} kept flaws pass certifier (det>=1, 0<repair<1)")

    # (d2) SERVABILITY GATE: every kept flaw must be servable — its
    # search_yelp-visible value differs from the GT `venues` value (has teeth).
    from scripts.generation.inject_flaws import _served_yelp_value
    for k in plan["kept"]:
        name = dconn.execute(
            "SELECT name FROM venues WHERE venue_id = ?", (k["venue_id"],)
        ).fetchone()["name"]
        served = _served_yelp_value("new_york", dst, k["venue_id"], name, k["field"])
        gt = dconn.execute(
            f"SELECT {k['field']} AS v FROM venues WHERE venue_id = ?",
            (k["venue_id"],),
        ).fetchone()["v"]
        assert served is not None and str(served) != str(gt), \
            (k["venue_id"], k["field"], "served", served, "gt", gt)
        # plan records the served value + servable flag
        assert k.get("servable") is True, k
    print(f"  ✓ (d2) all {plan['n_kept']} kept flaws are SERVABLE "
          f"(search_yelp value != GT venues value)")

    # (d3) v1 KNOWN LIMITATION: cost/price flaws are rejected as not_servable.
    # search_yelp sources avg_cost_local/price_tier from the immutable venues GT
    # (and does not surface them), so those lies have no teeth in v1.
    kept_fields = {k["field"] for k in plan["kept"]}
    assert "avg_cost_local" not in kept_fields, "cost flaw must not survive v1"
    assert "price_tier" not in kept_fields, "price flaw must not survive v1"
    assert all(k["field"].startswith("hours_") for k in plan["kept"]), \
        "only hours_* flaws are servable in v1"
    ns_reasons = [r for r in plan["rejected"]
                  if str(r.get("reason", "")).startswith("not_servable")]
    ns_fields = {r["field"] for r in ns_reasons}
    assert ns_fields, "expected some not_servable rejections in v1"
    assert ns_fields <= {"avg_cost_local", "price_tier", "booking_required"}, ns_fields
    # at least cost OR price is among them (the documented v1 case)
    assert ns_fields & {"avg_cost_local", "price_tier"}, ns_fields
    nsbf = plan.get("not_servable_by_field", {})
    assert sum(nsbf.values()) == len(ns_reasons), (nsbf, len(ns_reasons))
    assert plan.get("servability_note") and "DEFERRED" in plan["servability_note"]
    print(f"  ✓ (d3) cost/price rejected as not_servable "
          f"(by field: {nsbf}); hours survive — v1 limitation documented")

    # (e) reproducible — second run yields identical overlay hash
    dst2 = os.path.join(tmp, "dst2.db")
    plan2 = inject(SRC_DB, dst2, SEED, DEFAULT_PROFILE, created_at=CREATED_AT,
                   write_plan=False)
    assert plan["overlay_hash"] == plan2["overlay_hash"], "overlay must reproduce"
    assert plan["n_kept"] == plan2["n_kept"]
    print(f"  ✓ (e) reproducible (overlay_hash={plan['overlay_hash'][:16]}…)")

    # (f) corruption_runs row written
    cr = dconn.execute("SELECT * FROM corruption_runs").fetchall()
    assert len(cr) == 1, cr
    row = cr[0]
    assert row["master_seed"] == SEED
    assert row["created_at"] == CREATED_AT       # no datetime.now()
    assert row["corruptor_version"], "corruptor_version constant must be set"
    assert row["gt_hash"], "gt_hash must be recorded"
    print("  ✓ (f) corruption_runs row written with provenance")

    # disjoint scopes: no two kept flaws share the same (venue,field)
    seen = set()
    for k in plan["kept"]:
        key = (k["venue_id"], k["field"])
        assert key not in seen, f"duplicate flaw on {key}"
        seen.add(key)
    print("  ✓ disjoint (venue,field) scopes across kept flaws")

    # false-positive direction sanity on a cost flaw (incorrect < correct)
    cost_kept = [k for k in plan["kept"] if k["field"] == "avg_cost_local"]
    if cost_kept:
        k = cost_kept[0]
        assert float(k["incorrect_value"]) < float(k["correct_value"]), k
        print(f"  ✓ false-positive direction: cost lie {k['incorrect_value']} "
              f"< truth {k['correct_value']}")

    # rejections are logged with reasons (never silently dropped)
    assert plan["n_rejected"] == len(plan["rejected"])
    assert all(r.get("reason") for r in plan["rejected"])
    print(f"  ✓ all {plan['n_rejected']} rejections carry a reason")

    src.close(); dconn.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return plan


def main():
    print("Mask unit tests:")
    test_mask_cost_down()
    test_mask_price_tier_down()
    test_mask_hours_widen()
    test_mask_booking_off()
    test_masks_library_shape()
    test_masks_for_field()
    test_sub_seed_order_independent()
    test_constraint_field_extraction()

    if not os.path.exists(SRC_DB):
        print(f"\n[skip] source DB not found: {SRC_DB}")
        print("Mask + unit tests passed.")
        return

    print("\nIntegration tests (on /tmp copy of test_70):")
    run_integration()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
