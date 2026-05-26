"""
scripts/generation/constraint_engine.py

Shared generic constraint evaluation engine.

Extracted from eval/evaluator.py so that both the evaluator and the
task generation validation pipeline can use the same engine.

eval/evaluator.py imports from here (no functional change).
test_generate_tasks.py and generate_task.py also import from here for
pool-level constraint satisfiability checks.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ORDERING CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TRAFFIC_TIER_ORDER = ["low", "mid", "high"]
PRICE_TIER_ORDER   = ["free", "budget", "mid", "upscale", "fine-dining", "luxury"]
PACE_ORDER         = ["relaxed", "moderate", "intense"]
DRESS_CODE_ORDER   = ["none", "casual", "smart_casual", "formal"]

# Tag normaliser: canonical form is lowercase kebab-case.
# Used in scope matching (has_tag=) and condition checks (has_tag condition key).
def _nt(s: str) -> str:
    return s.lower().replace("_", "-").replace(" ", "-")


# ─────────────────────────────────────────────────────────────────────────────
# SCOPE + CONDITION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _hhmm_to_min(t: str) -> int:
    """Convert HH:MM string to minutes since midnight."""
    try:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    except Exception:
        return 0


def _get_field_value(v: dict, act: dict, field: str):
    """
    Look up a field value for condition checks.

    Order of lookup:
      1. Flat venue field (e.g. v["wheelchair_accessible"])
      2. Nested regulations dict (e.g. v["regulations"]["wheelchair_accessible"])
      3. Activity field (e.g. act["estimated_cost_local"] or act["time_start"])

    Returns None if the field is not found in any of these places (matches
    legacy handler behavior; callers treat None as "unknown" rather than
    "fails").
    """
    # Flat venue first — covers most cases
    if field in v:
        return v[field]
    # Nested regulations dict (some pools store flags this way)
    regs = v.get("regulations")
    if isinstance(regs, dict) and field in regs:
        return regs[field]
    # Finally activity-local fields (time_start, estimated_cost_local, etc.)
    if field in act:
        return act[field]
    return None


def _activity_matches_scope(act: dict, scope, venues: dict) -> bool:
    """
    Check if an activity falls within the given scope spec.

    Scope forms:
      - "all"                       → every activity matches
      - "activity_type=X"           → activity.activity_type == X (or X=="any")
      - "category=X"                → venue.category == X
      - "has_tag=X"                 → X in venue.tags
      - "time_window=HH:MM-HH:MM"   → activity.time_start in window
      - "time_window=YYYY-MM-DD"     → activity._day_date matches date
      - "time_window=YYYY-MM-DDTHH:MM-..." → date AND time match
      - [scope_a, scope_b, ...]     → AND: activity matches all inner scopes
      - {"any_of": [...]}           → OR: activity matches at least one inner scope

    List-of-scopes is intersection (AND). The {"any_of": [...]} dict form is
    the explicit union (OR) marker — used by _translate_scope_for_pool when
    translating activity_type=meal into its constituent food categories, and
    available to callers who need it.
    """
    if scope == "all":
        return True
    if isinstance(scope, dict):
        if "any_of" in scope:
            inner = scope["any_of"]
            if not isinstance(inner, list):
                return False
            return any(_activity_matches_scope(act, s, venues) for s in inner)
        # Unknown dict form — treat as always-matching rather than crashing.
        return True
    if isinstance(scope, str):
        if scope.startswith("activity_type="):
            atype = scope.split("=", 1)[1]
            return atype == "any" or act.get("activity_type") == atype
        if scope.startswith("category="):
            cat = scope.split("=", 1)[1]
            vid = act.get("venue_id", "")
            return venues.get(vid, {}).get("category") == cat
        if scope.startswith("has_tag="):
            tag = scope.split("=", 1)[1]
            vid = act.get("venue_id", "")
            v = venues.get(vid, {})
            return _nt(tag) in {_nt(t) for t in (v.get("tags") or v.get("category_tags") or [])}
        if scope.startswith("venue_id="):
            target_vid = scope.split("=", 1)[1]
            return act.get("venue_id", "") == target_vid
        if scope.startswith("venue_name~"):
            # `~` signals substring match (case-insensitive) rather than equality.
            needle = scope.split("~", 1)[1].lower()
            vid = act.get("venue_id", "")
            # Activity may carry venue_name directly; fall back to venue record.
            vname = (act.get("venue_name")
                     or venues.get(vid, {}).get("name", ""))
            return needle in (vname or "").lower()
        if scope.startswith("time_window="):
            window = scope.split("=", 1)[1]
            # Detect date-based format: "2026-05-25" or "2026-05-25T18:00-..."
            if len(window) >= 10 and window[4:5] == "-" and window[7:8] == "-":
                # Date-based scope — match against activity's _day_date
                act_date = act.get("_day_date", "")
                if "T" in window:
                    # "2026-05-25T18:00-2026-05-25T23:59" → check date AND time
                    # Format: YYYY-MM-DDTHH:MM-YYYY-MM-DDTHH:MM (two ISO datetimes)
                    _parts = window.split("T")
                    _date_part = _parts[0]  # "2026-05-25"
                    if act_date != _date_part:
                        return False
                    # Extract start and end times from the two T-parts
                    # _parts[1] = "18:00-2026-05-25" (start time + end date)
                    # _parts[2] = "23:59"            (end time)
                    try:
                        _start_time = _parts[1].split("-")[0]          # "18:00"
                        _end_time   = _parts[-1]                        # "23:59"
                        ts = act.get("time_start", "00:00")
                        t  = _hhmm_to_min(ts)
                        return _hhmm_to_min(_start_time) <= t <= _hhmm_to_min(_end_time)
                    except (IndexError, ValueError):
                        return True  # malformed range — don't reject
                else:
                    # Pure date: "2026-05-25" → activity must be on this date
                    return act_date == window
            # Standard HH:MM-HH:MM format
            start_s, end_s = window.split("-")
            ts = act.get("time_start", "00:00")
            t  = _hhmm_to_min(ts)
            return _hhmm_to_min(start_s) <= t <= _hhmm_to_min(end_s)
    if isinstance(scope, list):
        return all(_activity_matches_scope(act, s, venues) for s in scope)
    return True


def _activity_satisfies_condition(act: dict, condition: dict, venues: dict) -> bool:
    """Check if an activity satisfies the condition spec."""
    if not condition:
        return True

    vid = act.get("venue_id", "")
    v   = venues.get(vid, {})

    # Tag checks
    if "has_tag" in condition:
        tags = v.get("tags") or v.get("category_tags") or []
        return _nt(condition["has_tag"]) in {_nt(t) for t in tags}
    if "not_tag" in condition or "not_has_tag" in condition:
        tag_key = "not_tag" if "not_tag" in condition else "not_has_tag"
        tags = v.get("tags") or v.get("category_tags") or []
        return _nt(condition[tag_key]) not in {_nt(t) for t in tags}

    # Field comparison
    if "field" in condition and "operator" in condition:
        field = condition["field"]
        op    = condition["operator"]
        value = condition.get("value")

        # Look up field value across flat venue → regulations dict → activity.
        actual = _get_field_value(v, act, field)

        # Equality-to-null: handled before the is-None guard so that an
        # explicit `value: None` condition matches absent fields.
        # (E.g. `min_age == None` matches venues with no age restriction.)
        if value is None and op in ("==", "!="):
            if op == "==":
                return actual is None
            else:  # !=
                return actual is not None

        if actual is None:
            return False

        if op == "==":  return actual == value
        if op == "!=":  return actual != value

        # Ordered string enum comparisons
        if field == "traffic_tier" and actual in TRAFFIC_TIER_ORDER:
            a_idx = TRAFFIC_TIER_ORDER.index(actual)
            if value not in TRAFFIC_TIER_ORDER:
                return False
            v_idx = TRAFFIC_TIER_ORDER.index(value)
            if op == ">=": return a_idx >= v_idx
            if op == "<=": return a_idx <= v_idx
            if op == ">":  return a_idx > v_idx
            if op == "<":  return a_idx < v_idx

        if field == "price_tier" and actual in PRICE_TIER_ORDER:
            a_idx = PRICE_TIER_ORDER.index(actual)
            if value not in PRICE_TIER_ORDER:
                return False
            v_idx = PRICE_TIER_ORDER.index(value)
            if op == ">=": return a_idx >= v_idx
            if op == "<=": return a_idx <= v_idx
            if op == ">":  return a_idx > v_idx
            if op == "<":  return a_idx < v_idx

        if field == "recommended_pace" and actual in PACE_ORDER:
            a_idx = PACE_ORDER.index(actual)
            if value not in PACE_ORDER:
                return False
            v_idx = PACE_ORDER.index(value)
            if op == ">=": return a_idx >= v_idx
            if op == "<=": return a_idx <= v_idx

        if field == "dress_code" and actual in DRESS_CODE_ORDER:
            a_idx = DRESS_CODE_ORDER.index(actual)
            if value not in DRESS_CODE_ORDER:
                return False
            v_idx = DRESS_CODE_ORDER.index(value)
            if op == ">=": return a_idx >= v_idx
            if op == "<=": return a_idx <= v_idx
            if op == ">":  return a_idx > v_idx
            if op == "<":  return a_idx < v_idx

        try:
            if op == ">=": return float(actual) >= float(value)
            if op == "<=": return float(actual) <= float(value)
            if op == ">":  return float(actual) > float(value)
            if op == "<":  return float(actual) < float(value)
        except (TypeError, ValueError):
            # Neither enum-ordered nor numeric — comparison is undefined.
            if op in (">=", "<=", ">", "<"):
                return False

        if op == "in":     return actual in (value or [])
        if op == "not_in": return actual not in (value or [])

        # `contains` is the mirror of `in`: here `actual` is a list field
        # on the venue (e.g. suitable_occasions) and `value` is a scalar we
        # test for membership. E.g. {field: "suitable_occasions",
        # operator: "contains", value: "anniversary"} asks whether the
        # venue's suitable_occasions list contains "anniversary".
        # Returns False if the field isn't list-shaped.
        if op == "contains":
            if not isinstance(actual, (list, tuple, set)):
                return False
            return value in actual

    # Composite conditions
    if "all" in condition:
        return all(_activity_satisfies_condition(act, c, venues) for c in condition["all"])
    if "any" in condition:
        return any(_activity_satisfies_condition(act, c, venues) for c in condition["any"])

    return True


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC CONSTRAINT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_generic_constraint(cid: str, constraint: dict,
                                  days: list, all_activities: list,
                                  venues: dict) -> dict:
    """
    Evaluate a constraint expressed in the generic schema:
    { scope, condition, aggregation, consequence }

    Returns standard handler result dict with 'id', 'score', 'reason'.
    """
    scope       = constraint.get("scope", "all")
    condition   = constraint.get("condition", {})
    aggregation = constraint.get("aggregation", "all")
    description = constraint.get("description", cid)

    # Resolve per_day scope separately
    per_day = (scope == "per_day")

    if per_day:
        agg = aggregation

        # sum aggregation per day: sum a numeric field across each day's
        # matching activities (e.g. travel_minutes <= 60 per day).
        if isinstance(agg, dict) and "sum" in agg:
            field = agg["sum"]
            op    = agg.get("operator", "<=")
            val   = float(agg.get("value", 0))
            all_pass  = True
            day_sums  = []
            for day in days:
                acts     = day.get("activities", [])
                matching = [a for a in acts
                            if _activity_matches_scope(a, "all", venues)]
                total = sum(
                    float(a.get(field) or
                          venues.get(a.get("venue_id", ""), {}).get(field)
                          or 0)
                    for a in matching)
                day_ok = ((op == "<=" and total <= val) or
                          (op == ">=" and total >= val) or
                          (op == "<"  and total <  val) or
                          (op == ">"  and total >  val))
                if not day_ok:
                    all_pass = False
                day_sums.append(round(total, 1))
            score = 1.0 if all_pass else 0.0
            return {"id": cid, "score": score,
                    "reason": f"{description}: per-day sums {day_sums} "
                              f"{op} {val} ({'pass' if all_pass else 'fail'})"}

        # Count-based aggregations per day
        day_results = []
        for day in days:
            acts     = day.get("activities", [])
            matching  = [a for a in acts if _activity_matches_scope(a, "all", venues)]
            satisfied = [a for a in matching if _activity_satisfies_condition(a, condition, venues)]
            day_results.append((len(matching), len(satisfied)))

        all_pass = True
        for total, sat in day_results:
            if isinstance(agg, dict):
                if "at_least" in agg and sat < agg["at_least"]:
                    all_pass = False
                if "at_most" in agg and sat > agg["at_most"]:
                    all_pass = False
            elif agg == "all":
                if sat < total:
                    all_pass = False

        score = 1.0 if all_pass else 0.0
        return {"id": cid, "score": score,
                "reason": f"{description}: {'pass' if all_pass else 'fail'} (per_day)"}

    # at_least_days: count days where ALL activities in scope satisfy the
    # condition, then check count >= threshold.
    # Distinct from per_day which requires EVERY day to pass.
    # Used by pace_relaxed: "at least N full days with all-relaxed venues."
    if isinstance(aggregation, dict) and "at_least_days" in aggregation:
        n_required = aggregation["at_least_days"]
        passing_days = 0
        for day in days:
            acts = day.get("activities", [])
            in_scope = [a for a in acts if _activity_matches_scope(a, scope, venues)]
            if not in_scope:
                continue  # empty day doesn't count for or against
            if all(_activity_satisfies_condition(a, condition, venues) for a in in_scope):
                passing_days += 1
        passed = passing_days >= n_required
        score  = 1.0 if passed else (0.5 if passing_days > 0 else 0.0)
        return {"id": cid, "score": score,
                "reason": f"{description}: {passing_days}/{n_required} fully-passing days "
                          f"({'pass' if passed else 'fail'})"}

    # Standard: gather matching activities
    matching = [a for a in all_activities if _activity_matches_scope(a, scope, venues)]

    # Early-exit N/A only when empty matching AND the aggregation is not one
    # that requires a positive count.  Specifically:
    #   - "all"  vacuously passes (no activities → no violations)
    #   - "none" vacuously passes
    #   - {at_least: 0} vacuously passes (handled above via at_least_0 string,
    #     kept for completeness)
    # But {at_least: N≥1}, {exactly: N≥1} MUST fail when matching is empty —
    # "must visit at least 1 X" should be 0.0 when no X was visited, not N/A.
    def _requires_positive_count(agg) -> bool:
        if isinstance(agg, dict):
            if "at_least" in agg and agg["at_least"] > 0:
                return True
            if "exactly" in agg and agg["exactly"] > 0:
                return True
        return False

    if not matching:
        if aggregation in ("none", "at_least_0") or not _requires_positive_count(aggregation):
            return {"id": cid, "score": 1.0,
                    "reason": f"{description}: no matching activities (N/A)"}
        # Falls through to normal scoring — n_matching=0, n_satisfied=0 → fails

    satisfied  = [a for a in matching if _activity_satisfies_condition(a, condition, venues)]
    n_matching = len(matching)
    n_satisfied = len(satisfied)

    agg    = aggregation
    passed = False

    if agg == "all":
        passed = n_satisfied == n_matching
    elif agg == "none":
        passed = n_satisfied == 0
    elif isinstance(agg, dict):
        if "at_least" in agg:
            # P6-T9: count DISTINCT venue_ids — revisiting the same venue
            # should not double-credit toward an at_least floor. count_distinct
            # is the correct aggregation when counting distinct *values*; this
            # branch tests distinct *venues* visited.
            distinct_vids = {a.get("venue_id") for a in satisfied if a.get("venue_id")}
            n_distinct    = len(distinct_vids)
            passed = n_distinct >= agg["at_least"]
        elif "at_most" in agg:
            passed = n_satisfied <= agg["at_most"]
        elif "exactly" in agg:
            passed = n_satisfied == agg["exactly"]
        elif "count_distinct" in agg:
            field = agg["field"]
            vals  = set()
            for a in satisfied:
                v2  = venues.get(a.get("venue_id", ""), {})
                val = _get_field_value(v2, a, field)
                if val:
                    vals.add(val)
                elif not val:
                    # Tag fallback: if field doesn't exist as a flat value,
                    # scan tags for matches. For "cuisine_label" → tags
                    # containing "cuisine" (e.g. "british-cuisine" → "british").
                    _tag_key = field.replace("_label", "").replace("_", "-")
                    tags = v2.get("tags") or v2.get("category_tags") or []
                    for tag in tags:
                        if _tag_key in tag:
                            vals.add(tag)
            passed = len(vals) >= agg["count_distinct"]
        elif "at_most_distinct" in agg:
            # Sibling of count_distinct but with ≤ semantics.
            field = agg["field"]
            vals  = set()
            for a in satisfied:
                v2  = venues.get(a.get("venue_id", ""), {})
                val = _get_field_value(v2, a, field)
                if val:
                    vals.add(val)
                elif not val:
                    _tag_key = field.replace("_label", "").replace("_", "-")
                    tags = v2.get("tags") or v2.get("category_tags") or []
                    for tag in tags:
                        if _tag_key in tag:
                            vals.add(tag)
            passed = len(vals) <= agg["at_most_distinct"]
        elif "ratio" in agg:
            denom  = len([a for a in all_activities
                          if _activity_matches_scope(a, scope, venues)])
            passed = (n_satisfied / denom) >= agg["ratio"] if denom > 0 else True
        elif "sum" in agg:
            field = agg["sum"]
            total = sum(float(a.get(field) or
                              venues.get(a.get("venue_id", ""), {}).get(field) or 0)
                        for a in matching)
            op    = agg.get("operator", "<=")
            val   = float(agg.get("value", 0))
            if op == "<=": passed = total <= val
            elif op == ">=": passed = total >= val
            elif op == "<":  passed = total < val
            elif op == ">":  passed = total > val

    # Partial credit for at_least: proportional progress toward threshold,
    # using DISTINCT venue count (P6-T9) so it matches the pass criterion above.
    # 1/4 required → 0.25, 2/4 → 0.5, 3/4 → 0.75, 4/4 → 1.0
    if not passed and isinstance(agg, dict) and "at_least" in agg:
        _denom  = agg["at_least"]
        _n_dist = len({a.get("venue_id") for a in satisfied if a.get("venue_id")})
        score = round(min(_n_dist / _denom, 1.0), 3) if _denom > 0 else 0.0
    else:
        score = 1.0 if passed else 0.0

    # For at_least also surface the distinct-venue count so the reason string
    # makes clear when matching activities collapse to fewer distinct venues.
    _extra = ""
    if isinstance(agg, dict) and "at_least" in agg:
        _n_dist = len({a.get("venue_id") for a in satisfied if a.get("venue_id")})
        if _n_dist != n_satisfied:
            _extra = f" (distinct venues: {_n_dist})"

    return {"id": cid, "score": score,
            "reason": f"{description}: {n_satisfied}/{n_matching} matching{_extra} "
                      f"({'pass' if passed else 'fail'})"}


# ─────────────────────────────────────────────────────────────────────────────
# POOL-LEVEL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _translate_scope_for_pool(scope) -> object | None:
    """
    Translate a constraint scope for pool-level (venue-existence) checks.

    Returns translated scope, or None if the scope should be skipped
    (time-based or day-structure scopes don't apply to pool checks).

    Key translation rules:
      - activity_type=meal  → {"any_of": ["category=restaurant", "category=cafe", "category=bar"]}
        (an "any_of" marker — venue qualifies if its category is ONE of those)
      - activity_type=visit → "all"
      - activity_type=any   → "all"
      - activity_type=<other> → f"category={other}"
      - time_window=...     → None (skip — scheduling, not venue existence)
      - per_day             → None (skip — day structure)
      - a top-level list    → AND of translated inner scopes (list-means-AND)
      - all others          → unchanged
    """
    MEAL_CATEGORIES = ["restaurant", "cafe", "bar"]

    if scope == "per_day":
        return None
    if isinstance(scope, dict):
        # Pass dict scopes (e.g. {"any_of": [...]}) through unchanged — the
        # engine's scope predicate understands them directly.
        return scope
    if isinstance(scope, str):
        if scope.startswith("time_window="):
            return None
        if scope.startswith("activity_type="):
            atype = scope.split("=", 1)[1]
            if atype == "meal":
                # Meal venues are the union of three food categories.
                # Wrap as an any_of marker so _activity_matches_scope treats
                # this as OR, not AND.
                return {"any_of": [f"category={c}" for c in MEAL_CATEGORIES]}
            if atype in ("visit", "any"):
                return "all"
            # Other types: return category scope
            return f"category={atype}"
    if isinstance(scope, list):
        # List means AND at scope level. Translate each element, skip None
        # (plan-only scopes that drop out at pool level).
        translated = []
        for s in scope:
            t = _translate_scope_for_pool(s)
            if t is None:
                continue
            translated.append(t)
        if not translated:
            return None
        return translated if len(translated) > 1 else translated[0]
    return scope


def pool_as_activities(venue_pool: list[dict]) -> tuple[list, dict]:
    """
    Wrap venue pool as synthetic activities + venues dict for generic engine.
    Each venue becomes one activity with activity_type='visit'.
    """
    activities = [
        {"venue_id": v["venue_id"], "activity_type": "visit"}
        for v in venue_pool
    ]
    # Build venues dict — handle both flat and nested regulation formats
    venues = {}
    for v in venue_pool:
        vd = dict(v)
        # Ensure tags is a list
        if "tags" not in vd:
            vd["tags"] = []
        venues[v["venue_id"]] = vd
    return activities, venues


def check_constraint_pool_satisfiability(
    constraint: dict,
    venue_pool: list[dict],
) -> tuple[bool, str]:
    """
    Check whether a constraint can be satisfied by at least one venue in the pool.

    Returns (satisfiable, reason).
    Used by validate_task_schema for pool-level existence checks.

    Only hard-fails for consequence='p_score_full' or 'f_score_hard'.
    b_score_bonus failing at pool level is not an error.

    Aggregation routing:
      at_least, count_distinct  → run pool check (meaningful existence test)
      all, none                 → skip (existence verified by solvability lower bars;
                                  agg="all"/"none" are plan-level, not pool filters)
      at_most, at_most_distinct,
      ratio, exactly            → skip (plan-level ceilings/ratios — pool may have
                                  more qualifying venues than the constraint allows,
                                  and that is fine; agent selects at plan time)
      sum, at_least_days        → skip (temporal/sum; already handled)
    """
    consequence = constraint.get("consequence", "p_score_full")
    if consequence == "b_score_bonus":
        return True, "b_score_bonus — pool check skipped"

    scope = constraint.get("scope", "all")
    agg   = constraint.get("aggregation", "all")

    translated = _translate_scope_for_pool(scope)
    if translated is None:
        return True, "time/day scope — pool check skipped"

    # Temporal / sum aggregations cannot be verified at pool level
    if isinstance(agg, dict) and ("at_least_days" in agg or "sum" in agg):
        return True, "temporal/sum aggregation — pool check skipped"

    # Plan-level aggregations: skip entirely.
    # agg="all"/"none" — agent satisfies these by choosing which venues to visit;
    #   the pool may contain non-qualifying venues that the agent simply avoids.
    #   Existence is verified by _verify_task_solvable lower bars.
    if agg in ("all", "none"):
        return True, "agg=all/none — pool check skipped (existence via solvability lower bars)"

    # at_most / at_most_distinct / ratio / exactly — plan-level ceilings or ratios.
    #   The pool having more qualifying venues than the ceiling is fine; the agent
    #   selects a subset. Pool ratio != achievable plan ratio.
    if isinstance(agg, dict) and any(
        k in agg for k in ("at_most", "at_most_distinct", "ratio", "exactly")
    ):
        k = next(k for k in ("at_most", "at_most_distinct", "ratio", "exactly") if k in agg)
        return True, f"plan-level aggregation ({k}) — pool check skipped"

    # Remaining: {at_least: N} and {count_distinct: N}
    # These ARE meaningful at pool level: can N qualifying venues be found?
    modified = dict(constraint)
    modified["scope"] = translated

    activities, venues = pool_as_activities(venue_pool)
    result = _evaluate_generic_constraint(
        constraint.get("id", "?"), modified,
        days=[], all_activities=activities, venues=venues
    )

    if result["score"] == 0.0:
        return False, (
            f"Pool existence check failed: {result['reason']}. "
            f"Not enough qualifying venues in pool to satisfy this at_least/count_distinct "
            f"constraint — review scope, condition, or reduce the threshold."
        )
    return True, result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION-FACING HELPERS
#
# These are the "reverse direction" of the scoring engine. Scoring asks
# "given a plan, does it satisfy the rule?" and uses the aggregation layer
# to produce 0.0/1.0. Validation asks "given the rule, which venues in the
# pool CAN satisfy it?" — we want the list of survivors, not a pass/fail.
#
# Both directions use the same two per-venue predicates
# (_activity_matches_scope, _activity_satisfies_condition). These helpers
# just call them in "list survivors" mode and apply _translate_scope_for_pool
# so that plan-only scope kinds (per_day, time_window) are handled sensibly.
# ─────────────────────────────────────────────────────────────────────────────


def venues_in_scope(venue_pool: list[dict], scope) -> list[dict]:
    """
    Return venues in the pool whose scope predicate matches.

    Used as the DENOMINATOR for upper-bar ratio checks. If a scope is plan-only
    (e.g. per_day, time_window=...) and has no venue-level meaning, we return
    the full pool — these scopes don't narrow the pool.

    Example:
        # How many meal-venues exist in the pool?
        meals = venues_in_scope(pool, "activity_type=meal")
    """
    translated = _translate_scope_for_pool(scope)
    if translated is None:
        # Plan-only scope — every venue is "in scope" for pool purposes
        return list(venue_pool)

    activities, venues_dict = pool_as_activities(venue_pool)
    return [
        v for v, act in zip(venue_pool, activities)
        if _activity_matches_scope(act, translated, venues_dict)
    ]


def venues_matching(
    venue_pool: list[dict],
    scope,
    condition: dict,
) -> list[dict]:
    """
    Return venues in the pool that are BOTH in scope AND satisfy the condition.

    Used as the NUMERATOR for upper-bar ratio checks, and as the primary
    "does the pool support this constraint?" query.

    An empty or missing `condition` passes every in-scope venue (i.e. the
    result is equivalent to venues_in_scope(pool, scope)). Plan-only scopes
    fall through to "every venue is in scope" — the condition still narrows.

    Example:
        # Which meal-venues can accommodate a wheelchair user?
        wheelchair_meals = venues_matching(
            pool,
            scope="activity_type=meal",
            condition={"field": "wheelchair_accessible", "operator": "==", "value": True},
        )
    """
    translated = _translate_scope_for_pool(scope)
    # If scope is plan-only, all venues are in scope, but condition still applies
    effective_scope = "all" if translated is None else translated

    activities, venues_dict = pool_as_activities(venue_pool)
    return [
        v for v, act in zip(venue_pool, activities)
        if _activity_matches_scope(act, effective_scope, venues_dict)
        and _activity_satisfies_condition(act, condition or {}, venues_dict)
    ]