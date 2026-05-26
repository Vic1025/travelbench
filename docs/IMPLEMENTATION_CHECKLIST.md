# Pre-implementation checklist — TravelBench Phase 4

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.

**Purpose:** Before touching code for any subtask, work through this checklist. It exists because the user is giving natural-language designs, not code-level specs. The engineer (me) owns translating design intent into clean code structure — and must show the translation BEFORE implementing, for user approval.

**When to use:** Start of every subtask implementation session, including mid-subtask if scope shifts.

**Output:** A code-structure design doc (separate markdown file under `docs/subtasks/<subtask>_structure.md`) that answers every question below, presented to the user for approval before writing any code.

---

## Part 0: Division planning (for oversized subtasks)

**When a subtask is too big to implement cleanly in one pass, do a division session FIRST before any structure doc or code.**

Signs the subtask is too big:
- Implementation estimate exceeds ~6 hours of focused work
- Scope touches >6 files or >3 architectural layers
- Multiple independent concerns bundled (e.g., schema + pipeline + handbook + tests)
- You find yourself wanting to write multiple structure docs to cover it

When this applies, produce a **division doc** at `docs/subtasks/<subtask>_division.md` that answers:

1. **Natural seams**: where does the subtask naturally split? Look for boundaries like "schema before logic," "pure helpers before integration," "internal correctness before UX surface."
2. **Division proposal**: list 2-5 sub-subtasks. For each:
   - One-sentence purpose
   - Estimated effort
   - What files it touches (rough)
   - Can it ship independently? (green tests at end)
3. **Dependency graph**: which sub-subtask must land before which? Is it strictly sequential, or is there parallel work possible?
4. **Checkpoint alignment**: which sub-subtask is a good "stop, run tests, possibly export" point? Which is a good end-to-end test point?
5. **Rollback granularity**: if sub-subtask N breaks something, can we revert just N, or does it entangle with N-1?
6. **What's left for follow-ups**: is there anything the division leaves as TODO for another subtask or phase?

**Present the division doc to the user. Wait for approval of the division BEFORE writing any structure docs for the sub-subtasks.**

After division is approved, the normal Parts 1-6 apply to each sub-subtask individually.

Why this rule exists: oversized subtasks invite inconsistent design decisions mid-flight. Better to explicitly decide "this is 4 pieces, here's the order" than to discover the decomposition while writing code.

---

## Part 1: Understanding the subtask

### 1.1 Read the related design docs

Start from the top, in order:
- `docs/VALIDATION_DESIGN.md` — the unified big picture
- The specific subtask doc (`docs/subtasks/P<N>_*.md`)
- Any subtask docs this one DEPENDS on (e.g., P9 depends on P13 — read P13 first)
- Any subtask docs this one BLOCKS (to understand what our output must enable)
- `docs/task_structural_types.md` if the subtask touches per-type behavior
- `docs/DESIGN_DECISIONS.md` if the subtask touches schema or DB

Specifically look for:
- **Locked decisions** (things marked "confirmed," "user decided") — these are non-negotiable
- **Open questions** (things still marked "resolved during implementation" or similar) — flag them, resolve with user before coding
- **Edge cases** mentioned in design — enumerate them explicitly in the structure doc

### 1.2 Write back the design in your own words

Before looking at code, answer in the structure doc:
- What does this subtask actually do, in 3-5 sentences?
- What are the 5-10 most important edge cases?
- What counts as "done"?
- What would a user see change after this lands?

If you can't answer these from design docs alone, the design isn't clear enough — escalate before coding.

### 1.3 Enumerate edge cases explicitly

For each subtask, spell out:
- **Missing/null inputs**: what if schema fields are absent?
- **Boundary conditions**: empty pools, single-venue pools, pools where every venue matches a filter
- **Malformed data**: agent writes `min_count: "2"` instead of `2`, agent writes partial scope object
- **Contradictory constraints**: two filters that together eliminate the pool
- **Pool-specific edge cases**: what if FOOD or SITE count is 0?
- **Migration cases**: what happens to in-flight tasks generated with old schema?

Write these in the structure doc with expected behavior for each.

---

## Part 2: Understanding the current code

### 2.1 Map the current pipeline end-to-end

Before writing new code, trace:
- Where does data ENTER this subtask's concern? (e.g., which validation entry point calls into what)
- Where does data EXIT? (e.g., what downstream code consumes the output)
- What's the current file/function layout around this concern?
- Which imports cross module boundaries?

**Concrete for validation work:**
- `test_generate_tasks.py:validate_task_schema` is one entry point
- `scripts/generation/generate_task.py:_verify_task_solvable` is the other
- `scripts/generation/pool_utils.py:_apply_pool_filters` does pool narrowing
- `eval/evaluator.py` has per-pattern scoring handlers
- `scripts/generation/task_agent.py` has the handbook + agent prompt

Don't invent the layout — go READ each file's relevant sections and note current structure in the structure doc.

### 2.2 What's already working end-to-end?

TravelBench currently generates tasks end-to-end with 60 real tasks across 5 models. **Do not break the current pipeline**. Before touching a function:
- Note what currently calls it
- Note what it currently returns
- Note any existing tests that verify it
- Keep its public interface stable IF POSSIBLE; if changing, update every caller

Breaking the current pipeline is a high-cost failure — diagnosing "why did generation just start crashing" across the 10-file validation chain is expensive. Minimize damage.

### 2.3 Identify which files/functions need to change

In the structure doc, list:
- **Files to modify** — with reason per file
- **Files to create** — with purpose and scope
- **Files NOT to touch** even though they're tempting (scope discipline)
- **Functions to deprecate** — what replaces them, migration path

Use this list to sanity-check scope. If the list has >8 files, the subtask is probably too big and should split.

---

## Part 3: Designing the new structure

### 3.1 Module boundaries — the main design discipline

The user wants "minimal damage, few dependencies between modules, wrapped helpers that don't affect other stuff."

For each new piece of logic, ask:
- **Can this live as a pure helper function with no side effects?** If yes, put it in a helper module (e.g., `scripts/generation/scope_model.py`) and import it where needed. Pure helpers are the safest.
- **Does this need to mutate shared state or call other modules?** If yes, think hard about whether you can refactor the logic to be pure first. If genuinely stateful, contain the statefulness to one module and expose a clear API.
- **Is there duplication between existing modules?** Don't introduce new modules that overlap with existing ones. Extend or refactor existing.
- **What's the LEAST invasive change that does the job?** Sometimes an import and a function call at one location is enough.

### 3.2 Module layout proposal

In the structure doc, propose a concrete layout:

```
Existing file: scripts/generation/pool_utils.py
  CHANGE: add parameter `scope_filter` to _apply_pool_filters
          (default: skip non-universal filters to preserve current behavior
           for callers that don't pass scope_filter)
  
New file: scripts/generation/scope_model.py
  CONTAINS:
    - build_universal_pool(pool, constraints) → list[venue]
    - build_scoped_pools(universal_pool, constraints) → dict[pc_id, list[venue]]
    - build_inclusion_pools(universal_pool, constraints) → dict[pc_id, list[venue]]
    - check_upper_bars(pools, pool_stats) → list[str]  # violations
  
Existing file: scripts/generation/generate_task.py
  CHANGE: _verify_task_solvable calls scope_model helpers
          replaces current single-pass with staged check
```

Draw the import graph. If module X imports Y imports X, that's a problem — fix the design before coding.

### 3.3 Interface design — what each function takes and returns

For each new function, decide:
- **Input**: what arguments, what types, what invariants must hold? (document the invariants in the docstring)
- **Output**: what return type, what does it mean? (NEVER return `dict[str, Any]` without documenting keys)
- **Side effects**: ideally none; if some, document them
- **Failure modes**: does it raise? return None/False? return an error list?

Pick a consistent failure-reporting style PER SUBTASK (don't mix raising and returning error lists in the same module).

### 3.4 Possible attacks / silent failures

List what could go wrong silently:
- **Agent submits wrong scope_mode** — will validation catch it or silently proceed with a default?
- **Empty list edge case** — if `universal_constraints` is empty, does `build_universal_pool` return the full pool or an empty list? What's the contract?
- **Float precision** — if we divide 23 by 25 to get 0.92, and threshold is 0.5, we compare floats — any issues?
- **Unicode in tags** — filter strings with Unicode characters
- **In-context task state** — is `task` passed by reference? Does anyone mutate it?
- **Registry collisions** — if two filter IDs collide, does the code quietly overwrite?

For each risk, decide: **expose or swallow?** Default should be EXPOSE. The previous sessions saw several silent failures (thread crashes hidden by the executor; the broken `category_count_minimum` scorer silently passing). We want loud failures in future.

Rule of thumb: any "should never happen" case should `raise` or return a clear error, not default silently. The thread_crash_*.json mechanism from earlier sessions is the right pattern — make invisible failures visible.

### 3.5 Scope boundaries

Before writing code, answer:
- **What does this subtask cover?** (positive scope)
- **What does it explicitly NOT cover?** (negative scope — things the user might expect but aren't in this task)
- **What does it leave to future subtasks?**

Example (P13):
- IN: scope_mode schema, pipeline refactor to 5 stages, tag-affinity, upper/lower bars
- OUT: type-3 tension logic (P9), type-5 viable-schedule expansion (P10), new patterns (P12)
- OUT: B-score handling (P11 on hold)
- LEAVES: existing tasks need regeneration (handled via a separate regeneration step, not in-code migration)

---

## Part 4: Verification plan

### 4.1 Test coverage

For each subtask, identify:
- **Existing tests affected**: which E1/E2/E3/E4 tests touch code in this subtask's scope? Will they still pass? Or do they need updates to match new behavior?
- **New tests needed**: per edge case from Part 1.3, do we have a test? Write them.
- **Regression tests**: any bug-fix should come with a test that would have caught the bug.

Target: **100% of existing tests still green after subtask lands**. If a test needs to CHANGE (not break — genuinely updated to match new correct behavior), document WHY in the structure doc and get user approval before changing.

### 4.2 End-to-end impact

Before committing to implementation, predict:
- Will existing generated tasks (the 60 we have) still validate after this change? If not, what's the migration?
- Will the agent still be able to generate tasks? Any prompt changes needed?
- Will current success rates change? Is the change intentional?

### 4.3 Rollback plan

If the subtask lands and breaks something we didn't anticipate:
- What's the minimal revert? (ideally one file or a few related commits)
- Can the system limp along with the subtask disabled via a feature flag?
- Is there a strict dependency chain that makes partial revert impossible?

Simpler is better. If rollback is hard, the subtask is probably too coupled.

---

## Part 5: Final approval gate

### Before writing any code, the structure doc should answer:
- [ ] I've read all related design docs and can state the subtask's purpose in my own words
- [ ] I've enumerated the edge cases
- [ ] I've mapped the current code structure and identified files to change
- [ ] I've proposed a clear module layout with minimal dependency surface
- [ ] I've designed function interfaces with types and invariants
- [ ] I've listed possible silent-failure modes and decided to expose them
- [ ] I've defined scope boundaries (IN, OUT, FUTURE)
- [ ] I've planned test coverage
- [ ] I've predicted end-to-end impact
- [ ] I've noted rollback considerations

### Present the structure doc to the user and wait for approval.

Do not write implementation code until the user has approved the structure. If the user requests changes, iterate on the structure doc. Only when approved, start writing code.

---

## Part 6: During implementation

Once approved:

- **One module at a time.** Don't start another file until the current one is clean, tested, and imports properly.
- **Tests as you go.** Write the test for a function before or immediately after the function itself. Don't defer tests to the end.
- **Run the full test suite periodically** — not just at the end. Catch regressions early.
- **Surface silent failures.** If you find yourself writing `except: pass`, stop — that's almost always wrong. Log at minimum; raise if truly exceptional.
- **Commit-size discipline** (in head, even if we're not using git here): if one logical change spans 8 files, that's probably 2-3 coherent changes conflated.

### Signs to STOP and re-check with user:
- You discover the design as documented can't work as-stated
- You realize two subtasks overlap more than expected
- You find existing tests that contradict the design
- You need to touch a file not listed in the structure doc
- You've been working for >3 hours without landing a green test suite

### When the subtask is DONE:
- All related tests green
- No new `except: pass` blocks
- No new silent failures
- Structure doc updated if reality diverged from the original plan
- Export zip for user review before declaring complete

---

## Part 7: Example — what this looks like for P13

Bad approach (what NOT to do):
> "Start refactoring `_apply_pool_filters` to support scope_mode."

Good approach:
> "1. Read P13_scope_model.md (scope modes, 5-stage pipeline, upper bars).
>  Read VALIDATION_DESIGN.md stages 1-5 section.
>  Read pool_utils.py (current _apply_pool_filters).
>  Read generate_task.py _verify_task_solvable.
>  Read test_generate_tasks.py validate_task_schema."
> 
> "2. Current structure: _apply_pool_filters is one function, 70 lines,
>  applies every P-constraint universally. Called from _verify_task_solvable
>  and compute_avg_venue_difficulty. Has no concept of scope. Solvability
>  check is single-pass right after filter application."
> 
> "3. Proposed structure:
>  - New file scripts/generation/scope_model.py with pure helpers
>  - _apply_pool_filters gets a scope_mode param that defaults to 'all' for
>    back-compat with callers that don't know about scope
>  - _verify_task_solvable calls the new staged pipeline when task has scope_mode,
>    falls back to existing behavior when scope_mode is absent (old tasks)
>  - Schema validation adds scope_mode required-field check
>  - Upper-bar checks as separate functions in scope_model.py, easy to unit-test"
> 
> "4. Edge cases: task with no P-constraints (no filters to process - all pools
>  equal universal pool), constraint with malformed scope dict, pool with zero
>  food venues, pool with zero site venues, ..."
> 
> "5. Scope IN: schema + pipeline + upper/lower bars + tag affinity
>     Scope OUT: tension detection (P9), P12 patterns, B-scores"
> 
> "Present this doc to user. Wait for approval. Then code."

---

## Part 8: Anti-patterns to watch for

Things to never do without explicit user approval:

- **Rewriting a function from scratch** when an edit would do. The current code has subtle behavior captured in edge cases — preserve unless genuinely refactoring.
- **Adding a new dependency** (new PyPI package, new API). If you think you need one, surface it before committing.
- **Changing test expectations** to match new behavior without flagging it. If a test that was passing needs to change, that's a signal the change is more than "correcting the implementation."
- **Adding feature flags or config toggles** to defer decisions. Make the decision; use constants.
- **Silent defaults** when schema is malformed. Hard-require is usually the right answer per the user's past direction.
- **Importing from `test_*.py` files** — tests should depend on production code, not the other way around.
- **Circular imports** — if A imports B imports A, the design is wrong.

---

## One more thing: the meta-lesson

The user's explicit instruction was "natural-language design, I don't have energy to figure out code structure WITH you, you figure it out and show me before coding." This means:

1. The structure doc is not optional; it's the contract.
2. "Obvious" implementation choices still need to appear in the doc — the point isn't that the user needs to discover them, it's that writing them down catches mistakes.
3. When the user says "approved," they're approving what's written, not what's implied. Write things explicitly.
4. If mid-implementation you realize the doc is wrong — STOP. Update the doc, get re-approval. Don't silently deviate.

This discipline is the difference between "subtask lands clean" and "subtask creates 3 follow-up bugs." The overhead of writing the doc is paid back many times over by avoiding mid-implementation confusion.
