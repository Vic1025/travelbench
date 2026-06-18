# Valid-Difficulty Redesign — making corruption actually add difficulty

**Branch:** `valid-difficulty-redesign`
**Status:** design contract for workstreams (a) this doc → (b) reusable flaw
library + task binding → (c) controlled 3-arm ablation.
**Grounding:** the literature review in `docs/PHASE7_FUTURE_WORK.md` (P7-I/J/K),
PDFs in `~/Documents/Books and Papers/Papers/Internet-Noise-Benchmark/`, and the
pilot result in `scripts/_pilot_analysis.md`.

---

## 1. Problem — corruption that doesn't add difficulty

A clean-vs-faulty pilot (`scripts/_pilot_analysis.md`, `results/scores.db`,
toggled by `run_benchmark.py --clean-environment`) found the faulty environment
did **not** make tasks harder, and on some types made them *easier*. Three design
bugs, all confirmed in code — not model robustness:

1. **Corruption misses the rubric.** 83% of `wrong_info` (24/29 NYC) targets
   F-score fields (`hours_*`, `visit_minutes`, `booking`). Task success is
   defined by P-constraints that rarely depend on those fields. The only type
   with a binding P-constraint on a corrupted field — Type 4 (`avg_cost_local`)
   — is the only one that showed the predicted effect (P 0.75→0.25 with the
   trap). Corruption is assigned at venue-gen from `traffic_tier`/category
   (`generate_city_venues.py:443`), **decoupled** from what tasks test.
2. **Truth is free.** `get_official_site()` is always authoritative and never
   corrupted; every `wrong_info` carries a mandatory truth-carrier; mid/high
   venues have 2–6 mostly-agreeing docs; high-traffic venues are corruption-
   exempt. Redundancy out-votes the lone fault at zero cost.
3. **Resolution is off the scoring critical path.** C/F/P/B all score against GT
   (`venues`). The only corruption-aware signal is F2c (flat −0.05 for scheduling
   a wrong-info venue without fetching its truth-carrier). Avoiding wrong-info
   venues entirely costs nothing; nothing rewards picking truth over a lie.

**Measurement confound:** clean mode *deletes* incorrect-source docs
(`mock_tools._load_city_from_db`), changing doc count/context — so the A/B mixes
"trap exposure" with "how much text the agent saw." That is why Type 1 got
*worse* in clean mode (−0.25): removing docs starved the agent of triangulation
context and shifted its venue selection.

**Realization that drives the whole redesign:** the scorer is already correct
(GT-based). We do not change scoring. We (i) put corruption on the fields the
scorer reads, (ii) remove the free shortcuts to truth, and (iii) make the clean
arm a true control.

---

## 2. Core concept — two wedges

The benchmark difficulty is the gap between a **naive** agent (trusts the first
source / the majority / the official site) and a **careful** agent (cross-checks,
weighs recency & engagement, detects copied sources). That gap is *two distinct
quantities at two granularities* — keeping them separate is the key insight.

**Wedge 1 — the flaw wedge (per venue + field).** Unit = "did you get the field
value right?", not task score:
`field_correct(careful) − field_correct(naive)`.
A pure property of a flaw + that venue's served docs. **No task involved.** This
is what we manufacture and validate.

**Wedge 2 — the task wedge (per task).** The flaw wedge propagates into task
score, but *only through a binding constraint*:
`task_score(careful) − task_score(naive)`.

The flaw wedge is the **reusable cause**; the task wedge is the **effect**,
switched on when a task's binding constraint reads the flawed field.

> **Worked example — one flaw, two tasks.**
> Flaw: Veselka GT `avg_cost=$30` (over budget), Yelp shows `$12` (false-positive).
> - *Sandbox (flaw wedge):* careful reads docs → $30 ✓; naive trusts top Yelp →
>   $12 ✗. Wedge exists → flaw admitted.
> - *Task A "dinner ≤ $24/head" (budget binds avg_cost):* naive includes Veselka,
>   thinks total $20, submits → scored vs GT → real $38 → over → P=0.4. Careful
>   recovers $30, swaps it out → real $22 → P=1.0. **Task wedge = 0.6.**
> - *Task B "find Italian restaurants" (cuisine binds, not cost):* cost flaw
>   touches nothing the rubric reads → same picks → **task wedge = 0** (dormant).

Consequences: a flaw lives on a venue (intrinsic, reusable across tasks and
cities); difficulty is per-task; the same library serves everything; and a flaw
that is dormant for one task is load-bearing for another with no change.

---

## 3. Flaws are reusable, venue-intrinsic assets (the mask library)

**Why F felt easy and P felt impossible — and why they're the same.** F-faults
(hours, booking, visit_minutes) felt "universal" because they're *intrinsic venue
properties* you could plant at venue-gen. But most P-constraints test **tags and
categorical/numeric venue features** — `cuisine`, `wheelchair_accessible`,
`local_cuisine`, `price_tier`, `waterfront`, `district`, `avg_cost`. Those are
*also* intrinsic. So a P-flaw is exactly as venue-intrinsic and reusable as an
F-flaw; we were only picturing P-flaws as bespoke.

**Flaw mask** = `(predicate over venue features/tags, target field, direction,
category, detectability)`. Examples:
- `price_tier=upscale → flaw avg_cost DOWN (temporal_decay), detectability=0.4`
- `category=restaurant ∧ ¬wheelchair → flaw wheelchair_accessible UP (subjective)`
- `cuisine=Italian → flaw cuisine to a near-neighbor (propagation_error)`

A **flaw** (the concrete instance a mask produces on a matching venue) =
`{venue, field, incorrect_value, correct_value=GT, direction, category,
detectability, recovery_paths}`. The library is a handful of masks; the seeded
operator applies them to produce concrete, reusable per-venue flaws. Each flaw is
self-contained and recoverable on its own (§4), so it can be reused by any task
and is portable across cities.

**Direction matters — bias to false-positive.** Corrupt so a constraint-
*violating* venue *looks satisfying* (cost down, hours wider, accessibility
claimed). The naive agent includes an infeasible venue → direct, attributable
violation when scored vs GT. The opposite direction (good venue looks bad) only
makes the agent skip a fine option → soft, hard-to-attribute penalty.

---

## 4. Validity — the per-venue sandbox (replaces the mandatory truth-carrier)

Validity is a **per-venue property**: *given this venue's served docs, can a
strong reasoner recover GT, while a naive one trips?* We validate it empirically,
in isolation, with two cheap passes over **only that venue's docs**:

- **Careful sandbox** (strong/SOTA reference agent): must recover the GT field
  value → proves the flaw is **fair / recoverable**.
- **Naive sandbox** (trust-first-source agent, no cross-check): must get it wrong
  → proves the flaw **has teeth**.

**Admit a flaw iff `careful recovers AND naive trips`.** Careful can't recover →
unfair, reject. Naive also right → toothless, reject. This *is* the flaw wedge,
measured directly.

**The truth-carrier becomes optional.** It was only a *structural guarantee* of
recoverability; the sandbox is an *empirical, tested* guarantee — and more
realistic, since real-internet recovery comes from corroboration, recency, and
plausibility reasoning, not always a doc that says "actually it's X." A flaw may
list one or more `recovery_paths` (corroborating sources / authority / a
truth-carrier / pure plausibility); the sandbox just has to find one. Caveats
that keep it valid: (a) recoverability is now defined *relative to the reference
careful agent* — make it strong and pin its version; (b) if a flaw also removes
the free authority (b2) and is a copying-majority flaw (b3), the sandbox must
still find some path or it is correctly rejected as unrecoverable.

---

## 5. Architecture & components

Post-hoc, seeded operator over the **clean canonical corpus** + the **tasks'
binding constraints**. Canonical GT is immutable; corruption is an additive
overlay; tools serve the overlay; the evaluator scores against GT only.

```
                       flaw-mask library
                              │ seeded apply
                              ▼
  venues (GT, immutable) ─▶ concrete per-venue flaws ──┐
                              ▲                         │ render
        author-on-demand      │ (admitted flaws)        ▼
        request ┌─────────────┘            served views (yelp_listings, source_docs)
                │                                        │
        task-gen agent                                   │  agent plans
        - selects existing flaws (flaw-aware)            ▼
        - emits "need flaw on field F                eval/evaluator.py
          of venue V" ───────▶ flaw-gen sandbox     (scores vs GT `venues` only)
                                - authors flaw (+opt truth-carrier)
                                - runs careful+naive sandbox (§4)
                                - admitted → library (reusable)
```

Components:
- **Flaw-mask library** — the reusable masks (§3); seeded operator instantiates
  them. New: `scripts/generation/flaw_masks.py` + an operator
  `scripts/generation/inject_flaws.py`.
- **Flaw-gen sandbox** — an *isolated* agent/service that authors a single flaw
  and runs the §4 careful+naive validation. **Task-gen never authors flaws
  inline** (that would stack flaw-authoring + validation into the task agent's
  one context window); it emits a *request* and the sandbox returns an admitted,
  reusable flaw. New: `scripts/generation/flaw_sandbox.py`.
- **Task-gen binding (hybrid)** — default **flaw-aware task-gen**: bias task
  constraints to land on already-flawed fields of in-pool venues (pure reuse,
  zero new corruption). Fallback **author-on-demand**: when no existing flaw
  covers a needed field, request one from the flaw-gen sandbox. Touches
  `scripts/generation/generate_task.py` (constraint schema: `scope`/`condition`/
  `field`) + `compute_task_difficulty.py` (pool intersection).
- **Storage (reuse, don't reinvent)** — `wrong_info` already holds
  `incorrect_value`+`correct_value`+`source_type`+`category`; extend with
  `seed_used`, `detectability`, `profile_id`, `mask_id`. `doc_venue_roles` keeps
  reachability edges (now optional). New `corruption_runs` table for
  reproducibility metadata.
- **Validator** — `scripts/generation/validate_corruption.py` (mirrors
  `validate_city.py`): checks the §6 invariants + reports, per task, whether ≥1
  binding constraint is trapped (i.e., a live task wedge exists).

---

## 6. Invariants the operator must guarantee

1. **Recoverable (per venue)** — the careful sandbox recovers GT via ≥1 recovery
   path. (Empirical; supersedes the mandatory-truth-carrier rule / P7-G.)
2. **Has teeth (per venue)** — the naive sandbox trips. A flaw that doesn't trip
   naive is dead weight; reject.
3. **GT valid & solvable (per task)** — GT is read-only; for every task whose
   binding constraint hits a flawed in-pool venue, the GT-optimal plan still
   satisfies all hard constraints against GT (the careful agent can still build a
   valid plan, e.g. by excluding the over-budget venue). Separates *GT-feasible*
   (always true) from *agent-discoverable* (the difficulty).
4. **Plausible** — corrupted value is type-valid and in-domain (corrupt
   `hours_fri` to another real time, never `99:99`). Implausible = trivially
   detectable = no difficulty (BART detectability).
5. **Reproducible** — same `(gt_hash, master_seed, profile)` ⇒ byte-identical
   overlay. Per-`(venue, field)` sub-seed `= hash(master_seed, venue_id, field)`
   (order-independent; adding a venue doesn't perturb others). `corruption_runs`
   records `master_seed, profile_id, profile_version, corruptor_version, gt_hash`.

---

## 7. Workstream (b) — levers, by leverage

**(b1) is the unblocker and is now the masked-library + binding design above.**

- **(b1) Reusable flaw-mask library + task binding (hybrid).** Manufacture flaw
  wedges (§3), validate per-venue (§4), bind to tasks by constraint-targeting
  (§5). Puts corruption on the fields the GT scorer reads ⇒ **trap bites with no
  scorer change.** Fixes bugs #1 and #3.
- **(b2) Kill the free authority for trapped fields.** Make `get_official_site`
  absent (`has_official_site=0`) or itself stale (P7-J) for the trapped field, so
  resolution isn't free. `server/mock_tools.py:641`. Fixes bug #2. *Minimal pair
  with b1 to first prove the wedge.*
- **(b3) Copying / propagation done right** (truth discovery). The *majority* of
  sources share one copied wrong value; an independent/authoritative source is
  right ⇒ naive majority-vote yields the wrong answer. Operator-generated (today
  "hand-designed only", `handbook.py:1142`), with a discoverable copy-signal so
  the careful sandbox can still recover.
- **(b4) Omission** (Klinkhardt). Binding value absent from cheap sources,
  present only in one hard-to-reach place / inferable — removes cross-check.
  `listed_in_yelp`-style flag + `mock_tools.search_yelp` filtering.
- **(b5) F2c → 3-tier credit** (from `_pilot_analysis.md` l.102): 0 = truth-carrier
  not retrieved, 0.5 = retrieved but corrupt value still used, 1.0 = retrieved
  AND true value applied. Rewards *resolution*, not just retrieval.
  `eval/evaluator.py` F2c (~l.1193). Optional; b1's GT-scoring already bites.

Each trap's difficulty is set by the **detectability dial** ∈ [0,1] (BART):
number of corroborating truth sources, correction placement, truth-carrier
engagement/recency. The dial is a `profile` setting ⇒ same seed + harder profile
= a controlled-harder twin corpus.

---

## 8. Workstream (c) — controlled 3-arm ablation

Fixes the confound so we can *measure* the wedge. The clean arm must hold
doc-count/context constant — **heal, don't delete.**

- **Arm A — clean (equal-volume control):** replace each incorrect-source doc
  with a *neutral* doc of equal count/length, heal yelp fields to GT. Only
  variable vs faulty = the lie itself. New mode in `mock_tools` beside the
  existing `--clean-environment`.
- **Arm B — current faulty:** today's corpus/behavior (regression baseline).
- **Arm C — load-bearing faulty:** the (b)-regenerated corpus.

Run all three across the model panel on the same tasks. Primary metrics: P-score
and the 3-tier F2c (b5). Report **C−A** and **B−A**. **Success = C−A shows a
clear, significant difficulty increase on tasks whose binding constraints are
trapped, with the careful-agent path verified reachable.** Touches
`run_benchmark.py` (arm flag), a runner (cf. `scripts/_run_pilot_clean_vs_faulty.sh`),
`results/scores.db`.

---

## 9. Execution plan & checkpoints

`a (this doc) → (b ∥ c-infra) → regenerate corpus → run ablation → read deltas`

- (b) (generation files) and (c-infrastructure) (measurement files) parallelize —
  disjoint files. The measurement *run* is strictly gated on b + c + corpus regen.
- Start with **b1 + b2** (the minimal pair to prove the wedge exists) before
  building b3/b4/b5.
- **Cost checkpoints (require explicit go — API keys + money):** (i) regenerating
  a corpus with the operator; (ii) the careful/naive sandbox validation passes;
  (iii) the 3-arm ablation. Start small: one city, the 6 task types, one strong +
  one weak model before any full sweep.

## 10. References
Levers catalogued in `docs/PHASE7_FUTURE_WORK.md` P7-I/J/K. Anchors: Klinkhardt
2023 (omission), Arocena 2015 / BART (detectability, constraint-aware injection),
Li 2016 truth discovery (copying / latent reliability), Li 2020 Ditto (entity
resolution), Rahm & Do 2000 (taxonomy). PDFs in
`~/Documents/Books and Papers/Papers/Internet-Noise-Benchmark/`.
