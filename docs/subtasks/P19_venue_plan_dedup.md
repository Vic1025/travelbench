# P19 — Duplicate venue deduplication at plan_venues output
**Status: ✅ DONE — _dedup_briefs implemented, wired before count validation, all 5 known duplicate patterns caught, 652/652 tests passing**
**Blocks: nothing (data quality — must run before next generation)**

---

## The problem

The `plan_venues` LLM call returns an 82-venue JSON array. The LLM sometimes includes
near-duplicate entries — the same real-world venue listed twice with minor name variations:

| Duplicate pair | Variation type |
|---|---|
| "St James's Park" / "St. James's Park" | Punctuation |
| "Kyoto Garden Holland Park" / "Kyoto Garden, Holland Park" | Comma variant |
| "Sketch (The Lecture Room & Library)" / "Sketch (The Lecture Room)" | Name truncation |
| "Lyle's" / "Lyles" | Apostrophe |
| "Monmouth Coffee Bermondsey" / "Monmouth Coffee Company" | Subtitle swap |

Each duplicate goes through the full venue agent pipeline independently — two separate
agent runs, two distinct `venue_id`s, two sets of source docs. They consume 2× the
generation budget and produce two pool entries that will confuse task agents (which venue
should a task reference? why are there two versions of Sketch?).

---

## Root cause

The `plan_venues` LLM call has no self-consistency check on names. There is no dedup
step between the parsed JSON array and brief dispatch. The existing enrich step
(`enrich_venues.py`) has fuzzy name matching for Overpass lookups but does not remove
duplicates from the brief list.

---

## Impact

- ~5% pool slots consumed by duplicates (5 dupes in 82 venues = 6%).
- Solving agents may plan two visits to Sketch in the same day.
- Task agents may design constraints referencing one Sketch ID that the solving agent
  can only satisfy using the other.
- Double generation cost for affected venues.

---

## Design

A single dedup pass immediately after the plan_venues JSON is parsed, before any
briefs are dispatched to venue agents. The pass uses token-overlap similarity
(same approach as `_find_best_match` in `enrich_venues.py`):

1. Normalise all names: lowercase, strip punctuation (`.-,'()`), collapse spaces.
2. For each pair (i, j) where i < j: compute Jaccard similarity on word tokens.
3. If similarity > 0.75 AND same category AND same district: mark j as duplicate.
4. Remove marked duplicates from the brief list.
5. Log removed duplicates: `"Removed duplicate brief: 'Lyles' (similar to 'Lyle's', 
   score=0.83, same category=restaurant, same district=Shoreditch)"`.

Threshold 0.75 is conservative — high enough to catch punctuation/apostrophe/comma
variants without risk of removing genuinely distinct venues (e.g. "Monmouth Coffee
Bermondsey" and "Monmouth Coffee Company" are at the borderline and were caught by
same-category + same-district check, not similarity alone).

The "same category + same district" filter is essential: "The Palomar" (restaurant,
Soho) and "The Palomar Shoreditch" (hypothetical restaurant, Shoreditch) should NOT
be merged even if similarity > 0.75.

---

## Tasks

- [ ] **Add `_dedup_briefs(briefs: list[dict]) → list[dict]` to `generate_city_venues.py`.**

  ```python
  import re
  from itertools import combinations

  def _dedup_briefs(briefs: list[dict]) -> list[dict]:
      def _norm(name: str) -> set:
          # Remove punctuation, lowercase, tokenise
          cleaned = re.sub(r"[^\w\s]", " ", name.lower())
          return set(cleaned.split())

      to_remove = set()
      for i, ba in enumerate(briefs):
          if i in to_remove:
              continue
          for j in range(i + 1, len(briefs)):
              if j in to_remove:
                  continue
              bb = briefs[j]
              # Must be same category and same district to be considered a duplicate
              if ba.get("category") != bb.get("category"):
                  continue
              if ba.get("district") != bb.get("district"):
                  continue
              ta, tb = _norm(ba["name"]), _norm(bb["name"])
              if not ta or not tb:
                  continue
              jaccard = len(ta & tb) / len(ta | tb)
              if jaccard >= 0.75:
                  # Keep the longer (more complete) name — or first if same length
                  keep, drop = (i, j) if len(ba["name"]) >= len(bb["name"]) else (j, i)
                  to_remove.add(drop)
                  print(f"  [dedup] Removed '{briefs[drop]['name']}' "
                        f"(duplicate of '{briefs[keep]['name']}', "
                        f"score={jaccard:.2f})")
      return [b for i, b in enumerate(briefs) if i not in to_remove]
  ```

- [ ] **Call `_dedup_briefs(briefs)` in `generate_city_venues.py`** immediately after
  `plan_venues` output is parsed into the `briefs` list, before the enrich step and
  before any parallel venue agent dispatch.

  Location: in `generate_city_venues()`, after:
  ```python
  briefs = _parse_plan_venues_output(raw_plan)
  ```
  Add:
  ```python
  original_count = len(briefs)
  briefs = _dedup_briefs(briefs)
  if len(briefs) < original_count:
      print(f"  [plan] Dedup removed {original_count - len(briefs)} duplicate brief(s). "
            f"Proceeding with {len(briefs)} venues.")
  ```

- [ ] **No additional validation needed.** The dedup is logged verbosely, and the
  downstream venue count will reflect the cleaned list. `validate_city.py` already
  reports final venue counts — a reduced count after dedup will be visible there.

---

## Files to change

- `scripts/generation/generate_city_venues.py` — add `_dedup_briefs()`, call it
  after plan_venues parsing ⬜

---

## Notes

- Threshold 0.75 was calibrated against the 5 known duplicate pairs in test_50:
  all 5 would be caught at 0.75 + same category + same district.
- "Monmouth Coffee Bermondsey" / "Monmouth Coffee Company" (different districts)
  would NOT be merged by this logic — they end up as distinct pool entries in different
  neighbourhoods, which may be intentional (same brand, two locations). Only same-district
  duplicates are removed.
- If the LLM returns fewer than `n_venues` after dedup, the pipeline does not re-request
  to fill the gap. The pool runs slightly short, which is preferable to requesting more
  and risking another round of duplicates. A warning is printed.
