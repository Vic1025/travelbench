# E5 — End-to-end scaling run (London first)
**Status: ⬜ not started — blocked on E1–E4**

---

## What this is

The first complete non-Paris city run. Not just "generate venues" — the full pipeline from scratch to a benchmark-ready dataset:

```
generate_city_venues.py --city london
  → Step 1: research_city (already done — London config in DB)
  → Step 2: plan_venues (50 venues + events, unified LLM call)
  → Step 2b: enrich_venues (Overpass + Nominatim for coordinates)
  → Step 3: generate all 50 venues in parallel (per-venue agent)
  → Step 4: validate_city
  → Step 5: generate events (4 windows × distinct event sets)
  → Step 6: build_travel_matrix (haversine fallback)     ← E2
  → Step 7: populate_ticket_availability                 ← E2
  → Step 8: generate_multi_venue_docs                    ← E4

test_generate_tasks.py --city london --mode 6type
  → 6 tasks × 4 windows × 5 models = 120 tasks
  → solvability gate enforced                            ← E3
  → difficulty scores written                            ← E3
```

---

## Why London first (not Istanbul or Tokyo)

- Windows already exist in DB (4 windows) — E1 isn't required for London
- London venue generation has been tested (sample run in logs/)
- English venue names → Overpass enrichment works reliably
- Known failure modes from the Istanbul run are fixed (Overpass non-Latin)

London is the integration test before adding genuinely new cities.

---

## Success criteria

After the London run, the dataset should have:

| Item | Target |
|------|--------|
| Venues | 50 verified (page_status = 'verified') |
| Travel matrix pairs | ~2450 (50 × 49) |
| Ticket availability rows | booking_required venues × 4 windows × 7 dates |
| Events | 4–8 per window × 4 windows = 16–32 total |
| Multi-venue docs | ~10 per window × 4 windows = ~40 |
| Tasks (passing gate) | ≥ 18 across windows (≥ 1 of each type per window) |
| Difficulty scores | All saved tasks have constraint_complexity + avg_venue_difficulty |

**Quality checks to run manually after the run:**
- `validate_city london --full` passes with no issues (warnings OK)
- Sample 5 tasks: do the queries read naturally? Are constraints traceable to query?
- Sample 2 blog docs: do they read as human writing? Are venue references accurate?
- Run `run_benchmark.py --tasks lon_xxx --dry-run` to verify pipeline end-to-end

---

## Known risks

**Overpass enrichment rate** — London has Latin names but some venues may still fail (obscure pubs, new openings). Expect 70–80% match rate. Unmatched venues generate fine but lack coordinates (travel matrix uses haversine anyway).

**Venue agent quality** — The per-venue agent (generate_venue.py) is the most expensive step and the most likely to produce inconsistent data. First London run may have venues with unrealistic hours or invented addresses. Check a sample manually.

**Multi-venue doc coherence** — First run of E4. Expect some docs to have generic content or miss venue cross-references. May need prompt iteration before the docs are useful to agents.

**Task solvability rate** — For London with 50 venues and 4 varied windows, expect ~80% of generated tasks to pass the gate on first attempt. The remaining 20% will be discarded and need regeneration.

---

## After London: next cities

Once London passes quality checks:
1. **Hokkaido** — requires distinct venue pools per window (winter/summer), cross-match pass for Sapporo urban venues. Uses E1 auto-window for any gaps.
2. **Rio** — Carnival window has the most complex event set. Good stress test for event generation.
3. **Istanbul** — first genuinely new city. Requires E1 auto-window. Tests Overpass non-Latin fixes.
4. **Tokyo, NYC, Barcelona** — scale out once Istanbul works.

---

## Tasks
- [ ] Confirm E1–E4 are all complete before starting
- [ ] Run `generate_city_venues.py --city london` with full API keys
- [ ] Check all 7 steps complete without errors
- [ ] Run `validate_city london --full` and address any blocking issues
- [ ] Manually sample 5 venues, 5 tasks, 3 docs for quality
- [ ] Run `test_generate_tasks.py --city london --mode 6type` for all 5 models
- [ ] Verify saved tasks have difficulty scores and passed solvability gate
- [ ] Document any prompt issues found for iteration
