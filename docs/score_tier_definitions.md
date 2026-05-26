# TravelBench — Score Tier Definitions and Constraint Classification

> ⚠ **Pre-P22 scoring design.** The C/F-score mechanics have since been
> redesigned (BFCL-style deduction for C; deduction model for F, no hard fails).
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) "Done this phase"
> entries for P22 / P22-F, and `eval/evaluator.py` for the implementation.
> The constraint-tier semantics (C/F/P/B) and tier definitions in this doc remain
> accurate.
*Internal reference · Phase 3 design*

---

## The Three Score Tiers

### C-score — Competence
Did the agent use its tools correctly and consult the right sources?
Tool call validity, source_required checks, information retrieval quality.
Independent of whether the final plan is good — measures the process.

### F-score — Feasibility
Is the plan physically executable in the real world?

**Hard constraints (any fail → F-score = 0, plan infeasible):**
- Opening hours: venue must be open at scheduled time
- No overlap: activities cannot overlap in time
- Travel time: gap between activities must cover physical travel
- Ticket availability: venue must not be sold out on that date
- Physical impossibility arising from traveller situation (see boundary section)

**Partial constraints (graded):**
- Travel buffer: gap − travel ≥ 30 min
- Visit duration within recommended bounds

### P-score — Personalisation
Did the agent understand and honour the traveller's personal needs?
Scored against constraints derived from the user's stated situation.
Graded 0/0.5/1.0 per constraint — no single constraint causes total failure.

### B-score — Bonus / Proactive Quality
Did the agent reason deeply about what this specific person needs,
beyond what could be immediately derived?
Additive only — cannot reduce base score.
Covers multi-hop inferences, schedule arc quality, route coherence.

---

## The F/P Boundary — The Correct Test

The tier is NOT determined by:
- Whether the constraint was explicitly stated
- How many inference hops it required

> **The test: would violating this constraint make the plan physically
> impossible or legally unworkable for this specific traveller?**
> If yes → F-score. If no → P-score.
>
> Practically: the divide happens within hop-1. Those that make
> activities physically impossible → F-score (Case A). Those that
> shape preference or quality → P-score (Case B). Hop-2 and hop-3
> almost always land in P/B-score — deep inferences are about
> human preference, not physical impossibility.

A constraint requiring multiple inference hops can still be F-score
if the consequence is physical impossibility.
A directly stated constraint can still be P-score
if violating it is inconsiderate but not physically impossible.

### F-score examples (physical/legal impossibility)
These are almost always hop-1 Case A — the impossible consequence
is immediately obvious.

"I use a wheelchair"
  → Consequence: inaccessible venues cannot be physically entered
  → F-score: wheelchair_accessible=true all venues

"I'm bringing my 10-year-old"
  → Consequence: age-restricted venues legally refuse entry to minors
  → F-score: age_restriction=0 all venues

"My dog will be with me"
  → Consequence: dog cannot enter venues that prohibit pets
  → F-score: dog_friendly=true all venues

"I have a severe nut allergy"
  → Consequence: serious health risk
  → F-score: official_site allergen verification required

### P-score examples (failure of consideration, not impossibility)

"My father has limited mobility"
  → Ambiguous degree — could be walking cane (manageable) or wheelchair (hard bar)
  → Default: inconsiderate but not necessarily physically impossible
  → P-score: regulation_required wheelchair_accessible, partial credit
  → Escalate to F-score only if statement clarifies physical impossibility
    ("my father is in a wheelchair", "my father is paralysed")

"I'm travelling with my elderly mother"
  → Infer: pace matters, mobility reduced, energy lower
  → Violating these is inconsiderate, not physically impossible
  → P-score: regulation_required + noise_level_max + pace_relaxed

"We want a romantic evening"
  → Pure preference — no physical constraint
  → P-score: social_match + LLM judge

"I'm vegetarian" (without severity)
  → Preference — not a physical impossibility to eat at other venues
  → P-score: label_required vegetarian-options
  → Escalate to F-score only if stated as a medical/safety requirement

### The escalation boundary

| Signal | Default tier | Escalates to F-score when |
|--------|-------------|--------------------------|
| "limited mobility" | P | "in a wheelchair" / "paralysed" |
| "vegetarian" | P | "severe dietary restriction" / "allergy" |
| "gluten intolerant" | P | "coeliac disease, medically diagnosed" |
| "doesn't drink" | P | religious prohibition (legal in some contexts) |
| "young child" | F | always — legal age restrictions are hard bars |

When ambiguous, default to P-score and note in `source_in_profile`.

---

## Inference Depth

Hop count measures how many non-obvious reasoning steps a thoughtful human
needs to derive a constraint from a stated signal — the human reasoning
difficulty, NOT the evaluator's logic chain.

**Hop 1:**
One direct inference any careful reader makes immediately.
No need to think about what the situation feels like.
Hop-1 divides into two cases by consequence:

  Case A → F-score: derived constraint is physically or legally impossible
  to violate without making the plan unworkable for this traveller.
    "10-year-old" → bars refuse entry to minors → F-score
    "in a wheelchair" → inaccessible venue cannot be entered → F-score
    "severe nut allergy" → serious health risk → F-score
    Note: the tier is determined by consequence, not by whether inference
    was needed. "10-year-old" requires knowing bar age limits, but the
    consequence is still physical impossibility → F-score.

  Case B → P-score standard weight: derived constraint shapes preference
  or quality. Violating it is inconsiderate, not physically impossible.
    "vegetarian" → vegetarian-options required at meals → P-score
    "budget $60/day" → daily cost ≤ $60 → P-score
    "not a morning person" → no activities before noon → P-score

**Hop 2 → P-score reduced weight**
Requires mentally stepping into the person's situation.
  "single parent with young child" → managing alone in crowds is harder
    → quieter, less crowded venues preferred
  "elderly parent" → energy lower → shorter day, tighter geography
  "anniversary" → this dinner matters more → upscale evening dinner

**Hop 3 → B-score bonus**
Requires modelling human physiology, psychology, or social dynamics.
  "10-year-old child" → attention span ~90min → max visit_minutes ≤ 90
  "10-year-old" → afternoon energy drop → rest window needed
  "anniversary" → schedule should build toward dinner as peak → LLM judge

In practice, hop-2 and hop-3 signals almost always land in P/B-score —
deep inferences are about human preference, not physical impossibility.

---

## Combination Effects

**Type A — Additive:** Two constraints apply independently. No interaction.
  "vegetarian" + "budget" → two separate handlers, moderate satisfaction difficulty.

**Type B — Intersecting:** Both constraints apply but their intersection is rare.
  "vegetarian" + "anniversary" → upscale vegetarian restaurant
  Still two separate handlers. Satisfaction difficulty increases.

**Type C — Emergent:** Combination implies a constraint neither signal generates alone.
  "first time visitor" + "tight budget" → prefer free/cheap iconic venues
  Cannot be expressed as two independent handlers.
  → Task generation agent must reason about the combination, or LLM judge.

---

## Difficulty Axes

**Axis 1: Deduction difficulty**
How hard to derive the constraints from the stated situation?
Spans F-score and P-score alike.
  Low: directly stated physical need
  Medium: 1-hop P-score inference
  High: 2-3 hop P/B-score inference
  Very high: Type C emergent combination

**Axis 2: Satisfaction difficulty**
How hard to find venues jointly satisfying all constraints?
  Low: common constraints (family_friendly, budget)
  Medium: moderately rare (upscale vegetarian)
  High: rare intersection
  Very high: potentially unsatisfiable → agent should flag uncertainty

These axes are independent. F-score constraints can be hard to deduce
but trivially satisfied ("10-year-old" → avoid bars — bars are few).
P-score constraints can be directly stated but hard to satisfy
("upscale vegetarian", explicitly requested).

---

## Constraint Classification Checklist

1. **Hop count + consequence:** How many reasoning steps to derive,
   and what is the consequence of violation?
   1 hop, physical/legal impossibility → F-score hard constraint
   1 hop, preference/quality → P-score standard
   2 hops → P-score reduced weight
   3 hops → B-score bonus

3. **Combination check:** Type C emergent constraint?
   YES → flag for LLM judge or task agent reasoning
   NO → decompose into independent handler activations

---

## Implementation: score_tier field on constraints

F-score physical constraint:
```json
{
  "id": "f_physical_001",
  "score_tier": "F",
  "check_method": "code",
  "source_in_profile": "I'm bringing my 10-year-old",
  "description": "No age-restricted venues — child cannot be admitted",
  "params": {"regulation_key": "age_restriction", "required_value": 0}
}
```

P-score hop-1:
```json
{
  "id": "p_001",
  "score_tier": "P",
  "hop": 1,
  "pattern": "label_required",
  "check_method": "code",
  "source_in_profile": "I'm vegetarian",
  "params": {"activity_type": "meal", "required_label": "vegetarian-options"}
}
```

B-score bonus:
```json
{
  "id": "b_001",
  "score_tier": "B",
  "hop": 3,
  "pattern": "llm_semantic",
  "source_in_profile": "10-year-old child",
  "description": "Schedule includes a quiet rest window in the mid-afternoon"
}
```

---
*Evaluator note at line 641 — "regulation/dietary/accessibility in P-score" —
is correct only for inferred constraints. Stated physical impossibilities belong in F-score.*

---

## Final Score Formula

```
if any C check fails:     final_score = 0
if any F hard fail:       final_score = 0
else: final_score = 0.7 × F_partial + 0.3 × P + 0.2 × B
```

**C — full gate (any failure → 0):**
All C checks are hard gates. This includes source_required checks —
if the agent schedules a venue without consulting any document about it,
that is a hard failure. The benchmark tests information retrieval, not
world knowledge recall. An agent relying entirely on world knowledge
for precise scheduling introduces uncertainty the benchmark is designed
to expose.
C_hard (tool validity, no hallucinated venues) and C_soft (source checks)
are treated identically — any failure zeros the final score.

**F — hard fails zero final score; partial checks contribute 0.7 weight:**
Hard fails (venue closed, time overlap, travel physically impossible,
ticket sold out, physical persona impossibility) → final_score = 0.
These are not scoring opportunities — they represent plans that don't work.
F_partial (travel buffer, visit duration bounds) → graded 0-1,
weighted at 0.7 in the composite.

**P — 0.3 weight:**
Graded 0/0.5/1.0 per constraint. No single P failure zeros the score.
Covers hop-1 and hop-2 persona inferences.

**B — 0.2 bonus, additive:**
Cannot reduce the base score. Maximum total score = 1.2.
Covers hop-3 inferences and route efficiency.
Fixed coefficient x=0.2 regardless of how many B constraints the task has.
B is normalised 0-1 across however many B constraints exist for the task.

**Rationale for structure:**
C, F, P, B serve parallel functions — they are not ordered by difficulty.
C and F hard failures represent plans that are fundamentally broken
(bad process or impossible execution). These override everything else.
The 0.7/0.3 F/P split reflects that a feasible but generic plan is
better than a personalised but infeasible one.
The 0.2 B bonus signals clearly that it is above-and-beyond quality,
not a core requirement.

---

## Tool Set — Planning Agent

**Current tools (Phase 1/2):**
  search_yelp(query, city)
  search_blogs_and_forums(query, city)
  get_official_site(venue_id, city)              ← needs date parameter
  get_travel_time(from_venue_id, to_venue_id, city)
  find_nearby_venues(anchor_venue_id, city, max_walk_minutes)
  get_weather_forecast(city, date)

**Required changes for Phase 3:**
  get_official_site(venue_id, city, date=None)
    When date provided: returns date-specific hours, ticket availability
    for that date, and any active event overrides.
    This is the critical unlock for seasonal conditional wrong info
    and ticket availability traps. Without it, Christmas closures,
    Carnival shutdowns, and sold-out scenarios are undetectable.

  All tools: read from SQLite DB for new cities (London, Hokkaido, Rio).
  Fall back to flat JSON for Paris (backward compatibility).

**Tools deliberately not added:**
  get_venue_details(venue_id) — bypasses search, defeats retrieval purpose
  get_seasonal_events(city, date_range) — agents should discover events
    through get_official_site and search_blogs_and_forums
  get_ticket_availability(venue_id, date) — bundled into get_official_site;
    separating it creates a shortcut around full venue information retrieval

---

## Final Scoring Formula

```
if any C check fails:    final_score = 0
if any F hard fail:      final_score = 0
else:
  final_score = 0.7 × F_partial + 0.3 × P + 0.2 × B
```

**C-score — full gate (any failure → 0):**
Includes: tool call validity, no hallucinated venues, source_required checks.
source_required is a hard fail: scheduling a venue without consulting any
related document is the behaviour the benchmark is designed to penalise.
Agents must not rely solely on world knowledge for precise scheduling.

**F hard fails — full gate (any failure → 0):**
hours_check, no_overlap, travel_time_hard, ticket_availability,
explicit_physical persona constraints (child at age-restricted venue,
wheelchair user at inaccessible venue, dog at no-pets venue, severe
allergy at unsafe venue).
Rationale: C, F, P, B are parallel in function. Anything in F that
completely invalidates the plan design should zero the score, same as C.

**F_partial — graded 0-1 (only evaluated if no hard fails):**
travel_buffer, visit_duration_within_bounds.
These are quality dimensions, not binary failures.

**P — graded 0-1:**
Personalisation constraints derived from persona signals (hop-1 and hop-2).
Weighted average across all P-score constraints in the task.

**B — graded 0-1, bonus only:**
Hop-3 inferences + route efficiency + structural quality checks.
Additive, never penalises. B=0 does not affect base score.
B=1 adds 0.2 to final score. Theoretical maximum: 1.2.

B-score checks are implemented in two ways:
  1. Generic schema engine: common patterns (ratio, route efficiency,
     consecutive-pair tag checks, relative_to anchor events)
  2. Task-specific Python scripts: the task generation agent writes a
     Python script that imports benchmark functions and implements
     bespoke B-score logic for that task. Evaluator runs the script
     in a sandboxed environment with a restricted import whitelist.
     This handles any B-score logic not expressible in the generic schema.

Doc-appearance awareness checks (extends existing source_required mechanism):
  Many B-score "awareness" checks are deterministic — did the agent
  consult the doc that contains the relevant information?
  "Did the agent find free-entry venues?" → check if any source doc
    tagged free_entry=true appeared in the tool call log
  "Did the agent notice the Carnival closure?" → check if official_site
    called with Carnival date appeared in the log
  No LLM needed when a specific doc carries the relevant information.

**x = 0.2 fixed:**
Fixed constant rather than task-dependent to keep scoring predictable.
Tasks naturally vary in B-score opportunity based on persona richness —
this is intentional and reflects real persona complexity differences.

---

## Tool Set for New Cities

**Required extension — get_official_site(venue_id, city, date=None):**
date parameter enables date-specific responses:
  - Hours for that specific date (seasonal/holiday overrides)
  - Ticket availability for that date (sold-out detection)
  - Active event overrides (special hours, pricing, capacity)
Without date parameter: returns base hours + general info (current behaviour).
With date parameter: returns window-specific information.
This is the critical unlock for conditional wrong info traps and
ticket availability F-score checks.

**Infrastructure update (not new tools):**
search_yelp, search_blogs_and_forums, get_official_site, find_nearby_venues
all need SQLite DB reading for new cities, falling back to JSON for Paris.

**Tools deliberately not added:**
- get_venue_details(venue_id): direct ID lookup bypasses retrieval — defeats benchmark purpose
- get_seasonal_events(city, date_range): shortcut that bypasses blog/forum discovery
- get_ticket_availability(venue_id, date): already bundled in get_official_site

---

## Task Solvability Guarantee

Every generated task must pass a feasibility verification before publishing.
This is a lightweight arithmetic check — no LLM, no full evaluator.

**Feasibility check algorithm:**
1. Filter venue pool by all hard constraints:
   - Open on the task date(s)
   - No age restriction violations given traveller composition
   - In the required district(s) if district constraint exists
   - Category/label requirements satisfied by ≥ N venues in pool
2. Check filtered pool has enough venues to satisfy minimum count requirements
   (e.g. bar-hopping task: ≥ N bars exist in the filtered pool)
3. Check a greedy schedule fits within the time window:
   Sum(recommended_visit_minutes for selected venues) +
   Sum(travel_matrix walk_minutes between consecutive venues) ≤ available_minutes
4. If any check fails → task generation agent relaxes a constraint and retries
   (e.g. expand district, reduce minimum count, widen time window)

This reuses the same mechanism as compute_task_difficulty.py (feasible venue
set sampling) as a gate before task publication.

**Selection problem solvability (bar-hopping type):**
For tasks where the agent must choose a subset from a larger pool,
feasibility requires: ≥ N venues of the required type exist that are
open during the time window and fit within travel/time budget.
The rubric checks minimum quality (visited ≥ N, geographically clustered,
time allocated sensibly) not a specific correct subset.
