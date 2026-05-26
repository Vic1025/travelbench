# TravelBench — Generic Constraint Schema
*Internal reference · Sprint B*
*Formal specification of the unified constraint representation*

---

## Motivation

The existing 25 named P_SCORE_HANDLERS are all special cases of the same
underlying structure. Formalising this structure into a generic schema:

1. Lets the task generation agent construct any constraint without knowing
   handler names — it only needs to understand four concepts
2. Lets the evaluator use one generic engine instead of 25 separate functions
3. Makes new constraints expressible without new code in the common cases
4. Provides a clear extension point for genuinely novel constraints

The existing named handlers remain as legacy aliases during migration.
New constraints from Phase 3 onwards are expressed in the generic schema.

---

## Schema Definition

A constraint is defined by four required parameters and two optional ones:

```
{
  "scope":       <ScopeSpec>,      -- REQUIRED: what does this apply to?
  "condition":   <ConditionSpec>,  -- REQUIRED: what is being checked?
  "aggregation": <AggregationSpec>,-- REQUIRED: how do results combine?
  "consequence": <ConsequenceSpec>,-- REQUIRED: what happens on violation?
  "relative_to": <RelativeSpec>,   -- OPTIONAL: cross-activity reference
  "description": string            -- OPTIONAL: human-readable explanation
}
```

---

## ScopeSpec — What does this apply to?

Defines the set of activities the constraint evaluates over.

```
scope: "all"
  Every activity in the plan across all days.

scope: "activity_type=<type>"
  Only activities of the given type.
  Values: "meal" | "visit" | "leisure" | "any"

scope: "category=<category>"
  Only activities at venues of the given category.
  Values: any venue category (restaurant, museum, park, ...)

scope: "per_day"
  Constraint is evaluated independently within each day.
  The aggregation applies per-day, not across the full trip.

scope: "per_trip"
  Constraint is evaluated across all days combined.

scope: "time_window=<start>-<end>"
  Only activities that start within the given time window.
  Example: "time_window=18:00-23:59" (evening activities only)

scope: "day_index=<N>"
  Only activities on day N (0-indexed).
  Example: "day_index=0" (first day only)

scope: "venue_id=<id>"
  A specific named venue.
```

Scopes can be combined with AND using a list:
```
scope: ["activity_type=meal", "time_window=19:00-23:59"]
  → dinner activities only
```

---

## ConditionSpec — What is being checked?

Defines the property being evaluated on each activity in scope.

### Field comparison
```
condition: {
  "field": <field_name>,
  "operator": "==" | "!=" | ">=" | "<=" | ">" | "<" | "in" | "not_in",
  "value": <value>
}
```

Field names:
- Venue fields: `price_tier`, `noise_level`, `recommended_pace`,
  `traffic_tier`, `recommended_visit_minutes`, `age_restriction`,
  `wheelchair_accessible`, `photography_allowed`, `pet_friendly`,
  `family_friendly`, `booking_required`, `outdoor_sensitivity`
- Schedule fields: `time_start`, `time_end`, `duration_minutes`
- Venue category: `category`, `cuisine_label`
- Tags: `has_tag` (special — see below)

### Tag check
```
condition: {
  "has_tag": "vegetarian-options"
}

condition: {
  "not_tag": "tourist-trap"
}
```

### Composite condition (AND)
```
condition: {
  "all": [
    {"field": "price_tier", "operator": ">=", "value": "upscale"},
    {"has_tag": "vegetarian-options"}
  ]
}
```

### Composite condition (OR)
```
condition: {
  "any": [
    {"has_tag": "hidden-gem"},
    {"field": "traffic_tier", "operator": "==", "value": "low"}
  ]
}
```

---

## AggregationSpec — How do results combine?

```
aggregation: "all"
  Every activity in scope must satisfy the condition.
  Violation: any activity fails.

aggregation: "none"
  No activity in scope may satisfy the condition.
  Violation: any activity passes.
  (Used for exclusion constraints: no loud venues, no tourist traps)

aggregation: "at_least_N"
  At least N activities in scope must satisfy the condition.
  Example: {"at_least": 1} — at least one upscale dinner

aggregation: "at_most_N"
  No more than N activities in scope may satisfy the condition.
  Example: {"at_most": 1} — max one museum per day (with scope: per_day)

aggregation: "exactly_N"
  Exactly N activities must satisfy.

aggregation: "count_distinct_N"
  The number of distinct values of a field across matching activities
  must be at least N.
  Example: {"count_distinct": 3, "field": "cuisine_label"}
  → at least 3 different cuisines

aggregation: "ratio_N"
  The fraction of matching activities satisfying the condition must be ≥ N.
  Example: {"ratio": 0.6, "of": "activity_type=meal"}
  → at least 60% of meals satisfy condition (local_cuisine_preference)

aggregation: "sum_operator"
  The sum of a numeric field across matching activities satisfies operator.
  Example: {"sum": "estimated_cost_local", "operator": "<=", "value": 120}
  → total cost across all activities ≤ $120
```

---

## ConsequenceSpec — What happens on violation?

```
consequence: "f_score_hard"
  Any violation → plan is infeasible, F-score = 0.
  Used for physical/legal impossibility constraints.

consequence: "p_score_full"
  Violation → P-score deduction, full weight.
  Used for hop-1 persona inference constraints.

consequence: "p_score_partial"
  Violation → P-score deduction, half weight.
  Used for hop-2 persona inference constraints.

consequence: "b_score_bonus"
  Satisfaction → B-score bonus credit added.
  Violation → no penalty.
  Used for hop-3 inferences and structural quality.
```

---

## RelativeSpec — Cross-activity reference (optional)

Used when a constraint on one activity depends on another activity.

```
relative_to: {
  "activity_type": "visit",
  "relationship": "before" | "after" | "within_minutes",
  "value": <N>        -- used with within_minutes
}
```

Examples:

"Meal within 15 minutes walk of the museum, before entry"
```json
{
  "scope": "activity_type=meal",
  "condition": {"field": "travel_minutes_to_next", "operator": "<=", "value": 15},
  "aggregation": "at_least_1",
  "consequence": "p_score_full",
  "relative_to": {
    "activity_type": "visit",
    "relationship": "before"
  }
}
```

"Visit the market as the first activity of the day"
```json
{
  "scope": ["category=neighbourhood", "has_tag=market"],
  "condition": {"field": "day_position", "operator": "==", "value": "first"},
  "aggregation": "at_least_1",
  "consequence": "p_score_partial"
}
```

"Sunset rooftop visit: timing within 30 minutes of sunset"
```json
{
  "scope": ["has_tag=rooftop"],
  "condition": {
    "field": "time_start",
    "operator": "within_minutes",
    "value": 30,
    "relative_to": "sunset"
  },
  "aggregation": "at_least_1",
  "consequence": "b_score_bonus"
}
```

---

## Route Efficiency (B-score, graph-based)

Geographic coherence cannot be expressed in the field-comparison schema.
It is computed separately using the travel matrix.

**Algorithm:**
1. For each day's activities, treat venues as nodes and travel times as edge weights
2. Compute the Minimum Spanning Tree (MST) of the visited venues
3. Sum of MST edge weights = theoretical minimum travel for any route visiting all venues
4. Actual total travel time from the plan = sum of consecutive activity travel times
5. Route efficiency = MST_total / actual_total
   → 1.0 = perfectly efficient (no backtracking)
   → < 0.7 = significant backtracking, penalise

**Threshold:** Efficiency ≥ 1/1.5 (i.e. actual travel ≤ 1.5× MST minimum)
is considered acceptable. Below this → B-score deduction.

This is a B-score check — rewarding geographic thoughtfulness, not
penalising plans that don't optimise perfectly.

---

## LLM Judge Cases

Constraints that cannot be expressed in the schema go to LLM judge:

1. **Semantic quality:** "Schedule feels coherent for a romantic anniversary"
2. **Schedule arc:** "Dinner is the emotional and culinary peak of the trip"
3. **Rest window:** "Plan includes a quiet seated rest mid-afternoon"
4. **Narrative coherence:** "Activities build a meaningful day with variety"
5. **Type C emergent combinations:** constraints that no handler generates
   from individual signals

LLM judge constraints use `pattern: "llm_semantic"` and receive the full
plan + constraint description. Scored 0/0.5/1.0.

---

## Complete Examples

### "I'm bringing my 10-year-old" → F-score
```json
{
  "id": "f_001",
  "score_tier": "F",
  "source_in_profile": "I'm bringing my 10-year-old",
  "description": "No age-restricted venues — child cannot be admitted",
  "scope": "all",
  "condition": {"field": "age_restriction", "operator": "==", "value": 0},
  "aggregation": "all",
  "consequence": "f_score_hard"
}
```

### "I'm vegetarian" → P-score hop-1
```json
{
  "id": "p_001",
  "score_tier": "P",
  "hop": 1,
  "source_in_profile": "I'm vegetarian",
  "description": "All meal venues must have vegetarian options",
  "scope": "activity_type=meal",
  "condition": {"has_tag": "vegetarian-options"},
  "aggregation": "all",
  "consequence": "p_score_full"
}
```

### "Anniversary dinner" → P-score hop-2
```json
{
  "id": "p_002",
  "score_tier": "P",
  "hop": 2,
  "source_in_profile": "celebrating our anniversary",
  "description": "At least one dinner at an upscale venue",
  "scope": ["activity_type=meal", "time_window=18:00-23:59"],
  "condition": {"field": "price_tier", "operator": ">=", "value": "upscale"},
  "aggregation": {"at_least": 1},
  "consequence": "p_score_partial"
}
```

### "Want diverse cuisines" → P-score hop-1
```json
{
  "id": "p_003",
  "score_tier": "P",
  "hop": 1,
  "source_in_profile": "foodie trip, want to try different cuisines",
  "description": "At least 3 distinct cuisines across all meals",
  "scope": "activity_type=meal",
  "condition": {"field": "cuisine_label"},
  "aggregation": {"count_distinct": 3, "field": "cuisine_label"},
  "consequence": "p_score_full"
}
```

### "Budget $80/day" → P-score hop-1
```json
{
  "id": "p_004",
  "score_tier": "P",
  "hop": 1,
  "source_in_profile": "daily budget around $80",
  "description": "Total estimated cost per day ≤ $80",
  "scope": "per_day",
  "condition": {"field": "estimated_cost_local"},
  "aggregation": {"sum": "estimated_cost_local", "operator": "<=", "value": 80},
  "consequence": "p_score_full"
}
```

### "No tourist traps" → P-score hop-1
```json
{
  "id": "p_005",
  "score_tier": "P",
  "hop": 1,
  "source_in_profile": "no tourist traps please",
  "description": "No venues tagged as tourist-trap",
  "scope": "all",
  "condition": {"not_tag": "tourist-trap"},
  "aggregation": "all",
  "consequence": "p_score_full"
}
```

### "10-year-old child" → B-score hop-3
```json
{
  "id": "b_001",
  "score_tier": "B",
  "hop": 3,
  "source_in_profile": "bringing my 10-year-old",
  "description": "No single venue visit exceeds 90 minutes (child attention span)",
  "scope": "all",
  "condition": {"field": "recommended_visit_minutes", "operator": "<=", "value": 90},
  "aggregation": "all",
  "consequence": "b_score_bonus"
}
```

### Route efficiency → B-score graph check
```json
{
  "id": "b_002",
  "score_tier": "B",
  "pattern": "route_efficiency",
  "description": "Daily route should not backtrack significantly",
  "scope": "per_day",
  "condition": {"route_efficiency_min": 0.67},
  "aggregation": "all",
  "consequence": "b_score_bonus"
}
```

### Anniversary arc → B-score LLM
```json
{
  "id": "b_003",
  "score_tier": "B",
  "hop": 3,
  "pattern": "llm_semantic",
  "source_in_profile": "celebrating our anniversary",
  "description": "The schedule builds toward dinner as its emotional and culinary peak"
}
```

---

## What Cannot Be Expressed in This Schema

1. **Schedule arc / narrative quality** → LLM judge
2. **Cross-activity narrative coherence** → LLM judge
3. **Type C emergent combination constraints** → task agent reasoning + LLM judge
4. **Weather-conditional constraints** (existing weather_aware handler handles this)

---

## Migration Path

Phase 3: new constraints expressed in generic schema.
Phase 3 evaluator: generic engine added alongside legacy handlers.
Phase 4 cleanup: legacy handlers expressed as schema instances, removed.

Legacy handler names remain as aliases:
  `label_required` → scope+condition+aggregation pattern
  `numeric_aggregate` → sum aggregation pattern
  etc.

---
*Document status: design phase — schema not yet implemented in evaluator*
*See also: score_tier_definitions.md, personalised_benchmark_design.md*
