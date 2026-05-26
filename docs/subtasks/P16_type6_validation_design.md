# P16 — Type 6 validation design (context-window tension)
**Status: 📝 design problem documented, implementation deferred**
**Blocks: nothing currently — basic grounding check added in P-fix-1**
**Depends on: nothing**

---

## What type 6 is supposed to be

Type 6 difficulty comes from a **persona preference conflicting with the seasonal window's character**. One signal comes from the user (e.g. "business dinner", "outdoor picnic"), the other from the world (e.g. Carnival road closures, Christmas week limited hours). The agent solving the task must cross-reference both to find a plan that satisfies the user while navigating the window constraints.

This is fundamentally different from type 1-5: the tension isn't between two user signals or between user signals and the pool — it's between the user and the *season*.

---

## The current state

A basic grounding check was added in P-fix-1 (task analysis session):

```python
# Type 6 grounding: must have ONE of:
#   (a) B-score doc_appeared constraint  → agent must read official site for anchor date
#   (b) P-constraint with time_window= scope  → timing relative to window matters
#   (c) P-constraint with venue_id= scope  → specific window-affected venue required
```

This prevents the worst case (type 6 task with no window connection at all), but it doesn't validate the **quality** of the tension.

---

## The real validation problem

**What we need to verify but don't:**

**1. The conflict is real, not fictional.**
The task's claimed persona signal actually conflicts with this specific window. Example bad case: agent labels a task type 6 for "someone who loves outdoor dining" during Easter week — but Easter week in London has no outdoor-affecting constraints in the window data. The conflict is invented.

To verify: the window's `character` and `conditional_wrong_info_hints` must contain content that actually conflicts with the stated persona signal. This requires either:
- Structured window metadata (conflict_triggers: ["outdoor", "crowded areas"]) — not currently in schema
- LLM judge at generation time — expensive, adds another API call per task
- Pattern matching on window character text against persona signals — fragile

**2. The conflict is discoverable through documents.**
The whole point of type 6 is that the agent solving the benchmark must *read docs* to discover the conflict (e.g. "official site says venue is in the Carnival closure zone on Saturday"). If the conflict isn't encoded in any source document, the solving agent has no way to find it — the task is unfair.

Currently: `_check_event_mentions` in `agent_tools.py` verifies events appear in source docs. But it doesn't verify that the *window conflict* for the user's persona signal appears in docs. A type 6 task about "road closures during Carnival" only works if at least one source doc mentions that the specific venue is in a closure zone.

**3. The persona signal that's in tension must actually be a hop-2 signal.**
"Person wants outdoor dining" → "Carnival closes streets → outdoor venues nearby are affected" is a hop-2 inference. But if the agent just writes `scope="all" condition={has_tag="outdoor"} agg="all"` as a universal filter, it's just a pool filter disguised as type 6. The window tension should appear in a B-score constraint (the solving agent must reason about it), not purely in pool filters.

---

## Options for fixing

### Option A: Structured conflict metadata on the window
Add a `conflict_hints` field to the seasonal window schema:
```json
{
  "conflict_hints": [
    {"type": "closure", "affects": ["outdoor", "street-food"], "dates": ["2026-08-23", "2026-08-24"]},
    {"type": "sold_out", "venue_tags": ["carnival", "notting-hill"], "dates": ["2026-08-23"]}
  ]
}
```
Validation can then check: does at least one PC/B-score constraint reference a topic from `conflict_hints`?

Pros: precise, no LLM call needed
Cons: requires schema change + re-generating window data for all cities, and humans/LLMs need to fill in conflict_hints accurately

### Option B: LLM judge at generation time
After SUBMIT passes the basic schema checks, run a quick LLM call: "Does this type 6 task's query persona genuinely conflict with this window's character? Answer yes/no with one sentence of reasoning."

Pros: flexible, catches semantic mismatches
Cons: adds ~0.5–1s latency and cost per task; another LLM call in the generation loop

### Option C: Validate that B-score contains codeable window-specific check
Require that at least one B-score constraint for type 6 explicitly references the anchor event name or a window-specific attribute (closed district, sold-out venue, modified hours date). The agent must prove it grounded the type 6 in concrete window data.

This is the lightest extension of the current grounding check. Something like:
```python
anchor_names = [e["name"].lower() for e in window.get("anchor_events", [])]
bscore_texts = " ".join(c.get("description","") + c.get("rubric_prompt","") 
                        for c in task.get("rubric",{}).get("b_score_constraints",[]))
if not any(name in bscore_texts.lower() for name in anchor_names):
    issues.append("Type 6 B-score constraints don't reference any anchor event...")
```

Pros: no schema change, no extra LLM call
Cons: fragile (agent could mention the anchor event name without real conflict encoding)

---

## Recommendation (deferred)

Option C is the fastest to implement and catches the most common failure mode (generic task mislabelled as type 6 without any anchor event reference). Option A is the right long-term answer but requires schema + data work.

**For now:** the basic grounding check (doc_appeared OR time_window scope OR venue_id scope) prevents the worst cases. Implement Option C as a next step when type 6 success rate warrants deeper investigation.

---

## Data point from London run

2/4 type 6 tasks failed with "scoped constraint leaves 0 candidate venues" — these were pool gap failures (agent referenced `district=Notting Hill` for meals, no food venues there; and `outdoor` tag on attractions, 0 matches). Neither failure was a type 6 semantic failure — they were ordinary pool-gap failures that would fail for any type.

The 2 type 6 successes both had `doc_appeared` B-score constraints and used `venue_id=` scope — exactly the grounding pattern the new check requires.

## Files to change when implementing

- `test_generate_tasks.py` — add Option C anchor-event reference check
- `docs/seasonal_windows_design.md` — add `conflict_hints` field spec (for Option A, if pursued)
- `scripts/generation/populate_seasonal_windows.py` — add `conflict_hints` to window generation prompt (Option A only)

