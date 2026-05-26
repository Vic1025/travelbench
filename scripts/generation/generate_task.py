"""
scripts/generation/generate_task.py

B4 — Task generation agent.

Takes a city + seasonal window + venue pool and generates benchmark tasks.
Each task has:
  - public_input: natural language query (never states derived constraints)
  - rubric: F/P/B constraints derived from persona signals
  - window_id, structural_type, difficulty

The agent selects persona signals from the taxonomy, derives constraints,
writes the public_input in natural language, and outputs a complete task JSON.

Usage:
  python scripts/generation/generate_task.py \\
    --city london --window london_carnival_2026 \\
    --count 3 --api-key KEY

  python scripts/generation/generate_task.py \\
    --city london --window london_carnival_2026 \\
    --dry-run --count 2
"""

import json
import argparse
import sys
import random
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.pool_utils import (
    load_venue_pool as _load_venue_pool_impl,
    load_unavailable_dates as _load_unavailable_dates_impl,
    _apply_pool_filters as _apply_pool_filters_impl,
)
from scripts.generation.populate_seasonal_windows import get_window, get_windows

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# ─────────────────────────────────────────────────────────────────────────────
# P7 — Feasibility constants (type 2 time-ceiling, type 4 budget-ceiling)
# ─────────────────────────────────────────────────────────────────────────────
#
# Type 2 uses K-nearest venues; minimum time = K × 0.5 × avg_visit_duration +
# nearest-neighbour travel between them. Stated time ceiling must be within
# [RATIO_MIN, RATIO_MAX] × minimum time — too tight = infeasible, too loose =
# no real selection pressure.
#
# Type 4 uses cheapest K restaurants + K sites per day; stated budget must be
# within the same ratio band × that minimum.
#
VISIT_DURATION_FRACTION = 0.5      # fraction of recommended_visit_minutes used in min-time estimate
FEASIBILITY_RATIO_MIN   = 1.1      # stated ceiling must be ≥ 1.1 × minimum (not impossibly tight)
FEASIBILITY_RATIO_MAX   = 1.3      # stated ceiling must be ≤ 1.3 × minimum (real pressure, not trivial)
SINGLE_DAY_CAP_MINUTES  = 10 * 60  # 1 day of awake/schedulable time

# Type 4 per-day venue quota used in the cheapest-combo floor calculation
RESTAURANTS_PER_DAY = 2
SITES_PER_DAY       = 2
_SITE_CATEGORIES    = {"museum", "attraction", "park", "neighbourhood"}

# Premium-venue detection: required venues with avg_cost_local above
# PREMIUM_COST_FACTOR × category median count as "expensive" and their cost
# gets added to the budget floor.
PREMIUM_COST_FACTOR = 1.5


# ─────────────────────────────────────────────────────────────────────────────
# VENUE POOL LOADER
# ─────────────────────────────────────────────────────────────────────────────

def load_venue_pool(city: str, db_path: Path = None) -> list[dict]:
    """Load venue briefs — delegates to pool_utils."""
    return _load_venue_pool_impl(city, db_path=db_path or get_city_db_path(city))


def load_unavailable_dates(city: str, window_dates: list[str],
                            db_path: Path = None) -> dict[str, list[str]]:
    """Return sold-out dates per venue — delegates to pool_utils."""
    return _load_unavailable_dates_impl(city, window_dates, db_path=db_path or get_city_db_path(city))


# ─────────────────────────────────────────────────────────────────────────────
# TASK GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def _stub_task(city: str, window: dict, venue_pool: list,
               task_index: int) -> dict:
    """Generate a deterministic stub task for dry-run / testing."""
    window_dates = window.get("dates", ["2026-08-22"])
    start_date   = window_dates[0]
    d            = date.fromisoformat(start_date)
    dow_map      = ["mon","tue","wed","thu","fri","sat","sun"]
    dow          = dow_map[d.weekday()]

    # Pick a couple of venues from pool
    food_venues = [v for v in venue_pool if v["category"] in ("restaurant","cafe")]
    sight_venues = [v for v in venue_pool if v["category"] in ("museum","attraction","park")]

    stub_id = f"{city[:3]}_{window['window_id'].split('_')[1][:6]}_{task_index+1:03d}"

    p_constraints = []
    if food_venues:
        p_constraints.append({
            "id": "p_001",
            "score_tier": "P",
            "hop": 1,
            "pattern": "local_cuisine_preference",
            "check_method": "code",
            "source_in_profile": "want to eat like a local",
            "description": "At least 60% of meals at local cuisine venues",
            "params": {"min_ratio": 0.6}
        })

    return {
        "task_id":       stub_id,
        "city":          city,
        "window_id":     window["window_id"],
        "days":          2,
        "start_date":    start_date,
        "start_day_of_week": dow,
        "difficulty":    "medium",
        "structural_type": "type1_hidden_requirements",
        "structural_type_secondary": None,
        "public_input": {
            "query": f"Two days in {city.title()} during {window['label']}. "
                     f"Looking for a mix of local food and cultural highlights.",
            "user_profile": "Traveller who wants an authentic local experience. "
                            "Interested in local cuisine and culture."
        },
        "rubric": {
            "required_venue_ids": [],
            "hard_constraints": [
                {"id":"hc_001","type":"hours_check","check_method":"code","params":{}},
                {"id":"hc_002","type":"no_overlap","check_method":"code","params":{}},
                {"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{}},
            ],
            "personal_constraints": p_constraints,
            "b_score_constraints": []
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# SOLVABILITY VERIFICATION (B4.5 — lightweight pre-check)
# ─────────────────────────────────────────────────────────────────────────────

def _apply_pool_filters(venue_pool: list, task: dict) -> list:
    """Apply hard + P-score filters — delegates to pool_utils.
    Returns universal_pool (only universal/exclusion constraints applied).
    Inclusion and B×B counting constraints are NOT pool filters.
    """
    return _apply_pool_filters_impl(venue_pool, task)


def _build_scope_pools(venue_pool: list, task: dict) -> dict:
    """
    Build the three scope-aware pools for solvability checking.

    Returns:
        {
          "universal":   list[venue]   — pool after all universal (agg="all"/"none") constraints
          "scoped":      {pc_id: list[venue]}  — per scoped constraint candidate sets
          "inclusion":   {pc_id: (list[venue], min_count)}  — per inclusion constraint sets
        }

    Classification rules (from VALIDATION_DESIGN):
      universal:  agg == "all" OR "none"  — narrows the whole-plan pool
      scoped:     agg == "all" + non-trivial scope (activity_type, category, time_window)
      inclusion:  agg == {at_least: N} or other counting agg — does NOT narrow universal pool
    Bucket C pattern constraints are skipped (handled by their own handlers).
    """
    from scripts.generation.constraint_engine import venues_matching

    pcs = task.get("rubric", {}).get("personal_constraints", [])

    # ── Step 1: build universal_pool ─────────────────────────────────────────
    # Apply hard constraints first (delegated to pool_utils)
    universal_pool = _apply_pool_filters_impl(venue_pool, task)

    # ── Step 2: build scoped_pools ────────────────────────────────────────────
    scoped_pools: dict[str, list] = {}
    for c in pcs:
        if "scope" not in c:
            continue   # Bucket C
        agg   = c.get("aggregation", "all")
        scope = c.get("scope", "all")
        cond  = c.get("condition", {})
        pc_id = c.get("id", "?")
        if agg != "all":
            continue   # not a universal/scoped filter
        # Distinguish truly universal (scope="all") vs scoped (non-trivial scope)
        scope_is_trivial = (
            scope == "all" or
            scope == "activity_type=any" or   # equivalent to "all" scope
            (isinstance(scope, str) and scope in ("per_day",)) or
            (isinstance(scope, str) and scope.startswith("time_window=")) or
            (isinstance(scope, str) and scope.startswith("venue_id=")) or
            (isinstance(scope, str) and scope.startswith("venue_name~"))
        )
        if not scope_is_trivial:
            # This is a scoped constraint — build its candidate set within universal_pool
            try:
                scoped_pools[pc_id] = venues_matching(universal_pool, scope, cond)
            except Exception:
                scoped_pools[pc_id] = []

    # ── Step 3: build inclusion_pools ────────────────────────────────────────
    # inclusion_pools: {pc_id: (representative_venues, min_count)}
    # Covers at_least, ratio (existence only), count_distinct (distinct-value reps)
    inclusion_pools: dict[str, tuple] = {}
    for c in pcs:
        if "scope" not in c:
            continue   # Bucket C
        agg   = c.get("aggregation", {})
        scope = c.get("scope", "all")
        cond  = c.get("condition", {})
        pc_id = c.get("id", "?")

        if not isinstance(agg, dict):
            continue

        # at_least: need >= N qualifying venues
        if "at_least" in agg:
            min_n = agg["at_least"]
            try:
                matched = venues_matching(universal_pool, scope, cond)
                inclusion_pools[pc_id] = (matched, min_n)
            except Exception:
                inclusion_pools[pc_id] = ([], min_n)

        # ratio: need >= 1 qualifying venue (existence; agent selects ratio at plan time)
        elif "ratio" in agg:
            try:
                matched = venues_matching(universal_pool, scope, cond)
                inclusion_pools[pc_id] = (matched, 1)
            except Exception:
                inclusion_pools[pc_id] = ([], 1)

        # count_distinct: need >= N distinct values of field across qualifying venues.
        # Build one representative venue per distinct value; lower bar len >= N catches gaps.
        elif "count_distinct" in agg:
            n_distinct = agg["count_distinct"]
            field      = agg.get("field", "")
            try:
                matched = venues_matching(universal_pool, scope, cond)
                seen: dict = {}
                for v in matched:
                    val = v.get(field)
                    if val is not None and val not in seen:
                        seen[val] = v
                inclusion_pools[pc_id] = (list(seen.values()), n_distinct, "count_distinct")
            except Exception:
                inclusion_pools[pc_id] = ([], n_distinct, "count_distinct")

    return {
        "universal":  universal_pool,
        "scoped":     scoped_pools,
        "inclusion":  inclusion_pools,
    }


def _verify_task_solvable(task: dict, venue_pool: list) -> tuple[bool, str]:
    """
    Solvability check with diagnostic messages.

    Returns (is_solvable, reason).
    On failure, reason explains the specific problem and suggests a fix.

    Pipeline (post-Phase 4 scope-aware model):
      1. Build three pools (universal / scoped / inclusion)
      2. A6: pool-size-max for type 3 only
      3. Lower bars: viable schedule, scoped ≥1, inclusion ≥ min_count
      4. Upper bars: 50/50/25 (type 1/3/6) or 25/25/10 (type 5); types 2/4 exempt
      5. B×B plan-space checks (types 1, 3, 5)
      6. Type 5 OR verdict (A×A bars OR B×B tightness)
      7. Required venue availability
      8. P7 type2 time feasibility / type4 budget feasibility
    """
    FOOD_CATS = {"restaurant", "cafe", "bar"}
    SITE_CATS = {"museum", "attraction", "park", "neighbourhood"}

    structural = str(task.get("structural_type", "") or "")
    days       = task.get("days", 1)
    qr         = task.get("public_input", {}).get("query_resources", {}) or {}
    is_type2   = "type2" in structural
    is_type4   = "type4" in structural
    is_type5   = "type5" in structural
    is_type3   = "type3" in structural
    is_type1   = "type1" in structural
    is_type6   = "type6" in structural
    bars_exempt = is_type2 or is_type4   # no pool bar checks for 2/4

    # ── 1. Build three pools ─────────────────────────────────────────────────
    pools      = _build_scope_pools(venue_pool, task)
    universal  = pools["universal"]
    scoped     = pools["scoped"]    # {pc_id: [venue, ...]}
    inclusion  = pools["inclusion"] # {pc_id: ([venue, ...], min_count)}

    # Build pc lookup used by diagnostics throughout this function
    pcs      = task.get("rubric", {}).get("personal_constraints", [])
    pc_by_id = {c.get("id", "?"): c for c in pcs if isinstance(c, dict)}

    if not universal:
        # Give a hint about which universal constraint most likely caused the wipeout
        pcs_uni = [c for c in pcs
                   if "scope" in c and c.get("aggregation") in ("all","none")]
        hint = ""
        if pcs_uni:
            ids = ", ".join(f"[{c.get('id','?')}]" for c in pcs_uni)
            hint = f" Universal constraints {ids} are pool filters — one of them eliminates every venue."
        return False, (
            f"Constraints eliminate all venues from pool.{hint} "
            f"Check each universal (scope='all', aggregation='all'/'none') constraint individually "
            f"with query_pool to find which one wipes the pool."
        )

    # Pre-compute group sizes used by both the upper bars and the type5 OR check
    orig_food = sum(1 for v in venue_pool if v.get("category") in FOOD_CATS)
    orig_site = sum(1 for v in venue_pool if v.get("category") in SITE_CATS)

    # ── 2. Pool-size-max: only type 5 ────────────────────────────────────────
    # Type 3 does NOT have a pool-size-max. Its difficulty comes from competing
    # inclusion constraints (B×B tension f ≤ 10%), not from narrow pools.
    # The design spec says: "Always satisfiable if both venue types exist."

    def _diagnose_zero_venues(pc_id: str, scope, venue_pool_full: list) -> str:
        """
        Return a one-line diagnostic explaining WHY a constraint has 0 survivors.
        Checks: (1) tag doesn't exist in pool at all, (2) tag exists but only in
        the wrong category group for this scope, (3) condition too strict for scope.
        Falls back to empty string if constraint has no has_tag condition.
        """
        from scripts.generation.constraint_engine import venues_matching as _vm
        c = pc_by_id.get(pc_id, {})
        cond = c.get("condition", {})

        # Extract has_tag from condition (handles nested any/all)
        def _find_tag(d):
            if not isinstance(d, dict): return None
            if "has_tag" in d: return d["has_tag"]
            for sub in d.get("all", []) + d.get("any", []):
                t = _find_tag(sub)
                if t: return t
            return None

        tag = _find_tag(cond)
        if not tag:
            return ""  # no tag to diagnose — condition is field-based

        FOOD = {"restaurant", "cafe", "bar"}
        SITE = {"museum", "attraction", "park", "neighbourhood"}

        # Normalise tag for lookup (matches P1 canonical form)
        def _nt(s): return s.lower().replace("_", "-").replace(" ", "-")
        tag_n = _nt(tag)

        # Check how many venues carry this tag across the FULL pool
        tag_venues = [v for v in venue_pool_full
                      if tag_n in {_nt(t) for t in v.get("tags", [])}]
        if not tag_venues:
            return f"Tag '{tag}' doesn't exist anywhere in this pool — pick a tag from query_pool results."

        n_food = sum(1 for v in tag_venues if v.get("category") in FOOD)
        n_site = sum(1 for v in tag_venues if v.get("category") in SITE)
        n_total = len(tag_venues)

        # Determine what group the scope targets
        sc_str = scope if isinstance(scope, str) else str(scope)
        scope_targets_food = (
            sc_str.startswith("activity_type=meal") or
            sc_str.startswith("category=restaurant") or
            sc_str.startswith("category=cafe") or
            sc_str.startswith("category=bar")
        )
        scope_targets_site = (
            sc_str.startswith("activity_type=visit") or
            sc_str.startswith("category=museum") or
            sc_str.startswith("category=attraction") or
            sc_str.startswith("category=park") or
            sc_str.startswith("category=neighbourhood")
        )

        if scope_targets_food and n_food == 0:
            return (f"Tag '{tag}' exists on {n_total} venue(s) in the pool "
                    f"(all site: {n_site} museum/attraction/park) but scope '{sc_str}' "
                    f"targets food venues — none of those carry this tag. "
                    f"Either use a food tag (check query_pool) or change scope.")
        if scope_targets_site and n_site == 0:
            return (f"Tag '{tag}' exists on {n_total} venue(s) in the pool "
                    f"(all food: {n_food} restaurant/cafe/bar) but scope '{sc_str}' "
                    f"targets site venues — none carry this tag. "
                    f"Either use a site tag (check query_pool) or change scope.")

        # Tag exists in matching category group but still 0 after universal constraints
        return (f"Tag '{tag}' exists on {n_total} venue(s) total "
                f"(food:{n_food}, site:{n_site}), but 0 survive after "
                f"your other universal constraints — your filters are too restrictive in combination.")

    if not bars_exempt:
        # ── 3. Lower bars ────────────────────────────────────────────────────

        # 3a. Viable-schedule lower bar on universal_pool
        food_in_uni = [v for v in universal if v.get("category") in FOOD_CATS]
        site_in_uni = [v for v in universal if v.get("category") in SITE_CATS]
        if len(food_in_uni) < 2 * days:
            return False, (
                f"Viable-schedule lower bar: universal constraints leave only "
                f"{len(food_in_uni)} food venues (restaurant/cafe/bar) — need "
                f"≥{2 * days} (2 per day × {days} day(s)). "
                f"Loosen a universal constraint or reduce days."
            )
        if len(site_in_uni) < days:
            n = len(site_in_uni)
            return False, (
                f"Viable-schedule lower bar: universal constraints leave only "
                f"{n} site {'venue' if n == 1 else 'venues'} (museum/attraction/park/neighbourhood) "
                f"— need ≥{days} (1 per day × {days} day(s)). "
                f"Loosen a universal constraint or reduce days."
            )

        # 3b. Scoped lower bar: each scoped_pool ≥ 1 (≥2 for type 3)
        for pc_id, svenues in scoped.items():
            min_scoped = 2 if is_type3 else 1
            if len(svenues) < min_scoped:
                c = pc_by_id.get(pc_id, {})
                scope = c.get("scope", "?")
                diag = _diagnose_zero_venues(pc_id, scope, venue_pool)
                if diag:
                    return False, (
                        f"Scoped constraint [{pc_id}] leaves only {len(svenues)} "
                        f"candidate venues (need ≥{min_scoped}). {diag}"
                    )
                return False, (
                    f"Scoped constraint [{pc_id}] leaves only {len(svenues)} "
                    f"candidate venues (need ≥{min_scoped}). "
                    f"Loosen the condition or broaden the scope."
                )

        # 3c. Inclusion lower bar: each inclusion_pool ≥ min_count
        for pc_id, _ie in inclusion.items():
            ivenues, min_n = _ie[0], _ie[1]
            _agg_type = _ie[2] if len(_ie) > 2 else "at_least"
            if len(ivenues) < min_n:
                c = pc_by_id.get(pc_id, {})
                scope = c.get("scope", "all")
                agg   = c.get("aggregation", {})
                diag  = _diagnose_zero_venues(pc_id, scope, venue_pool)

                # count_distinct-specific guidance: 0 distinct values = field likely missing
                if _agg_type == "count_distinct":
                    field = agg.get("field", "?") if isinstance(agg, dict) else "?"
                    _VALID_CD_FIELDS = (
                        "district, category, price_tier, traffic_tier, "
                        "recommended_pace, cuisine"
                    )
                    _cd_hint = (
                        f"count_distinct on field='{field}' found {len(ivenues)} distinct "
                        f"values in the pool (need {min_n}). "
                        + (
                            f"Field '{field}' does not exist as a structured venue attribute — "
                            f"it returned 0 distinct values. "
                            f"Valid fields for count_distinct: {_VALID_CD_FIELDS}."
                            if len(ivenues) == 0 else
                            f"Only {len(ivenues)} distinct value(s) of '{field}' exist in the "
                            f"scoped pool — cannot satisfy count_distinct: {min_n}. "
                            f"Lower the threshold or use a field with more distinct values "
                            f"(e.g. district has 8+ values). "
                            f"Alternatively, replace with at_least + a specific tag condition."
                        )
                    )
                    return False, f"Pool gap: [{pc_id}] {_cd_hint}"

                if diag:
                    return False, (
                        f"Pool gap: inclusion constraint [{pc_id}] needs {min_n} "
                        f"qualifying venues, pool has {len(ivenues)}. {diag}"
                    )
                return False, (
                    f"Pool gap: inclusion constraint [{pc_id}] needs {min_n} "
                    f"qualifying venues, pool has {len(ivenues)} after universal "
                    f"constraints. Reduce min_count or loosen a universal filter."
                )

        # ── 4. Upper bars (meaningfulness) ───────────────────────────────────
        # type1: 75/75/50 — cascading constraints that improve the plan; no need for
        #   aggressive narrowing, but must do SOMETHING to the pool.
        # type3/6: 50/50/25 — genuine competing pressure required.
        # type5: 25/25/10 — extreme narrowing is the explicit point.
        if is_type5:
            UNIV_THRESHOLD  = 0.40
            SCOPE_THRESHOLD = 0.40
            INCL_THRESHOLD  = 0.20
        elif is_type1:
            UNIV_THRESHOLD  = 0.75
            SCOPE_THRESHOLD = 0.75
            INCL_THRESHOLD  = 0.50
        elif is_type3:
            # Type 3 uses inclusion constraints for competing demands.
            # Each side can qualify up to 60% of venues — the difficulty
            # comes from fitting BOTH sides into limited schedule slots
            # (verified by B×B tension f ≤ 10%, not by narrow pools).
            UNIV_THRESHOLD  = 0.75
            SCOPE_THRESHOLD = 0.75
            INCL_THRESHOLD  = 0.60
        else:
            UNIV_THRESHOLD  = 0.50
            SCOPE_THRESHOLD = 0.50
            INCL_THRESHOLD  = 0.25

        # 4a. Universal cumulative upper bar — only when universal constraints present
        # "Universal" = scope is truly pool-wide ("all" or "activity_type=any").
        # Scoped agg="all" constraints (e.g. scope="category=museum") are in scoped_pools
        # and don't touch the global food/site pool — exclude them here.
        _TRIVIAL_SCOPES = {"all", "activity_type=any", None}
        has_universal = any(
            "scope" in c
            and c.get("aggregation") in ("all", "none")
            and (c.get("scope") in _TRIVIAL_SCOPES
                 or (isinstance(c.get("scope"), str)
                     and (c["scope"].startswith("venue_id=")
                          or c["scope"].startswith("venue_name~"))))
            for c in task.get("rubric", {}).get("personal_constraints", [])
        )
        if has_universal and orig_food > 0:
            food_ratio = len(food_in_uni) / orig_food
            if food_ratio > UNIV_THRESHOLD:
                return False, (
                    f"Universal upper bar: your constraints leave {len(food_in_uni)}/{orig_food} "
                    f"food venues ({food_ratio:.0%}) — need ≤{UNIV_THRESHOLD:.0%} "
                    f"(≤{int(orig_food * UNIV_THRESHOLD)} food venues). "
                    f"To reduce this count, add a scope='all' + aggregation='all'|'none' constraint "
                    f"that targets a food-venue property (tag, price_tier, noise_level, district). "
                    f"IMPORTANT: scope='category=restaurant' or scope='activity_type=meal' constraints "
                    f"do NOT reduce the universal food count — they go into the scoped pool. "
                    f"Only scope='all' constraints are universal pool filters. "
                    f"Use query_pool with food filters and check the 'food' count until it's ≤{int(orig_food * UNIV_THRESHOLD)}. "
                    f"Alternative: reduce 'days' — with {days} days you need ≥{2*days} food venues surviving; "
                    f"fewer days gives more room (e.g. 3 days only needs ≥6)."
                )
        if has_universal and orig_site > 0:
            site_ratio = len(site_in_uni) / orig_site
            if site_ratio > UNIV_THRESHOLD:
                return False, (
                    f"Universal upper bar: your constraints leave {len(site_in_uni)}/{orig_site} "
                    f"site venues ({site_ratio:.0%}) — need ≤{UNIV_THRESHOLD:.0%} "
                    f"(≤{int(orig_site * UNIV_THRESHOLD)} site venues). "
                    f"To reduce this count, add a scope='all' + aggregation='all'|'none' constraint "
                    f"that targets a site-venue property (district, traffic_tier, tag). "
                    f"IMPORTANT: scope='category=museum' or scope='activity_type=visit' constraints "
                    f"do NOT reduce the universal site count — they go into the scoped pool. "
                    f"Only scope='all' constraints are universal pool filters. "
                    f"Use query_pool with site filters and check the 'site' count until it's ≤{int(orig_site * UNIV_THRESHOLD)}. "
                    f"Alternative: reduce 'days' — with {days} days you need ≥{2*days} food venues surviving; "
                    f"fewer days leaves more margin (e.g. 4 days only needs ≥8 food)."
                )

        # 4b. Scoped upper bar (per scoped constraint)
        for pc_id, svenues in scoped.items():
            food_in_scope = sum(1 for v in svenues if v.get("category") in FOOD_CATS)
            site_in_scope = sum(1 for v in svenues if v.get("category") in SITE_CATS)
            if food_in_scope >= site_in_scope:
                scope_total, group, n_scoped = len(food_in_uni), "food", food_in_scope
            else:
                scope_total, group, n_scoped = len(site_in_uni), "site", site_in_scope
            if scope_total > 0 and n_scoped > 0:
                ratio = n_scoped / scope_total
                if ratio > SCOPE_THRESHOLD:
                    return False, (
                        f"Scoped upper bar [{pc_id}]: {ratio:.0%} of {group} venues "
                        f"({n_scoped}/{scope_total}) match this constraint's condition — "
                        f"need ≤{SCOPE_THRESHOLD:.0%} for real selection pressure. "
                        f"The condition is too broad; tighten it (e.g. stricter tag, add price or tier filter)."
                    )

        # 4c. Inclusion upper bar (per inclusion constraint)
        # Skip count_distinct and ratio — their "qualifying venues" fraction is not a
        # selection-pressure signal. count_distinct pressure comes from needing N *distinct*
        # values (geometry, not filter); ratio pressure comes from the percentage target.
        _count_distinct_ids = {
            c.get("id", "?") for c in pcs
            if isinstance(c.get("aggregation"), dict)
            and ("count_distinct" in c.get("aggregation", {})
                 or "ratio" in c.get("aggregation", {}))
        }
        for pc_id, _ie in inclusion.items():
            ivenues, min_n = _ie[0], _ie[1]
            if pc_id in _count_distinct_ids:
                continue   # upper bar not meaningful for these aggregation types
            food_qualifying = sum(1 for v in ivenues if v.get("category") in FOOD_CATS)
            site_qualifying = sum(1 for v in ivenues if v.get("category") in SITE_CATS)
            if food_qualifying >= site_qualifying:
                orig_group, group, n_qualifying = orig_food, "food", food_qualifying
            else:
                orig_group, group, n_qualifying = orig_site, "site", site_qualifying
            if orig_group > 0 and n_qualifying > 0:
                ratio = n_qualifying / orig_group
                if ratio > INCL_THRESHOLD:
                    return False, (
                        f"Inclusion upper bar [{pc_id}]: {n_qualifying}/{orig_group} {group} venues "
                        f"({ratio:.0%}) already satisfy this inclusion constraint — "
                        f"need ≤{INCL_THRESHOLD:.0%} so the constraint is genuinely selective. "
                        f"Use a rarer tag or a stricter condition so only a small fraction of venues qualify."
                    )

    # ── 5. B×B plan-space checks (types 1, 3, 5 only) ───────────────────────
    _bxb_tight = False   # set True if B×B confirms type 5 tightness (f <= 2%)
    if not bars_exempt and not is_type6:
        bxb_pcs = [c for c in pcs
                   if "scope" in c and isinstance(c.get("aggregation"), dict)]
        if len(bxb_pcs) >= 2:
            try:
                from scripts.generation.constraint_engine_bxb import _compute_bxb_joint
                f_bxb, vc_bxb = _compute_bxb_joint(bxb_pcs, universal, days)
                solvability_floor = days * 4  # 2 food + 2 site slots per day

                if is_type1 and f_bxb <= 0.05 and vc_bxb < solvability_floor:
                    return False, (
                        f"Counting constraints too competitive for type 1: your stacked "
                        f"at_least/ratio/at_most constraints leave only ~{vc_bxb:.0f} valid plans "
                        f"(need ≥{solvability_floor} for a solvable schedule). "
                        f"This level of plan-space competition belongs in type 3 (competing requirements), "
                        f"not type 1. Either reclassify as type 3, or remove one counting constraint "
                        f"to give the solving agent more room."
                    )
                if is_type5:
                    _bxb_tight = (f_bxb <= 0.04)
            except Exception:
                pass  # B×B estimation failure is non-fatal

    # ── 6. Type 5 OR verdict ─────────────────────────────────────────────────
    # A×A bars (≤40% food/site surviving) OR B×B tightness (f≤4%) must confirm
    # extreme narrowing. orig_food/orig_site were computed before the bars block.
    if is_type5 and not bars_exempt:
        uni_food = sum(1 for v in universal if v.get("category") in FOOD_CATS)
        uni_site = sum(1 for v in universal if v.get("category") in SITE_CATS)
        _axa_tight = (
            (orig_food > 0 and uni_food / orig_food <= 0.25) or
            (orig_site > 0 and uni_site / orig_site <= 0.25)
        )
        if not _axa_tight and not _bxb_tight:
            food_pct = f"{100*uni_food//orig_food}%" if orig_food > 0 else "n/a"
            site_pct = f"{100*uni_site//orig_site}%" if orig_site > 0 else "n/a"
            return False, (
                f"Type 5 requires extreme narrowing but your constraints are not tight enough: "
                f"food={uni_food}/{orig_food} ({food_pct}) and site={uni_site}/{orig_site} ({site_pct}) "
                f"survive — both need ≤25%. "
                f"Stack 2-3 filters (tag + district, or regulation + tier) until query_pool shows "
                f"≤{orig_food//4} food and ≤{orig_site//4} site venues surviving."
            )

    # ── 7. Required venue availability check ─────────────────────────────────
    req_vids = task.get("rubric", {}).get("required_venue_ids", [])
    if req_vids:
        pool_map = {v["venue_id"]: v for v in venue_pool}
        for vid in req_vids:
            if vid not in pool_map:
                return False, f"required_venue_id '{vid}' not in pool."

    # ── 8. P7 schedule / budget feasibility ──────────────────────────────────
    if is_type2 and universal:
        stated_ceiling = qr.get("time_ceiling_minutes")
        # P6-T4b: ceiling_mode + ceiling_scope. The feasibility comparison
        # below treats the ceiling as a TOTAL across the trip. For per_day
        # scope (only meaningful with spread mode on a multi-day trip) the
        # effective total = per-day ceiling × days. Contiguous-mode ceilings
        # already represent a single block on a single day → total identical.
        ceiling_mode  = qr.get("ceiling_mode",  "contiguous")
        ceiling_scope = qr.get("ceiling_scope", "total")
        if stated_ceiling is not None:
            effective_total = stated_ceiling
            if ceiling_scope == "per_day":
                effective_total = stated_ceiling * max(days, 1)
            cap_total = SINGLE_DAY_CAP_MINUTES * days
            if effective_total > cap_total:
                return False, (
                    f"Type 2 time_ceiling_minutes={stated_ceiling} "
                    f"(scope={ceiling_scope}; effective total {effective_total}min) "
                    f"exceeds {days}-day cap of {cap_total}min "
                    f"({SINGLE_DAY_CAP_MINUTES}min/day). "
                    f"Reduce the stated ceiling or add days."
                )

            k_target = days * 2
            for c in task.get("rubric", {}).get("personal_constraints", []):
                agg = c.get("aggregation", {})
                if isinstance(agg, dict) and "at_least" in agg:
                    # scope=per_day means N venues *per day* → N × days total
                    scope = c.get("scope", "all")
                    n_required = agg["at_least"]
                    if isinstance(scope, str) and scope == "per_day":
                        n_required *= max(days, 1)
                    k_target = max(k_target, n_required)

            venues_with_coords = [
                v for v in universal
                if v.get("lat") is not None and v.get("lng") is not None
            ]
            coverage = len(venues_with_coords) / max(len(universal), 1)
            did_geom_check = False

            if coverage >= 0.5 and len(venues_with_coords) >= k_target:
                try:
                    from scripts.generation.build_travel_matrix import haversine_km, estimate_minutes
                    clat = sum(v["lat"] for v in venues_with_coords) / len(venues_with_coords)
                    clng = sum(v["lng"] for v in venues_with_coords) / len(venues_with_coords)
                    by_dist = sorted(
                        venues_with_coords,
                        key=lambda v: haversine_km(clat, clng, v["lat"], v["lng"])
                    )
                    nearest_k = by_dist[:k_target]
                    total_travel = sum(
                        estimate_minutes(
                            haversine_km(nearest_k[i]["lat"], nearest_k[i]["lng"],
                                         nearest_k[i+1]["lat"], nearest_k[i+1]["lng"]) * 1.3,
                            20.0
                        )
                        for i in range(len(nearest_k) - 1)
                    )
                    half_visit_sum = sum(
                        VISIT_DURATION_FRACTION * v.get("recommended_visit_minutes", 75)
                        for v in nearest_k
                    )
                    min_time = half_visit_sum + total_travel
                    floor    = min_time * FEASIBILITY_RATIO_MIN
                    cap_r    = min_time * FEASIBILITY_RATIO_MAX

                    if effective_total < floor:
                        return False, (
                            f"Type 2 time ceiling infeasible: min schedule for {k_target} "
                            f"venues needs ~{min_time:.0f}min "
                            f"(half-visits={half_visit_sum:.0f}, travel={total_travel:.0f}), "
                            f"effective total {effective_total}min < 1.1× min ({floor:.0f}min). "
                            f"Increase time_ceiling_minutes or reduce at_least count."
                        )
                    if effective_total > cap_r:
                        ratio = effective_total / min_time if min_time > 0 else 0.0
                        return False, (
                            f"Type 2 time ceiling too loose: effective total "
                            f"{effective_total}min is {ratio:.2f}× the minimum schedule "
                            f"({min_time:.0f}min); selection pressure requires 1.1–1.3×. "
                            f"Tighten time_ceiling_minutes to ~{floor:.0f}–{cap_r:.0f}min."
                        )
                    did_geom_check = True
                except ImportError:
                    pass

            if not did_geom_check:
                k_slice   = universal[:k_target]
                avg_visit = sum(v.get("recommended_visit_minutes", 75) for v in k_slice) / max(len(k_slice), 1)
                needed    = k_target * (VISIT_DURATION_FRACTION * avg_visit + 15)
                if needed > effective_total:
                    return False, (
                        f"Type 2 schedule too tight (coord-free fallback): need ~{needed:.0f}min, "
                        f"effective total {effective_total}min."
                    )

            # ── P6-T5: Scoped KNN ────────────────────────────────────────────
            # Each at_least constraint with a non-trivial subset scope must
            # also pass the geometry check against its OWN scoped pool. The
            # universal-pool check above can miss a tight scoped sub-bar
            # (e.g. at_least: 4 scope=category=museum with only 5 museums in
            # the universal pool, some of them distant). The most-constrained
            # scoped subset determines feasibility.
            from scripts.generation.constraint_engine import venues_matching as _vm
            try:
                from scripts.generation.build_travel_matrix import (
                    haversine_km as _hk, estimate_minutes as _em,
                )
                _have_matrix = True
            except ImportError:
                _have_matrix = False

            if _have_matrix:
                for c in task.get("rubric", {}).get("personal_constraints", []):
                    agg_c = c.get("aggregation", {})
                    if not (isinstance(agg_c, dict) and "at_least" in agg_c):
                        continue
                    scope_c = c.get("scope", "all")
                    # Trivially-universal scopes — covered by universal check.
                    if not isinstance(scope_c, str) or scope_c in ("all", "activity_type=any"):
                        continue
                    # Scopes that aren't venue-subset filters — skip.
                    if scope_c.startswith("time_window=") or scope_c.startswith("venue_id="):
                        continue
                    try:
                        scoped_full = _vm(universal, scope_c, c.get("condition", {}))
                    except Exception:
                        continue
                    scoped_coords = [v for v in scoped_full
                                     if v.get("lat") is not None and v.get("lng") is not None]
                    n_required_c = agg_c["at_least"]
                    if scope_c == "per_day":
                        n_required_c *= max(days, 1)
                    if len(scoped_coords) < n_required_c:
                        # Existing inclusion lower-bar already reported this.
                        continue
                    clat_c = sum(v["lat"] for v in scoped_coords) / len(scoped_coords)
                    clng_c = sum(v["lng"] for v in scoped_coords) / len(scoped_coords)
                    by_dist_c = sorted(
                        scoped_coords,
                        key=lambda v: _hk(clat_c, clng_c, v["lat"], v["lng"])
                    )
                    nearest_c = by_dist_c[:n_required_c]
                    travel_c = sum(
                        _em(
                            _hk(nearest_c[i]["lat"], nearest_c[i]["lng"],
                                nearest_c[i+1]["lat"], nearest_c[i+1]["lng"]) * 1.3,
                            20.0
                        )
                        for i in range(len(nearest_c) - 1)
                    )
                    half_c = sum(
                        VISIT_DURATION_FRACTION * v.get("recommended_visit_minutes", 75)
                        for v in nearest_c
                    )
                    min_time_c = half_c + travel_c
                    floor_c    = min_time_c * FEASIBILITY_RATIO_MIN
                    pc_id_c    = c.get("id", "?")
                    if stated_ceiling < floor_c:
                        return False, (
                            f"Type 2 scoped geometry [{pc_id_c}] infeasible: "
                            f"{n_required_c} venues in scope '{scope_c}' need "
                            f"~{min_time_c:.0f}min (half-visits={half_c:.0f}, "
                            f"travel={travel_c:.0f}); stated ceiling "
                            f"{stated_ceiling}min < 1.1× scoped min ({floor_c:.0f}min). "
                            f"Increase time_ceiling_minutes or relax the scoped "
                            f"at_least count."
                        )

    elif is_type4 and universal:
        from scripts.generation.pool_utils import get_city_currency, format_money
        city = task.get("city", "") or ""
        stated_budget = qr.get("budget_per_day")
        if stated_budget is not None:
            restaurants = sorted(
                [v for v in universal if v.get("category") == "restaurant"],
                key=lambda v: v.get("avg_cost_local") if v.get("avg_cost_local") is not None else float("inf")
            )
            sites = sorted(
                [v for v in universal if v.get("category") in _SITE_CATEGORIES],
                key=lambda v: v.get("avg_cost_local") if v.get("avg_cost_local") is not None else float("inf")
            )
            k_r = RESTAURANTS_PER_DAY * days
            k_s = SITES_PER_DAY * days

            if len(restaurants) < k_r:
                return False, (
                    f"Type 4 pool gap: need {k_r} restaurants within your constraints, "
                    f"but only {len(restaurants)} remain. Loosen a constraint or reduce days."
                )
            if len(sites) < k_s:
                return False, (
                    f"Type 4 pool gap: need {k_s} site venues within your constraints, "
                    f"but only {len(sites)} remain. Loosen a constraint or reduce days."
                )

            base_min   = (sum((v.get("avg_cost_local") or 0) for v in restaurants[:k_r]) +
                          sum((v.get("avg_cost_local") or 0) for v in sites[:k_s]))
            premium_add = 0.0
            req_vids_t4 = task.get("rubric", {}).get("required_venue_ids", []) or []
            if req_vids_t4:
                from collections import defaultdict as _dd
                cat_costs = _dd(list)
                for v in venue_pool:
                    c = v.get("avg_cost_local")
                    if c is not None and v.get("category"):
                        cat_costs[v["category"]].append(c)
                cat_median = {}
                for cat, costs in cat_costs.items():
                    s = sorted(costs)
                    if s:
                        mid = len(s) // 2
                        cat_median[cat] = s[mid] if len(s) % 2 == 1 else (s[mid-1]+s[mid])/2
                pool_map_t4 = {v["venue_id"]: v for v in venue_pool}
                for vid in req_vids_t4:
                    v = pool_map_t4.get(vid)
                    if v is None: continue
                    cost = v.get("avg_cost_local")
                    med  = cat_median.get(v.get("category",""), 0.0)
                    if cost and med > 0 and cost > PREMIUM_COST_FACTOR * med:
                        premium_add += cost

            min_budget_per_day = (base_min + premium_add) / max(days, 1)
            floor = min_budget_per_day * FEASIBILITY_RATIO_MIN
            cap_b = min_budget_per_day * FEASIBILITY_RATIO_MAX

            def _fmt(x): return format_money(x, city) if city else f"{x:.0f}"
            if stated_budget < floor:
                ppart = (f" (+{_fmt(premium_add/max(days,1))}/day premium from required venues)"
                         if premium_add > 0 else "")
                return False, (
                    f"Type 4 budget infeasible: cheapest {k_r}r+{k_s}s sums to "
                    f"{_fmt(min_budget_per_day)}/day{ppart}, "
                    f"stated budget {_fmt(stated_budget)} < 1.1× min ({_fmt(floor)}). "
                    f"Increase budget_per_day or loosen P-filters."
                )
            if stated_budget > cap_b:
                ratio = stated_budget / min_budget_per_day if min_budget_per_day > 0 else 0.0
                return False, (
                    f"Type 4 budget too loose: stated {_fmt(stated_budget)}/day is "
                    f"{ratio:.2f}× cheapest viable plan ({_fmt(min_budget_per_day)}/day); "
                    f"requires 1.1–1.3×. Tighten to ~{_fmt(floor)}–{_fmt(cap_b)}."
                )

    return True, "ok"




# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

TASK_TYPES = ["type1", "type2", "type3", "type4", "type5", "type6"]


def generate_tasks(city: str, window_id: str,
                   count: int = 5,
                   api_key: str = None,
                   model: str = "claude-sonnet-4-20250514",
                   dry_run: bool = False,
                   output_dir: Path = None,
                   db_path: Path = None,
                   type_key: str = "all",
                   verbose: bool = False,
                   max_turns: int | None = None,
                   _shared_venue_ids: set | None = None,
                   _shared_tension_axes: set | None = None) -> list[dict]:
    """
    Generate tasks for a city + window combination.

    count: number of tasks to generate. If None, derived automatically
           from pool size via tasks_for_pool(n_venues).

    Returns list of generated task dicts.
    Tasks are written to output_dir if provided.
    """
    # Resolve db_path from city if not provided
    if db_path is None:
        db_path = get_city_db_path(city)

    # Load window
    window = get_window(city, window_id, db_path=db_path)
    if window is None:
        raise ValueError(f"Window '{window_id}' not found for city '{city}'. "
                         f"Run populate_seasonal_windows.py first.")

    # Load venue pool
    venue_pool = load_venue_pool(city, db_path=db_path)
    if not venue_pool:
        raise ValueError(f"No verified venues found for '{city}'. "
                         f"Run venue generation pipeline first.")

    # Derive city centre from mean of high-traffic venue coordinates
    hi_coords = [(v["lat"], v["lng"]) for v in venue_pool
                 if v.get("traffic_tier") == "high" and v.get("lat") and v.get("lng")]
    if not hi_coords:
        hi_coords = [(v["lat"], v["lng"]) for v in venue_pool
                     if v.get("lat") and v.get("lng")]
    centre_lat = (sum(c[0] for c in hi_coords) / len(hi_coords)) if hi_coords else 48.8
    centre_lng = (sum(c[1] for c in hi_coords) / len(hi_coords)) if hi_coords else 2.35

    # Load sold-out dates for this window
    unavailable = load_unavailable_dates(city, window.get("dates", []), db_path=db_path)

    print(f"\n{'='*60}")
    print(f"Task generation: {city} / {window['label']}")
    print(f"Venue pool: {len(venue_pool)} venues")
    print(f"Generating {count} task(s){'(dry-run)' if dry_run else ''}")
    print(f"{'='*60}")

    tasks           = []
    generated_types = []
    # Use caller-supplied sets when running multi-window so tracking persists
    # across windows within the same run. Fallback to fresh sets for single calls.
    _used_venue_ids    = _shared_venue_ids    if _shared_venue_ids    is not None else set()
    _used_tension_axes = _shared_tension_axes if _shared_tension_axes is not None else set()
    skipped_slots   = 0
    MAX_RETRIES_PER_SLOT = 3
    slot_attempts   = 0
    current_slot    = 0  # logical slot index — advances on success or after MAX_RETRIES

    while len(tasks) + skipped_slots < count:
        slot_attempts += 1
        task_idx = current_slot
        print(f"\n[{task_idx + 1}/{count}] Generating task... "
              f"(attempt {slot_attempts}/{MAX_RETRIES_PER_SLOT})")

        # Pick type: round-robin if "all", otherwise pin to specified type
        chosen_type = (TASK_TYPES[task_idx % len(TASK_TYPES)]
                       if type_key == "all" else type_key)

        if dry_run or not api_key:
            task = _stub_task(city, window, venue_pool, task_idx)
        else:
            from scripts.generation.task_agent import run_task_agent, MAX_TURNS
            task, _stop_info = run_task_agent(
                city=city, window=window, pool=venue_pool,
                type_key=chosen_type, model=model, api_key=api_key,
                unavailable=unavailable or {}, centre_lat=centre_lat,
                centre_lng=centre_lng, verbose=verbose,
                max_turns=max_turns if max_turns is not None else MAX_TURNS,
                used_venue_ids=_used_venue_ids,
                used_tension_axes=_used_tension_axes,
            )
            tok = (_stop_info or {}).get("usage_totals") or {}
            if tok:
                cache_str = ""
                if tok.get("cache_read_input_tokens"):
                    cache_str = f" cache_read={tok['cache_read_input_tokens']}"
                print(f"  🔢 tokens: in={tok.get('input_tokens', 0)} "
                      f"out={tok.get('output_tokens', 0)} "
                      f"total={tok.get('total_tokens', 0)}{cache_str} "
                      f"(turns={_stop_info.get('turns_used', '?')})")
            if task is None:
                if slot_attempts >= MAX_RETRIES_PER_SLOT:
                    print(f"  ❌ Slot {task_idx + 1} ({chosen_type}) failed "
                          f"{MAX_RETRIES_PER_SLOT}× — skipping")
                    skipped_slots += 1
                    current_slot  += 1
                    slot_attempts  = 0
                else:
                    print(f"  ❌ Generation failed, will retry")
                continue

        # Solvability check
        solvable, reason = _verify_task_solvable(task, venue_pool)
        if not solvable:
            print(f"  ⚠  Solvability check failed: {reason}")
            if not dry_run:
                if slot_attempts >= MAX_RETRIES_PER_SLOT:
                    print(f"  ❌ Slot {task_idx + 1} ({chosen_type}) unsolvable "
                          f"after {MAX_RETRIES_PER_SLOT} attempts — skipping")
                    skipped_slots += 1
                    current_slot  += 1
                    slot_attempts  = 0
                else:
                    print(f"  Retrying with relaxed constraints...")
                continue
            # In dry-run, accept the task anyway
            task["_solvability_warning"] = reason

        # Track structural types used
        stype = task.get("structural_type")
        if stype:
            generated_types.append(stype)

        # Save
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / f"{task['task_id']}.json"
            out_path.write_text(json.dumps(task, indent=2))
            print(f"  ✅ Saved: {out_path.name}")
        else:
            print(f"  ✅ Generated: {task.get('task_id')} "
                  f"({task.get('difficulty','?')}, {task.get('structural_type','?')})")

        tasks.append(task)
        # Track required venue IDs for cross-task diversity enforcement
        for _rvid in task.get("rubric", {}).get("required_venue_ids", []):
            _used_venue_ids.add(_rvid)
        for _pc in task.get("rubric", {}).get("personal_constraints", []):
            _sc = _pc.get("scope", "")
            if isinstance(_sc, str) and _sc.startswith("venue_id="):
                _used_venue_ids.add(_sc.split("=", 1)[1])
        # V2: Track Type 3 tension axes for cross-task variety
        if "type3" in str(task.get("structural_type", "")):
            for _pc in task.get("rubric", {}).get("personal_constraints", []):
                _cond = _pc.get("condition", {})
                if isinstance(_cond, dict):
                    _axis = _cond.get("field") or _cond.get("has_tag") or _cond.get("not_tag")
                    if _axis:
                        _used_tension_axes.add(_axis)
        current_slot  += 1
        slot_attempts  = 0

    print(f"\n{'='*60}")
    print(f"Generated {len(tasks)}/{count} tasks"
          + (f" ({skipped_slots} slot(s) skipped after "
             f"{MAX_RETRIES_PER_SLOT} retries)" if skipped_slots else ""))
    if generated_types:
        from collections import Counter
        type_counts = Counter(generated_types)
        for t, n in type_counts.items():
            print(f"  {t}: {n}")
    print(f"{'='*60}")

    return tasks



# ─────────────────────────────────────────────────────────────────────────────
# TASK OUTPUT PATH HELPER
# ─────────────────────────────────────────────────────────────────────────────

_DATA_ROOT = Path(__file__).parent.parent.parent / "data" / "cities"


def get_task_output_dir(city: str,
                        window_id: str,
                        run_name: str = None) -> Path:
    """
    Return the canonical output directory for task JSON files.

    Mirrors the venue DB layout:
      No run-name:  data/cities/{city}/tasks/{window_id}/
      With run:     data/cities/{city}/tasks/runs/{run_name}/{window_id}/

    Examples:
      get_task_output_dir("london", "london_carnival_2026")
        → data/cities/london/tasks/london_carnival_2026/

      get_task_output_dir("london", "london_carnival_2026", run_name="test_30")
        → data/cities/london/tasks/runs/test_30/london_carnival_2026/
    """
    if run_name:
        return _DATA_ROOT / city / "tasks" / "runs" / run_name / window_id
    return _DATA_ROOT / city / "tasks" / window_id


# ─────────────────────────────────────────────────────────────────────────────
# ALL-WINDOWS WRAPPER
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate benchmark tasks (B4)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Single window
  python generate_task.py --city london --window london_carnival_2026 --count 10 --api-key KEY

  # All windows, same count each
  python generate_task.py --city london --count 10 --api-key KEY

  # All windows, isolated test run (reads test_30 venue DB, writes to tasks/runs/test_30/)
  python generate_task.py --city london --count 5 --run-name test_30 --api-key KEY

  # All windows, per-window count override (JSON)
  python generate_task.py --city london --count 10 --run-name test_50 \
      --counts-per-window '{"london_carnival_2026": 15, "london_easter_2026": 8}' --api-key KEY
""")
    parser.add_argument("--city",     required=True,
                        help="City name e.g. london")
    parser.add_argument("--window",   default=None,
                        help="Window ID for single-window mode. "
                             "Omit to run all windows for the city.")
    parser.add_argument("--count",    type=int, default=5,
                        help="Tasks per window (default 5). "
                             "Override per-window with --counts-per-window.")
    parser.add_argument("--counts-per-window", default=None, metavar="JSON",
                        help="JSON dict of per-window count overrides, "
                             "e.g. '{window_id: count, ...}'")
    parser.add_argument("--run-name", default=None,
                        help="Isolates venue DB + task output folder. "
                             "Reads data/cities/{city}/runs/{run_name}/travelbench.db "
                             "and writes tasks to tasks/runs/{run_name}/{window_id}/.")
    parser.add_argument("--api-key",  default=None,
                        help="Anthropic API key")
    parser.add_argument("--model",    default="claude-sonnet-4-20250514")
    parser.add_argument("--type",     default="all",
                        choices=["all", *TASK_TYPES],
                        help="Structural type to generate (default: all = round-robin "
                             "through type1..type6).")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Use stubs, no LLM calls")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print turn-by-turn agent tool calls.")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="Override agent turn cap per task (default: 20, "
                             "set in task_agent.MAX_TURNS).")
    args = parser.parse_args()

    # Parse per-window count overrides
    cpw: dict = {}
    if args.counts_per_window:
        try:
            cpw = json.loads(args.counts_per_window)
        except Exception as e:
            parser.error(f"--counts-per-window is not valid JSON: {e}")

    db_path = get_city_db_path(args.city, run_name=args.run_name)

    if args.window:
        # ── Single-window mode ────────────────────────────────────────────
        cnt = cpw.get(args.window, args.count)
        out = get_task_output_dir(args.city, args.window, args.run_name)
        generate_tasks(
            city=args.city,
            window_id=args.window,
            count=cnt,
            api_key=args.api_key,
            model=args.model,
            dry_run=args.dry_run,
            output_dir=out,
            db_path=db_path,
            type_key=args.type,
            verbose=args.verbose,
            max_turns=args.max_turns,
        )
        print(f"\nOutput: {out}")
    else:
        # ── All-windows mode ──────────────────────────────────────────────
        windows = get_windows(args.city, db_path=db_path)
        if not windows:
            parser.error(f"No windows found for city '{args.city}'. "
                         f"Run populate_seasonal_windows.py first.")
        _run_venue_ids    = set()  # shared across all windows in this run
        _run_tension_axes = set()  # shared across all windows in this run
        for w in windows:
            wid = w["window_id"]
            cnt = cpw.get(wid, args.count)
            out = get_task_output_dir(args.city, wid, args.run_name)
            generate_tasks(
                city=args.city, window_id=wid, count=cnt,
                api_key=args.api_key, model=args.model,
                dry_run=args.dry_run, output_dir=out, db_path=db_path,
                type_key=args.type, verbose=args.verbose,
                max_turns=args.max_turns,
                _shared_venue_ids=_run_venue_ids,
                _shared_tension_axes=_run_tension_axes,
            )
            print(f"\nOutput: {out}")