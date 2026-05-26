# P8 — Currency consistency rename (local currency throughout)
**Status: ✅ DONE — rename complete, helpers in place, all 390 tests passing**
**Blocks: P7** (P7's schema field names depend on what we landed here — unblocked)
**Depends on: nothing**

---

## Done summary

- DB columns renamed: `avg_cost_usd → avg_cost_local`, `lunch_cost_usd → lunch_cost_local`, `dinner_cost_usd → dinner_cost_local`, `price_usd → price_local`, `estimated_cost_usd → estimated_cost_local`. 31 files touched across `scripts/`, `eval/`, `server/`, and all E1–E4 tests.
- `CITY_CURRENCY` registry in `scripts/generation/pool_utils.py` with 21 cities (London→GBP, Tokyo→JPY, Paris→EUR, etc.).
- `get_city_currency(city)` helper — looks up the ISO code, warns once for unknown cities (defaults to USD).
- `format_money(amount, city)` helper — renders `£48`, `¥4800`, `$50` etc. for error messages.
- Handbook prompts updated to say "in local currency of the city (GBP for London, JPY for Tokyo, etc.)"
- All 390 tests pass. Verified: no residual references to the old `_usd` field names anywhere except this doc and P7 meta-references.

---

## The problem

`scripts/generation/db.py` defines columns named `avg_cost_usd`, `lunch_cost_usd`, `dinner_cost_usd`, `price_usd`. The handbook tells the venue-generation agent:

> `avg_cost_usd      REAL    Average cost per person in USD`

In practice the agent appears to ignore the "USD" part and fills in the local-currency number directly. Checking London's test_50 DB:

- Clove Club: `$350` — actual London tasting menu is ~£225. So either the agent converted wrong (should be ~$290) or just put the local number in.
- Sketch: `$250` — actual Sketch tasting menu is ~£180. Should be ~$230 if converted.

The numbers consistently look like **local-currency prices with a USD label**. The venue-gen agent is almost certainly not converting, because "don't convert" is the simpler and correct choice for the downstream use case: when a user says "£40/day budget in London", the agent comparing against "£40 worth of venues" needs London prices in GBP, not a USD conversion round-trip.

## The fix

Rename all cost columns to drop the currency suffix. Local currency is implied by the city. Add a single city-level metadata field that names the currency.

### Column renames

| Before | After | Location |
|---|---|---|
| `avg_cost_usd` | `avg_cost_local` | `venues` table |
| `lunch_cost_usd` | `lunch_cost_local` | `venues` table |
| `dinner_cost_usd` | `dinner_cost_local` | `venues` table |
| `price_usd` | `price_local` | `event_overlays` table (sold-out events) |
| `estimated_cost_usd` | `estimated_cost_local` | calibrate_constraints placeholder |

### New city-metadata field

Cities already have centre coords. Add a currency code alongside:

```python
# scripts/generation/load_city_pool.py or similar
CITY_CURRENCY = {
    "london":    "GBP",
    "paris":     "EUR",
    "tokyo":     "JPY",
    "istanbul":  "TRY",
    "new_york":  "USD",
    ...
}
```

Exposed via `get_city_currency(city: str) -> str` for use in handbook prompts and validator error messages ("budget infeasible: cheapest plan is ¥4800/day, stated budget ¥3200/day").

### Handbook updates

Everywhere the handbook says "in USD", change to **"in the local currency of the city (e.g. GBP for London, JPY for Tokyo)"**. The venue-gen agent is instructed unambiguously: use the local currency the venue actually charges, no conversion.

Affected locations in `scripts/generation/handbook.py`:
- Line 429: schema field description for `avg_cost_usd`
- Line 468: the filled example comment
- Line 706: recurring mention in the "things that might be stale" section
- Line 115: migration null-check list

Plus `docs/generic_constraint_schema.md` and `docs/task_structural_types.md` if they use the old name (need to grep).

---

## Scope — files touched

1. `scripts/generation/db.py` — `CREATE TABLE venues` and `event_overlays`, plus `get_columns()` helpers and migration metadata
2. `scripts/generation/agent_tools.py` — venue field list (line 47)
3. `scripts/generation/generate_city_venues.py` — instruction templates and field references
4. `scripts/generation/handbook.py` — 4+ prompt references
5. `scripts/generation/compute_task_difficulty.py` — SELECT statement (line 133)
6. `scripts/generation/validate_city.py` — field check (line 102)
7. `scripts/generation/generate_events.py` — base_price logic (line 147, 199)
8. `scripts/generation/generate_multi_venue_docs.py` — formatting line (line 202, 475)
9. `scripts/generation/calibrate_constraints.py` — placeholder key (line 107)
10. `scripts/generation/generate_task.py` — constraint examples in docstrings (line 483)
11. `scripts/generation/load_city_pool.py` — new `CITY_CURRENCY` and `get_city_currency()`
12. All E1–E4 test files that reference these columns

---

## Migration strategy

**No data migration needed** — London's values are already in GBP, just mislabelled. The rename only touches column names, not values.

**For existing DBs** (London test_50, any user's existing generation):
- The `CREATE TABLE` in db.py sets schema for new DBs
- For existing DBs, add a one-shot rename in `db.py`'s migration path:
  ```python
  def _migrate_currency_columns(conn):
      # ALTER TABLE venues RENAME COLUMN avg_cost_usd TO avg_cost_local
      # etc.
      # Idempotent: check column existence first
  ```
- Call from whatever init path `db.py` uses today

**One caveat:** the E1-E4 tests may use canned SQL or read back from files that hard-code the old name. Each needs updating. Rough grep count: ~15 references across test files.

---

## Order of operations

1. Grep the full codebase for `avg_cost_usd|lunch_cost_usd|dinner_cost_usd|price_usd|estimated_cost_usd` to get a complete hit list
2. Add `CITY_CURRENCY` registry + `get_city_currency()` helper
3. Rename CREATE TABLE statements in `db.py`; add migration
4. Rename all source references in the 11 files above
5. Update handbook prompts to say "local currency" and reference `get_city_currency(city)` where appropriate
6. Run full E-test suite — fix any test-side references
7. Sanity-check: spin up a fresh London DB from scratch, generate 2-3 venues, confirm the new column name populates correctly
8. Export + hand back for validation

---

## Why this must land before P7

P7's schema adds `public_input.query_resources.budget_per_day_usd`. If we ship P7 first with that name and then P8 renames it, we'd need to break P7's schema again. Landing P8 first means P7 can cleanly define:

```json
"query_resources": {
  "time_ceiling_minutes": 330,
  "budget_per_day": 40           // no currency suffix — implied by city
}
```

And the validator error messages can render like: `"budget infeasible: need £47/day, stated £30/day"` using `get_city_currency("london") == "GBP"` to format.

---

## Risk

Low — pure refactor, no behavior change. Risk is "missed a reference somewhere" which would break a test. The full test suite catches this before export.

---

## Completion summary

- ✅ All cost columns renamed: `avg_cost_{usd→local}`, `lunch_cost_{usd→local}`, `dinner_cost_{usd→local}`, `price_{usd→local}` (events + ticket_availability)
- ✅ `CITY_CURRENCY` registry + `get_city_currency()` + `format_money()` helpers in `scripts/generation/pool_utils.py` (21 cities mapped)
- ✅ Handbook updates: all "in USD" references now say "in the city's local currency"
- ✅ Idempotent migration added to `_apply_migrations()` in `scripts/generation/db.py` — `ALTER TABLE ... RENAME COLUMN` for existing DBs
- ✅ Migration tested against a snapshot of the live `london/runs/test_50` DB — renames correctly, preserves all data, idempotent on re-run
- ✅ All three live DBs migrated (`london/runs/test_50`, `london`, `tokyo`)
- ✅ Full test suite passes: 390/390 (E1: 56, E2: 51, E2.5: 99, E3: 68, E4: 116)
- ✅ Zero remaining `_usd` references in scripts/ or test code

**P7 is now unblocked** — its `query_resources.budget_per_day` field and the `query_pool` `include=["price"]` enrichment will reference `avg_cost_local` cleanly, and error messages can use `format_money(amount, city)` to render in the appropriate currency symbol.
