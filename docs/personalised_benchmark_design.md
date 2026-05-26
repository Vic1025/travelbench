# TravelBench — Personalised Benchmark Design
*Internal reference · Sprint B*

---

## Core Concept

Current TravelBench uses explicit constraints — the rubric directly states
what the agent must satisfy.

Personalised benchmark uses implicit constraints — the user states a
situation, and the agent must infer what that situation demands.
This tests world-model reasoning about human needs.

---

## Hop Definition

Hop count measures how many non-obvious reasoning steps a thoughtful human
needs to derive a constraint from a stated signal. It is NOT the evaluator's
logic chain — it is the human reasoning difficulty.

**Hop 1:**
One direct inference any careful reader makes immediately.
No need to think about what the situation feels like.
Hop-1 signals divide into two cases by consequence:

  Case A → F-score: the derived constraint reflects physical or legal
  impossibility. Violating it means the traveller literally cannot
  complete the activity.
    "I'm bringing my 10-year-old" → bars refuse entry to minors → F
    "I use a wheelchair" → inaccessible venue cannot be entered → F
    "severe nut allergy" → eating there poses a health risk → F
    "my dog is with me" → venue prohibiting dogs cannot be entered → F

  Case B → P-score standard weight: the derived constraint shapes
  preference or quality. Violating it is inconsiderate, not impossible.
    "I'm vegetarian" → vegetarian options needed at meals → P
    "budget $60/day" → daily cost must stay under $60 → P
    "not a morning person" → no activities before noon → P
    "want hidden gems" → low-traffic venues preferred → P

**Hop 2 → P-score reduced weight:**
Requires mentally stepping into the person's situation and reasoning
about what it feels like to be them.
  "single parent with young child" → managing alone in crowds is harder
    than with a partner → quieter, less crowded venues preferred
  "elderly parent" → energy lower, recovery slower
    → shorter day, more rest time, tighter geography
  "anniversary trip" → this dinner matters more than usual
    → at least one upscale dinner, in the evening

**Hop 3 → B-score bonus:**
Requires modelling human physiology, psychology, or social dynamics
that most people wouldn't spontaneously apply without being prompted.
  "10-year-old child" → children's attention span caps around 90min
    → no single venue visit should exceed 90 minutes
    → afternoon energy drop likely → rest window needed mid-afternoon
  "anniversary" → the schedule should build toward dinner as its peak
    → narrative arc: lighter activities during the day, special dinner last

The key test: would a thoughtful but non-specialist reader derive this
constraint immediately (hop 1), after a moment's reflection on the
situation (hop 2), or only with specific knowledge of child development
or human psychology (hop 3)?

The F vs P divide happens at hop-1: impossible consequence → F-score,
preference consequence → P-score. Hop-2 and hop-3 signals almost always
land in P/B-score — deep inferences are about human preference, not
physical impossibility.

---

## Scoring Architecture

**F-score:** constraints derived from hop-0 signals. Physical impossibility.
Any violation makes the plan infeasible for this traveller.

**P-score:** constraints derived from hop-1 and hop-2 signals.
Agent was never told the constraint — but the derivation is clear.
Graded 0/0.5/1.0. No single failure makes the plan infeasible.
Hop-1 constraints carry full weight; hop-2 carry reduced weight.

**B-score:** constraints derived from hop-3 signals, plus route efficiency.
Bonus credit only — additive, never penalises.
Rewards agents that reason about human needs at depth.

---

## Persona Signal Categories

These are the menus of situations the task generation agent draws from
when constructing user queries. Each category describes a dimension of
who the traveller is, what they need, or why they're travelling.

Some categories are exhaustable (Category 1 — there are only so many
group compositions). Others provide representative examples rather than
exhaustive lists (Category 2 — any dietary restriction follows the same
F/P pattern based on severity).

---

### Category 1 — Traveller Composition
*Who is in the group. Exhaustable.*
*Note: business traveller and romantic trip are occasions (why), not
compositions (who) — they appear in Category 7.*

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| Solo traveller | 1 | P | social_match group_size=1 |
| Couple | 1 | P | social_match group_size=2 |
| Group of friends (N people) | 1 | P | social_match group_size=N; group-friendly venues |
| Family with young children (under 12) | 0 | F | family_friendly=true; age_restriction=0 |
| — | 2 | P | quieter, less crowded venues; relaxed pace |
| — | 3 | B | max visit_minutes ≤ 90; afternoon rest window |
| Family with teenagers (13-17) | 1 | P | no hard age restriction; later start ok |
| Multi-generational (with elderly member) | 2 | P | pace relaxed; district_count_max tighter |
| Travelling with dog | 0 | F | dog_friendly=true all venues |
| Single parent | 2 | P | noise_level_max moderate; relaxed/moderate pace |

The hop-1 divide within this category:
  Hop-1 signals that make activities physically impossible → F-score
    (dog present, young child at age-restricted venues)
  Hop-1 signals that shape preference or quality → P-score
    (solo traveller, couple, group size)
The same pattern applies across all categories — hop count alone does
not determine tier, consequence does.

---

### Category 2 — Physical and Dietary Needs
*Facts about the traveller's body — mobility, medical requirements,
dietary restrictions. Representative examples; any restriction follows
the same severity pattern.*

Severity determines tier:
- Stated as medical necessity, physical impossibility, or safety risk → F-score
- Stated as preference or lifestyle choice → P-score

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "I use a wheelchair" / "paralysed" / "cannot climb stairs" | 0 | F | wheelchair_accessible=true all venues |
| "limited mobility" (ambiguous degree) | 2 | P | wheelchair_accessible preferred; pace relaxed |
| "I have a [food] allergy" (medical) | 0 | F | official_site allergen verification required |
| "coeliac disease" (medically diagnosed) | 0 | F | gluten-free required all meals |
| "vegan / vegetarian" (lifestyle) | 1 | P | label_required veg-options at all meals |
| "gluten-free" (preference) | 1 | P | label_required gluten-free options |
| "halal / kosher" | 1 | P | label_required at all meals |
| "I don't drink alcohol" | 1 | P | no bar as primary; non-alcoholic options available |
| "afraid of heights" | 2 | P | LLM judge: avoid rooftop/observation venues |

Escalation rule: "limited mobility" → "in a wheelchair" → "paralysed"
escalates from P-score to F-score as the physical impossibility becomes clear.
When ambiguous, default to P-score and note in source_in_profile.

---

### Category 3 — Schedule and Rhythm
*How the person structures their day.*

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "won't be up before noon" | 1 | P | no activity starts before 12:00 |
| "like to start early, before 9am" | 1 | P | first activity open by 09:00 |
| "want to take it slow" | 1 | P | pace_relaxed; no intense venues |
| "packed itinerary, want a lot" | 1 | P | category_count_minimum high |
| "dinner after 20:00" | 1 | P | dinner time_start ≥ 20:00 |
| "want evenings free" | 1 | P | last activity ends ≤ 18:00 |
| "long lunches are important to me" | 2 | P | lunch duration ≥ 90 min |
| "need a mid-day break" | 3 | B | LLM: quiet seated rest window 13:00-15:00 |

---

### Category 4 — Budget and Value

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "budget of $X per day" | 1 | P | numeric_aggregate ≤ X per day |
| "budget traveller / on a tight budget" | 1 | P | price_tier_required max=budget |
| "I'm a student" | 1 | P | price_tier_required max=mid; free-entry preferred |
| "money is no object / happy to splurge" | — | — | no constraint; opens premium venue pool |

Note: budget + occasion combinations (e.g. "tight budget, but want
something special for anniversary") are Type B intersecting constraints —
two independent handlers that create satisfaction difficulty.

---

### Category 5 — Local Authenticity

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "no tourist traps" | 1 | P | label_excluded tourist-trap |
| "want hidden gems / less known spots" | 1 | P | hidden_gem_required |
| "first time, want the classics" | 1 | P | prefer high-traffic iconic venues |
| "been before, want something different" | 1 | P | label_required hidden-gem, locals-favourite |
| "want to eat like a local" | 1 | P | local_cuisine_preference ≥60% meals |

---

### Category 6 — Interests and Activity Type
*Representative examples — any interest maps to label_required or
category_count_minimum on the appropriate tag/category.*

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "love museums / art galleries" | 1 | P | category_count_minimum museum/attraction ≥ N |
| "photography is important" | 1 | P | regulation_required photography_allowed |
| "love the outdoors" | 1 | P | label_required outdoor + weather_aware check |
| "want to try diverse cuisines" | 1 | P | cuisine_diversity_minimum ≥ N |
| "nightlife / late night" | 1 | P | venues open until ≥ 22:00 |
| "live music" | 1 | P | label_required live-music |
| "architecture enthusiast" | 1 | P | label_required architecture |
| "coffee enthusiast" | 1 | P | category_count_minimum cafe ≥ N |

---

### Category 7 — Occasion and Purpose
*Why they're travelling. Business and romantic trip belong here, not in
Category 1. Category 1 says who is present; Category 7 says why.*

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "anniversary trip" | 1 | P | social_match occasion=anniversary |
| — | 2 | P | at least one upscale evening dinner |
| — | 3 | B | schedule arc: dinner as emotional peak |
| "honeymoon" | 1 | P | social_match occasion=anniversary |
| — | 2 | P | noise_level_max quiet; avoid crowded venues |
| "birthday celebration" | 1 | P | social_match occasion=birthday |
| "business trip" | 1 | P | social_match occasion=business |
| — | 2 | P | dress_code_required smart_casual; reliable booking |
| "romantic trip" (couple, no specific occasion) | 1 | P | social_match occasion=romantic; ambience matters |
| "exhausting week, want total relaxation" | 1 | P | pace_relaxed; noise_level_max quiet |
| "first time in this city" | 1 | P | high-traffic iconic venues preferred |
| "been before, returning visitor" | 1 | P | hidden-gem, locals-favourite preferred |
| "celebrating a milestone (promotion, graduation)" | 2 | P | at least one upscale experience |

Note: anniversary and couple often co-occur but are independent signals.
A solo anniversary trip is valid. A couple on a purely practical trip
doesn't trigger anniversary constraints.

---

### Category 8 — Logistical Constraints
*Transport, timing, and geography.*

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "no car, public transport only" | 1 | P | transport_mode_required transit |
| "walking only / no transport" | 1 | P | transport_mode_required walking |
| "staying in [district]" | 1 | P | district_count_max tight; anchor near district |
| "want to stay in one area" | 1 | P | district_count_max 1-2 |
| "flight at [time] on last day" | 1 | P | last activity ends ≥ 2hr before flight |
| "arriving late, afternoon only on day 1" | 1 | P | day 1 first activity ≥ 14:00 |

---

### Category 9 — Specific Venue Requests

| Signal | Hop | Tier | Constraint derived |
|--------|-----|------|-------------------|
| "I want to visit [venue name]" | 1 | P | must_visit venue_id |
| "must include [landmark]" | 1 | P | must_visit venue_id |

Note: specific venue requests interact with the rest of the schedule
as cross-activity dependency constraints (relative_to) when timing
around the requested venue matters.

---

## Combination Effects

**Type A — Additive (independent constraints, additive satisfaction difficulty):**
  "vegetarian" + "budget" → two independent handlers, both apply
  No new constraint logic. Satisfaction difficulty = moderate.

**Type B — Intersecting (same handlers, harder to satisfy):**
  "vegetarian" + "anniversary" → upscale vegetarian restaurant
  Still two independent handlers. Intersection is rare in venue pool.
  No new constraint logic needed. Satisfaction difficulty = high.

**Type C — Emergent (neither signal alone generates the constraint):**
  "first time visitor" + "tight budget"
    → prefer free/cheap iconic venues
    → cannot be decomposed into two independent handlers
  Task generation agent must reason about the combination, or LLM judge.

---
*See also: score_tier_definitions.md, generic_constraint_schema.md*

---

## Task Generation Agent — Information Access

### What the task agent receives from venue data
The task generation agent receives venue ground truth and character
descriptions only — NOT the full source doc bodies.

Specifically per venue:
- name, category, district, traffic_tier
- One-sentence character description (from generation brief)
- Tags (what kind of experience it offers)
- has_wrong_info flag (boolean — existence only, not what the wrong info is)
- seasonal_windows list (which windows this venue operates in)
- Event info if exists (name, dates, capacity_limited flag)

**Why not full source docs:**
Source docs belong to the planning agent's information environment.
They are what the planning agent discovers through tool calls.
Giving the task agent source doc bodies would risk:
- Accidentally echoing stale source wording in user queries
- Introducing bias toward venues with richer source doc descriptions
- Conflating the task generator's knowledge with the planning agent's knowledge

**Why character + tags is sufficient:**
For high/mid traffic real venues: world knowledge covers the rest.
For low-traffic fictional venues: the character description and tags
are the only definition of what the venue is — sufficient to write
a plausible user query that would lead someone there.

### Wrong info and task design
The task agent knows *which* venues have wrong info (has_wrong_info=True)
but not *what* the wrong info is. This is intentional:

- The task agent does not deliberately route tasks through wrong-info venues
- Wrong info distribution mirrors reality: concentrated in low/mid traffic
  venues because that's where it persists in the real world
- Tasks are not designed as controlled obstacle courses — they should feel
  like realistic travel planning requests
- An agent that learns to distrust all low-traffic venues because "those
  have traps" is learning a benchmark heuristic, not a real-world skill

**The natural self-regulation:**
High-traffic venues → rarely have wrong info + often get specific requests
Low-traffic venues → often have wrong info + rarely get specific requests
The overlap (specific request for a wrong-info venue) is naturally rare,
which mirrors real-world planning uncertainty.

Users can request specific venues in their query. If that venue happens
to have wrong info, the trap is encountered naturally — not engineered.
This is the intended mechanism.
