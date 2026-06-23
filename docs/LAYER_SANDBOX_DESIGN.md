# LLM Layer-Authoring Sandbox — design (build spec + prompts)

**Status:** design for an HONEST end-to-end mini-test (Vic, 2026-06-22). Build a faithful
minimal version of this pipeline, author a couple of layers on the REAL NYC pool, run the
ablation, and report whether it gets harder — **with no cherry-picking / no refining the
LLM output**, because the test is *of the pipeline*, not of an idealized layer. If it doesn't
get harder, the pipeline design is what we fix. Grounding: `VALID_DIFFICULTY_REDESIGN.md` §14.

---

## 1. What a layer is (the structure we're testing)

A **layer** corrupts ONE binding attribute systematically across many venues, carried by a
small set of **recurring unreliable sources**. That makes the bias *source-correlated* (real
internet pattern) and keeps truth *recoverable*: a careful agent infers a source is unreliable
about the attribute from its **track record across venues** (the cross-venue "tell", Li 2016
§3.2.3), then discounts it. Truth is held by a minority independent source.

```
Layer = {
  attribute,        # binding field+tag the layer lies about
  direction,        # FALSE-POSITIVE: non-qualifying venue looks qualifying
  density,          # ~0.30 of eligible venues (Klinkhardt gradient), visibility-scaled
  unreliable_srcs,  # 2-3 (author, source_name) identities that recur across venues = the TELL
}
```

Three concrete layers for the test (chosen because tasks bind on them — §14 confirm):
- **free-confusion** — paid venue looks free. attribute=`avg_cost_local`→0 + tag `free-entry`.
  direction defeats `avg_cost==0` / `has_tag:free-entry` / budget constraints.
- **accessibility-optimism** — inaccessible venue looks accessible. `wheelchair_accessible`→1
  + tag `step-free`. defeats `wheelchair==true`.
- **price-deflation** — `price_tier` one rung cheaper. defeats `price_tier<=budget/mid`.

## 2. Pipeline (per layer)

```
eligible  = [v in pool if layer.applies(v)]          # e.g. paid venues for free-confusion
selected  = seeded_sample(eligible, density, weight=visibility)   # deterministic by seed
assign each selected venue an unreliable source from layer.unreliable_srcs (round-robin)
for v in selected:
  1. STRUCTURED overlay: set the served yelp_* field to the wrong value (reuse b1.5 overlay);
     add the wrong tag as yelp_visible.  (venues GT table untouched.)
  2. INCORRECT-SOURCE doc(s): LLM authors 1-2 docs from v's assigned unreliable source stating
     the wrong attribute value in natural prose + an origin story.  → majority of v's evidence.
  3. TRUTH-CARRIER doc: LLM authors 1 doc from an INDEPENDENT (reliable) source stating the
     true value.  → minority truth + recovery path.
  4. write wrong_info row + doc_venue_roles (incorrect_source / truth_carrier), via the existing
     atomic ADD_WRONG_INFO path; structure='layer:<name>', records seed/provenance.
```
The **same unreliable source recurs across the layer's venues** (step "assign"): that recurrence,
checkable against the truth-carriers on other venues, is the tell that keeps the layer fair-hard.
Collisions (two layers on one venue+field) → keep highest-priority layer, drop the other (seeded).

## 3. Reused machinery (no reinvention; from the code map)
- pool+tags: `pool_utils.load_city_pool(city)`
- author prose: `generate_multi_venue_docs._call_generation_llm(prompt, model, api_key)`
  (default `claude-sonnet-4-20250514`, Anthropic) → returns parsed JSON docs.
- write doc: `agent_tools.tool_CREATE_PAGE/FILL/COMMIT` (or a direct source_docs insert for the
  sandbox; COMMIT enforces length/persona/engagement — keep it for validity).
- wrong_info+roles: `agent_tools.tool_ADD_WRONG_INFO(...)` (atomic, VERIFY-gated).
- GT immutability + overlay + recovery metric: reuse `inject_flaws` overlay write + the
  feasible-shift / recovery-probe analyzers.

## 4. The prompts (the crux — verbatim)

**(a) Incorrect-source doc** (one call per venue, authored as the recurring unreliable source):
```
You are {author}, who writes on {source_name}. Write a realistic {doc_type}
({persona}, {tone}) about {venue_name}, a {category} in {district}, New York.

Embed this claim NATURALLY, stated as plain fact with no hedging, woven into the post
(not a bullet): "{wrong_claim}".
The author believes it because: "{origin_seed}".
Do NOT mention anything that contradicts the claim. 350-900 words of body only.

Return JSON: {{"title":"","author":"{author}","source_name":"{source_name}",
"date":"YYYY-MM","body":""}}
```
  where e.g. wrong_claim="admission is completely free" / "step-free and fully wheelchair
  accessible throughout"; origin_seed="visited a few years ago before they introduced an
  entry fee" (free-confusion / temporal) — the handbook origin-story gate, honestly authored.

**(b) Truth-carrier doc** (independent reliable source, minority truth):
```
You are {author2} on {source_name2}, a recent first-hand visitor. Write a realistic
{doc_type} about {venue_name}. State plainly, as recent fact: "{true_claim}"
(e.g. "there's a $25 admission" / "there are steps at the entrance, not step-free").
You may note older posts get this wrong. 300-700 words, body only. Return JSON {{...}}.
```

No prompt mentions tasks, scoring, or "make it hard" — purely authoring realistic biased prose.

## 5. The honest test protocol (no refining)
1. Build the minimal sandbox above (faithful to §2-§4).
2. Run it on NYC test_70 → `runs/New_York/test_layer/` for the 3 layers at density 0.30.
   **Whatever the LLM authors is what we use** — no manual edits, no regeneration to "improve" a
   weak doc, no dropping flaws that look unconvincing.
3. Validate fairness (recovery path exists per flaw) — reject only on *validity*, never to tune difficulty.
4. Ablation: claude-sonnet, `faulty` vs `clean_equalvol`, the binding tasks. Plus the FREE
   per-field recovery metric on the authored evidence.
5. Report ΔF/ΔP + recovery-rate honestly. **If not harder → the pipeline is wrong; iterate §2-§4**
   (e.g. wrong-claim not landing in prose, tell too weak, density too low), not the result.

## 6. Cost
Authoring: ~3 layers × ~15-20 venues × 2 docs ≈ 60-120 short LLM calls (cheap).
Ablation: ~6-12 planner runs. Both API. This is the one checkpoint.
