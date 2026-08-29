# TravelBench — Technical Closeout Report

**Date:** 2026-08-29 · **Repo:** `travelbench_Phase5.10` · **Author:** V. Jin
**Companion:** `docs/preprint/travelbench_corruption_wedge.md` (research write-up)

This report is the engineering-facing closeout: what the system is, its final
state, what each Phase-6+ thread delivered, how to reproduce the results, and the
decisions taken at close. The scientific narrative lives in the preprint; this
document is for a future maintainer picking the repo up cold.

---

## 1. What TravelBench is

A ground-truth-graded benchmark for LLM travel-planning agents under **noisy
multi-source retrieval**. An agent receives a natural-language trip request plus
four frozen mock tools (`search_yelp`, `search_blogs_and_forums`, `fetch_url`,
`estimate_travel`) over a per-city corpus, and emits a day-by-day itinerary.
Plans are graded deterministically against an immutable ground-truth (GT) venue
database on four independent tiers:

| Tier | Meaning | Reads |
|---|---|---|
| **C** | correctness (BFCL-style logical/planning deduction) | GT |
| **F** | feasibility (hours, travel, budget, booking) | GT |
| **P** | personal-constraint satisfaction (persona binding constraints) | GT |
| **B** | bonus | GT |

The composite formula was retired in Phase 5.10; tiers are reported separately.

**Entry points.**
- `test_generate_tasks.py` — task generation (API).
- `run_benchmark.py` — evaluation harness (API); `--arm {faulty,clean_equalvol}`,
  `--clean-environment`.
- `scripts/generation/generate_city_venues.py` — full city corpus (API).
- `eval/evaluator.py` — the GT scorer.
- `server/mock_tools.py` — what the agent sees (served overlay).
- Per-city data: `data/cities/{city}/travelbench.db`; runs under
  `data/cities/{city}/runs/{run}/`.

**Canonical docs** (read first): `docs/CLAUDE_GUIDE.md` (master index) →
`docs/TODO_PHASE6.md`, `docs/DESIGN_DECISIONS.md`, `docs/DATA_FORMATS.md`,
`docs/VALID_DIFFICULTY_REDESIGN.md` (the corruption redesign contract), and this
report.

---

## 2. Final state at close

**Phase 6 (data quality / source-doc coverage): complete.** The `Done this phase`
log in `docs/TODO_PHASE6.md` runs through P6-T23. All remaining markers in that
doc are explicitly *deferred to Phase 7*, not unfinished Phase-6 work. The core
benchmark — task generation, mock-tool environment, GT scorer, 12-model harness,
NYC + London reference corpora — is functional and closable.

**June 2026 research thread (`valid-difficulty-redesign` branch): landed at a
demonstrated-mechanism milestone.** The thread set out to fix a benchmark that
did not get harder under corruption. It ends with the **first ground-truth-
attributable personalization wedge in the project's history (ΔP = −0.146)**,
honest about being mechanism-demonstrated but not yet statistically powered. Full
arc and numbers: the preprint §7.

**Data assets:**
- Active city: **New York** (`test_70` clean reference; `test_lb`, `test_layer`
  corruption corpora, deterministic/regenerable from seed).
- Secondary: **London** (`test_70`), stale since 2026-06-06.
- `results/scores.db`: 211 rows current `scores` schema (2026-05-25 → 06-23, 13
  task_ids, 12 models) + a superseded 250-row `scores_pre_p22` snapshot (April,
  kept for history).
- ~467 gzipped transcripts across 14 model folders in `results/transcripts/`.
- Seven per-experiment reports (see §4).

---

## 3. What each thread delivered

### 3.1 Phase 6 (data quality) — complete
Per-window weather, atomic `wrong_info` workflow (P6-T22, resolves the
orphan-wrong_info failure mode), source-doc coverage, tone/persona vocab, the P22
evaluator/scoring redesign. Details: `docs/TODO_PHASE6.md`.

### 3.2 Corruption redesign (`valid-difficulty-redesign`, 26 commits)
The intellectual centerpiece. Diagnosed three code-level reasons corruption did
not add difficulty (corruption misses the rubric; truth is free; resolution off
the scoring path — preprint §3), then rebuilt the operator across three
generations:

1. **Certifier + retro validation** (`flaw_certifier.py`,
   `scripts/analysis/certifier_retro.py`) — null result; certifier demoted to a
   validity gate. Bonus finding: trip-rate is governed by *load-bearing*-ness, not
   detectability.
2. **Load-bearing per-field operator** (`flaw_masks.py`, `inject_flaws.py`) +
   equal-volume clean arm (`mock_tools.py`) + authority suppression. Moved ΔF
   (−0.17) but not ΔP — hours bind feasibility, not personalization.
3. **Recovery probe** (`results/recovery_probe/`) — isolated copying-blocs as the
   difficulty lever (recovery 75–100% → 0%; a cross-venue "tell" restores it to
   88%); showed strong models *abstain* rather than adopt a lie.
4. **Layer operator** (LLM-authored source×attribute layers) — the validated
   direction; produced ΔP = −0.146 on a volume-controlled ablation.
5. **Drift-independent recovered-vs-misled metric** + model-discrimination probe —
   no weak/strong gradient at current n, but surfaced the targeting bug (free-
   admission lie inert on restaurants, potent on attractions).

### 3.3 Deferred (Phase 7, design-only)
- `docs/PHASE7_FUTURE_WORK.md` — temporal-arc reasoning (P7-A), semantic
  source-doc quality, wrong-info problems 2/3.
- `docs/SELF_EVOLVE_DESIGN.md` — cross-session experience agent built on the
  corruption wedge (v1 design contract, **no code**).

---

## 4. Reproducibility map

| Result | Report | Produced by |
|---|---|---|
| Certifier null | `results/certifier_retro_report.md` | `certifier_retro.py` (free) |
| Hours wedge (ΔF −0.17) | `results/ablation_wedge/wedge_report.md` | `run_benchmark.py --run-name test_lb --arm ...` |
| Copying-bloc lever | `results/recovery_probe/summary.md` | `recovery_probe.py` (free-ish) |
| Layer P-wedge (ΔP −0.146) | `results/ablation_layer/layer_wedge_report.md` | `run_benchmark.py --run-name test_layer --arm ...` |
| Model discrimination / targeting | `results/misled_metric/model_comparison.md` | misled/recovered analyzer |

**Determinism:** corruption corpora regenerate byte-identically from
`(gt_hash, seed, profile)`. Validate fairness *before any API spend* with
`scripts/generation/validate_corruption.py --db <corpus> --src <baseline>`.

**Cost discipline (standing project rule):** run `validate_corruption` (free)
first; use `--retries 0`; never parallel-sweep a single provider key; multiply
cost estimates by 5×. A "large sweep" is $100s, not $tens.

---

## 5. Decisions taken at close

1. **No large data generation.** A big generation/ablation sweep was considered
   and declined: the model-discrimination probe proved the current flaw set is
   half-mistargeted (inert on restaurants), so a large sweep now would amplify a
   known-diluted signal at real API cost. The finding's status
   (*mechanism demonstrated, power pending*) does not change with more underpowered
   n. The honest write-up is the stronger deliverable. Path to significance is
   scoped in the preprint §9 (fix targeting for free, then one tight powered run).
2. **Certifier is a validity gate, not a difficulty predictor** (retro null).
3. **Clean arm heals, not deletes** — the equal-volume control removes the
   doc-count confound that corrupted the original clean-vs-faulty ablation.
4. **Metric is recovered-vs-misled on scheduled venues**, drift-independent —
   aggregate ΔP alone is too crude.

---

## 6. Known cruft / housekeeping at close

- **Empty stub city dirs** under `data/cities/` (`brooklyn`, `manhattan`, `ny`,
  `nyc`, `newyork`, `new-york`, `new york`, `new york city`, `new_york_city`) —
  leftovers from a city-name-normalization test; all empty, none git-tracked.
- **Malformed artifact dirs** where tool-argument strings leaked into paths
  (`data/cities/new_york<arg_key>...`) — a path-construction bug's output; junk.
- **`scores_pre_p22`** table — superseded April snapshot, retained for history.
- **`paris`** — a 4 KB stub DB, no runs.

These are inert (untracked / non-authoritative) and safe to remove or leave; they
do not affect any result. Removal deferred to explicit sign-off.

---

## 7. If you are picking this up cold

1. Read `docs/CLAUDE_GUIDE.md`, then this report, then the preprint.
2. The active corpus is `data/cities/New_York/runs/test_layer` (deterministic).
3. To *reproduce* a result: use the reproducibility map (§4); everything free
   (validators, probes, retro) runs with no API key.
4. To *advance* the science: preprint §9, step 1 (fix targeting) is free and is
   the single highest-leverage next action.
5. To *extend* to a new city: the runbook in `docs/CLAUDE_SESSION.md` (clean
   corpus → tasks → seeded flaws → validate → ablation).
