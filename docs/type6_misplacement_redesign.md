# Type 6 Redesign — Persona Misplacement

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.

**Status:** 🔴 Design phase  
**Supersedes:** P16 (context-window tension) — that design never produced B-score constraints
and degenerated to type1. This replaces it entirely.

---

## Core concept

Current type6: "persona preference conflicts with seasonal window event scheduling."  
**New type6: person is fundamentally MISPLACED — their identity or obligation is in direct
tension with where/when they are.**

The difficulty comes not from navigating event schedules but from the structural mismatch:
- What the person needs barely exists in this environment
- OR the person has a fixed obligation that reshapes everything around it
- The P-constraints emerge from how the person COPES with being misplaced — not from
  what they freely prefer

This is structurally distinct from all other types:
| Type | Tension source |
|------|---------------|
| 1 | Cascading user preferences |
| 2 | Time budget forces selection |
| 3 | Two of the user's own desires compete |
| 4 | Budget precision |
| 5 | Hard feasibility gate |
| **6** | User's core identity vs what the environment actually provides |

---

## The four misplacement scenarios

### Scenario A — Business meeting in carnival (Notting Hill / Rio)

**Setting:** A formal business professional must host a client during the city's most
chaotic festive window. The city is full of street parties, road closures, and festive
atmosphere incompatible with formal business.

**Query structure:**
> "I have a critical client dinner with a senior partner on [parade day]. It needs to
> be at a Michelin-level restaurant — formal, quiet, professional atmosphere. We'll
> want to show the client around during the day but need to avoid the parade routes.
> I need somewhere impressive that still works when the city is in carnival mode."

**P-constraints derivation (coping strategy):**
- **Core need**: fine-dining dinner in the evening  
  → `at_least 1 | scope=['activity_type=meal','time_window=19:00-23:59'] | condition={price_tier:'fine-dining'}`
- **Avoid chaos**: must stay outside the parade/closure zone  
  → `all | scope=all | condition={in_closure_zone:False}` ← requires window_flag promotion (see gaps)
- **Show client around**: iconic sights but with manageable crowds  
  → `at_least 2 | scope=activity_type=visit | condition={traffic_tier:'low'}`
- **Stay focused geographically**: don't cross parade routes multiple times  
  → `at_most_distinct 2 districts | scope=all`

**Misplacement tension:** Fine dining exists in London during Notting Hill Carnival, but
many are closed, in closure zones, or swamped. The solver must find the intersection of
"impressive restaurant" AND "accessible on parade day."

---

### Scenario B — Funeral during festive season

**Setting:** A person must attend a friend's funeral during the city's most celebratory
period. Everything around them is loud and festive; they want the opposite.

**Query structure:**
> "I'm attending my close friend's funeral at [church name], [district], on [day] at
> [time]. I'll be staying for 3 days total. I don't want to travel far — we both lived
> in [neighbourhood], so I want to stay there and just move around locally. I want to
> avoid the parade and the tourists. Something quiet and reflective will do."

**Anchor expressed in query, NOT as a P-constraint.** The funeral is a fixed obligation
that the solving agent reads from the query text. The planning constraint is:

**P-constraints derivation (coping strategy):**
- **Stay local**: entire trip centered on the funeral neighbourhood  
  → `at_most_distinct 1 district | scope=all` (or `ratio ≥ 0.9 | scope=all | condition={district:'Saúde'}`)
- **Avoid crowds**: no carnival areas, no high-traffic tourist spots  
  → `all | scope=all | condition={traffic_tier:'low'}`
- **Appropriate atmosphere**: venues should be quiet  
  → `all | scope=all | condition={noise_level:'quiet'}`
- **Don't over-schedule**: reflect, don't rush  
  → `at_most 3 | scope=per_day | condition={}` ← soft daily activity cap

**Misplacement tension:** The entire city is celebrating. Finding quiet, solemn, locally-
concentrated venues in a city that is fundamentally NOT quiet that week is the challenge.

**Note on anchor venue:** If the funeral church is NOT in the pool, it should be added
as a venue with category `attraction` or `other`. The anchor constraint  
`at_least 1 | scope=['venue_id=CHURCH_ID','time_window=14:00-15:30']`  
works with the existing framework and was verified to correctly:
- Pass when the church is visited in the correct time window
- Fail when visited at the wrong time
- Fail when not visited at all

---

### Scenario C — Nature/landscape photographer assigned to an architectural city

**Setting:** A professional 自然风光 (landscape/nature) photographer is sent to London
(or any major urban centre) by their employer. London is a 建筑/人文 (architectural/
cultural) city with almost no natural-scenery venues. They must work with what exists.

**Query structure:**
> "I've been assigned to photograph London for 3 days. I specialise in natural
> landscapes, not urban architecture. I need to visit every outdoor/natural spot
> London has — parks, gardens, any elevated viewpoint for wide shots. Every single
> venue I visit (except meals) absolutely must allow photography, and ideally be good
> for photos. Are there any spots with natural light and open space?"

**P-constraints derivation (core need dominates, coping is exhaustion):**
- **Photography allowed everywhere**:  
  → `all | scope=activity_type=visit | condition={photography_allowed:True}`
- **Only natural/outdoor venues for visits** (the sparse pool IS the difficulty):  
  → `all | scope=activity_type=visit | condition={has_tag:'outdoor'}`
- **Instagrammable / photogenic**:  
  → `ratio ≥ 0.7 | scope=activity_type=visit | condition={has_tag:'instagrammable'}`
- **Visit every qualifying venue** (only ~5-7 exist in London):  
  → `at_least 5 | scope=activity_type=visit | condition={has_tag:'outdoor'}`

**Misplacement tension:** London has ~5 outdoor venues. A 3-day trip typically needs
9-12 visit slots. The pool is structurally undersized for this persona — the solver
must pad with meals and re-examine what "outdoor" means in an urban context.

**Note:** This scenario's P-constraints ARE generalisable (photography_allowed + outdoor
tag). Unlike other scenarios, the coping strategy here is simple exhaustion of a sparse
pool rather than navigation around an event. Worth checking pool size against day count
at task generation to ensure the task isn't trivially solved or unsolvable.

---

### Scenario D — Conference attendee (anchor_activity pattern)

**Setting:** A professional attends a multi-day conference with fixed morning and
afternoon sessions. They want to maximise useful sightseeing and meals in the gaps,
but must stay near the conference venue and work around the session schedule.

**Query structure:**
> "I'm at the ExCeL London conference, sessions run 9am–12pm and 2pm–5pm each day.
> I have lunch breaks and evenings free. I want to explore around the venue area —
> the Royal Docks neighbourhood. No need to go central, just good food and maybe
> one cultural stop per evening. Budget is tight on the conference card."

**P-constraints derivation (obligation-first, fill the gaps):**
- **Morning session (fixed):**  
  → `at_least 1 | scope=['venue_id=EXCEL_VENUE_ID','time_window=09:00-12:00']` per day
- **Afternoon session (fixed):**  
  → `at_least 1 | scope=['venue_id=EXCEL_VENUE_ID','time_window=14:00-17:00']` per day
- **Stay local**: Royal Docks / Newham only  
  → `at_most_distinct 1 district | scope=all`
- **Evening cultural stop**:  
  → `at_least 1 | scope=['activity_type=visit','time_window=17:30-21:00']` per day
- **Budget constraint** (conference card):  
  → `sum | scope=per_day | condition={}` with `budget_per_day` in query_resources

**Why this is type6 not type4:** The difficulty isn't budget calibration — it's the
interplay between fixed session anchors (which eat the day), geographic restriction
(Royal Docks has limited venues), and the desire to still have meaningful experiences.
The session anchor changes WHEN activities can happen, not just WHAT they cost.

---

## Common patterns across scenarios

| Pattern | Constraint form | Applicable to |
|---------|----------------|---------------|
| Core identity need | `at_least N on qualifying type` | All scenarios |
| Avoid the environment | `all scope=all, avoidance_condition` | A, B |
| Locality radius | `at_most_distinct 1 district` OR `ratio scope=all district=X` | B, D |
| Sparse pool exhaustion | `at_least N` on a tiny qualifying set | C |
| Temporal anchor | `scope=['venue_id=X','time_window=T1-T2']` | B (funeral), D (sessions) |
| Don't over-schedule | `at_most N scope=per_day` | B |
| Budget within constraint | `sum scope=per_day` | D |

**Key insight on P-constraint generation:** Unlike other types where constraints are
freely derived from user preferences, misplacement type6 constraints are SITUATIONALLY
derived — they emerge from how a specific persona copes with a specific mismatch.
There is NO universal template. The generation protocol must guide agents to:
1. Identify the persona's non-negotiable core (nature photography, business formality, grief)
2. Identify what the environment makes HARD for this persona
3. Derive constraints from the COPING STRATEGY for that specific clash

---

## Open gaps requiring resolution

### Gap 1 — Window-flag conditions in constraint engine
`in_closure_zone` and `in_wider_affected_area` exist in `window_flags` per venue but
the constraint engine cannot reference them. To express "avoid parade zone" in Scenario A,
we need to promote these to queryable venue fields for the current window.

Options:
- Materialise window_flags into venue-level boolean columns at pool-load time
- Add a `window_condition` field to the condition schema handled separately
- Proxy: use `traffic_tier='low'` as an imperfect substitute (parade-affected areas
  are usually high-traffic, but not always)

### Gap 2 — Anchor venue in pool
For Scenario B (funeral) and similar, the anchor location may not be in the venue pool.
Options:
- Add the venue to the pool with a special category (`anchor`, `obligation`)
- Accept that the anchor is in the query text only (not a P-constraint) and the P-constraints
  handle the downstream requirements (locality, atmosphere). This works if the solving
  agent can infer the anchor's district from the query text.

### Gap 3 — P-constraint derivation is situation-dependent
There is no generalizable set of constraint patterns for misplacement type6. Each task
requires a different coping strategy. The generation handbook must explain the CONCEPT
(misplacement + coping) not a recipe. This makes type6 the hardest type to generate
consistently high-quality tasks for.

---

## What replaces B-score (removed)

All evaluation must go through P-score. The checks previously imagined for B-score:
- "Did agent find the restaurant is NOT in closure zone?" → P-constraint with
  `condition={in_closure_zone:False}` + `check_method=code` evaluates this in the plan
- "Did agent verify conference venue hours?" → `at_least 1 scope=['venue_id=X','time_window=...]`
  fails if agent doesn't schedule the venue at the right time
- "Did agent stay in the right district?" → `at_most_distinct 1 district` handles this

The misplacement scenarios ARE evaluable through P-score alone — the constraints capture
the coping behavior, not the reasoning process.

