# P18 — Venue field completeness: dress_code, reservation_required, opening hours
**Status: ✅ DONE — handbook expanded, reservation_required default removed + FILL feedback fixed, _check_hours_coverage added to VERIFY, 652/652 tests passing**
**Blocks: nothing (data quality improvement)**

---

## The problem

Three venue fields are systematically incomplete across all generated venues:

| Field | London test_50 | Expected |
|---|---|---|
| `dress_code` | 0/82 set (100% null) | Set on all venues with a door policy |
| `reservation_required` | 0/82 deliberately set (all DB default 0) | ~15-25% of restaurants/attractions |
| Opening hours (hours_mon–sun) | 4/82 missing, no soft warning | 0 missing |

Each has a different root cause, but all three are fixable in the same system
(`handbook.py`, `agent_tools.py`, `generate_venue.py`).

---

## Root cause analysis

### dress_code
The field is listed as "Optional" in `handbook.py` with no examples:
```
dress_code        TEXT    Description or null
```
Agents never set it because there's no signal about when it applies or what values
are valid. It is never mentioned in `_build_assignment`, never in `REQUIRED_ON_COMMIT`,
and never flagged as `still_null` by FILL. Agents simply skip it.

### reservation_required
The DB schema has `reservation_required INTEGER DEFAULT '0'`. When `CREATE_PAGE("venue")`
inserts only `(venue_id, city, page_status)`, SQLite applies the column default, setting
`reservation_required = 0` immediately — before the agent does anything. The COMMIT check
uses `if val is None`, which passes because `val = 0`. The agent never needs to think about
it. Additionally, the FILL `still_null` response explicitly excludes `reservation_required`
from the "you still need to fill this" list, so the agent gets zero feedback.

The result: 0/82 venues have `reservation_required = 1` in the London pool, even though
Sketch, The Clove Club, Gymkhana, Sabor and many others demonstrably require reservations
in real life.

### Opening hours
No check exists in VERIFY that notices when all 7 day columns are null. The agent can
COMMIT a venue with no hours at all, and nothing objects. Parks and neighbourhood walks
that are always accessible don't have structured hours, but restaurants and museums without
hours will confuse scheduling agents.

---

## Impact on benchmark

- Solving agents cannot discover dress code requirements from task queries if the field is
  always null — the `dress_code` field becomes a dead data dimension.
- Type 4 (budget allocation) tasks and general scheduling depend on reservation status:
  a venue with `reservation_required=1` needs advance booking, which is exactly the kind
  of constraint a solving agent should reason about.
- Scheduling agents use opening hours to avoid booking venues at times they're closed.
  Missing hours makes the schedule trivially solvable (no time constraint from hours).

---

## Tasks

### A. dress_code — richer documentation in handbook

- [ ] **In `handbook.py`, expand the `dress_code` field definition** from a bare
  one-liner to a documented field with examples, usage cue, and value format:

  ```
  dress_code        TEXT    null if no policy; otherwise a short plain-English description.
                            Set based on the venue's character — do not default to null.
                            Examples by venue type:
                              Michelin restaurant / private members club → "smart casual"
                              Rooftop bar with door policy              → "smart casual"
                              Formal hotel restaurant                   → "formal"
                              Jacket-required institution               → "jacket required"
                              Casual bar, cafe, street food, park       → null (no policy)
                            Include in source docs only if set: mention "jacket required"
                            or "smart casual" in at least one doc body when dress_code is set.
  ```

- [ ] **Add `dress_code` to `REQUIRED_ON_COMMIT`** for the `"venue"` entity type.
  Currently it is absent — adding it means COMMIT will flag it as missing.
  However it must be allowed to be null (no dress code is a valid state), so the
  COMMIT check must allow null explicitly for this field only. Implement as: if the
  venue `category` is `restaurant`, `bar`, or `attraction`, `dress_code` is required
  (either set to a value OR explicitly set to the string `"none"` to signal the agent
  made a deliberate choice). For `cafe`, `park`, `neighbourhood`, `museum`: optional.

  This is a soft enforcement: the agent must actively choose, not passively skip.

### B. reservation_required — remove DB default, fix FILL feedback

- [ ] **Remove `DEFAULT '0'` from `reservation_required` in the DB schema.**
  Concretely: update the `init_db()` function in `db.py` to create the column
  without a default:
  ```sql
  reservation_required  INTEGER,   -- was: INTEGER DEFAULT '0'
  ```
  This means `CREATE_PAGE("venue")` will leave it NULL, and COMMIT's
  `if val is None` check will fire if the agent never fills it.

  Note: this is a schema change. Existing DBs have the old default. The fix only
  applies to newly-created venues. No migration needed for test data since
  London will be regenerated.

- [ ] **Remove `reservation_required` from the `bool_fields` exclusion list in
  `tool_FILL`** (line ~341 in `agent_tools.py`).
  Currently `still_null` hides boolean fields from the agent. `reservation_required`
  should be removed from this exclusion so the agent sees it in the `still_null`
  list when it hasn't been set. The agent will then know it needs to make a deliberate
  0 or 1 decision.

  Other `bool_fields` members (`pet_friendly`, `wheelchair_accessible`, etc.) can
  remain excluded because their defaults are more defensible (0 = "no" is often
  correct for pet_friendly) and agents already set them from character prompts.

- [ ] **Add explicit guidance in `handbook.py`** on when to set `reservation_required=1`:
  ```
  reservation_required  INTEGER  0 = walk-in OK, 1 = advance booking essential.
                                 Set to 1 for: Michelin/tasting-menu restaurants,
                                 popular attractions with timed entry (e.g. major
                                 museums with ticketed slots), private members clubs.
                                 Set to 0 for: casual restaurants, cafes, bars,
                                 parks, and anywhere walk-in is the norm.
                                 You MUST set this field — it has no default.
  ```

### C. Opening hours — soft VERIFY warning

- [ ] **Add `_check_hours_coverage(conn, venue_id) → list[dict]` in `agent_tools.py`.**
  Check: are all 7 day columns (`hours_mon` through `hours_sun`) null?

  If all null:
  - Look up the venue's category.
  - If category is `park`, `neighbourhood`, or `university`/similar always-accessible
    type: return a soft warning (not a blocking error):
    ```json
    {
      "check": "hours_coverage",
      "severity": "warning",
      "detail": "No opening hours set. If this is always-accessible public space,
                 confirm by setting at least hours_mon to 'always open' or '24 hours'.
                 Otherwise set specific hours for each day before finalising."
    }
    ```
  - If category is `restaurant`, `cafe`, `bar`, `museum`, `attraction`: return a hard
    error (blocks VERIFY):
    ```json
    {
      "check": "hours_coverage",
      "severity": "error",
      "detail": "Opening hours missing for all 7 days. Restaurants, cafes, bars,
                 museums, and attractions must have hours set. Call FILL with
                 hours_mon through hours_sun before VERIFY will pass."
    }
    ```

- [ ] **Wire `_check_hours_coverage` into `tool_VERIFY`** alongside the existing
  `_check_regulation_visibility` and `_check_hours_override_coverage` calls.

---

## Files to change

- `scripts/generation/handbook.py` — expand `dress_code` definition, add
  `reservation_required` guidance ⬜
- `scripts/generation/agent_tools.py` — update `REQUIRED_ON_COMMIT` for `dress_code`,
  remove `reservation_required` from `bool_fields` exclusion, add
  `_check_hours_coverage`, wire into `tool_VERIFY` ⬜
- `scripts/generation/db.py` — remove `DEFAULT '0'` from `reservation_required`
  column in `init_db()` ⬜

---

## Validation

After implementation: regenerate London pool. Check:
1. `dress_code` is set on ≥15% of restaurants and bars (realistically ~20-30 venues).
2. `reservation_required = 1` on ≥10 venues (Sketch, Clove Club, Gymkhana, etc.).
3. 0 venues with all-null hours after VERIFY passes.
