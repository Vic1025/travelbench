# P13 — division doc

**Purpose:** P13 is ~11.5h of focused work touching schema, pipeline, handbook, validator, tests, and migration. Too big for one pass. This doc decides how to divide it before writing any structure docs.

**Parent design:** `docs/subtasks/P13_scope_model.md`
**Checklist reference:** `docs/IMPLEMENTATION_CHECKLIST.md` Part 0

---

## The natural architectural seams

Walking P13's scope, there are 5 distinct layers of concern that naturally separate:

1. **Schema layer** — what a P-constraint looks like on disk. Adding `scope_mode` + `scope` fields. Pure data shape.
2. **Pure computation layer** — given P-constraints with scope, produce the three pools (universal/scoped/inclusion). No side effects, just set operations on the pool.
3. **Bar-checking layer** — given the three pools, evaluate lower bars (viability) and upper bars (meaningfulness). Pure functions consuming layer-2 output.
4. **Tag affinity layer** — derive tag→categories affinity from real pool data; validate a P-constraint's tag against affinity. Self-contained helper.
5. **Integration layer** — rewire `_verify_task_solvable` and `validate_task_schema` to use the new pipeline. This is where end-to-end behavior changes.
6. **Agent surface** — handbook rewrite, prompt examples, per-pattern guidance table. Text-only, no logic.
7. **Migration** — regenerate existing 60 tasks OR decide their fate.

Layers 2, 3, 4 are **pure helper modules** that can be built and unit-tested in isolation without touching the live pipeline. This is the biggest leverage point in the division — build the helpers first, verify them thoroughly with unit tests, THEN wire them in.

---

## Proposed division: 5 sub-subtasks

### P13.1 — Schema + pattern defaults (~1.5h) ✅ COMPLETE

**Status:** shipped. `_check_scope_declarations()` and 4 constant frozensets added to `test_generate_tasks.py`. 33 new test checks in `test_e3.py`, all green. Full test suite 462/462. Function is dormant (not called from `validate_task_schema` yet) — P13.5 wires it in.

**Purpose:** Add `scope_mode` + `scope` fields to the P-constraint schema. Schema validator hard-requires `scope_mode`. Define the pattern-to-default-scope-mode table for documentation (used in handbook later).

**Files touched:**
- `test_generate_tasks.py` — `validate_task_schema` adds scope_mode/scope checks
- New test cases in E3 test suite for schema validation

**Deliverable:** schema rejects P-constraints missing `scope_mode`. Tests green.

**Scope boundaries:**
- IN: schema field validation, required-field enforcement, scope shape validation (scope_mode ∈ {universal, scoped, inclusion}; scope field present when scoped)
- OUT: actually USING scope_mode anywhere. Pure schema-level checks.
- OUT: tag affinity (P13.4 handles that)

**Can ship independently:** yes. Once landed, task generation will fail until agents also produce scope_mode. So P13.1 and P13.5+P13.6 need to land together OR P13.1 needs a feature flag. See below.

**Risk:** If P13.1 lands alone without the handbook updates (P13.6), the existing agent prompts won't include `scope_mode` in their output and ALL generation will fail. So P13.1 ships "built but not enforced" — the schema validator code exists but isn't called until P13.6 lands the handbook changes. Practical implementation: P13.1 adds the validation code as a separate function (`_check_scope_declarations`) that isn't called from `validate_task_schema` yet. Gets called only after P13.6.

---

### P13.2 — Pure scope-model helpers (~2h)

**Purpose:** Build the three-pool construction helpers as a pure module. No integration with existing code; just the primitives and unit tests.

**Files touched:**
- New file: `scripts/generation/scope_model.py` — pure functions
- New tests: extend E3 test suite or add dedicated test_scope_model.py

**Proposed API surface:**
```python
def partition_pool(pool: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (food_venues, site_venues)."""

def build_universal_pool(
    pool: list[dict], 
    personal_constraints: list[dict]
) -> list[dict]:
    """AND of all universal-mode filters. Returns venues surviving all universals."""

def build_scoped_pools(
    universal_pool: list[dict],
    personal_constraints: list[dict]
) -> dict[str, list[dict]]:
    """Per-PC scoped pool. Keyed by constraint id."""

def build_inclusion_pools(
    universal_pool: list[dict],
    personal_constraints: list[dict]
) -> dict[str, list[dict]]:
    """Per-PC inclusion candidate set. Keyed by constraint id."""
```

**Deliverable:** `scope_model.py` exists with pure functions, unit tested in isolation. Nothing in the live pipeline imports from it yet.

**Scope boundaries:**
- IN: pure pool construction for the three modes, FOOD/SITE partition
- OUT: bar checking (P13.3)
- OUT: integration with `_verify_task_solvable` (P13.5)
- OUT: tag affinity (P13.4)

**Can ship independently:** yes. Since nothing imports it yet, tests on the new module can pass without affecting anything else.

**Risk:** Low. Pure function module with no integration. Worst case we write it wrong and discover in P13.5 — but we have unit tests first.

---

### P13.3 — Bar-checking helpers (~1.5h)

**Purpose:** Build the lower-bar and upper-bar check functions as pure helpers in the same `scope_model.py` (or a sibling module).

**Files touched:**
- `scripts/generation/scope_model.py` (extend from P13.2)
- Tests extend

**Proposed API surface:**
```python
def check_lower_bars(
    universal_pool, scoped_pools, inclusion_pools,
    personal_constraints, days: int
) -> list[str]:
    """Returns list of violation messages. Empty list = all bars pass."""

def check_upper_bars(
    universal_pool, scoped_pools, inclusion_pools,
    personal_constraints, food_total: int, site_total: int
) -> list[str]:
    """Returns list of violation messages. Empty list = all bars pass."""

# Constants at top of module:
UNIVERSAL_UPPER_BAR = 0.5
SCOPED_UPPER_BAR = 0.5
INCLUSION_UPPER_BAR = 0.25
VIABLE_RESTAURANTS_PER_DAY = 2
VIABLE_SITES_PER_DAY = 1
```

**Deliverable:** Bar-checking functions in `scope_model.py`, unit-tested. Still no integration.

**Scope boundaries:**
- IN: all lower/upper bar logic, error message formatting
- OUT: actual usage in the validation pipeline (P13.5)

**Can ship independently:** yes. Still pure helpers.

**Risk:** Low. Pure functions.

---

### P13.4 — Tag-category affinity (~2h)

**Purpose:** Compute per-city tag-category affinity from pool data. Validator check that enforces affinity when agent writes `label_required`/`label_excluded`.

**Files touched:**
- New file: `scripts/generation/tag_affinity.py` (or extend `scope_model.py`)
- Possibly: cache affinity in the city DB to avoid recomputing (decide during structure doc)
- Tests extend

**Proposed API surface:**
```python
def compute_tag_affinity(pool: list[dict]) -> dict[str, set[str] | None]:
    """
    For each tag, returns allowed categories or None (universal).
    Follows the rules in P13_scope_model.md:
      - ≥80% concentrated → scope to that category
      - ≥30% in 2+ cats → scope to those categories
      - otherwise → None (universal)
      - <3 appearances → excluded (rare)
    """

def validate_tag_scope(
    pc: dict, 
    affinity: dict[str, set[str] | None]
) -> list[str]:
    """Returns violation messages. Empty = OK."""
```

**Deliverable:** `tag_affinity.py` exists with pure functions. Unit tested.

**Scope boundaries:**
- IN: affinity computation from pool, validator check function
- OUT: cache-the-affinity-in-DB optimization (defer to later subtask if needed)
- OUT: integration into main validator (P13.5)

**Can ship independently:** yes.

**Risk:** Low. Pure function module.

---

### P13.5 — Pipeline integration (~2.5h)

**Purpose:** Rewire `_verify_task_solvable` and `validate_task_schema` to use the P13.1-4 helpers. This is where end-to-end behavior actually changes.

**Files touched:**
- `scripts/generation/generate_task.py` — `_verify_task_solvable` replaced with staged check
- `scripts/generation/pool_utils.py` — `_apply_pool_filters` changed to only apply universals (or deprecated in favor of `scope_model.py` helpers)
- `test_generate_tasks.py` — `validate_task_schema` calls `_check_scope_declarations` (from P13.1) and tag affinity check (from P13.4)
- Existing E3 tests — verify they still pass with new pipeline OR update them

**Deliverable:** live pipeline uses the scope model. The 60 existing tasks may start failing validation if they don't have scope_mode (covered by P13.7 migration).

**Scope boundaries:**
- IN: rewiring of existing validators, deprecation/removal of duplicate logic
- OUT: handbook changes (P13.6) — agents still using old prompts may produce invalid tasks, but that's handled by P13.6 landing before the next generation run
- OUT: migration of existing tasks (P13.7)

**Can ship independently:** NO — without P13.6 (handbook), the next generation run will fail because agents won't produce scope_mode. Must land together with P13.6 (or P13.6 first).

**Risk:** HIGH. This is the breaking change. Everything that imported `_apply_pool_filters` now needs to know about scope_mode, or fall through to a compat path. Risk mitigations:
- Keep `_apply_pool_filters` signature unchanged; internally delegate to scope-aware logic with a compat shim when scope_mode is absent (old tasks default to universal)
- All E3 tests must still pass
- Add new E3 tests for the new pipeline
- Structure doc must explicitly list every call site of the changing functions

---

### P13.6 — Handbook + agent prompt (~2h)

**Purpose:** Update the task agent's handbook and prompt to teach scope_mode. Produce working examples, the per-pattern guidance table, tag-category affinity explanation for agents.

**Files touched:**
- `scripts/generation/task_agent.py` — handbook text
- `scripts/generation/generate_task.py` — if there's a SYSTEM_PROMPT or task prompt, update
- Nothing in the validator changes

**Deliverable:** agents produce scope_mode-declared tasks. Next generation run succeeds.

**Scope boundaries:**
- IN: handbook prose, pattern-default table, tag-affinity guide for agents, wrong-vs-right worked examples
- OUT: validator logic (P13.5)
- OUT: new patterns (P12)

**Can ship independently:** sort of. Without P13.5 (validator), the handbook teaches a format the validator doesn't enforce yet — harmless but inconsistent. BEST to land P13.6 and P13.5 in the same session so they flip together.

**Risk:** Low. Text-only. Worst case the handbook is unclear and agents write tasks incorrectly — we'll see this in testing and iterate.

---

### P13.7 — Migration + end-to-end test (~1h)

**Purpose:** Regenerate the 60 existing tasks with new schema. Run the full E2E test to verify task generation works.

**Files touched:**
- Run generation script, produce new `data1/tasks/unfiltered/` output
- Compare success rates vs. baseline (pre-P13)
- If anything broke, iterate on P13.5 or P13.6

**Deliverable:** fresh 60-task run with scope_mode declarations. Success rates noted. Problems documented for follow-up.

**Scope boundaries:**
- IN: one-time regeneration, results analysis
- OUT: fixing any discovered bugs in the scope model (those become P13.X iterations or new subtasks)

**Can ship independently:** requires everything else.

**Risk:** Will discover things that weren't caught in unit tests. Budget for follow-up work.

---

## Dependency graph

```
P13.1 (schema) ──────────────────────────┐
                                          │
P13.2 (pool helpers) ─┐                   │
                      ├─ P13.5 ──── P13.7
P13.3 (bar helpers) ──┤  (integration)  (migration + E2E test)
                      │
P13.4 (tag affinity) ─┘  
                                          │
P13.6 (handbook) ─────────────────────────┘  (can run in parallel with P13.2-4)
```

Sequential sequence for implementation (one session each, approximate):

1. **P13.1** — schema (~1.5h). Doesn't break anything. Tests green.
2. **P13.2** — pool helpers (~2h). Pure module. Tests green.
3. **P13.3** — bar helpers (~1.5h). Pure module. Tests green.
4. **P13.4** — tag affinity (~2h). Pure module. Tests green.
5. **P13.5 + P13.6 together** (~4.5h). The breaking-change moment. Live pipeline changes + handbook changes land in the same session.
6. **P13.7** — migration + E2E test (~1h). Validate everything worked.

**Parallelism opportunity:** P13.2, P13.3, P13.4 are independent pure modules. If multiple sessions are available, they could proceed in parallel. In practice I do them sequentially.

---

## Checkpoint alignment

**After each of P13.1-P13.4:** tests green, structure doc for the next subtask reviewed with user. These are natural "stop and export" points.

**After P13.5+P13.6:** FIRST integration checkpoint. Run ALL existing E3 tests. They may fail if pipeline behavior changed in ways tests weren't updated for — fix tests to match new correct behavior, document which test expectations changed.

**After P13.7:** END-TO-END TEST POINT as discussed. Run task generation with real models. Evaluate:
- Do SOTA models produce scope_mode declarations correctly?
- Do types 1, 2, 4, 6 succeed at normal rates?
- What's happening with types 3, 5 (expected: same as before because P9/P10 haven't landed yet)?

---

## Rollback granularity

Because P13.1-P13.4 are additive (new fields, new module, unused until P13.5), they can be reverted independently without breaking anything.

P13.5+P13.6 land together and must revert together. If the integrated pipeline has a bug, we revert both and the system returns to pre-P13 state.

P13.7 is a data regeneration — revert by re-running with old schema if needed.

---

## What this leaves for follow-ups

- **P9**: tension detection redesign. Depends on P13.5 being live (uses the staged pools).
- **P10**: Type 5 viable-schedule. Depends on P13.5.
- **P12**: new patterns, category_venue_count rename. Naturally uses inclusion mode from P13.
- **P2/P15**: pool composition improvements. Will make more of P13's upper bars pass on real data.

---

## Estimation summary

| sub | purpose | est | risk | blocks what |
|---|---|---|---|---|
| P13.1 | schema + defaults | 1.5h | low | P13.5 |
| P13.2 | pool helpers | 2h | low | P13.5 |
| P13.3 | bar helpers | 1.5h | low | P13.5 |
| P13.4 | tag affinity | 2h | low | P13.5 |
| P13.5 | pipeline integration | 2.5h | HIGH | P13.7 |
| P13.6 | handbook | 2h | low | P13.7 |
| P13.7 | migration + E2E | 1h | MED | P9/P10/P12 |
| **TOTAL** | | **~12.5h** | | |

Slightly over the original 11.5h because we're being explicit about testing and structure docs per sub-subtask.

---

## Proposed session plan

Rough session-by-session:

- **Session A**: P13.1 (structure doc → approval → implement → test → export)
- **Session B**: P13.2 (structure doc → approval → implement → test → export)
- **Session C**: P13.3 + P13.4 (may fit in one session since both are small pure modules)
- **Session D**: P13.5 + P13.6 together (the big integration)
- **Session E**: P13.7 — migration, E2E test, discuss findings

5 sessions. Each session produces a structure doc, gets approval, implements, tests, exports.

---

## Open questions before division approval

1. **P13.2 and P13.3 as one module or two?** I proposed both in `scope_model.py`. Alternative: `scope_model.py` for pool construction, `scope_bars.py` for bar checking. Two files is more separated; one file is simpler. Lean: one file — they're tightly related and split invites duplication.

2. **P13.4 cache strategy?** Recompute tag affinity on every validation call vs. compute once per city and cache in memory (or DB). Lean: compute-on-load, cache in a module-level dict keyed by city. Defer DB caching unless performance matters.

3. **P13.7 — what if E2E surfaces bugs?** If the integrated pipeline has real problems, does P13.7 extend into bug fixes, or do we declare P13 done at P13.6 and treat E2E findings as P16+ subtasks? Lean: treat real bugs as P13.7.1, P13.7.2 iterative fixes before declaring P13 done, UNLESS the bug is large enough that it warrants its own subtask.

4. **Where does `_apply_pool_filters` end up?** Currently called from `_verify_task_solvable` and `compute_avg_venue_difficulty`. Proposal: keep it as a compat shim that delegates to `scope_model.py`. Structure doc for P13.5 will decide. Worth flagging now because the answer affects how much P13.5 has to touch.
