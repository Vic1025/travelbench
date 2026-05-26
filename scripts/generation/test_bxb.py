"""
scripts/generation/test_bxb.py

Tests for constraint_engine_bxb.py.

Covers:
  - Per-type narrowing factor unit tests (known exact answers)
  - Joint multivariate hypergeometric (two at_least in same group)
  - _compute_bxb_joint group-aware computation
  - Tension / solvability integration scenarios
  - Edge cases: single constraint, empty list, P < K
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from math import comb
from scripts.generation.constraint_engine_bxb import (
    _f_at_least, _f_at_most, _f_ratio, _f_at_least_days,
    _f_count_distinct, _f_at_most_distinct, _f_sum_leq,
    _joint_two_at_least, _compute_group_joint, _compute_bxb_joint,
    _constraint_group,
)

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f": {detail}" if detail else ""))

def approx(a, b, tol=0.05):
    """Check two floats are within tol of each other (absolute)."""
    return abs(a - b) <= tol


# ─────────────────────────────────────────────────────────────────────────────
# 1. at_least unit tests
# ─────────────────────────────────────────────────────────────────────────────
print("1. at_least")

# P=10, m=4, K=5, N=2 → P(Hyper(5,4,10) >= 2)
# Manual: sum C(4,k)*C(6,5-k)/C(10,5) for k=2,3,4
denom = comb(10, 5)
manual = sum(comb(4, k) * comb(6, 5 - k) for k in range(2, 5)) / denom
got = _f_at_least(4, 10, 5, 2)
check("at_least basic", approx(got, manual, 0.001), f"got={got:.4f} expected={manual:.4f}")

# N=0 always 1
check("at_least N=0", approx(_f_at_least(4, 10, 5, 0), 1.0, 0.01))

# m=0, N=1 → impossible
check("at_least m=0", _f_at_least(0, 10, 5, 1) < 0.01)

# N > m → very small
check("at_least N>m", _f_at_least(2, 10, 5, 5) < 0.02)

# All qualify → high
check("at_least m=P", _f_at_least(10, 10, 5, 3) > 0.99)


# ─────────────────────────────────────────────────────────────────────────────
# 2. at_most unit tests
# ─────────────────────────────────────────────────────────────────────────────
print("2. at_most")

# P(Hyper(5,4,10) <= 2) = sum C(4,k)*C(6,5-k)/C(10,5) for k=0,1,2
manual_am = sum(comb(4, k) * comb(6, 5 - k) for k in range(3)) / denom
got_am = _f_at_most(4, 10, 5, 2)
check("at_most basic", approx(got_am, manual_am, 0.001), f"got={got_am:.4f} expected={manual_am:.4f}")

# at_most 0: very small when m is large
check("at_most N=0 low m", _f_at_most(1, 10, 5, 0) < 0.7)

# Consistency: at_least + at_most complement
# P(>=2) + P(<=1) should equal 1
f_ge2 = _f_at_least(4, 10, 5, 2)
f_le1 = _f_at_most(4, 10, 5, 1)
check("at_least + at_most = 1", approx(f_ge2 + f_le1, 1.0, 0.001))


# ─────────────────────────────────────────────────────────────────────────────
# 3. ratio unit test
# ─────────────────────────────────────────────────────────────────────────────
print("3. ratio")

# ratio=0.6, m=5, P=10, K=5 → P(Hyper(5,5,10) >= ceil(0.6*5)=3)
from math import ceil
N_ratio = ceil(0.6 * 5)  # 3
manual_ratio = sum(comb(5, k) * comb(5, 5 - k) for k in range(N_ratio, 6)) / denom
got_ratio = _f_ratio(5, 10, 5, 0.6)
check("ratio basic", approx(got_ratio, manual_ratio, 0.01), f"got={got_ratio:.4f} expected={manual_ratio:.4f}")

# ratio=1.0 → nearly impossible unless m=P
check("ratio=1.0 hard", _f_ratio(3, 10, 5, 1.0) < 0.05)
# ratio=0.0 → trivially satisfied
check("ratio=0.0 easy", _f_ratio(3, 10, 5, 0.0) > 0.99)


# ─────────────────────────────────────────────────────────────────────────────
# 4. at_least_days unit test
# ─────────────────────────────────────────────────────────────────────────────
print("4. at_least_days")

# D=3 days, 2 slots/day, m=3 qualifying out of P=10
# p_day = P(Hyper(2,3,10) >= 1) = 1 - C(3,0)*C(7,2)/C(10,2) = 1 - 21/45 = 0.533
p_day_manual = 1 - comb(7, 2) / comb(10, 2)
# P(Bin(3, p_day) >= 2)
f_days = _f_at_least_days(3, 10, 2, 3, 2)
from scipy.stats import binom
expected_days = float(binom.sf(1, 3, p_day_manual))
check("at_least_days D=3 N=2", approx(f_days, expected_days, 0.05),
      f"got={f_days:.4f} expected={expected_days:.4f}")

# N_days=0 → always satisfied
check("at_least_days N=0", _f_at_least_days(3, 10, 2, 3, 0) > 0.99)
# N_days > D → impossible
check("at_least_days N>D", _f_at_least_days(3, 10, 2, 3, 5) < 0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 5. count_distinct unit tests
# ─────────────────────────────────────────────────────────────────────────────
print("5. count_distinct")

# 4 distinct districts, equal distribution, K=8 draws
# P(cover >= 3 distinct out of 4) should be high
pool_4dist = [{"venue_id": f"v{i}", "district": f"d{i%4}"} for i in range(12)]
f_cd = _f_count_distinct(pool_4dist, "district", 8, 3)
check("count_distinct 4/3 K=8", f_cd > 0.7, f"got={f_cd:.3f}")

# Impossible: need more distinct than exist
f_cd_impossible = _f_count_distinct(pool_4dist, "district", 8, 5)
check("count_distinct N>D impossible", f_cd_impossible < 0.01, f"got={f_cd_impossible:.3f}")

# N=1 almost certain
f_cd_easy = _f_count_distinct(pool_4dist, "district", 8, 1)
check("count_distinct N=1 easy", f_cd_easy > 0.95, f"got={f_cd_easy:.3f}")

# Monotone: P(>=3) <= P(>=2)
f_cd2 = _f_count_distinct(pool_4dist, "district", 8, 2)
check("count_distinct monotone", f_cd <= f_cd2 + 0.001, f"P(>=3)={f_cd:.3f} P(>=2)={f_cd2:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. at_most_distinct unit tests
# ─────────────────────────────────────────────────────────────────────────────
print("6. at_most_distinct")

# P(cover <= 2 distinct out of 4) = 1 - P(cover >= 3)
f_amd = _f_at_most_distinct(pool_4dist, "district", 8, 2)
f_complement = _f_count_distinct(pool_4dist, "district", 8, 3)
check("at_most_distinct complement", approx(f_amd, 1.0 - f_complement, 0.01),
      f"got={f_amd:.3f} complement={1-f_complement:.3f}")

# at_most D is always satisfied
f_amd_full = _f_at_most_distinct(pool_4dist, "district", 8, 4)
check("at_most_distinct N=D always sat", f_amd_full > 0.99, f"got={f_amd_full:.3f}")

# at_most 0 nearly impossible with K>0
f_amd_zero = _f_at_most_distinct(pool_4dist, "district", 8, 0)
check("at_most_distinct N=0 impossible", f_amd_zero < 0.01, f"got={f_amd_zero:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. sum (CLT) unit tests
# ─────────────────────────────────────────────────────────────────────────────
print("7. sum (CLT)")

# Pool of K=4 draws from venues costing 10, 20, 30, 40 (mean=25, std≈12.9)
pool_costs = [{"venue_id": f"v{i}", "cost": c} for i, c in enumerate([10, 20, 30, 40])]
# P(sum of 4 draws <= 100) = P(mean <= 25) ≈ 0.5 by symmetry
f_sum = _f_sum_leq(pool_costs, "cost", 4, 100, "<=")
check("sum CLT symmetric", approx(f_sum, 0.5, 0.15), f"got={f_sum:.3f}")

# sum very high → nearly 1
f_sum_high = _f_sum_leq(pool_costs, "cost", 4, 500, "<=")
check("sum high ceiling", f_sum_high > 0.95, f"got={f_sum_high:.3f}")

# sum very low → nearly 0
f_sum_low = _f_sum_leq(pool_costs, "cost", 4, 10, "<=")
check("sum low ceiling", f_sum_low < 0.1, f"got={f_sum_low:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Joint two at_least (multivariate hypergeometric)
# ─────────────────────────────────────────────────────────────────────────────
print("8. joint two at_least")

# Build a small pool: 4 halal restaurants, 4 outdoor sites, 2 that are both
# P=10 total, K=4 slots
small_pool = (
    [{"venue_id": f"h{i}", "category": "restaurant",
      "tags": ["halal"], "district": "A"} for i in range(4)] +
    [{"venue_id": f"o{i}", "category": "museum",
      "tags": ["outdoor"], "district": "B"} for i in range(4)] +
    [{"venue_id": f"b{i}", "category": "cafe",
      "tags": ["halal", "outdoor"], "district": "A"} for i in range(2)]
)
# c_A: at_least 1 halal in scope=all
c_A = {"id": "p1", "scope": "all", "condition": {"has_tag": "halal"},
       "aggregation": {"at_least": 1}}
# c_B: at_least 1 outdoor in scope=all
c_B = {"id": "p2", "scope": "all", "condition": {"has_tag": "outdoor"},
       "aggregation": {"at_least": 1}}

f_joint = _joint_two_at_least(small_pool, 4, c_A, c_B)
# Should be high — many venues are halal or outdoor
check("joint two at_least >0", 0 < f_joint <= 1.0, f"got={f_joint:.4f}")
check("joint two at_least reasonable", f_joint > 0.5, f"got={f_joint:.4f}")

# Joint should be <= min of individual estimates
f_a_only = _f_at_least(6, 10, 4, 1)  # 6 halal venues out of 10
f_b_only = _f_at_least(6, 10, 4, 1)  # 6 outdoor venues out of 10
check("joint <= min of individuals", f_joint <= min(f_a_only, f_b_only) + 0.001,
      f"joint={f_joint:.4f} min={min(f_a_only,f_b_only):.4f}")

# Tight constraints: at_least 3 halal AND at_least 3 outdoor in pool of 10 K=4
c_A2 = {"id": "p3", "scope": "all", "condition": {"has_tag": "halal"},
        "aggregation": {"at_least": 3}}
c_B2 = {"id": "p4", "scope": "all", "condition": {"has_tag": "outdoor"},
        "aggregation": {"at_least": 3}}
f_tight = _joint_two_at_least(small_pool, 4, c_A2, c_B2)
check("joint tight constraints small", f_tight < 0.3, f"got={f_tight:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# 9. _compute_bxb_joint — group-aware computation
# ─────────────────────────────────────────────────────────────────────────────
print("9. _compute_bxb_joint")

# Build a realistic small pool
food_venues = [
    {"venue_id": f"r{i}", "category": "restaurant",
     "tags": ["halal"] if i < 4 else [], "district": f"d{i%3}",
     "avg_cost_local": 20.0 + i * 5, "price_tier": "mid"} for i in range(10)
]
site_venues = [
    {"venue_id": f"m{i}", "category": "museum",
     "tags": ["outdoor"] if i < 3 else ["indoor"], "district": f"d{i%3}",
     "avg_cost_local": 10.0, "price_tier": "budget"} for i in range(8)
]
test_pool = food_venues + site_venues

# Constraint A: at_least 2 halal meals (food group)
ca = {"id": "pa", "scope": "activity_type=meal",
      "condition": {"has_tag": "halal"}, "aggregation": {"at_least": 2}}
# Constraint B: at_least 1 outdoor museum (site group)
cb = {"id": "pb", "scope": "category=museum",
      "condition": {"has_tag": "outdoor"}, "aggregation": {"at_least": 1}}

f_j, vc = _compute_bxb_joint([ca, cb], test_pool, days=2)
check("compute_bxb food+site >0", 0 < f_j <= 1.0, f"f={f_j:.4f}")
check("compute_bxb valid_count >0", vc > 0, f"vc={vc:.1f}")
check("compute_bxb valid_count finite", vc < float("inf"))

# Food and site independent: f_joint = f_food * f_site
f_food_only, _ = _compute_bxb_joint([ca], test_pool, days=2)
f_site_only, _ = _compute_bxb_joint([cb], test_pool, days=2)
check("food×site independence",
      approx(f_j, f_food_only * f_site_only, tol=0.001),
      f"joint={f_j:.4f} product={f_food_only*f_site_only:.4f}")

# Two constraints in same group: should use joint hypergeometric
ca2 = {"id": "pa2", "scope": "activity_type=meal",
       "condition": {"has_tag": "halal"}, "aggregation": {"at_least": 2}}
ca3 = {"id": "pa3", "scope": "activity_type=meal",
       "condition": {"has_tag": "halal"}, "aggregation": {"at_least": 3}}
# at_least 2 AND at_least 3 = at_least 3 (the stricter wins)
f_23, _ = _compute_bxb_joint([ca2, ca3], test_pool, days=2)
f_3_only, _ = _compute_bxb_joint([ca3], test_pool, days=2)
check("same group joint <= stricter", f_23 <= f_3_only + 0.01,
      f"f_23={f_23:.4f} f_3_only={f_3_only:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# 10. Boundary / edge cases
# ─────────────────────────────────────────────────────────────────────────────
print("10. edge cases")

# Empty constraint list
f_empty, vc_empty = _compute_bxb_joint([], test_pool, days=2)
check("empty constraints", f_empty > 0.999)

# Single constraint
f_single, _ = _compute_bxb_joint([ca], test_pool, days=2)
check("single constraint >0", 0 < f_single <= 1.0)

# A×A constraint (agg="all") excluded from B×B
c_axa = {"id": "axa", "scope": "all", "condition": {}, "aggregation": "all"}
f_axa, _ = _compute_bxb_joint([c_axa], test_pool, days=2)
check("A×A constraint excluded (f≈1)", f_axa > 0.999)

# P < K: pool smaller than slots needed — clamp gracefully
tiny_pool = [{"venue_id": "t1", "category": "restaurant", "tags": ["halal"],
              "district": "A", "avg_cost_local": 10.0}]
c_tiny = {"id": "pt", "scope": "activity_type=meal",
          "condition": {"has_tag": "halal"}, "aggregation": {"at_least": 1}}
f_tiny, _ = _compute_bxb_joint([c_tiny], tiny_pool, days=3)
check("P<K graceful clamp", 0 < f_tiny <= 1.0, f"got={f_tiny:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Integration: tension detection scenarios
# ─────────────────────────────────────────────────────────────────────────────
print("11. tension scenarios")

# Type 3 tension: should detect f<=5% for tight constraints on small pool
# Build a pool where halal + upscale restaurants are very rare
tension_pool = (
    [{"venue_id": f"r{i}", "category": "restaurant",
      "tags": ["halal"] if i == 0 else [],
      "price_tier": "upscale" if i < 2 else "mid",
      "district": "A", "avg_cost_local": 40.0} for i in range(8)] +
    [{"venue_id": f"m{i}", "category": "museum",
      "tags": [], "district": "A", "avg_cost_local": 10.0} for i in range(6)]
)
# at_least 2 halal (only 1 exists!) → very tight
c_halal = {"id": "ph", "scope": "activity_type=meal",
           "condition": {"has_tag": "halal"}, "aggregation": {"at_least": 2}}
# at_most_distinct 1 district (all are district A → actually easy)
c_dist = {"id": "pd", "scope": "all",
          "condition": {}, "aggregation": {"at_most_distinct": 1, "field": "district"}}
f_tight_tension, vc_tight = _compute_bxb_joint([c_halal, c_dist], tension_pool, days=2)
check("tight tension f<0.5", f_tight_tension < 0.5,
      f"f={f_tight_tension:.4f}")

# Type 1 loose scenario: should NOT detect tension (f high)
loose_pool = (
    [{"venue_id": f"r{i}", "category": "restaurant",
      "tags": ["italian"], "price_tier": "mid",
      "district": f"d{i%3}", "avg_cost_local": 25.0} for i in range(15)] +
    [{"venue_id": f"m{i}", "category": "museum",
      "tags": [], "district": f"d{i%3}", "avg_cost_local": 10.0} for i in range(10)]
)
c_loose = {"id": "pl", "scope": "activity_type=meal",
           "condition": {"has_tag": "italian"}, "aggregation": {"at_least": 1}}
f_loose, _ = _compute_bxb_joint([c_loose], loose_pool, days=2)
check("loose type1 f>0.5", f_loose > 0.5,
      f"f={f_loose:.4f} (should be easy)")

# Type 5 tightness: tight combo should give f <= 0.1
c_t5_a = {"id": "t5a", "scope": "activity_type=meal",
           "condition": {"has_tag": "halal"}, "aggregation": {"at_least": 2}}
c_t5_b = {"id": "t5b", "scope": "category=museum",
           "condition": {"has_tag": "outdoor"}, "aggregation": {"at_least": 2}}
# In tension_pool: only 1 halal, 0 outdoor museums → very tight
f_t5, _ = _compute_bxb_joint([c_t5_a, c_t5_b], tension_pool, days=2)
check("type5 tight f<0.1", f_t5 < 0.1, f"f={f_t5:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
total = PASS + FAIL
print(f"\n{'='*55}")
if FAIL == 0:
    print(f"✅ All B×B tests passed ({PASS}/{total})")
else:
    print(f"❌ {FAIL}/{total} B×B tests FAILED")
    sys.exit(1)
