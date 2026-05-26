# TravelBench — Task Review Round 1 Report

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.
**Scope:** All 22 generated tasks (types 1–6), London late_spring_2026 window  
**Session focus:** Task quality audit, framework gap discovery, bug fixes, design decisions  

---

## Overall Verdict by Type

| Type | Quality | Key issue | Best example |
|------|---------|-----------|--------------|
| 1 | 🟡 Mixed | Budget/constraint variety too narrow | `london_t1_bank_holiday.json` |
| 2 | 🟡 Mixed | Time ceiling never evaluated at solve time (P6-T4) | `lon_type2_late_spring_art_quiet_270.json` |
| 3 | 🔴 2 of 4 fake | `agg=all scope=all` used as tension instead of `at_least` | `london_may_bank_holiday_tension_museum_gems.json` |
| 4 | 🟡 Monotone | All tasks: tight budget + one special dinner | `london_t4_may_weekend.json` (most interesting) |
| 5 | ✅ Strong | Best type overall — genuine hard feasibility | All 3 pass; `lon_type5_booking_westminster.json` standout |
| 6 | 🔴 All fake | All 4 are type1 in type6 wrapper, 0 B-score | `london_type6_bank_holiday.json` (most technical) |

---

## Type 1 — Findings

**What works:** Hop-2 constraint derivation is mostly correct. Bank Holiday context
is used naturally in some tasks (Columbia Road, Bank Holiday Monday timing).

**Key good file:**
- `london_t1_bank_holiday.json` — Claude. Clean hop-2 reasoning: "strict budget" → 
  price constraint; "eating where locals eat" → popular-with-locals tag. Adds 
  `at_most_distinct: 2 districts per_day` — the strongest type1 seen.
- `lon_late_spring_type1_hackney_birthday.json` — GPT-5.4. "Staying with friends in
  Hackney" → district=Hackney universal filter (hop-2). `at_most: 1 museum per_day`.

**Agent behaviour:**
- Claude: 19/25 turns avg, efficient
- GPT-5.4: 17/25 turns
- Gemini Pro: 24/25 — 16 query_pool calls, over-exploring
- Gemini Flash: stuck 8 turns on `count_distinct cuisine_label` (field doesn't exist)

**Fix applied:** Error message for `count_distinct` on unknown field now lists valid fields
and cuisine_label removed from handbook examples.

---

## Type 2 — Findings

**What works:** Time ceiling concept is clean. Partial-day tasks are richer than full-day.

**Key good files:**
- `lon_type2_late_spring_art_quiet_270.json` — 270 min, art+coffee, noise_level 
  constraint from "quiet afternoon" (hop-2). Clean partial-day task.
- `london_spring_weekend_2d.json` — GPT-5.4. 2-day type2 with 380 min total.
  Introduced `ceiling_mode` question (P6-T4b).

**Critical framework gap discovered — P6-T4:** Time ceiling is NEVER evaluated at
solve time. The evaluator doesn't check whether the solving agent's schedule fits
within the stated time ceiling. This is a major scoring gap — the defining
constraint of type2 goes unmeasured.

**Also discovered — P6-T4b:** `ceiling_mode: contiguous | spread` switch needed.
"4.5 hours on Saturday afternoon" vs "4.5 hours total across 2 days" are 
fundamentally different tasks with different evaluation logic.

**Scoped KNN gap — P6-T5:** Museum sprint (330 min) used 4 wheelchair+family-friendly
museums. Actual minimum travel time was 366 min > 330 min ceiling. The validator
accepted it because it uses city-wide travel estimates, not scoped-pool estimates.

---

## Type 3 — Findings

**Two genuine tasks, two fake:**

**Good (genuine tension):**
- `london_may_bank_holiday_tension_museum_gems.json` — museums ≥6 vs low-traffic ≥5
  with ≤2 museums/day cap. Only 5/15 museums are low-traffic, creating real competing
  demand. Daily cap forces distribution. This is textbook type3.
- `london_type3_002.json` — budget-friendly vs upscale, zero overlap confirmed.
  Classic opposing demands on price axis. Clean.

**Bad (mislabeled):**
- `lon_type3_01.json` — pc_001 uses `agg=all scope=all traffic_tier=low` (universal
  filter, type1 structure). This passed the old tension detector. Not type3.
- `london_late_spring_type3_03.json` — Westminster universal filter + heritage + 
  museums. Heritage and museums OVERLAP heavily. No genuine opposing pull.

**Fix applied:** Type3 pre-check now requires ≥2 `at_least` inclusion constraints
and explicitly rejects `agg=all scope=all` as a "tension side."

**Protocol update:** Type3 handbook now warns that tags must not substantially overlap
(family-friendly vs history-royal-heritage = large overlap ✗; budget-friendly vs 
upscale = 0 overlap ✓).

**Agent efficiency (type3):**
- Claude: severe tension axis reuse across windows (9 submits, carnival window)
- Gemini Pro: 27-32 query_pool calls — over-exploration. Type3 only needs 3-4 pool
  checks (verify each side has ≥2×days venues, check overlap).

---

## Type 4 — Findings

**Structure is correct; creative variety is missing.**

**All 4 tasks have "tight budget + one special dinner."** Root cause: handbook said
*"name a reason for a premium spend (birthday meal, celebration dinner)"* and showed
only `price_tier >= upscale` as the example P-constraint. Agents filled the template.

**Fix applied:** Type4 protocol now presents 5 archetypes (celebration splurge,
budget challenge, business expense card, free-entry maximiser, occasion+exploration)
with explicit instruction "do not default to birthday dinner."

**Budget validation confirmed working correctly:**
- Minimum is computed from universal pool (post-constraint filter) — confirmed scoped
- Free museums (avg_cost_local=0.0) dominate site minimum → floor ~£24/day unfiltered
- Wheelchair+mid-traffic filtered pool raises floor to ~£50/day (only expensive
  restaurants survive) → explains why london_t4_may_weekend correctly shows £55

**Bugs fixed:**
- `fine-dining` was missing from `PRICE_TIER_ORDER` — every `price_tier >= upscale`
  constraint silently returned False for London's fanciest restaurants (Sketch,
  Gymkhana, Duck & Waffle, The Hawksmoor). All type4 "special dinner" constraints
  were broken.
- `not_has_tag` added as condition key (alias to existing `not_tag`).

**Novel pattern confirmed:** `scope=['activity_type=meal', 'time_window=18:00-23:59']`
— list scope combining activity type + time window. Two tasks independently generated
this for "evening meal only" constraints. The time_window IS working correctly.
Previously appeared broken due to the fine-dining price_tier bug masking results.

**Unsupported stories (recorded as P6-T13):**
- Per-day budget variation ("host pays on day 2") — not supported, schema extension needed
- YouTuber challenge `avg_cost_local <= N` per item — IS expressible via field 
  condition with `<=` operator, but NULL costs treated as out-of-budget (gap).

---

## Type 5 — Findings

**Strongest type in the pipeline. No issues found.**

All 3 tasks are genuine hard feasibility scenarios:
- `lon_type5_booking_westminster.json` — `reservation_required=True` + `district=Westminster`.
  Only a handful of Westminster restaurants require pre-booking. The solving agent MUST
  verify availability. Purest expression of type5 in the entire dataset.
- `type5_london_budget_pet_hidden_gems.json` — 4 stacked universal filters (pet_friendly
  + low-traffic + budget + moderate-pace). Each reasonable individually; combined they
  create a genuinely tiny qualifying pool.
- `LON_type5_accessibility_locals.json` — wheelchair + low-traffic + local culture. Solid.
  Minor note: `at_least scope=all, popular-with-locals` on 34 venues is barely selective.

**Agent behaviour:**
- GPT-5.4: 8-9 turns, 2 submits — outstanding. Type5's logical structure plays to its
  strengths.
- Gemini Flash: 15 get_venue calls — some over-exploration but acceptable.
- Claude: multiple iterations through full validator gauntlet (pool gap, scoped upper
  bar, etc.) — validator doing its job.

**Decision:** All solvability validation logic for type5 is confirmed working correctly.
The mechanism (stacking `agg=all` universal filters) is fine regardless of diversity of
aggregation types — what matters is that the pool is genuinely narrow.

---

## Type 6 — Findings

**All 4 tasks are type1 in type6 wrapper. 0 B-score constraints across all tasks.**

None creates genuine window tension. The Bank Holiday appears in query text but is not
encoded in P-constraints. The window is decorative.

**Tasks reviewed:**
- `lon_t6_001.json` — Anniversary at Sketch. Pure type1 with venue_id anchor.
- `lon_type6_bankholiday_monday_museum_coffee.json` — Closest to genuine type6.
  "Won't get derailed by holiday closures" → time_window scope. Right instinct but the
  P-constraint doesn't enforce that specific venues are actually open on Bank Holiday Monday.
- `london_bank_holiday_6.json` — Columbia Road anchor + district OR constraint (Hackney
  OR Tower Hamlets OR City of London). Novel `any` compound condition. Type1 with venue
  anchoring.
- `london_type6_bank_holiday.json` — Most sophisticated (6 constraints). Venue_id +
  time_window anchors for each dinner. Uses the conference-attendee anchor pattern from
  redesign doc. But anchored to user preferences, not window conditions.

**Agent behaviour:**
- Gemini Flash: 23-32 get_official_site calls — researching every venue for window-
  specific availability. Right instinct at wrong time (generation, not solving).
- Gemini Pro T17: tried to submit B-score constraint. Validator correctly rejected it.
- Claude: 22 turns, 7 submits — grinding through upper bar failures.

**Design decision confirmed:** Type6 needs the misplacement redesign 
(see `docs/subtasks/type6_misplacement_redesign.md`). The current 50% upper bar for
type6 is stricter than type1 but still produces type1-equivalent tasks.

**Novel patterns confirmed working:**
- Compound AND: `{"all": [{"has_tag":"free-entry"}, {"has_tag":"art"}]}` ✅
- Compound OR:  `{"any": [{"field":"district","operator":"==","value":"Hackney"}, ...]}` ✅
  Both validated through solvability check + P-score evaluator.

---

## Cross-Cutting Bugs Fixed During Review

| Bug | Impact | Fix |
|-----|--------|-----|
| `fine-dining` missing from PRICE_TIER_ORDER | All type4 "upscale dinner" P-constraints silently failed for London's top restaurants | Added `fine-dining` between `upscale` and `luxury` |
| Type3 tension detector accepted `agg=all scope=all` as a tension side | 2 of 4 type3 tasks are structurally type1 | Added pre-check requiring ≥2 `at_least` constraints |
| `not_has_tag` condition not supported | Agents couldn't express "must NOT have tag X" | Added as alias to existing `not_tag` |
| `get_official_site` stale booking tags in full_labels | Dishoom returned both `booking_required=False` AND `reservation-required` label — self-contradicting authoritative data | Sync labels with structured field on load |
| `avg_cost_local` missing from `get_official_site` | No correction path for price wrong-info venues | Added to official site response |
| Moro venue page_status `'committed'` | get_official_site returned "unknown venue_id" for Moro | Fixed to `'verified'` in venues, yelp_listings, official_site_docs |
| Columbia Road `hours_overrides` missing | Bank Holiday Monday schedule rejected as "venue closed" | Added modified hours entry for 2026-05-25 |

---

## Cross-Cutting Framework Gaps (New TODOs)

| ID | Item | Priority |
|----|------|----------|
| P6-T1 | Tag taxonomy standardisation | 🔴 |
| P6-T4 | Type2 time ceiling never evaluated at solve time | 🔴 |
| P6-T4b | Type2 ceiling_mode (contiguous vs spread) switch | 🔴 |
| P6-T5 | Type2 scoped KNN gap | 🔴 |
| P6-T7 | Blank time formula redesign (gap-based, explicit) | 🟡 |
| P6-T8 | recommended_visit_minutes from Foursquare timeSpent | 🔴 |
| P6-T9 | Revisit same venue: F4a sum + at_least deduplication | 🔴 |
| P6-T10 | Non-whole-day tasks beyond type2 | 💡 |
| P6-T11 | Wrong-info evaluation: 3 distinct problems | 🔴 |
| P6-T12 | Currency via open package (not hardcoded) | 🟡 |
| P6-T13 | Per-date budget support in type4 | 💡 |

---

## Key Design Decisions Made

1. **Type3 tension requires two `at_least` inclusion constraints.** `agg=all scope=all`
   is a pool filter (type1 logic) and must not be counted as one side of a tension pair.

2. **Type6 → Misplacement redesign.** Abandoned "context-window tension" (never worked —
   produced type1 tasks). New concept: persona is fundamentally misplaced (business at
   carnival, mourner during festival, nature photographer in urban city, conference 
   attendee). P-constraints derive from coping strategy, not free preferences.
   See `docs/subtasks/type6_misplacement_redesign.md`.

3. **anchor_activity is expressible with existing framework.**
   `scope=['venue_id=X', 'time_window=14:00-15:30']` with `at_least 1` correctly
   enforces that venue X is visited during that window. No new schema needed.
   The anchor venue must be in the pool.

4. **B-score permanently removed.** All evaluation via P-score. Was confirmed dead
   multiple times; misplacement type6's "verification checks" can be expressed as
   P-constraints directly.

5. **Blank time formula:** Uses `max_gap between consecutive activities` as the trigger,
   not time span. Single-activity days use `max_gap = day_ref` (the whole budget is
   "gap"). `remaining_budget` not needed as a condition — trailing time is ambiguous
   intent and not penalised.

6. **Wrong-info scoring (B11) → three separate problems:**
   P1: Data inconsistency (fixed this session).
   P2: Retrieval signal quality varies by field type.
   P3: Application unmeasured — agent may find correct info but not use it.
   Requires research into fragile information environment design. Not a simple 
   taxonomy-of-internet-errors problem.

7. **Compound conditions `all: [...]` / `any: [...]`** are confirmed working through
   solvability validator + P-score evaluator. Agents independently invented these.
   Documented in handbook with concrete examples.

8. **`ceiling_mode: contiguous | spread`** needed for type2 when ceiling spans multiple
   days. "6 hours total across a 2-day trip" and "6 hours on Saturday afternoon" need
   different evaluation logic.

---

## Recommended Tasks for Future Reference

### Keep as gold-standard examples
| File | Why |
|------|-----|
| `london_t1_bank_holiday.json` | Best type1: clean hop-2, district cap, meaningful constraints |
| `lon_late_spring_type1_hackney_birthday.json` | Elegant locality anchor from travel context |
| `lon_type2_late_spring_art_quiet_270.json` | Clean partial-day type2 with noise constraint |
| `london_may_bank_holiday_tension_museum_gems.json` | Best type3: genuine competing inclusion pressure |
| `london_type3_002.json` | Clean type3: budget vs upscale, zero overlap |
| `lon_type5_booking_westminster.json` | **Best task in dataset.** Reservation gate + district + solving agent must verify actual availability |
| `type5_london_budget_pet_hidden_gems.json` | Excellent multi-gate stacking |
| `london_type6_bank_holiday.json` | Best technical type6: venue anchors + time windows + compound OR district |

### Problematic cases revealing real framework issues
| File | Problem revealed |
|------|-----------------|
| `lon_type3_01.json` | `agg=all scope=all` used as tension side → fake type3; tension validator gap |
| `london_late_spring_type3_03.json` | Compatible tags (heritage + museums) passed tension check → gap in overlap detection |
| `london_t4_may_weekend.json` | `fine-dining` not in PRICE_TIER_ORDER → all upscale constraints silent-failing |
| `lon_t6_001.json` | Anniversary at Sketch = pure type1 in type6 wrapper; 0 window connection |
| `london_bank_holiday_6.json` | Columbia Road hours_mon=NULL → validator incorrectly rejects Bank Holiday Monday schedule |

---

## Evaluator Gap: hours_overrides Not Read

The F-score evaluator reads `hours_mon...hours_sun` from the `venues` table but does NOT
read the `hours_overrides` table. Date-specific hours modifications (e.g. Bank Holiday
Monday for Columbia Road) are invisible to the evaluator. A correct plan scheduling
Columbia Road on 2026-05-25 would receive an F2a "outside opening hours" deduction.

The `hours_overrides` IS read by `mock_tools._enrich_with_date` for the `get_official_site`
tool response, but the evaluator's F2a check uses a different code path.

**Fix needed:** evaluator should load `hours_overrides` and merge date-specific hours
into the venue hours dict before F2a evaluation. Record as P6 gap.

