# Self-Evolve Design — cross-session experience under a noisy corpus

**Status:** design contract (v1). Design-only — **no code yet** (framework-level
addition; propose → confirm → build, per CLAUDE_GUIDE construction rule).
**Proposed home:** Phase 7 (builds on the `valid-difficulty-redesign` corruption
wedge; assumes that branch is merged or its arms available).
**Grounding:**
- Field map `~/Documents/Books and Papers/Papers/long-horizon-agents-reading-map.md`
  (long-horizon threads: measurement, compounding error, planning, reflection,
  memory-as-bottleneck, RL credit assignment).
- Self-evolve reading list + PDFs `~/Documents/Books and Papers/Papers/self-evolve/`
  (AWM, ExpeL, Trajectory-Informed Memory, Memp, MemoryArena, Reflexion, Voyager,
  Mem0, A-MEM, ADAS, TextGrad — `literature.md` in that folder).
- The upstream sketch `Vic1025/Evolve-on-exp-agent-framework:docs/self-evolve-design.md`
  (this doc **adapts** it to the real TravelBench code; deltas flagged in §8).
- The corruption wedge in `docs/VALID_DIFFICULTY_REDESIGN.md` and its pilot
  (`results/ablation_wedge/wedge_report.md`: claude-sonnet mean **ΔF = −0.17**).

> **Why this doc exists separately from the upstream sketch.** The upstream sketch
> targets a hypothetical TravelBench with a `fetch_url` tool and live internet. The
> real benchmark has **4 frozen tools and no `fetch_url`** (§8). More importantly,
> the real benchmark now has something the sketch did not assume: a *deliberately
> corrupted, ground-truth-graded* corpus. That corpus is not an obstacle to
> self-evolve — it is the **substrate that makes self-evolve measurable**. This doc
> is built around that fact.

---

## 1. Thesis — and why the noisy corpus is load-bearing

An agent given few instructions, asked repeatedly for travel-planning tasks, should
**accumulate reusable experience across sessions and improve over time, with no
weight updates.** The novel angle vs. prior work (AWM, Trajectory-Informed Memory,
MemoryArena — all clean-environment) is that TravelBench's corpus is **noisy and
partially-wrong by construction**: venues lie about hours/cost via `yelp_*` overlays
(`inject_flaws.py`), and the authority/recovery path (`get_official_site`) can be
*suppressed* (`suppress_authority`, b2). So the agent must evolve not just
*workflows* but **source-reliability priors and verification habits**.

**The realization that drives this doc:** the corruption wedge manufactures the
exact failure a source-reliability tip would fix. The pilot already showed strong
models *can* reason about checking hours but don't *execute* the check — a flat
**ΔF = −0.17** penalty for trusting the lie. That gap is the thing experience should
close. On a *clean* corpus there is nothing interesting to learn — the agent would
just memorize venues (the overfitting failure mode, §7). The wedge is what gives
self-evolve a real, gradeable habit to acquire.

### Why keep the static corpus (no live internet)

Unchanged from the upstream sketch, and it matters more here: deterministic ground
truth is what makes "did experience help?" measurable at all. Going live destroys
reproducibility (hours/prices change hourly) **and** destroys the wedge (you can no
longer separate "the agent learned to distrust Yelp" from "Yelp happened to be right
today"). If more realism is wanted: crawl once → freeze a snapshot → grade against
it, never live access.

---

## 2. Problem — what "no experience" looks like today

Each benchmark task runs **cold**. The agent re-derives, every single time:
- the per-type workflow (e.g. Type 4: allocate per-day budget *before* selecting
  venues; Type 6: resolve closures *before* scheduling);
- the *fact that Yelp hours/cost can be a lie* and that `get_official_site` is the
  authority — it pays the ΔF = −0.17 penalty fresh on every corrupted venue it
  schedules;
- the recovery move after an F2c miss — which it never carries to the next task.

Nothing in `agents/runner.py` or `eval/evaluator.py` persists across tasks. There is
**no `memory/` module** (verified: no such directory). The graded transcript that
would be a perfect training signal is thrown away after scoring.

**Two-axis framing (borrowed from the long-horizon map):**
- *Long-horizon* = improvement as a function of **task index** over a fixed session
  sequence (our tractable analogue of METR's time-horizon-at-50%). The
  compounding-error / self-conditioning result (*Illusion of Diminishing Returns*,
  `2509.09677`) says the failure is **execution, not reasoning** — which is exactly
  the wedge result, and exactly what a persisted verification habit should fix.
- *Self-evolve* = the improvement comes from an **agent-authored memory page** that
  survives context windows (the memory-as-bottleneck thread: Context-Folding,
  COMPASS, A-MEM, Mem0), **not** from weight updates and **not** only within one
  task (the stronger-than-Reflexion claim). The agent decides what goes on the page —
  we give it no guidance (§3).

---

## 3. What the agent gets — a blank page, no guidance

> **Design correction (Vic, 2026-06-19):** the agent is given a **blank memory page
> to write on**, with *no* categories, *no* schema, and *no* instruction about what to
> put on it. Imposing typed stores (workflow / source-prior / recovery) or telling it
> "track source reliability" would **teach it the answer** — that is not a valid
> self-evolve. We keep our expectations to *ourselves* (§3b) and judge what it writes
> after the fact.

**The page (what the agent sees):**
- A single freeform scratch page, persisted across tasks and **also mounted as a file
  in the agent's sandbox** (readable + writable there as well as via context/tool).
- **Hard caps, to stop "store everything":**
  - **max N entries** (start small, e.g. N≈10–15) — forces the agent to *choose* and
    to *overwrite/merge*, not append forever;
  - **rough word cap per entry** (e.g. ≤30–40 words) — forces compression into a
    usable tip rather than dumping transcripts.
  - Over a cap → the write is rejected/truncated and the agent is told only "page full
    / entry too long" (no hint about *what* to keep).
- **No starter content, no template, no field names.** It begins literally empty.
- The only instruction is mechanical: "you have a memory page you may read and write;
  it persists across tasks; it holds at most N entries of ≤W words." Nothing about
  *why* or *what*.

### 3b. What WE expect it to discover (hidden eval lens — never shown to the agent)

This is our private hypothesis, used only for post-hoc analysis of what it wrote — it
is **not** given to the model and **not** used to seed the page. We expect a
self-evolving agent to, unprompted, converge toward writing things like:
- source-reliability priors — "Yelp hours/cost can be wrong → confirm via official
  site" (the wedge-specific habit);
- per-type workflow notes — "Type 4: allocate per-day budget before selecting";
- recovery patterns — "verify hours for venues on holiday/festival windows."

The headline finding is *whether and how fast it gets there on its own* — and what
it writes that we **didn't** anticipate.

---

## 4. The evolve loop — agent-authored, label-free

No experimenter-run EXTRACT/CONSOLIDATE. The **agent itself** decides what to write,
keep, and overwrite, under the §3 caps. This is a stronger self-evolve claim than the
Trajectory-Informed pipeline (which has the *system* mine tips); we adapt AWM's
label-free online idea (`2409.07429`) but move authorship to the agent.

```
for task in session_sequence:
    page             = LOAD_PAGE()                         # persisted; also on sandbox FS
    trajectory, sc   = RUN_AGENT(task, page_in_context=page)   # existing runner + eval, unchanged
    page'            = AGENT_WROTE(trajectory)             # the agent's own edits, capped at N×W
    SAVE_PAGE(page')                                       # no system rewriting of content
```

- **The agent reads its page at task start and may write to it any time** (a
  `write_memory(entry)` / `edit_memory()` tool, or end-of-task reflection turn — see
  the write-trigger conditions in §6).
- **We do not edit, cluster, or rerank its content.** The caps are the only pressure;
  consolidation is the agent's job (that *is* part of what self-evolve must learn). The
  graded C/F/P/B transcript is the reward the agent experiences only indirectly — it
  sees task outcomes, not the GT — so the page reflects what *it* judged worth keeping.
- **Our side stays read-only on content:** we parse the page post-hoc against the §3b
  lens and against C/F/P/B to measure *what kind* of knowledge it accreted, but we
  never inject or relabel.

### 4b. Write-trigger conditions (a comparison test, not a fixed choice)

How the agent is prompted to use the page is itself a variable:
- **Free-write** — the page exists; the agent writes *only if it decides to*. The
  purest self-evolve signal (does it spontaneously externalize learning?).
- **Forced-write** — at the end of each task the agent is required to update the page
  (still with *no* guidance on content). Tests whether forcing reflection helps or just
  produces noise the caps then thrash on.

E1b in §6 runs both and compares behaviour around the page.

---

## 5. Integration points (verified against the code)

- `agents/runner.py` / `base_runner.py` — wrap the planning loop to (a) inject
  retrieved tips into the system prompt, (b) emit the full trajectory. Multi-provider
  already; the wrapper is provider-agnostic.
- `eval/evaluator.py` — already returns `{c_score, f_score, p_score, b_score}` and
  computes F2c. Used **only** for our post-hoc analysis (§3b lens); the agent never
  sees these scores directly. **No evaluator change needed.**
- **New `memory/` module — a page, not a tip DB.** Persists one freeform page per
  agent/session under the N-entry × W-word caps; exposes `load_page()`,
  `write_entry()` / `edit_entry()` (cap-enforcing), and **mounts the page as a file in
  the agent's sandbox** so it is reachable both in-context and on the FS. A DB backend
  is fine, but the *interface to the agent is a page*. No embeddings/retrieval ranking
  needed at the start — the whole page is small enough to load wholesale (that is the
  point of the caps).
- `scripts/` — experiment driver that runs a task sequence and logs per-index scores
  to `scores.db` (reuse the existing schema + `--arm` plumbing from c-infra), plus the
  page snapshot after each task (for the §3b post-hoc analysis).
- **Corpus** — reuse the wedge corpora: `faulty` (lies served) as the environment;
  the agent's job is to learn to recover. `clean_equalvol` as a sanity control
  (nothing to learn → flat curve expected).

---

## 6. Experiments

Headline = **cross-session under noise** (does experience close the corruption gap).

### E0 — Oracle tip = the **upper bound**, not a gate (known result)
Hand-injecting the perfect tip ("Yelp hours/cost can be wrong → confirm via
`get_official_site` before scheduling") is **already known to work** — Vic ran this
instruction-injection a few versions ago and it makes the corrupted tasks *extremely
easy* (ΔF collapses toward 0). So E0 is **not a go/no-go probe**; the ceiling is
established. Its role is recalibrated:
- **The oracle tip is the upper-bound reference line.** It is the score the agent would
  reach if it wrote the perfect note on its page on task 1.
- **The real research question is therefore the self-discovery gap, not "can a tip
  help."** A single hand-written instruction trivially closes the gap — that is *given*.
  The claim worth proving is that the agent **rediscovers that habit on its own** and
  writes it to the page, without being told what to write, and **how close** its
  self-authored note gets to the oracle line, **how many tasks** it takes to get there,
  and whether it keeps the note alive under the entry caps rather than overwriting it.
- **Design consequence — preserve headroom.** Because the oracle instruction makes
  tasks *too* easy, E1 must use tasks with residual difficulty *beyond* the
  verification habit (which venues to verify, budget allocation, subset selection), so
  the evolve curve is a *climb*, not a one-task step function. Calibrate task mix so the
  oracle line sits clearly below 1.0.

  *(Re-run E0 only as a one-shot to fix the exact upper-bound number for the current
  corpus/model — not as a decision gate.)*

### E1 — Cross-session, evolve ON vs OFF (headline)
- Fixed sequence of N corrupted tasks (mix of the 6 types, single city to start).
- A (control): memory disabled, every task cold. B (evolve): memory persists/updates.
  **Oracle line (E0):** memory disabled but the perfect tip pre-injected — the
  known-easy upper bound.
- **Metric:** C/F/P/B and **ΔF (faulty − clean)** as a function of task index.
  Hypothesis: A stays flat at ≈ −0.17; B's ΔF **climbs from A toward the oracle line**
  as the source prior is self-discovered. **The headline number is the self-discovery
  gap** = (oracle line − B's plateau): how much of the hand-fed answer the agent
  recovers on its own, and at what task index it gets there. Mean over multiple seed
  orderings.

### E1b — Free-write vs forced-write (how it behaves around the page)
Same cross-session setup, varying only the §4b write trigger:
- **Free-write** — page available, agent writes only if it chooses.
- **Forced-write** — agent must update the page after each task (still no content
  guidance).
**Observe:** does it write at all unprompted? Does forcing produce useful tips or just
churn that thrashes against the N-entry cap? Does the *content* it writes differ
(spontaneous notes vs. compelled ones)? This is the behavioural study Vic called out.

### E2 — Hard task + unlimited tool calls + blank page (within-episode)
The standalone pipeline Vic flagged: take a **deliberately very hard** task (stacked
Type 5/6, or a heavily-corrupted variant), give **uncapped tool calls** and a **blank
page**, and ask: *does having the page improve performance at all?*
- Compare {no page} vs {blank page available} on final C/F/P/B and steps-to-solution.
- Hypothesis (AWM's "fewer steps"): the page lets the agent record intermediate
  findings (which venues it already verified, which sources lied) and avoid
  re-deriving them — fewer redundant tool calls for equal/better score.
- This is a *single-episode* test of the page as working memory, separate from the
  cross-session learning claim — a cleaner isolation of "does the scratch surface help."

### E3 — Ablations
- **Cap sensitivity** — vary N (entries) and W (words/entry). Too tight → can't retain
  the habit; too loose → "store everything" bloat. Find where it matters.
- **Authority-suppressed arm (b2)** — does the page still help when `get_official_site`
  is silenced and the agent must triangulate blogs/forums? Sharper than the original.
- **Held-out city** — evolve on NYC, cold-test on the *fresh city regenerated next*
  (the regen is already planned → held-out city is nearly free). The overfitting check:
  does the page carry a *generalizable* habit or memorized venue facts?
- **Page-content typology (post-hoc, §3b)** — classify what it wrote (source-prior /
  workflow / recovery / venue-specific / junk) and correlate each class with score
  movement. Tells us *which* self-discovered knowledge actually paid off.

---

## 7. Invariants / guardrails

- **No guidance leakage — the validity crux.** The agent is told *nothing* about what
  to write (no categories, no template, no "track source reliability"). Any hint about
  content invalidates the self-evolve claim. The §3b expectations live only in our
  analysis code, never in the prompt.
- **Caps are the only pressure.** We do not edit, cluster, or rerank page content. The
  N-entry × W-word limits force the agent to do its own consolidation — that selection
  *is* the behaviour under study.
- **No corpus memorization.** A page that wins by encoding venue IDs (not a
  generalizable habit) is the failure mode; the held-out-city test (E3) is the check.
- **Log every overwrite.** When the agent hits the cap and drops/overwrites an entry,
  record it — silent truncation reads as "covered everything." We want the full edit
  history of the page, not just the final state.
- **Separate "feasibility" from "best real trip."** We claim only the corpus-checkable
  axis (did it stop trusting the lie), never "found the objectively best trip."
- **Reward honesty.** The agent never sees the numeric C/F/P/B. It sees only
  **feasibility-violation feedback** (location + type — overlap / travel-infeasible /
  closed-at-scheduled-time), never the corrected GT value and never the P-rubric (§11
  D1). No leakage of `venues` GT into the prompt or page.

---

## 8. Deltas vs. the upstream sketch (`self-evolve-design.md`)

| Upstream sketch assumes | Real TravelBench | Consequence |
|---|---|---|
| a `fetch_url` tool | **no `fetch_url`**; 4 tools: `search_yelp`, `search_blogs_and_forums`, `get_official_site`, `get_travel_time` | the recovery/authority path the agent must *discover* is `get_official_site`, not `fetch_url` |
| typed tip stores, system-mined (3 categories) | a **blank page, agent-authored, no categories/guidance** (Vic, §3) | stronger self-evolve claim; we judge content post-hoc (§3b), never seed it |
| unbounded memory + system consolidation | **N-entry × W-word caps; the agent self-consolidates** | caps are the only pressure; no system reranking/pruning |
| authority always available | `get_official_site` **can be suppressed** (`suppress_authority`, b2) | new ablation: page under suppressed authority (E3) |
| "does experience help?" on clean tasks | the wedge gives a **−0.17 gap** to close | headline = self-discovery gap (page plateau vs oracle line) |

---

## 9. Open questions

- Where to set the caps (N entries, W words) so the habit fits but bloat can't form.
- Does the agent write *at all* unprompted (free-write), and does forcing help or churn
  (E1b)?
- Sample efficiency: how many corrupted tasks before the page-on curve separates from
  page-off?
- Does a self-written source-prior transfer across *fields* (learned on hours → helps
  on cost)? The b1.5 cost/price overlay makes this directly testable.
- Page granularity: does it write task-level or step-level notes, and which pays off?
- Multi-user / per-user pages vs one global page (later phase).

---

## 10. Build order (when greenlit)

1. **E0 one-shot** — pin the oracle upper-bound number for the current corpus/model
   (known to work; not a gate). Calibrate the E1 task mix so the oracle line sits
   below 1.0 (preserve headroom).
2. `memory/` module — the **page**: persisted store + cap-enforcing `write/edit`,
   mounted into the agent sandbox. No embeddings/retrieval at first (whole page loads).
3. Runner wrapper (load page into context + expose write tool + emit trajectory) +
   experiment driver that snapshots the page after each task.
4. **E1 / E1b** on the existing `faulty` wedge corpus — self-discovery gap, and
   free-write vs forced-write behaviour.
5. **E2** hard-task + unlimited-tools + blank-page (within-episode), then E3 ablations
   and the held-out-city generalization test on the regen city.

---

## 11. Stress-test findings & decisions (2026-06-19)

Method: 5 parallel critical reads of the §grounding papers (AWM, Trajectory-Informed,
Memp / Reflexion, Self-Refine, Generative Agents, Voyager / MemGPT, A-MEM, Mem0 /
Intrinsic-Metacognition, MemoryArena, Skills-in-the-Wild, Illusion-of-Diminishing-
Returns) + an outside-ideas scout + a code feasibility pass. Convergent verdict: the
*memory machinery* is already near simplest-robust (keep it); the *experiment* needed
three additions, all "tell the agent the **shape** of the task, never the **content**"
— so the §3 purity rule survives. Decisions below are locked unless re-litigated.

### What survives unchanged (do NOT add machinery)
- **Agent-authored** is well-supported: AWM's rule-vs-LM induction is a wash (35.5 vs
  35.6 SR), and Claude's shipped file-based memory tool *is* this design. Keep.
- **Whole page in context, no retrieval, hard size cap.** MemGPT/A-MEM/Mem0 add
  retrieval only at 10³–10⁶ entries; at N≈15 (~500 words) we are 1–2 orders below that.
  **Resist vector DB / embeddings / system-side consolidation.** Tripwire: revisit only
  if N ever exceeds ~30–40.

### D1 — Grounded error signal via a feasibility-feedback round *(decision; was the #1 gap)*
The literature is unanimous that cross-task improvement needs a grounded signal
(Reflexion regresses *below* baseline without its evaluator; Self-Refine → 0 under
generic feedback; Voyager −73% without verification; Intrinsic-Metacognition: a buffer
alone risks "trapped loops" when reward is absent).
- **Code reality (verified):** the *solving* runner (`agents/runner.py:run_agent`) has
  **no SUBMIT/validate tool and no feedback** — it emits `<final_plan>` as free text,
  the loop ends on `end_turn` or `MAX_TOOL_ROUNDS=20`, and the evaluator scores it
  offline. (The SUBMIT→validate→re-feed loop Vic recalled lives in the *task-gen* agent
  `task_agent.py`, a different agent.) So this signal must be **added**, not reused.
- **Mechanism:** when the agent submits a plan, run the **feasibility** checks
  (F-family: overlap, travel-infeasible, hours/closed, sold-out) and return the
  **violation locations + types** — **not** the corrected hours, **not** the P-rubric,
  **not** which source lied. If the agent burns out its rounds, give **one final
  error-log round**; **terminate at the fixpoint** (no further doc-/plan-changing
  request). Bounded Reflexion-style loop.
- **Why this preserves purity AND the wedge:** a violation contradicts the served lie
  ("closed at your scheduled time" vs Yelp's "open") → the lie becomes *discoverable*,
  but the agent learns only *that* it failed and must re-research to find *why* and
  decide what (if anything) to write. It is never handed the truth or the rubric.
- **Implementation (Vic, "add the error-log mechanism to the eval pipeline too"):**
  factor the evaluator's F-family checks (`eval/evaluator.py`) into a shared
  `feasibility_report(plan, task) → [{venue, time, type}]` that BOTH (a) the offline
  F-score and (b) the new in-loop feedback round call — so the signal the agent sees
  and the score it's graded on can never drift. The feedback round applies a
  **purity filter** to that report: emit `{location, violation_type}` only, strip
  `corrected_value` / score weights / P-rubric. Wire it into `run_agent` as a
  post-`<final_plan>` validation step (mirrors the task-gen agent's SUBMIT→validate→
  re-feed, which already exists in `task_agent.py`). **Build item — not yet coded.**

### D2 — Write-gate is NOT forced; verification behaviour is a dependent variable *(decision)*
Rejected the evidence-pointer gate. Forcing verification contaminates the exact thing
we measure. Present the page as "here is what you noted before"; let doubt arise
naturally. An agent that hoards an *unverified lie* is a **valuable observation** — it
is the memory-poisoning-vs-source-distrust signal (OWASP ASI06; "Whose Facts Win?"),
which is this benchmark's sharpest novelty. We **instrument** provenance (does each
entry trace to a tool observation? did the agent later doubt/overwrite it?) for
post-hoc audit, but **enforce nothing**.

### D3 — Measurement controls *(decision)*
- **Three arms, not two:** {no page} / {scratchpad cleared each task} / {page persists}.
  The cross-session claim survives only if **persists > cleared** — otherwise a "page
  helps" result is just a within-task ReAct scratchpad effect (MemoryArena documents
  memory writes *hurting*; a memory-free long-context model beat every memory variant).
- **Multi-seed × shuffled task order with CIs** before any expensive sweep — ΔF=−0.17 is
  one small-N pilot and could be ordering noise.
- **Manual page audit:** if the page never actually contains a correct "verify hours /
  distrust Yelp" rule, the gap-to-oracle is uninformative → report as a *discovery
  failure*, not a learning-rate.
- **Fix one thinking-capable model** to avoid the self-conditioning × model-strength
  confound (Illusion-of-Diminishing-Returns: a persisted *wrong* note re-read each task
  can drive a *negative* slope; CoT mitigates). Use **claude-sonnet-4-5** (not 4-6 —
  tool-use regression).

### D4 — Update rule: delta edits, SKIP-legal *(decision; the one real "reconsider")*
ACE (arXiv 2510.04618) shows monolithic whole-page rewrites under compression cause
"context collapse / brevity bias" — exactly our tight-word-cap + rewrite config.
- Prefer **delta edits** (str_replace/insert/delete, like Claude's memory tool) over
  rewrite-the-whole-page.
- When full, frame the choice as **{ADD-replacing-X / MERGE-into-X / SKIP}** and make
  **SKIP legal even when full** (Mem0's NOOP — a new observation may be worth *less*
  than everything stored). This attacks the "evict the one good tip under cap pressure"
  failure. Both are *shape, not content* → purity preserved.

### Residual risks (watch, not block)
- Implicit-reward may still be too weak for *unguided* discovery even with D1 — the
  honest framing remains "can a notebook substitute for metacognition?" with a real
  chance of an informative null.
- D1's feedback loop adds API rounds → cost. Simulate/`--dry-run` first (billing memo:
  5× the estimate).

---

## 12. References & sources

Consulted for this design (merged in from `docs/EXTERNAL_REFERENCES.md` for review).
Policy unchanged: ideas paraphrased/reimplemented, no verbatim code; any quote/figure
must be attributed inline + logged. Local PDFs:
`~/Documents/Books and Papers/Papers/self-evolve/` (19 papers + `literature.md`,
downloaded 2026-06-19) and `~/Documents/Books and Papers/Papers/` +
`long-horizon-agents-reading-map.md` (long-horizon set).

**A. On-point — induce/reuse experience from repeated tasks**
- AWM — Agent Workflow Memory — `2409.07429` — induce reusable workflows; rule-vs-LM
  induction a wash → agent-authoring is fine (§11 keep).
- ExpeL — LLM Agents Are Experiential Learners — `2308.10144` — unguided NL-insight
  distillation, no labels; closest precedent for "no guidance" (§1, §11).
- Trajectory-Informed Memory (IBM) — `2603.10600` — strategy/recovery/optimization
  tips; retrieval discipline + τ tuning dominate (§3, §4).
- Memp — Agent Procedural Memory — `2508.06433` — proceduralization + reflexion-update;
  inverted-U on memory size (§4, §11 D4).
- LLMs Can Self-Improve at Web Agent Tasks — `2405.20309`.

**B. Reflection / self-written memory — needs a grounded signal**
- Reflexion — `2303.11366` — verbal RL; regresses *below* baseline without its
  evaluator (§11 D1).
- Self-Refine — `2303.17651` — collapses to ~0 under generic feedback; 94% of failures
  = bad feedback (§11 D1, forced-write risk).
- Generative Agents — `2304.03442` — memory stream + reflection; unverified memory
  hallucinates "facts" (§11 D2 poison).
- Voyager — `2305.16291` — skill library; −73% without the verification gate (§11 D2).

**C. Memory infrastructure — eviction/paging (why we DON'T need it at N≈15)**
- MemGPT / Letta — `2310.08560` — OS-style paging; stuffing context hurts.
- A-MEM — `2502.12110` — Zettelkasten links; retrieval-k plateaus then degrades.
- Mem0 — `2504.19413` — ADD/UPDATE/DELETE/NOOP update ops (§11 D4 SKIP-legal).

**D. Skeptic / evaluation — when memory fails**
- Truly Self-Improving Agents Require Intrinsic Metacognitive Learning — `2506.05109` —
  buffer ≠ metacognition; "trapped loops" w/o reliable reward (§11 D1, residual risk).
- MemoryArena — `2602.16313` — multi-session memory; writes routinely *hurt*; mandates
  no-memory baseline + progress/success split (§11 D3).
- How Well Do Agentic Skills Work in the Wild — `2604.04323` — fragile transfer;
  negative transfer; weak models hurt by low-quality notes (§11 D3 confound).
- The Illusion of Diminishing Returns — `2509.09677` — long-horizon failure is
  execution not reasoning; self-conditioning (§2, §11 D3 negative-slope risk).
- METR — Measuring AI Ability to Complete Long Tasks — `2503.14499` — the time-horizon
  anchor (§2 long-horizon framing).

**E. Self-evolving / compiled alternatives**
- ADAS — Automated Design of Agentic Systems — `2408.08435`.
- Gödel Agent — `2410.04444`.
- DSPy / TextGrad — `2406.07496` — compiled prompt/workflow optimization (§9).
- Memory survey (Mechanisms, Evaluation, Frontiers) — `2603.07670`.

**F. Added during the 2026-06-19 stress test**
- ACE — Agentic Context Engineering — `2510.04618` — delta edits beat monolithic
  rewrite; "context collapse / brevity bias" (§11 D4 — the one real reconsider).
- Claude file-based memory tool (Anthropic docs,
  platform.claude.com/docs/.../tool-use/memory-tool) — shipped freeform `/memories`
  the agent self-curates; the production default this design matches.
- "Whose Facts Win? LLM Source Preferences under Knowledge Conflicts" — `2601.03746` —
  LLMs over-defer to context; active verification drives recovery (§11 D2).
- Memory poisoning / OWASP ASI06 — `2602.06052` — persistent memory as attack surface;
  our corrupted corpus = first *quantification* of poison-vs-distrust (§11 D2 novelty).

> Provenance caveat (from `literature.md`): pre-2025 IDs are established; 2025–2026 IDs
> were surfaced by search — verify each resolves before formal citation. All 19
> self-evolve PDFs downloaded cleanly 2026-06-19.
