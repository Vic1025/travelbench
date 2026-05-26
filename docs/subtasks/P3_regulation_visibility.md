# P3 — Regulation visibility in source docs
**Status: ⬜ not started**
**Blocks: nothing (improvement)**

---

## The problem

Only 19/65 restricted regulations in the test_50 pool are mentioned anywhere in the source doc bodies. The rest (46 restrictions, 71%) exist only as DB columns — invisible to any agent reading documents.

Sample from test_50:
- 11 venues marked `wheelchair_accessible=0`. 4 of them have any source doc mention of stairs, steps, narrow entry, or accessibility.
- 8 venues have a `dress_code` set. 2 have any source doc mention of dress code.
- 6 venues marked `pet_friendly=0`. 1 has any source doc mention of pets.
- 5 venues marked `photography_allowed=0`. 0 have source doc mention.

## Impact on benchmark

The benchmark premise: agents reason from documents to plan a trip. For a wheelchair user, the agent should *discover* which venues are accessible by reading blog posts that mention accessibility, forum threads discussing step-free entry, official site pages with access notes. If restrictions are invisible in documents, the task is either:

1. **Trivial** — the agent gets the restriction from a hidden regulation field it can query directly. No reasoning required. Benchmark score reflects lookup, not planning.
2. **Impossible** — the agent can't see the restriction, picks an inaccessible venue, gets marked wrong for reasons it had no way to know about. Benchmark score reflects luck.

Neither is what we want. Source docs must reflect the regulations so agents have a path to discover them.

## Why this is happening

The venue agent in `generate_venue.py` produces source docs without awareness of the venue's regulation field values. The VERIFY step only checks positive regulations (e.g. "if venue is wheelchair-accessible, is this visible in docs?") and does so weakly. Negative regulations (`=0` values) are never checked.

## Tasks

- [ ] **Add regulation briefing to the venue agent's assignment.**
  Currently the agent sees `regulations: {wheelchair_accessible: false, ...}` as a dict but has no specific instruction to weave each restriction into at least one doc. Add an explicit briefing:
  ```
  This venue has the following restrictions. Each must appear in at least
  one source document, either directly or through signals a reader could
  interpret:
    - wheelchair_accessible=false → mention steps, narrow entry, basement,
      "not fully accessible", or similar
    - dress_code="smart casual" → mention dress code directly, or signals
      like "no shorts", "jackets preferred"
    - pet_friendly=false → mention dogs must stay outside, no-pets policy,
      or omission in a dog-friendly context
  ```

- [ ] **Add `_check_regulation_coverage()` to VERIFY in `agent_tools.py`.**
  For each restriction on the venue, scan concatenated source doc bodies for at least one plausible mention. Signal-based matching (stairs OR step-free OR accessib* OR basement for wheelchair negative). Hard-fail if < 100% coverage — the agent must regenerate docs until every restriction is visible.

- [ ] **Sample matching patterns per regulation field:**
  | regulation | plausible signals in docs |
  |------------|--------------------------|
  | `wheelchair_accessible=0` | steps, stair(s), narrow, basement, upstairs, not accessible, accessibility limited |
  | `pet_friendly=0` | dogs outside, no pets, not dog friendly, pet-free |
  | `photography_allowed=0` | no photography, photos not allowed, no pictures, no cameras |
  | `family_friendly=0` | adults only, 18+, not suitable for children, no kids |
  | `dress_code=*` | dress code, smart casual, no shorts, jackets, formal |
  | `reservation_required=1` | reservations required, booking essential, must book ahead |
  | `age_restriction=N` | 18+, over 18, 21+, adults only, N+ entry |

- [ ] **Don't over-correct for positive regulations.**
  `wheelchair_accessible=1` doesn't need a doc mention — it's the default, and explicit "is accessible" statements feel unnatural. Only negative/restricted values need coverage.

- [ ] **Validation report line.**
  Add to `validate_city.py`: "Regulation visibility: 58/65 restrictions visible in source docs (12 missing — see details)". Gives at-a-glance signal on dataset readiness.

## Design notes

### False positives vs false negatives

The signal-based matching is deliberately loose. A blog post mentioning "stairs" near a discussion of Georgian architecture could match even if it's not specifically about accessibility. That's OK — the goal is that an agent *could* infer from the signal. If the signal is ambiguous, the agent does a reasonable job of interpretation (that's what we're testing).

Hard false negatives (restriction exists, zero plausible signal) are what we're guarding against.

### Why not just generate the source docs from the regulation list?

Tempting but wrong. Natural source docs don't enumerate regulations — they mention them in passing, incidentally, sometimes contradictorily. A forum post saying "tried to bring my dog, turned away at the door" is a more realistic signal than "this venue is not pet-friendly." The generation prompt should encourage natural weaving, not enumeration.

## Files to change

- `scripts/generation/generate_venue.py` — `_build_assignment()` add regulation briefing ⬜
- `scripts/generation/agent_tools.py` — add `_check_regulation_coverage()`, hook into `tool_VERIFY` ⬜
- `scripts/generation/validate_city.py` — post-gen visibility report ⬜
