# TravelBench — Task Structural Types
*Internal reference · Sprint B*
*Describes the six structural problem types that define task difficulty shape,
distinct from the persona taxonomy (which describes signal categories) and
the scoring tier system (which describes evaluation method).*

---

## What Structural Types Are

Structural type describes **the shape of the constraint space** — what makes
finding a valid, high-quality plan hard, independent of which persona signals
triggered the constraints.

Two tasks can have identical persona signals but different structural types
depending on the venue pool and seasonal window. Two tasks can have different
persona signals but the same structural type.

Structural type determines:
- Which B-score checks are appropriate
- What "a good plan" looks like beyond satisfying minimums
- Which structural type combinations compound interestingly vs create meaningless difficulty

## Hop-2/3 Signal Rule (universal — applies to all structural types)

Every hop-2 or hop-3 constraint in the rubric must have a corresponding
natural signal stated in the query. The agent needs raw material to reason from.

BAD: query says "couple visiting Paris" → rubric has max_visit_duration constraint
     (no signal in query → agent has nothing to reason from)

GOOD: query says "my partner gets tired after a couple of hours on her feet"
      → rubric has max_visit_duration: 120min (hop-2: requires inferring the bound)

The agent still does the reasoning — deriving the specific constraint from a
natural statement is hop-2 difficulty. What's removed is the requirement to
guess which dimension matters at all.

---

## The Six Types

### Type 1 — Cascading Requirements
**What:** The query contains a persona combination that naturally produces
multiple interacting constraints. The signals are stated in the query —
the agent hears them — but translating each into the correct rubric
constraint requires genuine hop-2 reasoning about the traveller's situation.

**Design revision (April 2026):** Earlier versions treated Type 1 as
"hidden" — constraints not stated at all. This was unfair to test agents:
you cannot evaluate reasoning quality if the agent has no raw material.
The revised design keeps the inference requirement but ensures every
hop-2/3 constraint has a natural signal in the query to reason from.

**Source:** Primarily Category 1 (traveller composition) and Category 7
(occasion). The cascade happens when one persona signal implies several
downstream constraints that interact.

**Example:** "Taking my 7-year-old daughter for her birthday weekend.
She gets overwhelmed in big crowds and we need to keep moving — she
loses focus after about an hour anywhere."
The agent hears: child + birthday + crowds + attention span + birthday.
Each stated signal implies a specific constraint via hop-2 reasoning:
  "7-year-old" → family_friendly regulation (hop-1 F)
  "gets overwhelmed in crowds" → noise_level_max: moderate (hop-2 P)
  "loses focus after about an hour" → max_visit_duration: 60min (hop-2 P)
  "birthday" → at least one celebratory meal, evening preferred (hop-2 P)
  "birthday + child" → arc: museum in morning, treat/meal as day peak (hop-3 B)
None of these constraints are named. All signals are stated. The cascade
is the difficulty — deriving 4–5 constraints from 2–3 signals.

**Hop-2/3 signal rule (applies to ALL types):**
Every hop-2 or hop-3 constraint in the rubric must have a corresponding
natural signal in the query. The agent should be able to reason to the
constraint; it should not need to guess what dimension matters.
BAD: query says "couple" → constraint is max_visit_duration (no signal)
GOOD: query says "partner gets tired quickly" → max_visit_duration (signal present)

**B-score shape:**
- Did the agent apply the cascade correctly? (hop-2/3 checks)
- Does the schedule arc reflect the occasion signal? (LLM judge)
- Python script: rest window present mid-day; max_visit_duration respected

**Validity guarantee:** Always satisfiable — cascaded constraints make
the plan better, not harder to satisfy.

---

### Type 2 — Subset Selection Under Ceiling
**What:** The pool has more candidates than fit. A hard resource ceiling
(time, energy, number of days) forces the agent to choose a subset and
sequence it well.

**Source:** Category 3 (schedule/rhythm constraints), Category 8 (logistical
constraints like arrival time, flight departure), and any tight time window
from seasonal anchor events.

**Example:** Bar-hopping in one evening. 8 bars in the city, 5 hours available,
must select and sequence optimally. Or: arriving at 14:00, leaving next
morning at 07:00 — one effective afternoon/evening, choose wisely.

**B-score shape:**
- Route efficiency: MST check — did the agent sequence geographically?
- Value maximisation: did it pick the subset that best satisfies preferences?
- Time allocation: sensible duration at each venue, no padding or rushing
- Generic schema: route_efficiency (graph), per-venue time_threshold

**Validity guarantee:** Must verify ≥ N viable venues exist within the
ceiling before publishing. Core solvability check.

**Note:** Time restriction creates Type 2 (selection). Budget restriction
creates Type 4 (allocation). Distinction: can the agent choose *which* to
include (Type 2) or does it include all and decide *how much* at each (Type 4)?

---

### Type 3 — Competing Requirements
**What:** Two or more constraints pull in opposite directions. No solution
fully satisfies both simultaneously. The agent must find a tradeoff point.

**Source:** Category 5 (authenticity) vs Category 6 (interests) — "hidden
gems" vs "must see the classics." Category 7 (occasion) combined with
Category 1 (composition) — romantic trip but travelling with a group.
Any two signals whose derived constraints point at different parts of the
venue pool.

**Example:** "I want hidden gems but my partner insists on all the popular
sites." Must include iconic high-traffic venues AND hidden-gem low-traffic
venues. Neither preference can dominate.

**B-score shape:**
- Balance ratio: neither type dominates (25-75% split) — codeable
- Temporal interleaving: alternates through the day, not clustered — LLM judge
- Narrative coherence: both travellers get something genuinely theirs — LLM judge
- Generic schema: ratio aggregation on traffic_tier or has_tag=hidden-gem

**Validity guarantee:** Always satisfiable if both venue types exist in pool.
The tension is about quality of balance, not existence of a solution.

---

### Type 4 — Precision Allocation
**What:** A single binding resource (almost always budget) is tight enough
that imprecise calculation fails. The agent must allocate the resource
intelligently — not just stay under the ceiling but understand the resource
as a tool for prioritisation.

**Source:** Category 4 (budget and value), specifically when budget is
tight relative to venue costs in the pool.

**Example:** £40/day in London. Not just "stay under £40" but: recognise
museum is free, spend the budget on one genuinely good meal, skip the
paid attraction that isn't worth the tradeoff. A cheaper lunch enables
a nicer dinner. Smart allocation beats random compliance.

**B-score shape:**
- Did the agent find free-entry alternatives where they exist?
- Did it shift budget toward the highest-value use (meal vs visit)?
- Did it sequence to enable value (cheap lunch → quality dinner)?
- Generic schema: numeric_aggregate with value optimisation (LLM for quality)

**Note:** Time allocation does NOT typically create Type 4 — visit duration
at venues is not usually freely divisible in the way money is. Time
constraints create Type 2 (selection). The exception is when the agent
explicitly chooses how long to spend at a venue as part of the optimisation.

**Validity guarantee:** Must verify at least one valid combination of
venues exists within the budget ceiling. Free-entry venues are essential
for tight-budget tasks.

---

### Type 5 — Hard Feasibility Reduction
**What:** The combination of constraints eliminates most of the venue pool,
leaving only a narrow viable set — potentially 0-3 venues. The difficulty
isn't optimising among many options but discovering which options even exist.

**Source:** Category 1 + Category 2 combinations (large group + halal +
wheelchair), Category 8 + Category 2 (walking only + specific district +
dietary restriction), any combination where multiple hard filters compound.

**Example:** Group of 8, halal required, wheelchair accessible, upscale
occasion. Each filter alone is manageable. Together, the intersection may
be 1-2 venues in the entire city.

**B-score shape:**
- Did the agent verify the intersection exists before committing?
- Did it check all constraints at the remaining viable venues explicitly?
- Did it flag correctly when the intersection is near-empty?
- Generic schema: all-aggregation across multiple conditions — verify each

**Validity guarantee: CRITICAL.** This type is the most dangerous for
unsolvable tasks. The solvability check must verify the constraint
intersection yields ≥ 1 viable venue before publishing. If intersection
= 0, the task must be rejected and constraints relaxed.

**Distinct from Type 1:** Type 1 is about discovering hidden implicit
constraints. Type 5 is about the venue pool being nearly eliminated by
explicit constraint combinations. Type 1 difficulty = inference. Type 5
difficulty = the narrowness of the remaining viable set.

---

### Type 6 — Context-Window Tension
**What:** A persona preference or occasion requirement is in tension with
the seasonal window's character. The constraint isn't unsatisfiable, but
the context makes it harder, more constrained, or qualitatively different
than it would be in a neutral window.

**Source:** Category 7 (occasion/purpose) combined with seasonal window
anchor events. Category 6 (interests) combined with seasonal weather or
event context. Any signal whose requirements conflict with the window's
specific character.

**Example:** Business dinner during Carnival in Rio. The B-score checks:
- Did the agent pick a restaurant outside Carnival road closure zones?
  (scope: activity_type=meal, condition: district NOT IN carnival_districts)
- Did it schedule the dinner before peak Carnival days (Thu, not Sat/Mon)?
  (relative_to: anchor_event=carnival_peak, relationship: before)
- These are codeable from the seasonal window data.

**Example:** "Love the outdoors" during London Christmas week. Outdoor
venues still exist (Hyde Park, Regent's Canal walk) but natural light
ends at 16:00 and it's cold. The agent should schedule outdoor activities
in the morning, transition indoors in the afternoon. Codeable as
time_threshold on outdoor-tagged activities.

**B-score shape:**
- Geographic avoidance of window-specific zones — codeable
- Timing relative to anchor events — codeable (relative_to parameter)
- Qualitative contextual adaptation — LLM judge
- Generic schema: scope filtered by tag + condition relative_to anchor_event

**Validity guarantee:** Always satisfiable — the tension is about quality
of adaptation, not existence of a solution. Some venues are always unaffected
by seasonal constraints.

**Distinct from Type 3:** In Type 3, both conflicting signals come from
the user. In Type 6, one signal comes from the user (business dinner) and
the other comes from the world (Carnival). Requires cross-referencing
persona signals WITH seasonal window character — the most tightly integrated
type with the seasonal windows design.

---

## Taxonomy Signal → Structural Type Mapping

| Category | Typical structural type | Notes |
|----------|------------------------|-------|
| Cat 1 — Traveller composition | Type 1 (hidden requirements) | Young child, single parent → implicit needs cascade |
| — large group signal | Type 5 (hard feasibility) | Group size eliminates most venues |
| Cat 2 — Physical/dietary | Type 5 (hard feasibility) | Multiple hard filters compound; each alone manageable |
| Cat 3 — Schedule/rhythm | Type 2 (subset selection) | Time windows create selection problems |
| — conflicting rhythm signals | Type 3 (competing) | "Early bird" + "packed itinerary" in tension |
| Cat 4 — Budget | Type 4 (precision allocation) | Tight budget requires smart distribution |
| Cat 5 — Authenticity | Type 3 (competing) | Hidden gems vs classics is the canonical Type 3 |
| Cat 6 — Interests | Type 5 or Type 6 | Specific interest + window context → Type 6; specific interest + pool constraints → Type 5 |
| Cat 7 — Occasion | Type 1 (hidden) or Type 6 (context-window) | Anniversary → hidden schedule arc needs; business + Carnival → context tension |
| Cat 8 — Logistics | Type 2 (arrival/departure ceiling) or Type 5 (geographic + other filters) | Clean these up case by case |
| Cat 9 — Specific venue | Type 3 if conflicts with other constraints; otherwise no structural type of its own |

---

## Valid Structural Type Combinations

Some combinations compound interestingly. Others create meaningless difficulty.

**Compound well (test distinct skills simultaneously):**
- Type 1 + Type 4: single parent + tight budget → considerate planning AND smart allocation; hidden requirements happen to align with budget-friendly choices (quiet venues tend to be cheaper)
- Type 2 + Type 3: bar-hopping + partner prefers one specific bar type → subset selection where the selection criteria conflict
- Type 5 + Type 6: group of 8 wheelchair users during Carnival → narrow pool AND context-aware venue choice within that pool

**Avoid (create confusion, not difficulty):**
- Type 1 + Type 2 + Type 3: hidden inference + time ceiling + competing requirements simultaneously → agent can't demonstrate skill at any one thing
- Type 4 + Type 1 deep (hop-3): tight budget + complex 3-hop persona inference → exhausting without revealing anything interesting
- Type 5 + Type 4: nearly-empty pool + tight budget → may be genuinely unsatisfiable; solvability check critical

**Rule of thumb:** One primary structural type per task. At most one secondary type that compounds interestingly with the primary. Tasks with three or more structural types simultaneously become torture rather than benchmarks.

---
*See also: personalised_benchmark_design.md, score_tier_definitions.md,
generic_constraint_schema.md, seasonal_windows_design.md*
