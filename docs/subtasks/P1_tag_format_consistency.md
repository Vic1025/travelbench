# P1 — Tag format consistency
**Status: 🔄 in progress — validator fix landed, venue-write canonicalization still open**
**Blocks: nothing (improvement)**

---

## The problem

Benchmark-critical tag coverage was showing 2/8 passing on real data when 7/8 actually had coverage. Cause: the venue agent writes tags in three inconsistent formats:

| What the agent writes | What validate_city.py checked |
|-----------------------|-------------------------------|
| `free_entry`          | `free-entry`                  |
| `wheelchair_accessible` | `wheelchair-accessible`     |
| `family_friendly`     | `family-friendly`             |
| `family friendly`     | `family-friendly`             |

Exact-string match missed all three format variants.

## Impact on benchmark

Type 5 (hard-feasibility reduction) tasks stack 3+ filters from different categories. If the validator thinks `wheelchair-accessible` has 1 matching venue when it has 6, tasks needing `wheelchair + halal + central-district` look unsolvable — the solvability gate rejects them — and we waste generation budget on retries. Also produces false warnings like "Low tag coverage for: ['wheelchair-accessible']" in validation reports.

## Tasks

- [x] Extend `_norm()` in `scripts/generation/validate_city.py` to strip `-`, `_`, and ` ` before comparison.
  Verified on real test_50 DB: 7/8 benchmark-critical tags now pass (only `halal` remains, and that's a genuine pool gap).

- [ ] Canonicalize tags at venue-write time in the venue agent.
  Target: all tags stored in DB use hyphenated kebab-case (`free-entry`, `wheelchair-accessible`).
  Location: the agent tool that accepts venue submissions, around `scripts/generation/agent_tools.py` tool_SUBMIT. Before writing, apply the same `_norm()` → `canonical` mapping.

- [ ] Update the venue planning prompt to emit `coverage_tags` in canonical form.
  Location: `generate_city_venues.py::plan_venues`. The brief already lists "canonical tags" — enforce it in the prompt by listing examples of the canonical form only, with explicit "use hyphens, not underscores or spaces" guidance.

- [ ] Update `_query_pool` tag filter in `scripts/generation/pool_utils.py` to apply normalization both sides.
  Currently: `if value not in v.get("tags", [])`. Change to use normalized comparison so the task agent can filter by `free-entry` and match venues that stored `free_entry`.

- [ ] Add a one-line migration comment in `DESIGN_DECISIONS.md` noting that tags are stored canonically going forward.

## Implementation notes

### Why not normalize only at query time

Normalizing at query time works (it's what the validator fix does) but has drawbacks:
- Every query site has to remember to normalize — easy to miss one
- Tag coverage reports show raw variants which looks messy in validate_city output
- Cross-city comparisons are harder when one city stores `free_entry` and another `free-entry`

Canonicalize at write time, query canonically. The validator normalizer stays as a defense against any legacy data that slipped through.

### Test plan

- Test_50 DB regeneration: run `generate_city_venues.py --city london --run-name test_50_p1` and confirm all tags in the DB use hyphens.
- Existing E-suite tests should all pass unchanged since the canonical form is a pure superset.

## Files changed

- `scripts/generation/validate_city.py` — `_norm()` extended ✅
- `scripts/generation/agent_tools.py` — tool_SUBMIT venue write path ⬜
- `scripts/generation/generate_city_venues.py` — planning prompt ⬜
- `scripts/generation/pool_utils.py` — `_query_pool` tag matching ⬜
- `DESIGN_DECISIONS.md` — migration note ⬜
