# Valid-Difficulty Redesign — making corruption actually add difficulty

**Branch:** `valid-difficulty-redesign`
**Status:** design contract (v2). Workstreams: (a) this doc → (b) reusable flaw
library + task binding → (c) controlled 3-arm ablation → (d) certifier plugin +
its validation experiment.
**Grounding:** the literature review (formerly P7-I/J/K in
`docs/PHASE7_FUTURE_WORK.md`, now consolidated into this doc),
PDFs in `~/Documents/Books and Papers/Papers/Internet-Noise-Benchmark/`, the pilot
result in `scripts/_pilot_analysis.md`, and a 2026-06 theory pass over BART
(repairability), truth discovery (copying / latent reliability), Ditto (entity
resolution), and the 2024–26 RAG knowledge-conflict literature.

> **v2 deltas vs v1** (so you can see what changed): the per-flaw LLM sandbox is
> **demoted** — validity + difficulty are estimated by a cheap **certifier plugin**
> (§4), and the LLM sandbox is kept only for *calibration* and task-wedge
> measurement, not per-flaw. Difficulty is a **2-vector** (detectability,
> repairability), not a single dial. Flaw taxonomy expanded with theory-grounded
> structures incl. auto-generable propagation, multi-truth omission, and
> entity-resolution traps (§3). Added a corpus-level **source model** (§5),
> **disjoint-scope / inferability / parametric-knowledge** invariants (§6), and a
> free **certifier validation experiment** on existing data (§9).

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

**Why minor faults can't help (the 2024–26 RAG literature):** strong models are
increasingly robust to unstructured noise ("diminishing returns of complex robust
RAG training in the era of powerful LLMs"). Difficulty must come from *structured*
conflict — copying, omission, load-bearing placement — not sprinkled noise.

**Realization that drives the whole redesign:** the scorer is already correct
(GT-based). We do not change scoring. We (i) put corruption on the fields the
scorer reads, (ii) remove the free shortcuts to truth, and (iii) make the clean
arm a true control.

---

## 2. Core concept — two wedges, two axes

Difficulty = the gap between a **naive** agent (trusts first source / majority /
official site = uniform-weight vote) and a **careful** agent (infers latent source
reliability, weighs recency/authority, detects copied sources = weighted vote).
This is exactly the truth-discovery "wisdom of minority"; BART gives the same gap
as detection-vs-repair. The gap is *two quantities at two granularities*:

**Wedge 1 — the flaw wedge (per venue + field).** Unit = "did you get the field
value right?". A property of a flaw + that venue's served docs. **No task
involved.** This is what we manufacture and validate.

**Wedge 2 — the task wedge (per task).** The flaw wedge propagates into task score
only through a **binding constraint**: `task_score(careful) − task_score(naive)`.

The flaw wedge is the **reusable cause**; the task wedge is the **effect**,
switched on when a task's binding constraint reads the flawed field.

Each flaw carries **two difficulty axes** (BART), not one dial:
- **Detectability** = how many independent claims *contradict* the lie. `0` →
  undetectable → invalid (careful has no signal). Low-but-nonzero = naive won't
  notice.
- **Repairability** ∈ (0,1) = the true value's share of the candidate-value set =
  can careful land back on GT, and how easily. `0` → detectable but unrecoverable
  → invalid. `1` → trivial. **≈0.5 with a single competing plausible value = the
  hardest fair regime** (naive coin-flips; careful must use a tiebreaker).

The wedge is widest when **detectability is low-but-nonzero AND repairability is
moderate.** Validity = `detectability ≥ 1 AND 0 < repairability < 1`.

> **Worked example — one flaw, two tasks.**
> Flaw: Veselka GT `avg_cost=$30` (over budget), Yelp shows `$12` (false-positive).
> - *Flaw wedge:* careful reads docs → $30 ✓; naive trusts top Yelp → $12 ✗.
> - *Task A "dinner ≤ $24/head" (budget binds avg_cost):* naive includes Veselka,
>   thinks total $20, submits → scored vs GT → real $38 → over → P=0.4. Careful
>   recovers $30, swaps it out → P=1.0. **Task wedge = 0.6.**
> - *Task B "find Italian restaurants" (cuisine binds, not cost):* cost flaw
>   touches nothing the rubric reads → same picks → **task wedge = 0** (dormant).

Consequence: a flaw lives on a venue (intrinsic, reusable across tasks and
cities); difficulty is per-task; the same library serves everything.

---

## 3. Flaws are reusable, venue-intrinsic assets (the mask library)

**Why F felt easy and P felt impossible — and why they're the same.** F-faults
(hours, booking, visit_minutes) felt "universal" because they're *intrinsic venue
properties*. But most P-constraints test **tags and categorical/numeric features**
— `cuisine`, `wheelchair_accessible`, `local_cuisine`, `price_tier`, `waterfront`,
`district`, `avg_cost`. Those are *also* intrinsic. So a P-flaw is exactly as
venue-intrinsic and reusable as an F-flaw.

**Flaw mask** = `(predicate over venue features/tags, target field, direction,
flaw-structure, target detectability+repairability)`. A **flaw** (concrete
instance) = `{venue, field, incorrect_value, correct_value=GT, direction,
structure, evidence_layout, recovery_paths}`. The library is a handful of masks; a
seeded operator instantiates them into reusable per-venue flaws.

**Direction — bias to false-positive.** Corrupt so a constraint-*violating* venue
*looks satisfying* (cost down, hours wider, accessibility claimed) → the naive
agent includes an infeasible venue → direct, attributable violation vs GT. The
opposite direction only makes the agent skip a fine option → soft penalty.

**Flaw structures to build (theory-grounded; structure, not just value):**

| Structure | Mechanism / source layout | Field examples | Truth type |
|---|---|---|---|
| **Minority-truth conflict** (BART FD-majority) | wrong value in *j* of *K* sources; truth present but not majority; repairability ≈ (K−j)/#distinct | `avg_cost`, `cuisine`, `price_tier` | single |
| **Copying bloc** (truth discovery — *now auto-generable*) | majority share one copied wrong value **+ a shared fingerprint on another observable venue**; honest minority right. Topology dial: direct / co-copy / transitive (deeper = harder to detect) | `hours`, `cuisine`, `booking` | single |
| **Omission / recall trap** (multi-truth) | majority *omit* a true list element rather than assert a false one; recall-blind merging drops it | amenities, `wheelchair`, per-day hours, payment types | **multi** |
| **Entity-resolution trap** (Ditto) | over-merge two venues (manufactures conflict, mimics a copying bloc); under-merge (starves an object of sources → long-tail hard) | name variants, address | — |
| **Stale + authority** (temporal_decay) | one stale source; recovery via an authoritative recent source → high repairability = the *easy floor* / calibration anchor | `hours`, `booking` | single |
| **No-truth / ambiguous control** | venue mid-rename / disputed; correct careful answer = "unknown" — calibration, prevents over-trust | name, hours | — |

This subsumes the existing `handbook.py` 5-category taxonomy (temporal_decay /
propagation / conditional / subjective / adversarial) and **unlocks
propagation_error for auto-generation** — truth-discovery theory specifies exactly
how (copying topology + shared fingerprint + reliability margin), previously
"hand-designed only" (`handbook.py:1142`).

---

## 4. Validity & difficulty — the certifier plugin (cheap) + sandbox (calibration)

We do **not** run an LLM agent per flaw. Validity and difficulty are estimated by
a cheap, swappable **certifier plugin** computed from the source structure; the
LLM is reserved for *calibration* and task-wedge measurement.

**Certifier plugin — a pure function, no coupling:**
```
certify(flaw, evidence_claims, source_model) ->
  { valid: bool,            # detectability >= 1 AND 0 < repairability < 1
    detectability: int,     # # independent claims contradicting the lie
    repairability: float,   # true value's share of the candidate set (0..1)
    difficulty_bin }        # coarse easy/med/hard on the structured layer
```
Mechanics: (1) project evidence into claim tuples `(source, venue, field, value,
timestamp)` — exact for structured fields, extracted for prose; (2) score
**detectability** (count contradicting independent claims) and **repairability**
(run a reference copy-aware truth-discovery solver, e.g. AccuCopy, over the
claims; does it recover GT, and by what margin over a majority-vote baseline?);
(3) **admit iff** `majority-vote → wrong` (teeth) **AND** `TD-solver → GT`
(recoverable). Both numbers are computable in milliseconds.

**What the certifier is — and is NOT (honest scope).** It is a *high-confidence
validity gate* (it reliably rejects undetectable/unrecoverable flaws — structural,
model-independent) and a *coarse rank-orderer*. It is **not** a calibrated
predictor of LLM P-score, for four reasons:
1. Half our evidence is **prose**; the number scores an idealized structured
   projection, not the reading-comprehension the agent actually faces.
2. The careful agent is an **LLM, not BART's uniform-repair model** — it exploits
   world knowledge/authority/recency the number can't see ⇒ the number is a
   *conservative ordering proxy*, not an absolute success rate.
3. **"Naive" varies by model** (trust-official-site vs trust-first vs recency);
   the certifier assumes one naive model.
4. **Flaw-wedge ≠ task-wedge** (binding, substitutability, plan structure).

So the certifier **pre-filters and ranks**; the LLM **sandbox** (careful + naive
passes over a venue's docs) is kept to **calibrate the certifier's thresholds** on
a sample and to measure the real task-wedge. This is BART's own philosophy:
repairability is computed cheaply but validated empirically against the repair
algorithm under test — here, the LLM.

**Truth-carrier becomes optional.** It was a structural recoverability guarantee;
the certifier replaces it with a tested one. A flaw lists `recovery_paths`
(corroboration / authority / a truth-carrier / plausibility); the certifier just
needs one. **Fairness keystone — inferability:** a source's unreliability must be
evidenced on *other visible* venues (an anchor track record), not only on the
hidden answer — else careful has no edge. (See the source model, §5.)

---

## 5. Architecture & components

Post-hoc, seeded operator over the **clean canonical corpus** + the **tasks'
binding constraints**. GT immutable; corruption is an additive overlay; tools
serve the overlay; the evaluator scores against GT only.

```
                       flaw-mask library          source model
                              │ seeded apply        (w_s + copying graph,
                              ▼                       anchored on clean venues)
  venues (GT, immutable) ─▶ concrete per-venue flaws ──┐         │
                              ▲          │ certify()    │ render  │
        author-on-demand      │      ┌───▼──────────┐   ▼         │
        request ┌─────────────┘      │ certifier    │ served views (yelp,docs)
                │                     │ plugin (§4)  │   │
        task-gen agent                └──────────────┘   │ agent plans
        - selects existing flaws (flaw-aware)            ▼
        - emits "need flaw on field F          eval/evaluator.py
          of venue V" ──▶ flaw-gen sandbox     (scores vs GT `venues` only)
                          - authors flaw + evidence layout
                          - certifier admits/rejects (cheap)
                          - LLM sandbox only for calibration sample
                          - admitted → library (reusable)
```

Components (all new files unless noted):
- **Flaw-mask library** (`scripts/generation/flaw_masks.py`) + seeded operator
  (`scripts/generation/inject_flaws.py`).
- **Certifier plugin** (`scripts/generation/flaw_certifier.py`) — the §4 pure
  function; swappable (BART-static / TD-solver / learned / LLM-as-certifier).
- **Source model** (`scripts/generation/source_model.py`) — per-source reliability
  `w_s` + copying graph, with reliability *inferable* from anchor venues (clean,
  uncontested fields). This is **corpus-level**: flaw *values* are venue-intrinsic,
  but recoverability depends on each source's cross-venue track record.
- **Flaw-gen sandbox** (`scripts/generation/flaw_sandbox.py`) — isolated; authors
  one flaw, certifies it, returns it. **Task-gen never authors flaws inline.**
- **Task-gen binding (hybrid)** — default **flaw-aware** (bias constraints onto
  already-flawed in-pool fields); fallback **author-on-demand**. Touches
  `generate_task.py` (constraint `scope`/`condition`/`field`) + `compute_task_difficulty.py`.
- **Storage** — extend `wrong_info` with `seed_used`, `detectability`,
  `repairability`, `structure`, `profile_id`, `mask_id`; `doc_venue_roles`
  reachability edges now optional; new `corruption_runs` table.
- **Validator** (`scripts/generation/validate_corruption.py`) — checks §6
  invariants + reports per-task whether a live task wedge exists.

---

## 6. Invariants the operator must guarantee

1. **Recoverable (per venue)** — certifier `repairability > 0` (≥1 recovery path).
   Supersedes the mandatory-truth-carrier rule / P7-G.
2. **Has teeth (per venue)** — certifier `detectability ≥ 1` and majority-vote
   trips. A flaw that doesn't trip naive is dead weight; reject.
3. **GT valid & solvable (per task)** — GT read-only; for every task whose binding
   constraint hits a flawed in-pool venue, the GT-optimal plan still satisfies all
   hard constraints vs GT. Separates *GT-feasible* (always true) from
   *agent-discoverable* (the difficulty).
4. **Plausible** — corrupted value type-valid and in-domain (whitelist−blacklist,
   à la BART): in the field's real domain and consistent with all *but* the
   targeted relationship. Never `99:99`.
5. **Disjoint evidence scopes** (BART NP-completeness lesson) — keep each flaw's
   supporting/contradicting sources non-overlapping with other flaws', so per-flaw
   difficulty stays independently computable. Keep error rate low.
6. **Inferable (fairness)** — every source's reliability is estimable from visible
   anchor venues, not only from the hidden answer.
7. **Parametric-knowledge safe** — guard against the model overriding synthetic GT
   from training memory (real venue names are a hole). Use fictional venues or
   frame tasks as "according to these sources." (2024–26 RAG conflict literature.)
8. **Reproducible** — same `(gt_hash, master_seed, profile)` ⇒ byte-identical
   overlay; per-`(venue,field)` sub-seed `= hash(master_seed, venue_id, field)`;
   `corruption_runs` records `master_seed, profile_id, profile_version,
   corruptor_version, gt_hash`.

---

## 7. Workstream (b) — levers, by leverage

**(b1) is the unblocker.**

- **(b1) Reusable flaw-mask library + task binding (hybrid).** Manufacture flaw
  wedges (§3), certify (§4), bind to tasks by constraint-targeting (§5). Puts
  corruption on the fields the GT scorer reads ⇒ **trap bites with no scorer
  change.** Fixes bugs #1 and #3.
- **(b2) Kill the free authority for trapped fields.** `get_official_site` absent
  or itself stale (P7-J) for the trapped field. `server/mock_tools.py:641`. Fixes
  bug #2. *Minimal pair with b1 to first prove the wedge.*
- **(b3) Copying / propagation done right** (truth discovery). Majority share a
  copied wrong value + discoverable fingerprint; honest minority right ⇒ naive
  majority-vote fails. Now operator-generated (was `handbook.py:1142` hand-only).
- **(b4) Omission / multi-truth recall trap** (Klinkhardt + truth discovery).
  Binding value/element absent from cheap sources. `listed_in_yelp`-style flag +
  `mock_tools.search_yelp` filtering.
- **(b5) F2c → 3-tier credit** (from `_pilot_analysis.md` l.102): 0 not retrieved,
  0.5 retrieved-but-corrupt-value-used, 1.0 retrieved-and-true-value-applied.
  `eval/evaluator.py` (~l.1193). Optional; b1's GT-scoring already bites.

Difficulty per flaw is the **2-vector (detectability, repairability)** from the
certifier, set by a `profile` ⇒ same seed + harder profile = a controlled-harder
twin corpus.

---

## 8. Workstream (c) — controlled 3-arm ablation

Fixes the confound so we can *measure* the wedge. The clean arm must hold
doc-count/context constant — **heal, don't delete.**

- **Arm A — clean (equal-volume control):** replace each incorrect-source doc with
  a *neutral* doc of equal count/length; heal yelp fields to GT. Only variable vs
  faulty = the lie. New mode in `mock_tools` beside `--clean-environment`.
- **Arm B — current faulty:** today's corpus (regression baseline).
- **Arm C — load-bearing faulty:** the (b)-regenerated corpus.

Run all three across the model panel on the same tasks. Primary metrics: P-score
and the 3-tier F2c. Report **C−A** and **B−A**. **Success = C−A shows a clear,
significant difficulty increase on trapped-constraint tasks, careful-path
reachable.** Touches `run_benchmark.py`, a runner (cf.
`scripts/_run_pilot_clean_vs_faulty.sh`), `results/scores.db`.

---

## 9. Workstream (d) — certifier validation experiment (FREE, on existing data)

Before betting on the certifier, **test whether its number agrees with the wedge
pipeline's empirical difficulty** — using only stored data, zero API.

- **X (predictor):** certifier `(detectability, repairability)` for each of the 29
  NYC flaws.
- **Y (ground truth):** the *observed* per-flaw wedge mined from stored transcripts
  + `scores.db` — per flaw, the fraction of model-runs that **tripped** (scheduled
  the venue when GT says closed = F2a; or used it without the truth-carrier = F2c)
  vs recovered. (F2c counts already partly extracted in `_pilot_analysis.md`.)
- **Test:** Spearman rank-corr of `repairability` vs `1 − trip_rate`; AUC for the
  valid/has-teeth classification.
- **Decision rule:** strong correlation → adopt certifier as cheap pre-filter +
  difficulty estimator (recalibrated periodically). Weak/null → demote to
  validity-gate-only, or build a variation.
- **Power caveat (asymmetric test):** existing flaws were engineered "clean" (one
  truth-carrier; official site authoritative), so `repairability` has **limited
  spread** — a null result is *inconclusive*, not a refutation. Natural variance
  does exist (via `has_official_site`, `traffic_tier`, doc count), so the test can
  **confirm** more strongly than it can **refute**. A definitive test needs the
  new flaw distribution (§7) with deliberate repairability spread.
- **Variations if it underperforms:** (a) validity-gate only, drop the difficulty
  claim; (b) a small **learned** predictor (features → observed trip-rate); (c)
  **LLM-as-certifier** (one cheap call per flaw — richer than the static number,
  far cheaper than two full agent runs); (d) richer constraint/source encoding.

The certifier stays a **plugin** throughout — nothing in the pipeline depends on
it, so it can be adopted, swapped, or dropped without touching generation or
scoring.

---

## 10. Execution plan & checkpoints

`a (this doc) → d (certifier retro test, FREE) → (b ∥ c-infra) → regenerate corpus → run ablation → read deltas`

- **(d) first** — it's free, on stored data, and tells us how much to trust the
  certifier before building on it. First confirm Y is cleanly extractable from the
  transcript/scores schema, then build a lightweight certifier v0 (on old data it
  reduces to "evidence distribution + authority weighting + repairability
  fraction" — no copy-detection/TD machinery yet) + the analysis script.
- (b) (generation files) and (c-infra) (measurement files) parallelize — disjoint
  files. The measurement *run* is gated on b + c + corpus regen.
- Start with **b1 + b2** (minimal pair to prove the wedge) before b3/b4/b5.
- **Cost checkpoints (require explicit go — API keys + money):** (i) corpus regen;
  (ii) LLM calibration / sandbox passes; (iii) the 3-arm ablation. Start small:
  one city, the 6 task types, one strong + one weak model.

---

## 11. References
Levers consolidated here from P7-I/J/K (now a tombstone in
`docs/PHASE7_FUTURE_WORK.md`); current flaw taxonomy in `handbook.py:1032–1346`
and `docs/DESIGN_DECISIONS.md`. Theory anchors:
- **Arocena 2015 / BART** — *detectability* (violation count) vs *repairability*
  (true-value share of candidate set), both computable statically; whitelist−
  blacklist for plausibility; NP-completeness ⇒ disjoint scopes.
- **Li 2016 truth discovery** — naive vote vs weighted vote ("wisdom of minority");
  copying detected via shared *mistakes* on observable objects; inferability;
  single- vs multi-truth (recall traps); reference solver (AccuCopy) as certifier.
- **Li 2020 Ditto** — entity-matching hardness; wrong merge → downstream conflict.
- **Klinkhardt 2023** — omission / visibility-conditioned completeness.
- **Rahm & Do 2000** — single- vs multi-source dirty-data taxonomy.
- **2024–26 RAG knowledge-conflict** — Astute RAG (arXiv 2410.07176), RAMDocs /
  MADAM-RAG (arXiv 2504.13079), "diminishing returns of robust RAG" (arXiv
  2502.11400): conflict types (context-memory / inter-context / intra-memory) and
  the parametric-knowledge guard.

PDFs in `~/Documents/Books and Papers/Papers/Internet-Noise-Benchmark/`.
