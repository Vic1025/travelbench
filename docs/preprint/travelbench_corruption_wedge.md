# When Does Noise Make a Planning Benchmark Harder? Diagnosing and Repairing Non-Load-Bearing Corruption in an LLM Travel-Planning Benchmark

*Preprint — TravelBench technical thread, 2026-06. Author: V. Jin.*

---

## Abstract

Benchmarks for LLM agents increasingly claim to test *robustness to noisy
information*, but rarely verify that the injected noise actually changes the
task's difficulty. We report a negative-to-positive result from **TravelBench**,
a ground-truth-graded travel-planning benchmark in which an agent plans a
day-by-day itinerary from four mock retrieval tools over a corrupted corpus. A
clean-vs-faulty ablation found that corrupting the corpus **did not make tasks
harder — and on some task types made them easier.** We trace this to three
concrete, code-level design faults rather than to model robustness: (i)
corruption landed on fields the scorer does not read; (ii) ground truth was
freely recoverable from an always-authoritative "official site"; and (iii)
conflict resolution sat off the scoring critical path. We formalize the missing
property as a **load-bearing corruption wedge** — a corruption is only difficult
if a *binding task constraint* reads the corrupted field, and if truth is not
freely recoverable. Guided by truth-discovery and data-repair theory (BART's
detectability/repairability axes; copying-bloc detection; omission traps), we
rebuild the corruption operator as **systematic source×attribute layers** that
corrupt the categorical/numeric attributes tasks actually bind on. On a
controlled equal-volume ablation (the clean arm *heals* the lie rather than
deleting documents, holding context volume constant), the layered corpus
produces the **first movement in personalization score in the project's history
(ΔP = −0.146)**, and the effect is *mechanism-attributable*: agents schedule
believed-free venues that bust the budget under ground-truth scoring. A
model-discrimination probe finds no weak-vs-strong gradient at current scale but
reveals *why* — a "free admission" lie is inert on restaurants (agents price food
from world knowledge) and only bites on paid-entry attractions, so half of the
flaws could not fire. We contribute: a diagnosis of a common but under-examined
benchmark failure mode, a difficulty criterion (load-bearing × non-recoverable),
a reusable layered corruption operator with fairness invariants, a
drift-independent *recovered-vs-misled* metric, and an honest account of what is
demonstrated (mechanism) versus what remains (statistical power).

---

## 1. Introduction

Most planning benchmarks evaluate agents on *clean* task specifications. Real
information-seeking planning — booking a trip, scheduling around opening hours
and budgets — happens against **stale listings, blog posts with outdated prices,
and forum threads where corrections are buried.** A benchmark that wants to
measure an agent's ability to synthesize under this mess must inject noise that
*actually changes the answer*. It is easy to inject noise; it is surprisingly
hard to inject noise that is (a) difficult, (b) fair (recoverable by a careful
agent), and (c) *attributable* (you can point to the corruption as the cause of a
score drop).

TravelBench is a ground-truth-graded benchmark: an agent receives a
natural-language trip request plus four mock tools (`search_yelp`,
`search_blogs_and_forums`, `fetch_url`, `estimate_travel`) and emits a day-by-day
itinerary, scored on four tiers against an immutable ground-truth (GT) venue
database — **C** (correctness), **F** (feasibility), **P** (personal-constraint
satisfaction), **B** (bonus). Corruption is an additive overlay: the served
documents and tool outputs may lie, but the scorer always grades against GT.

This paper is the write-up of a focused investigation with an unusual shape: **a
robustness manipulation that did nothing, followed by the diagnosis and repair of
why.** We believe the negative result and its resolution are more broadly useful
than a single positive number, because the failure mode — *corruption that misses
the rubric* — is latent in any benchmark that bolts a noise generator onto a
grader without checking that the two are coupled.

**Contributions.**

1. **A diagnosis** (§3) of three code-confirmed reasons corruption failed to add
   difficulty, generalizable beyond this benchmark.
2. **A difficulty criterion** (§4): the *load-bearing corruption wedge* — the gap
   between a naive and a careful agent, non-zero only when a binding constraint
   reads a corrupted, non-freely-recoverable field. We decompose difficulty into
   BART's two axes (detectability, repairability) rather than a single dial.
3. **A layered corruption operator** (§5) that replaces sparse per-field noise
   with systematic source×attribute bias at literature-grounded densities, with
   copying-blocs and cross-venue "tells" for fair recoverability.
4. **A drift-independent metric** (§6): *recovered-vs-misled* on flawed venues the
   agent actually scheduled, which does not confound with venue-selection drift.
5. **Empirical results** (§7): a certifier-validation null (honest,
   power-limited); a recovery probe isolating copying-blocs as the difficulty
   lever; the first P-score wedge (ΔP = −0.146) under a volume-controlled
   ablation; and a targeting diagnosis from a model-discrimination probe.
6. **An honest limitations section** (§8) separating *demonstrated mechanism* from
   *statistical significance*, and a cheap, scoped path to the latter.

---

## 2. Setup

**Corpus.** A per-city SQLite database of ~70 venues (New York reference corpus,
`test_70`) with structured attributes (hours, `avg_cost`, `price_tier`, cuisine,
`wheelchair_accessible`, district, category, `traffic_tier`) and a set of served
documents (Yelp-style listings, blog/forum prose, official-site pages). GT lives
in the `venues` table and is immutable; corruption is an overlay on the served
views only.

**Tools.** Four frozen mock tools; no live internet. The agent must triangulate
across `search_yelp` (structured), `search_blogs_and_forums` (prose),
`fetch_url`/official-site, and `estimate_travel`.

**Scoring.** Deterministic, GT-based. **C** deducts for logical/planning errors
(BFCL-style); **F** deducts for feasibility violations (scheduling a venue when GT
says closed, over-budget, etc.); **P** measures satisfaction of the persona's
binding personal constraints (budget, cuisine, accessibility, price tier, …);
**B** is bonus. The composite formula was retired; tiers are reported
separately. Critically, **the scorer is already correct** — it reads GT. The
redesign never changes scoring; it changes *where corruption lands relative to
what the scorer reads.*

**Models.** A panel of 12 contemporary models is present in the score store
(Claude Sonnet/Haiku/Opus 4.x, GPT-5.x, Gemini 3.x, DeepSeek chat/reasoner, GLM,
Doubao, Kimi). The controlled ablations in this paper use `claude-sonnet-4-5` as
the strong reference and `claude-haiku-4-5` for the discrimination probe;
several open models were too tool-brittle to reliably emit the plan schema the
metric requires (a measurement constraint discussed in §8).

---

## 3. The failure: corruption that does not add difficulty

A clean-vs-faulty pilot (toggled by `run_benchmark.py --clean-environment`) found
the faulty environment did **not** raise difficulty, and on some task types
*lowered* it. Three faults, all confirmed in code:

**Fault 1 — corruption misses the rubric.** 83% of `wrong_info` instances (24/29
in NYC `test_70`) targeted F-score fields (`hours_*`, `visit_minutes`,
`booking`). But task success is defined by **P-constraints**, which rarely depend
on those fields. Corruption was assigned at venue-generation time from
`traffic_tier`/category — *decoupled* from what any task actually tests. The one
type whose binding P-constraint read a corrupted field (Type 4, `avg_cost`) was
the only type that showed the predicted effect (P 0.75→0.25 with the trap).

**Fault 2 — truth is free.** `get_official_site()` was always authoritative and
never corrupted; every `wrong_info` shipped a mandatory truth-carrier;
mid/high-traffic venues carried 2–6 mostly-agreeing documents; high-traffic
venues were corruption-exempt. Redundancy out-voted the lone fault **at zero
cost** — a single cross-check resolved it.

**Fault 3 — resolution off the critical path.** C/F/P/B all grade against GT;
the only corruption-aware signal was a flat −0.05 (F2c) for scheduling a
wrong-info venue without fetching its truth-carrier. **Avoiding wrong-info
venues entirely cost nothing, and nothing rewarded picking truth over a lie.**

**A measurement confound compounded it.** "Clean" mode *deleted* incorrect-source
documents, changing document count and context length — so the A/B contrast mixed
"trap exposure" with "how much text the agent saw." This is why one task type got
*worse* in clean mode (−0.25): removing documents starved triangulation and
shifted venue selection. Any honest ablation must hold volume constant.

**Why minor faults can't help (2024–26 RAG knowledge-conflict literature).**
Strong models are increasingly robust to *unstructured* noise; robustness returns
diminish in the era of capable LLMs. Difficulty must come from **structured**
conflict — copying, omission, load-bearing placement — not sprinkled noise.

---

## 4. The load-bearing corruption wedge

We define difficulty as the **wedge**: the gap between a *naive* agent (trusts the
first source / the majority / the official site — a uniform-weight vote) and a
*careful* agent (infers latent source reliability, weighs recency and authority,
detects copied sources — a weighted vote). This is exactly truth discovery's
"wisdom of minority," and BART's detection-vs-repair gap. The wedge exists at two
granularities:

- **Flaw wedge** (per venue×field): did you recover the field's true value? A
  property of the flaw and the venue's served documents. No task involved. This
  is the reusable, manufactured, and *validated* quantity.
- **Task wedge** (per task): the flaw wedge propagates into the task score **only
  through a binding constraint** — `task_score(careful) − task_score(naive)`.

A corruption is **load-bearing** when a task's binding constraint reads the
corrupted field. Otherwise the flaw is *dormant*: it changes nothing the rubric
reads (Fault 1 restated as a design principle).

Each flaw carries **two difficulty axes** (BART), not one dial:

- **Detectability** = number of independent claims contradicting the lie. `0` →
  undetectable → invalid (careful has no signal). Low-but-nonzero = naive won't
  notice, careful can.
- **Repairability** ∈ (0,1) = the true value's share of the candidate-value set.
  `0` → detectable but unrecoverable → unfair. `1` → trivial. **≈0.5 with one
  competing plausible value is the hardest fair regime.**

The wedge is widest when **detectability is low-but-nonzero and repairability is
moderate.** Validity requires `detectability ≥ 1 ∧ 0 < repairability < 1`.

> **Worked example.** Flaw: Veselka GT `avg_cost=$30` (over budget), Yelp shows
> `$12`. *Flaw wedge:* careful reads docs → $30; naive trusts top Yelp → $12.
> *Task A, "dinner ≤ $24/head" (budget binds avg_cost):* naive includes Veselka,
> thinks total $20, submits; GT scoring reveals $38, over → P=0.4. Careful
> recovers $30, swaps it out → P=1.0. **Task wedge = 0.6.** *Task B, "find
> Italian restaurants" (cuisine binds, not cost):* the cost flaw touches nothing
> the rubric reads → **task wedge = 0** (dormant).

The consequence is architectural: a flaw is a **reusable, venue-intrinsic asset**
(a stale price is a property of the venue, reusable across tasks and cities);
difficulty is per-task, switched on by binding.

---

## 5. A layered corruption operator

Two design generations preceded the one that worked; we report all three because
the failures are informative.

**Generation A — load-bearing per-field flaws (b1).** Put corruption on
GT-scored fields; suppress free authority. Moved feasibility (ΔF = −0.17, §7.2)
but not personalization, because the operator corrupted *hours* (130 instances) —
which bind F, not P. NYC tasks bind their *selection* on tags / category /
price_tier; hours is never a selection condition. 93% of flaws sat on non-binding
fields.

**Generation B — diagnosis: isolation and sparsity (the real bug).** On
`test_70`: 29 flaws / 70 venues, **60% of venues clean**, 27/28 flawed venues had
exactly one flaw, and **no source was wrong on more than one venue** (no track
record). With the validity gate requiring `detectability ≥ 1`, every flaw shipped
a truth-carrier. Truth-discovery theory predicts the outcome exactly: independent
sources scatter on errors but agree on truth, so truth is the majority and
majority-vote recovers it trivially. *We had built the easy regime by
construction.* A recovery probe (§7.3) confirmed the lever: **copying-blocs**
(N sources sharing a *copied* wrong value, truth in the minority) collapse
recovery, and a cross-venue **"tell"** (a shared idiosyncratic mistake) restores
fair recoverability.

**Generation C — systematic source×attribute layers (validated).** A *layer* is
a source-correlated, attribute-systematic bias applied stochastically across the
pool — e.g. *"paid venues look free"* (free-confusion), *"accessibility info is
optimistically wrong"* (accessibility-optimism), *"prices are stale-cheaper"*
(price-deflation). Layers are:

- **Realistic:** real internet error *is* source-correlated and
  attribute-systematic (OSM edit patterns, truth-discovery copying), not i.i.d.
  field noise.
- **Load-bearing by design:** layers are chosen to cover the attributes tasks
  bind on — but *applied by a task-blind stochastic rule*, so it is systematic
  bias, not test-overfitting.
- **Fair:** within a flawed venue the wrong value is the majority of its ~3–4
  sources and truth is a discoverable minority; unreliable source identities recur
  across venues carrying the same idiosyncratic wrong claim (the cross-venue
  tell), so a cross-referencing agent can discount the bloc.
- **Literature-grounded densities:** per-attribute density ≈20–40%,
  visibility-scaled (Klinkhardt's omission gradient 27% visible → 78% hidden);
  wrong value as majority of a venue's sources with truth in 1–2 (Li 2016);
  recoverability tiers tuned to repairability ≈ 0.8 / 0.5 / 0.25 (BART
  easy/med/hard).

The operator is a **seeded, deterministic** post-hoc overlay on the clean
canonical corpus: same `(gt_hash, seed, profile)` ⇒ byte-identical overlay. GT is
immutable; a validator checks fairness invariants (recovery path exists; no bare
wrong-majority without a tell; values plausible/in-domain; density and live-wedge
report) *before any API spend.*

A **certifier plugin** (a pure function estimating detectability/repairability
from source structure) pre-filters and rank-orders flaws. Following the null
result in §7.1, it is used strictly as a **validity gate**, not as a calibrated
difficulty predictor. Nothing in generation or scoring depends on it — it can be
swapped or dropped freely.

---

## 6. A drift-independent difficulty metric

Aggregate ΔP confounds the wedge with **venue-selection drift**: on an identical
corpus, an agent may pick different venues run-to-run for reasons unrelated to the
lie, moving P without any trap firing. We therefore measure difficulty as
**recovered-vs-misled on flawed venues the agent actually scheduled**:

For each flawed venue in the emitted plan, classify the agent's *effective belief*
about the contested field against GT-vs-lie:

- **Recovered** — the plan respects GT on the contested field (hours within
  GT-open; cost counted at GT within budget; belief ≈ GT).
- **Misled** — the plan adopts the served lie (e.g. counts a believed-free venue
  at $0 when GT says $28).

This is **venue-drift-invariant** (it conditions on venues the agent chose) and it
matches a key empirical finding (§7.3): strong models rarely *adopt* a
copying-bloc lie — they **abstain**. So the honest target quantity is
*recovered vs. failed-to-recover*, not a raw misled-rate. The strict,
unambiguous signal is `est ≈ 0 while GT ≫ 0` on an attraction, which avoids
false positives on cheap food (low GT, plausible sub-GT estimates).

---

## 7. Experiments and results

All ablations hold **document volume constant**: the clean ("equal-volume") arm
*heals the served lie in place* (Yelp fields reset to GT; injected tags and cost
overlays removed; incorrect-source docs replaced by neutral docs of equal
count/length) rather than deleting documents. The only variable between arms is
the lie. Δ = faulty − clean; negative = the lie made the task harder.

### 7.1 Certifier validation (free, on stored data) — honest null

**Question:** does the certifier's static `(detectability, repairability)` predict
the *observed* per-flaw trip-rate mined from stored transcripts?
**Result (`results/certifier_retro_report.md`):** **inconclusive**, as its own
power caveat predicted. Over 20 scheduled flaws, repairability did not predict
combined trip-rate (Spearman ρ = +0.08, p = 0.73); the only significant signal
(F2a, ρ = +0.48, p = 0.03) had the *wrong sign*, traced to an outcome-geometry
confound (a small planted hours-shift rarely creates a real scheduling conflict
regardless of detectability). Predictor spread was tiny (4 distinct repairability
values, median 0.5); 9/29 flaws were never scheduled. **Decision:** demote the
certifier to a validity gate only. **Bonus finding** (which drove the whole
redesign): trip-rate is governed by whether corruption is *load-bearing* — whether
the wrong value would change the agent's action — not by detectability.

### 7.2 Load-bearing hours flaws (b1) — feasibility moves, personalization does not

On a 130-flaw hours corpus, `claude-sonnet-4-5` over 6 tasks:
**mean ΔF = −0.17** (feasibility measurably harder under the lie; 4/6 tasks
clearly negative, F-violation counts drop on healing) and **mean ΔP = +0.07
(flat)**. Exactly as predicted: hours bind F, not P. This is the difficulty the
*old* corruption failed to produce — but on the wrong tier for
personalization-driven tasks.

### 7.3 Recovery probe — copying-blocs are the lever

Per-field recovery by evidence structure (`claude-sonnet-4-5`,
`results/recovery_probe/`):

| Structure | Recovery |
|---|---|
| CLEAN | 100% |
| ISO (1 wrong vs 2 right) | 75% |
| **BLOC (3 wrong vs 1 right)** | **0% (100% abstain)** |
| **BLOC + TELL (bloc + off-object copy signature)** | **88%** |

Three conclusions: (1) copying-majority is the difficulty lever — it collapses
recovery 75–100% → 0%; isolated faults barely matter. (2) The off-object tell
restores recovery (0% → 88%) — *hard but fair*, not a coin flip. (3) Strong models
**abstain** rather than adopt the lie ⇒ the metric must be recovered-vs-failed
(§6).

### 7.4 Layer ablation — the first P-wedge

Corpus `test_layer` (35 LLM-authored layer flaws: free-confusion 19,
accessibility-optimism 10, price-deflation 6), `claude-sonnet-4-5`, 6 binding
tasks, faulty vs equal-volume clean:

| | mean Δ | n |
|---|---|---|
| ΔF (feasibility) | +0.007 | 6 |
| **ΔP (personalization)** | **−0.146** | 6 |

ΔF flat is *by design* (these layers corrupt selection-binding attributes, not
feasibility). **ΔP = −0.146 is the first personalization movement in the
project's history** (hours flaws: ΔP ≈ 0; earlier cost overlay: ΔP ≈ 0). The
effect is **mechanism-attributable** on budget tasks:

| task | P faulty→clean (ΔP) | trap mechanism |
|---|---|---|
| gpt Type4 (budget+free) | 0.50→1.00 (−0.50) | Halal Guys ×5 (GT $10, served free): believed-free busts budget vs GT |
| gemini Type4 (avg_cost+budget) | 0.67→1.00 (−0.33) | Rockefeller / 9-11 Memorial / Punjabi believed free — traceable |
| claude Type5 (price) | 0.62→0.75 (−0.12) | mild |
| gemini Type2 (avg_cost) | 0.50→0.67 (−0.17) | *drift* — no traps scheduled |
| gemini Type5 (price+wheelchair) | 0.33→0.33 (0.00) | flat |
| claude Type4 (budget+price) | 1.00→0.75 (+0.25) | *reversal* |

**Honest verdict: directionally positive, mechanism real, not yet conclusive.**
On budget tasks the faulty agent schedules believed-free/cheap venues that bust
the budget under GT scoring — a clean, attributable wedge. But n = 6, drift
contaminates (one task moves with zero traps scheduled), and one task reverses.
The aggregate ΔP and a global trap-count are too crude; the clean signal lives at
the per-(task, binding-attribute) level.

### 7.5 Model discrimination — no gradient, but the targeting bug surfaces

Does a weaker model get misled more? Same corpus, faulty arm,
recovered-vs-misled metric, `claude-haiku-4-5` vs `claude-sonnet-4-5`:

| layer (faulty) | haiku | sonnet |
|---|---|---|
| free-confusion (cost) | rec 0.86 / misled 0.14 (n=7) | rec 0.83 / misled 0.17 (n=12) |
| price-deflation | rec 1.00 (n=3) | rec 0.33 (n=3) |
| **overall** | **rec 0.90 / misled 0.10** | **rec 0.83 / misled 0.17** |

**No weak-vs-strong gradient** at this scale — the weaker model recovered as well
or better. But the per-venue breakdown reveals *why*, and it is the most
actionable result in the study:

- **Every misled case in both models is an attraction taken to $0** — 9-11
  Memorial (GT $28)→$0, Rockefeller (GT $40)→$0: unambiguous adoption of the
  "free admission" lie.
- **Every restaurant is recovered** — Halal Guys $10→$10, Ess-a-Bagel $12→$12:
  the agent prices *food* from menu/world knowledge, so "free admission" is
  **inert by construction** on a restaurant.

So ~half the free-confusion flaws (those on restaurants) cannot fire, which
diluted the aggregate misled-rate from a real ~40% on attractions down to the
observed ~10–17%. The earlier "83% recovery" was inflated by inert flaws, **not
by strong reasoning.** The lie genuinely fools models when well-targeted; it was
simply half-aimed. Fix (unbuilt, cheap): condition free-confusion on paid-entry
venues; give restaurants a price-tier deflation instead.

---

## 8. Limitations

We separate what is demonstrated from what is not.

- **Demonstrated:** the mechanism. Load-bearing, systematic layer corruption on
  binding attributes produces a GT-attributable P-score wedge that sparse noise
  could not. The pipeline is fair (100% of incorrect docs state the lie in prose,
  vs 1/8 for the mechanical operator; cross-venue tells present; clean control
  verified valid).
- **Not demonstrated:** statistical significance. n = 6 tasks / one strong model
  in the headline ablation; drift contaminates aggregate ΔP; one task reversed.
- **Targeting is incomplete.** ~half the flaws sit on venues where the lie is
  inert (restaurants for free-admission). A fair test of model discrimination
  requires correctly-targeted potent flaws.
- **Measurement is model-gated.** The recovered-vs-misled metric requires the
  agent to emit a parseable plan schema; several open models were too tool-brittle
  to score, biasing the panel toward frontier models.
- **Parametric-knowledge risk.** Real venue names let a model override synthetic
  GT from training memory; the design mitigates via majority-in-source framing and
  recommends fictional venues, but this is a standing validity threat.

**None of these undermine the mechanism claim; they bound the strength of the
difficulty claim.** The result is a *demonstrated mechanism awaiting power*, which
we regard as the honest and correct stopping point for this thread.

---

## 9. Future work

A **cheap, scoped** path to significance, in order of leverage:

1. **Fix targeting (free, deterministic):** condition each layer on venues where
   its attribute is load-bearing (free/price → paid-entry attractions; a distinct
   price-tier deflation for restaurants). No API cost.
2. **One powered ablation (bounded API):** the budget/free/access tasks only,
   where the mechanism is cleanest — all binding tasks × ≥3 seed orderings × one
   strong + one genuinely verification-weak model. Report the per-(task, venue,
   binding-attribute) recovered-vs-misled metric, not aggregate ΔP.
3. **Authority-suppressed / held-out-attribute arm** to sharpen the wedge.
4. **Cross-session experience (Phase 7):** the corrupted, GT-graded corpus is the
   substrate for a self-improving agent that accumulates reliability priors over
   sources across sessions — the corruption is not an obstacle but the thing that
   makes learned source-reliability *measurable*. Design in
   `docs/SELF_EVOLVE_DESIGN.md`.

---

## 10. Related work

- **BART / data repair (Arocena 2015):** detectability (violation count) vs
  repairability (true-value share of the candidate set), both statically
  computable; whitelist−blacklist plausibility; NP-completeness ⇒ disjoint scopes.
- **Truth discovery (Li 2016):** naive vote vs weighted vote ("wisdom of
  minority"); copying detected via shared *mistakes* on observable objects;
  inferability; single- vs multi-truth (recall traps); AccuCopy as a reference
  solver.
- **Entity resolution (Ditto, Li 2020):** matching hardness; wrong merge →
  downstream conflict.
- **Omission / visibility (Klinkhardt 2023):** visibility-conditioned
  completeness; the omission gradient used for density calibration.
- **Dirty-data taxonomy (Rahm & Do 2000):** single- vs multi-source.
- **RAG knowledge-conflict (2024–26):** Astute RAG (2410.07176), RAMDocs /
  MADAM-RAG (2504.13079), "diminishing returns of robust RAG" (2502.11400) —
  conflict types (context-memory / inter-context / intra-memory) and the
  parametric-knowledge guard.

---

## 11. Reproducibility

- **Design contract:** `docs/VALID_DIFFICULTY_REDESIGN.md` (source of truth,
  two-wedge model, invariants).
- **Operator + validator:** `scripts/generation/{flaw_masks,inject_flaws,
  validate_corruption,flaw_certifier,source_model}.py`.
- **Runner:** `run_benchmark.py --run-name <corpus> --arm {faulty,clean_equalvol}`.
- **Corpora:** `data/cities/New_York/runs/{test_70,test_lb,test_layer}`
  (deterministic; regenerable from seed).
- **Results:** `results/{certifier_retro_report,recovery_probe/summary,
  ablation_wedge/wedge_report,ablation_layer/layer_wedge_report,
  misled_metric/model_comparison}.md`; scores in `results/scores.db`.

*This is a research-thread preprint, not a peer-reviewed publication. Numbers are
from the runs cited above; small-n caveats are stated in-line.*
