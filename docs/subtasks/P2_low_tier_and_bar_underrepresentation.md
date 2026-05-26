# P2 — Low-tier and bar underrepresentation
**Status: ⬜ not started**
**Blocks: nothing (improvement)**

---

## The problem

Two runs of the London 50-venue generation pipeline returned:

| Dimension | Actual | Design target |
|-----------|--------|---------------|
| low traffic tier | 4/50 (8%) | ~16/50 (32%) |
| bar category | 1/50 (2%) | 4-5/50 (8-10%) |
| high/mid tiers | 46/50 | ~34/50 |

The `plan_venues` LLM call returns tier and category distributions skewed toward "safe" well-known venues (high/mid) and away from bars. The prompt states target distributions but doesn't enforce them — the model treats the targets as preferences rather than constraints.

## Impact on benchmark

**Low-tier shortage breaks Type 3.** Type 3 (competing requirements) needs genuine tension: hidden-gem (low-traffic) vs iconic (high-traffic). With only 4 low-tier venues in a pool of 50, the "hidden gem" side of every Type 3 task collapses to the same 3-4 candidates, making the constraint trivially satisfiable (agent just picks any low-tier venue) instead of a real selection challenge.

**Bar shortage breaks Type 5 and Type 6.** Type 5 (hard feasibility) stacks multi-category constraints — a typical task needs 1-2 restaurants + 1 cocktail-bar + 1 museum + 1 attraction. With 1 bar in the pool, any Type 5 that lands a bar constraint has exactly one valid option (or zero, if that one bar also has some other restriction). Type 6 (context-window tension) anniversary/birthday scenarios want "evening cocktail spot" — again, 1 option.

## Root cause

Current planning prompt in `generate_city_venues.py::_plan_prompt_with_events` lists tier percentages as guidance:

```
Aim for: high ~36%, mid ~32%, low ~32%.
Include variety across categories.
```

The LLM reads this as advisory. When its training prior strongly favors famous venues (which are high/mid by definition), the stated targets lose. Bars specifically suffer because most London "bar" venues are either also pubs (categorized as `attraction` in the LLM's head) or Michelin-starred cocktail bars it classes as `restaurant`.

## Tasks

- [ ] **Remove `~` from tier targets in planning prompt. Add explicit self-check instruction.**
  Change advisory language to hard requirements:
  ```
  TRAFFIC TIERS — EXACT COUNTS, CHECK BEFORE SUBMITTING:
    high: exactly {_n_high} venues  (famous real-world names required)
    mid:  exactly {_n_mid} venues
    low:  exactly {_n_low} venues   ← hidden gems, semi-fictional names fine

  Before submitting, count your venues by traffic_tier.
  Adjust until the counts match exactly.
  ```

- [ ] **Add category quotas with bar explicitly counted.**
  Replace the single "Exactly 25 food/drink venues: restaurants, cafes, bars" line with:
  ```
  CATEGORY COUNTS — EXACT, CHECK BEFORE SUBMITTING:
    restaurant: {_n_restaurant}  (target ~{round(n_venues*0.28)})
    cafe:        {_n_cafe}       (target ~{round(n_venues*0.14)})
    bar:         {_n_bar}        (target ~{round(n_venues*0.08)}) ← at least 4, always explicit
    museum:      {_n_museum}     (target ~{round(n_venues*0.12)})
    attraction:  {_n_attraction} (target ~{round(n_venues*0.14)})
    park:        {_n_park}       (target ~{round(n_venues*0.08)})
    neighbourhood: {_n_nbhd}    (target ~{round(n_venues*0.06)})
  ```

- [ ] **Code enforcement for tiers after plan_venues — same pattern as wrong_info.**
  The wrong_info rebalancer (lines 1217–1228) is the right template. Always enforces
  the target count, doesn't wait for a fully degenerate case.

  Pattern:
  ```python
  # Tier rebalancing: if low is under-target, downgrade mid venues
  tier_targets = {"high": _n_high, "mid": _n_mid, "low": _n_low}
  tier_counts = Counter(b["traffic_tier"] for b in briefs)
  low_deficit = tier_targets["low"] - tier_counts.get("low", 0)
  if low_deficit > 0:
      # Downgrade mid venues — prefer shorter/more obscure names (heuristic)
      mid_venues = sorted(
          [b for b in briefs if b["traffic_tier"] == "mid"],
          key=lambda b: len(b.get("name", ""))
      )
      for b in mid_venues[:low_deficit]:
          b["traffic_tier"] = "low"

  # Category rebalancing: if bars under target, convert least-distinctive cafes
  bar_target = max(4, round(len(briefs) * 0.08))
  bar_count = sum(1 for b in briefs if b["category"] == "bar")
  if bar_count < bar_target:
      cafe_surplus = [b for b in briefs
                      if b["category"] == "cafe"
                      and b["traffic_tier"] in ("mid", "low")]
      for b in cafe_surplus[:bar_target - bar_count]:
          b["category"] = "bar"
          # Patch character so venue agent gets the right brief signal
          b["character"] = b.get("character","").replace("cafe","bar").replace("coffee","cocktails")
  ```

  Location: same block as pace/wrong_info rebalancing (lines 1168–1228 in generate_city_venues.py).

- [ ] **Add regulation diversity targets to planning prompt.**
  The venue agent sets regulations — the planning prompt only controls the brief's
  `character` field, which nudges the agent's choices. Add to prompt:
  ```
  REGULATION DIVERSITY (guide your character descriptions):
    Target ~40-60% of venues with each regulation true.
    wheelchair_accessible: ~20-30 venues — NOT every venue, reflects real inaccessibility
    photography_allowed:   ~20-30 venues — galleries/museums may prohibit
    family_friendly:       ~20-30 venues — bars and late-night spots should be false
    pet_friendly:           ~8-15 venues — most indoor venues restrict pets
    reservation_required:  ~10-20 venues — fine dining and popular attractions
    Use character descriptions that signal the regulation naturally:
      "intimate basement bar, steep stairs" → wheelchair_accessible=false
      "modern gallery, strict no-photography policy" → photography_allowed=false
      "lively cocktail lounge, 21+ entry" → family_friendly=false
  ```

- [ ] **Post-generation audit in validate_city.**
  Hard fail if: `bar_count < 3` OR `low_tier_count < 12` OR
  any single regulation coverage > 85% or < 5%.

- [ ] **Update distribution targets per city in city configs.**
  London, Paris, Tokyo: 16 low-tier, 4-5 bars.
  Hokkaido, Rio: 14 low-tier, 3 bars.
  Istanbul: 16 low-tier, 2 bars.

## Validation plan

After implementing:
1. Regenerate London 50-venue pool. Confirm counts meet targets.
2. Regenerate Paris. Same check.
3. Run the full test_generate_tasks suite against the new pool. Type 3 and Type 5
   should produce tasks with genuinely distinct candidate sets across tasks.

## Files to change

- `scripts/generation/generate_city_venues.py` — `_plan_prompt_with_events` (remove ~, add counts + reg diversity) ⬜
- `scripts/generation/generate_city_venues.py` — post-plan_venues rebalancing block (tier + category) ⬜
- `scripts/generation/validate_city.py` — post-gen tier/category/regulation audit ⬜
- City config files for each seeded city's distribution targets ⬜
