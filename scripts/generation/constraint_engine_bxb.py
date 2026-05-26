"""
scripts/generation/constraint_engine_bxb.py

B×B plan-space narrowing estimator.

A×A constraints are pool filters — they remove venues.
B×B constraints are plan-space filters — they narrow the fraction of valid
schedules without removing venues from the pool.

This module estimates the fraction f of random K-slot plans that satisfy
a given set of counting-aggregation constraints.  The estimate is
approximate to ±20%, which is sufficient for threshold detection.

Public API
----------
_compute_bxb_joint(constraints, pool, days) -> (f_joint, valid_count)

    constraints : list of personal_constraint dicts (scope/condition/aggregation)
    pool        : list of venue dicts (full pool, post-A×A filtering)
    days        : int

    Returns:
        f_joint     : float  — fraction of random plans satisfying all constraints
        valid_count : float  — f_joint × baseline plan count

Group structure
---------------
A valid plan is K_food draws from food venues × K_site draws from site venues.
Food and site groups are independent — their f factors multiply directly.
Universal constraints span both groups and are treated as a third independent
factor (conservative approximation — see design doc Step 2).

Baseline plan count = C(P_food, K_food) × C(P_site, K_site)
"""

from __future__ import annotations

from math import comb, sqrt
from scipy.stats import hypergeom as _hypergeom, norm as _norm, binom as _binom

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

FOOD_CATS = frozenset({"restaurant", "cafe", "bar"})
SITE_CATS = frozenset({"museum", "attraction", "park", "neighbourhood"})
PRICE_ORDER = ["free", "budget", "mid", "upscale", "luxury"]

_CLAMP_LO = 1e-6
_CLAMP_HI = 1.0 - 1e-6


def _clamp(x: float) -> float:
    return max(_CLAMP_LO, min(_CLAMP_HI, x))


# ─────────────────────────────────────────────────────────────────────────────
# Scope → group classification
# ─────────────────────────────────────────────────────────────────────────────

def _constraint_group(c: dict, pool: list[dict]) -> str:
    """Return 'food', 'site', or 'universal' for a B×B constraint."""
    scope = c.get("scope", "all")
    if scope in ("all", "activity_type=any", "per_day") or not scope:
        return "universal"
    if isinstance(scope, list):
        # AND-list: check if all elements resolve to same group
        groups = {_scope_group_str(s, pool) for s in scope}
        return groups.pop() if len(groups) == 1 else "universal"
    return _scope_group_str(scope, pool)


def _scope_group_str(scope: str, pool: list[dict]) -> str:
    if not isinstance(scope, str):
        return "universal"
    if scope.startswith("activity_type=meal"):
        return "food"
    if scope.startswith("activity_type=visit"):
        return "site"
    if scope.startswith("category="):
        cat = scope.split("=", 1)[1]
        if cat in FOOD_CATS:
            return "food"
        if cat in SITE_CATS:
            return "site"
        return "universal"
    if scope.startswith("has_tag="):
        tag = scope.split("=", 1)[1]
        matched = [v for v in pool if tag in (v.get("tags") or [])]
        if not matched:
            return "universal"
        food_n = sum(1 for v in matched if v.get("category") in FOOD_CATS)
        site_n = sum(1 for v in matched if v.get("category") in SITE_CATS)
        total = len(matched)
        if food_n / total >= 0.8:
            return "food"
        if site_n / total >= 0.8:
            return "site"
        return "universal"
    return "universal"


# ─────────────────────────────────────────────────────────────────────────────
# Pool helpers
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Per-type narrowing factor functions
# ─────────────────────────────────────────────────────────────────────────────

def _f_at_least(m: int, P: int, K: int, N: int) -> float:
    """
    P(hypergeometric(K, m, P) >= N)
    = fraction of K-draws from P items (m qualifying) where at least N qualify.
    Uses scipy.stats.hypergeom.sf(N-1, P, m, K).
    """
    if P <= 0 or K <= 0:
        return _CLAMP_LO
    m = min(m, P)
    K = min(K, P)
    if m == 0:
        return _CLAMP_LO if N > 0 else _CLAMP_HI
    if N <= 0:
        return _CLAMP_HI
    if m >= P:
        return _CLAMP_HI if N <= K else _CLAMP_LO
    return _clamp(float(_hypergeom.sf(N - 1, P, m, K)))


def _f_at_most(m: int, P: int, K: int, N: int) -> float:
    """P(hypergeometric(K, m, P) <= N)"""
    if P <= 0 or K <= 0:
        return _CLAMP_LO
    m = min(m, P)
    K = min(K, P)
    if m == 0:
        return _CLAMP_HI
    return _clamp(float(_hypergeom.cdf(N, P, m, K)))


def _f_ratio(m_scope: int, P_scope: int, K_scope: int, R: float) -> float:
    """
    P(hypergeometric(K_scope, m_scope, P_scope) >= ceil(R * K_scope))
    """
    import math
    threshold = math.ceil(R * K_scope)
    return _f_at_least(m_scope, P_scope, K_scope, threshold)


def _f_at_least_days(m: int, P: int, K_per_day: int, D: int, N_days: int) -> float:
    """
    Probability that at least N_days of D days each have ≥1 qualifying venue
    drawn (i.e., at least 1 of K_per_day draws qualifies on that day).
    p_day = P(Hypergeometric(K_per_day, m, P) >= 1) = 1 - P(0 qualify)
    f = P(Binomial(D, p_day) >= N_days)
    """
    if P <= 0 or K_per_day <= 0 or D <= 0:
        return _CLAMP_LO
    m = min(m, P)
    K_per_day = min(K_per_day, P)
    p_day = _clamp(1.0 - float(_hypergeom.pmf(0, P, m, K_per_day)))
    return _clamp(float(_binom.sf(N_days - 1, D, p_day)))


def _f_count_distinct(pool: list[dict], field: str, K: int, N: int) -> float:
    """
    Probability that K draws from pool cover at least N distinct values of field.
    Uses a coupon-collector DP for D <= 20, and a normal approximation for D > 20.
    """
    val_counts: dict = {}
    for v in pool:
        val = v.get(field)
        if val is not None:
            val_counts[val] = val_counts.get(val, 0) + 1
    D = len(val_counts)
    if D == 0:
        return _CLAMP_LO
    if N <= 0:
        return _CLAMP_HI
    if N > D or N > K:
        return _CLAMP_LO

    if len(pool) == 0 or K == 0:
        return _CLAMP_LO

    if D <= 20:
        # Coupon-collector DP (uniform approximation).
        # dp[j] = P(exactly j distinct values seen after k draws)
        dp = [0.0] * (D + 1)
        dp[0] = 1.0
        for _ in range(K):
            ndp = [0.0] * (D + 1)
            for j in range(min(_, D) + 1):
                if dp[j] == 0:
                    continue
                # stay at j (draw an already-seen value)
                ndp[j] += dp[j] * (j / D)
                # add a new one
                if j < D:
                    ndp[j + 1] += dp[j] * ((D - j) / D)
            dp = ndp
        # P(cover >= N distinct)
        return _clamp(sum(dp[j] for j in range(N, D + 1)))
    else:
        # Large D: normal approximation for coupon collector
        # E[distinct after K draws] ≈ D*(1 - (1-1/D)^K)
        # Var ≈ D*(D-1)*(1-2/D)^K - D^2*(1-1/D)^(2K) + D*(1-1/D)^K
        # These are complex; use simpler approximation:
        p_miss_one = ((D - 1) / D) ** K  # P(specific value never drawn)
        expected_missed = D * p_miss_one
        if expected_missed <= 0:
            return _CLAMP_HI
        # P(cover >= N) ≈ P(missed <= D-N) ≈ normal approx on missed
        expected_covered = D - expected_missed
        var_covered = D * p_miss_one * (1 - p_miss_one) * (1 + (D - 1) * ((D - 2) / D) ** K / (1 - p_miss_one + 1e-9))
        var_covered = max(var_covered, 0.01)
        return _clamp(float(_norm.cdf((expected_covered - N + 0.5) / sqrt(var_covered))))


def _f_at_most_distinct(pool: list[dict], field: str, K: int, N: int) -> float:
    """
    Probability that K draws cover at most N distinct values of field.
    P(cover <= N) = 1 - P(cover >= N+1)
    Uses inclusion-exclusion via _f_count_distinct.
    """
    return _clamp(1.0 - float(_f_count_distinct(pool, field, K, N + 1)))


def _f_sum_leq(pool: list[dict], cost_field: str, K: int, V: float,
               operator: str = "<=") -> float:
    """
    P(sum of K draws from cost_field <= V)  [or >= V, > V, < V based on operator]
    Uses CLT approximation.
    """
    costs = [v.get(cost_field) or 0.0 for v in pool if v.get(cost_field) is not None]
    if not costs:
        costs = [0.0]
    mu = sum(costs) / len(costs)
    var = sum((c - mu) ** 2 for c in costs) / max(len(costs), 1)
    sigma = max(sqrt(var), 0.01)

    if K <= 0:
        return _CLAMP_HI if operator in ("<=", "<") else _CLAMP_LO
    total_mu = K * mu
    total_sigma = sqrt(K) * sigma
    z = (V - total_mu) / total_sigma
    if operator in ("<=", "<"):
        return _clamp(float(_norm.cdf(z)))
    else:  # >= or >
        return _clamp(float(_norm.sf(z)))


# ─────────────────────────────────────────────────────────────────────────────
# Per-constraint dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _estimate_single(c: dict, group_pool: list[dict], K: int, days: int) -> float:
    """
    Estimate narrowing factor f for a single constraint operating on group_pool
    with K total slots.
    Returns f in [1e-6, 1-1e-6].
    """
    from scripts.generation.constraint_engine import venues_matching
    agg = c.get("aggregation")
    scope = c.get("scope", "all")
    cond = c.get("condition") or {}
    P = len(group_pool)

    if not isinstance(agg, dict):
        # "all" or "none" — these are A×A, not B×B
        return _CLAMP_HI

    # Number of qualifying venues within the scope+condition
    try:
        m_venues = venues_matching(group_pool, scope, cond)
        m = len(m_venues)
    except Exception:
        m = P // 2  # safe fallback

    if "at_least" in agg:
        N = int(agg["at_least"])
        return _f_at_least(m, P, K, N)

    if "at_most" in agg:
        N = int(agg["at_most"])
        return _f_at_most(m, P, K, N)

    if "ratio" in agg:
        R = float(agg["ratio"])
        # K_scope = slots that fall within this scope
        # For scoped constraints, K_scope is the fraction of K in that scope
        if P > 0:
            scope_frac = m / P  # approx fraction of slots in scope
        else:
            scope_frac = 0.5
        K_scope = max(1, round(K * scope_frac))
        return _f_ratio(m, P, K_scope, R)

    if "at_least_days" in agg:
        N_days = int(agg["at_least_days"])
        K_per_day = max(1, K // max(days, 1))
        return _f_at_least_days(m, P, K_per_day, days, N_days)

    if "count_distinct" in agg:
        N = int(agg["count_distinct"])
        field = agg.get("field", "district")
        return _f_count_distinct(m_venues, field, K, N)

    if "at_most_distinct" in agg:
        N = int(agg["at_most_distinct"])
        field = agg.get("field", "district")
        return _f_at_most_distinct(m_venues, field, K, N)

    if "sum" in agg:
        cost_field = str(agg["sum"])
        op = agg.get("operator", "<=")
        val = float(agg.get("value", 0))
        return _f_sum_leq(m_venues, cost_field, K, val, op)

    return _CLAMP_HI  # unknown aggregation — treat as no constraint


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Joint computation for same-group constraint pairs
# ─────────────────────────────────────────────────────────────────────────────

def _joint_two_at_least(pool: list[dict], K: int,
                        c_a: dict, c_b: dict) -> float:
    """
    Exact multivariate hypergeometric for two at_least constraints in the same group.

    Partitions pool into 4 buckets (A_only, B_only, AB, neither) and
    enumerates all (k_a, k_b, k_ab, k_n) with k_a+k_b+k_ab+k_n=K,
    k_a+k_ab >= N_A, k_b+k_ab >= N_B.
    """
    from scripts.generation.constraint_engine import venues_matching
    scope_a, cond_a = c_a.get("scope", "all"), c_a.get("condition") or {}
    scope_b, cond_b = c_b.get("scope", "all"), c_b.get("condition") or {}
    N_A = int(c_a["aggregation"]["at_least"])
    N_B = int(c_b["aggregation"]["at_least"])

    try:
        set_a = frozenset(v["venue_id"] for v in venues_matching(pool, scope_a, cond_a))
        set_b = frozenset(v["venue_id"] for v in venues_matching(pool, scope_b, cond_b))
    except Exception:
        # Fallback: multiply individual estimates
        P = len(pool)
        return _clamp(_f_at_least(len(set_a) if 'set_a' in dir() else P//2, P, K, N_A) *
                      _f_at_least(len(set_b) if 'set_b' in dir() else P//2, P, K, N_B))

    ab   = set_a & set_b
    a_only = set_a - ab
    b_only = set_b - ab
    P = len(pool)
    AB = len(ab)
    A  = len(a_only)
    B  = len(b_only)
    NI = max(0, P - A - B - AB)  # neither

    denom = comb(P, K)
    if denom == 0:
        return _CLAMP_LO

    numerator = 0
    for k_ab in range(min(AB, K) + 1):
        for k_a in range(min(A, K - k_ab) + 1):
            for k_b in range(min(B, K - k_ab - k_a) + 1):
                k_n = K - k_ab - k_a - k_b
                if k_n < 0 or k_n > NI:
                    continue
                # Check both constraints satisfied
                if k_a + k_ab < N_A:
                    continue
                if k_b + k_ab < N_B:
                    continue
                numerator += comb(AB, k_ab) * comb(A, k_a) * comb(B, k_b) * comb(NI, k_n)

    return _clamp(numerator / denom)


def _joint_two_at_most(pool: list[dict], K: int,
                       c_a: dict, c_b: dict) -> float:
    """Joint computation for two at_most constraints — mirror of at_least."""
    from scripts.generation.constraint_engine import venues_matching
    scope_a, cond_a = c_a.get("scope", "all"), c_a.get("condition") or {}
    scope_b, cond_b = c_b.get("scope", "all"), c_b.get("condition") or {}
    N_A = int(c_a["aggregation"]["at_most"])
    N_B = int(c_b["aggregation"]["at_most"])

    try:
        set_a = frozenset(v["venue_id"] for v in venues_matching(pool, scope_a, cond_a))
        set_b = frozenset(v["venue_id"] for v in venues_matching(pool, scope_b, cond_b))
    except Exception:
        P = len(pool)
        return _clamp(_f_at_most(P // 2, P, K, N_A) * _f_at_most(P // 2, P, K, N_B))

    ab   = set_a & set_b
    a_only = set_a - ab
    b_only = set_b - ab
    P = len(pool)
    AB, A, B = len(ab), len(a_only), len(b_only)
    NI = max(0, P - A - B - AB)
    denom = comb(P, K)
    if denom == 0:
        return _CLAMP_LO

    numerator = 0
    for k_ab in range(min(AB, K) + 1):
        for k_a in range(min(A, K - k_ab) + 1):
            for k_b in range(min(B, K - k_ab - k_a) + 1):
                k_n = K - k_ab - k_a - k_b
                if k_n < 0 or k_n > NI:
                    continue
                if k_a + k_ab > N_A:
                    continue
                if k_b + k_ab > N_B:
                    continue
                numerator += comb(AB, k_ab) * comb(A, k_a) * comb(B, k_b) * comb(NI, k_n)

    return _clamp(numerator / denom)


def _compute_group_joint(constraints: list[dict], group_pool: list[dict],
                          K: int, days: int) -> float:
    """
    Compute joint f for a list of constraints all targeting the same group pool.
    Uses exact multivariate hypergeometric for at_least/at_most pairs.
    Falls through to independent product for other types.
    """
    if not constraints:
        return _CLAMP_HI
    if len(constraints) == 1:
        return _estimate_single(constraints[0], group_pool, K, days)

    # Separate at_least/at_most (joint-computable) from others
    at_least_cs = [c for c in constraints
                   if isinstance(c.get("aggregation"), dict) and "at_least" in c["aggregation"]]
    at_most_cs  = [c for c in constraints
                   if isinstance(c.get("aggregation"), dict) and "at_most" in c["aggregation"]]
    other_cs    = [c for c in constraints
                   if c not in at_least_cs and c not in at_most_cs]

    f = _CLAMP_HI

    # Joint at_least pairs (two at a time via multivariate hypergeometric)
    if len(at_least_cs) == 1:
        f *= _estimate_single(at_least_cs[0], group_pool, K, days)
    elif len(at_least_cs) >= 2:
        # Pairwise: compute joint for first pair, multiply remaining independently
        f *= _joint_two_at_least(group_pool, K, at_least_cs[0], at_least_cs[1])
        for c in at_least_cs[2:]:
            f *= _estimate_single(c, group_pool, K, days)

    # Joint at_most pairs
    if len(at_most_cs) == 1:
        f *= _estimate_single(at_most_cs[0], group_pool, K, days)
    elif len(at_most_cs) >= 2:
        f *= _joint_two_at_most(group_pool, K, at_most_cs[0], at_most_cs[1])
        for c in at_most_cs[2:]:
            f *= _estimate_single(c, group_pool, K, days)

    # Other aggregation types: independent product
    for c in other_cs:
        f *= _estimate_single(c, group_pool, K, days)

    return _clamp(f)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def _compute_bxb_joint(constraints: list[dict], pool: list[dict],
                        days: int = 1) -> tuple[float, float]:
    """
    Compute joint plan-space narrowing factor for a set of B×B constraints.

    Returns (f_joint, valid_count) where:
      f_joint    : fraction of random plans satisfying all constraints
      valid_count: f_joint × C(P_food, K_food) × C(P_site, K_site)

    Only constraints with dict aggregations are considered (B×B counting types).
    String aggregations ("all", "none") are A×A — excluded here.
    """
    if not constraints or not pool:
        return _CLAMP_HI, float("inf")

    # Filter to B×B constraints only
    bxb = [c for c in constraints
           if isinstance(c.get("aggregation"), dict)]
    if not bxb:
        return _CLAMP_HI, float("inf")

    K_food  = days * 2
    K_site  = days * 2
    K_total = days * 4

    food_pool = [v for v in pool if v.get("category") in FOOD_CATS]
    site_pool = [v for v in pool if v.get("category") in SITE_CATS]

    # Assign each constraint to food / site / universal
    food_pcs = [c for c in bxb if _constraint_group(c, pool) == "food"]
    site_pcs = [c for c in bxb if _constraint_group(c, pool) == "site"]
    univ_pcs = [c for c in bxb if _constraint_group(c, pool) == "universal"]

    f_food = _compute_group_joint(food_pcs, food_pool, K_food, days) if food_pcs else _CLAMP_HI
    f_site = _compute_group_joint(site_pcs, site_pool, K_site, days) if site_pcs else _CLAMP_HI
    f_univ = _compute_group_joint(univ_pcs, pool,      K_total, days) if univ_pcs else _CLAMP_HI

    # Food and site are independent (disjoint pools). Universal treated as independent.
    f_joint = _clamp(f_food * f_site * f_univ)

    # Baseline: C(P_food, K_food) × C(P_site, K_site)
    P_food, P_site = len(food_pool), len(site_pool)
    try:
        baseline = float(comb(P_food, K_food)) * float(comb(P_site, K_site))
    except (OverflowError, ValueError):
        baseline = float("inf")

    valid_count = f_joint * baseline
    return f_joint, valid_count
