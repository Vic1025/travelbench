# P4 — Agent failure diagnostics + turn exhaustion fix
**Status: ✅ 12 subitems landed across five live-run diagnostic cycles**
**Blocks: P5 (parallelism shouldn't ship without reliable per-model failure attribution)**

---

## The problem (as observed)

Live run with `--model deepseek-chat --max-turns 20`:

```
[1/6] Generating type1_cascading_requirements...
❌ Agent exhausted turns without valid SUBMIT
[2/6] Generating type2_subset_selection...
❌ Agent exhausted turns without valid SUBMIT
[3/6] Generating type3_competing_requirements...
❌ Failed: argument of type 'int' is not iterable
```

Three distinct bugs in one output:

1. **"Exhausted turns" is misleading.** With `max_turns=20` the agent should have had plenty of budget. It didn't — it returned prose without calling SUBMIT and the loop broke on turn 2-3, displaying "exhausted" anyway.

2. **Silent prose-only termination.** DeepSeek-chat (the base chat model, not the reasoner variant) writes the full task JSON into the assistant message body and sends `finish_reason=stop`. The loop saw no tool call and broke silently with `accepted_task = None`. The validation path was never reached, so the agent received no feedback and had no chance to recover.

3. **Mid-run int-not-iterable crash.** At type3, before the agent loop finished a single turn, something in the pipeline raised `argument of type 'int' is not iterable`. No transcript was captured, so the failure is un-diagnosable from the run log alone.

## Tasks

### 1. Replace misleading "exhausted" with precise stop reasons — ✅ DONE

All three provider loops (`_run_openai_loop`, `_run_anthropic_loop`, `_run_gemini_loop`) now return `(task, stop_info)` where `stop_info` contains:
```python
{
  "turns_used": N,
  "max_turns":  20,
  "stop_reason": "submitted" | "exhausted" | "no_tool_calls" | "stop_reason:X",
  "last_submit_errors": [...],
  "log_path": "/path/to/transcript.json",
}
```

`generate_all_types` unpacks this and prints the real reason:
```
❌ Agent failed: agent refused to call SUBMIT (stopped at turn 3/20) — wrote response as prose instead of using the tool
```

### 2. Nudge agents on prose-only responses — ✅ DONE

Instead of breaking silently when the model returns text with no tool call, each loop now appends a nudge user message:

> Your previous message contained no tool call. You cannot complete this task by writing JSON in a message body — the validation system only runs when you invoke the SUBMIT tool. Please call the SUBMIT tool now with task_json as an argument.

The loop continues. Only after `NO_TOOL_LIMIT = 2` consecutive prose-only turns does it give up with `stop_reason="no_tool_calls"`. One stray response doesn't kill an otherwise-progressing run.

### 3. Transcript logging on every run — ✅ DONE

`run_task_agent` now accepts `log_dir: Path | None`. Each loop captures a normalized transcript:
```python
[
  {
    "turn": 1,
    "assistant_text": "Let me think about this...",
    "finish_reason": "tool_calls",  # or stop_reason_api for Anthropic
    "tool_calls": [
      {"name": "THINK", "input": {...}, "status": "ok", "result": {...}},
    ],
    "nudge_sent": false,
  },
  ...
]
```

On exit (success or failure), the full transcript is written to:
```
data/cities/{city}/tasks/runs/{run_name}/{window_id}/agent_logs/
  {outcome}_{model_slug}_{window_id}_{type_key}_{timestamp}.json
```

Payload includes: `outcome`, `model`, `type_key`, `city`, `window_id`, `max_turns`, `stop_info`, `initial_message`, `transcript`, and truncated `system_prompt`.

Long strings (e.g. the 50kB `task_json` inside a SUBMIT) are capped at 2000 chars via `_truncate_for_log()` to keep the transcript readable without losing structure.

On failure, the log path surfaces inline:
```
❌ Agent failed: ...
   Last SUBMIT errors (2): ...
   📝 Full transcript: /path/to/failure_deepseek-chat_london_easter_2026_type3_1731884912.json
```

Logging is wrapped in try/except — never crashes the run.

### 4. Handbook + system prompt clarity — ✅ DONE

System prompt now opens with a prominent section:
```
═══════════════════════════════════════════════════════════════════════
HOW YOU INTERACT WITH THIS ENVIRONMENT — READ CAREFULLY
═══════════════════════════════════════════════════════════════════════
This is a TOOL-CALLING environment. You have access to 6 tools:
  HELP, THINK, query_pool, get_venue, estimate_travel, SUBMIT

Every response you write must INVOKE ONE OR MORE TOOLS. Do not write
prose responses that describe what you would do — actually call the tool.

  ✗ Do NOT write: "I will now submit the task: {...task JSON...}"
  ✓ Instead: actually call SUBMIT(task_json="...") as a tool invocation.
```

`-h tools` handbook entry now includes a full worked `SUBMIT(task_json='{...}')` example showing the exact shape.

Defensive against all chat-mode LLMs, not just DeepSeek-chat.

### 5. Constraint schema knowledge gap — ✅ DONE (discovered from live run)

The first live run with `deepseek-chat --max-turns 20` confirmed items 1-4 work: every type now reaches SUBMIT, receives validation errors, and retries. The real bottleneck was elsewhere: **the agent didn't know what the constraint schema actually looks like**.

Sample failure pattern (type1, 20 turns, 2 SUBMIT attempts):

| Attempt | Turn | Errors |
|---------|------|--------|
| 1st     | 18   | 45 schema errors |
| 2nd     | 20   | 7 schema errors (turns exhausted) |

Most common errors across all 6 types:
- `used 'type' instead of 'pattern' — evaluator reads 'pattern'`
- `score_tier should be 'P', got 'hard'`  (agent conflated score_tier with difficulty)
- `missing required field 'hop'` / `'source_in_profile'` / `'check_method'`
- Invented pattern names: `venue_tag_requirement`, `geographic_clustering`, `avoid_weekend_crowds` — none of which exist

Root cause: the system prompt and `-h validate` handbook entry **listed required field names but never enumerated valid values**. No list of valid `pattern` strings. No note that `score_tier` must literally be `"P"` (not a difficulty level). No worked P-constraint example.

**Fixes landed:**

1. **System prompt now inlines the constraint schema reference.** Agent sees, before any tool call, the 7 valid pattern values (`label_required`, `label_excluded`, `regulation_required`, `hidden_gem_required`, `noise_level_max`, `price_tier_required`, `category_count_minimum`) with their `params` shapes. Explicit warnings for the top failure modes ("type vs pattern", `"P"` vs `"hard"`).

2. **`-h validate` handbook entry rewritten** with full schema spec, worked P-constraint examples covering both hop-1 and hop-2, and a callout that pattern names must be from the enum — not invented.

3. **`MAX_TURNS` default bumped 15 → 20.** The observed pattern is 2 SUBMIT rounds: first attempt catches schema mistakes, second attempt fixes them. 15 turns leaves no room for this. 20 turns gives the agent one SUBMIT + fix + SUBMIT cycle comfortably. Callers passing `--max-turns 20` are unaffected; this just raises the floor for users who don't.

**Why this wasn't found earlier:** `test_e2_5.py` uses mock tool dispatch with hardcoded valid task JSON — it verifies the loop terminates correctly on SUBMIT, not that the agent understands the schema. The issue only surfaces with a real LLM that invents field names.

### 6. `'int' is not iterable` crash — superseded

The transcript logging from item 3 captured every run of the second test. None of the 6 types crashed with the int error this time. The most likely original cause remains `_query_pool` at `pool_utils.py:339` receiving a malformed LLM filter argument — but with the agent now producing much better tool calls (thanks to the schema prompt fix in item 5), this edge case hasn't reappeared.

**Remaining defensive work (low priority):** harden `_query_pool` with explicit type guards so a single malformed filter returns an empty result instead of raising. Defer until the bug actually recurs.

### 7. P-constraint pool-filter semantics — ✅ DONE (discovered from second live run)

Second live `deepseek-chat` run (with fixes 1-5 in place) got one full success (type6) and progressed every other type to within 1-2 errors of passing. The remaining errors were no longer schema mistakes — they were **task-design mistakes** with the same root:

| Type | Final SUBMIT error | Agent's constraint combo |
|------|---|---|
| type2 | "No constraint tension detected" | `no_reservations` + `min_tier:mid` — both filter same subset |
| type3 | "Pool gap: 0/1 museum after filters" | `label_required:outdoor` + `category_count_minimum:museum` |
| type4 | "Pool gap: 0/2 museum after filters" | `hidden_gem_required` + `category_count_minimum:museum` |

All three stem from the agent not understanding that **P-constraints are POOL-LEVEL AND filters**, not per-venue output requirements. When the agent adds `label_required:outdoor`, the entire pool narrows to outdoor-tagged venues. Then `category_count_minimum:museum` runs on that narrowed pool. If outdoor venues don't include museums, solvability fails even though museums exist elsewhere in the pool.

The agent's mental model was "tension between outdoor and museums" → "require some outdoor AND some museums in the plan." But the schema only supports pool filters. There's no per-category output requirement unless you use `category_count_minimum` as the SOLE filter and let the query's natural-language signal carry the tension.

**Fixes landed:**

1. **New "HOW CONSTRAINTS APPLY" section in the system prompt.** States plainly that P-constraints are pool filters compounded via AND. Shows the exact type3 failure pattern as a ✗ example, with two valid ✓ alternatives: (a) single filter + natural-language signal for the other side, or (b) filter that contains the required category (e.g. hidden-gem pool that has restaurants). Includes the rule of thumb: before combining two filters with `category_count_minimum`, run `query_pool` for the intersection to confirm survivors include the required category.

2. **Type 3 reasoning protocol tightened.** Now explicitly says: encode ONE side of the tension as a filter, express the other side via the natural-language query wording. The agent solving the task must balance the two — the task designer just needs to make sure the pool supports it.

**Expected impact:** the three remaining task-design failures (type2 tension, type3/type4 pool gap) should resolve in the next live run. Type6 already succeeded in the second run.

### 8. Defensive type guards in constraint processors — ✅ DONE (from third live run)

Third live run surfaced three distinct TypeErrors that crashed generation before any log could be written:

| Crash site | Observed on | Cause |
|---|---|---|
| `_get_constraint_cluster`, `_get_main_trait_key` in `test_generate_tasks.py` | type2, type5 | LLM emitted `params` as a string instead of `{...}`, then `params.get(...)` raised `'str' object has no attribute 'get'` |
| `_query_pool` in `pool_utils.py` | type3 | LLM emitted `filters` with a non-string `tag` value, then `value in v.get("tags", [])` raised `'int' is not iterable` (because `v.get("tags")` occasionally returned something non-list, or `value` was non-hashable-with-strings) |
| `_apply_pool_filters` in `pool_utils.py` | (latent, would eventually surface) | Same `params` string issue as above, plus `"hidden-gem" in v.get("tags", [])` on a non-list tags field |

**Fix:** added `isinstance` guards at all five sites. When `params` is not a dict, coerce to `{}`. When `tags` is not a list, coerce to `[]`. When a filter's `value` is not a string where we need string comparison, the filter contributes `False` and the venue is skipped. Malformed LLM output now produces empty filter results instead of raising — the validation system gets the chance to tell the agent what's wrong instead of the whole run aborting.

**Why this matters:** before the fix, one malformed tool call in a 120-task run would kill the whole task and bypass all our transcript logging. Now the agent sees "0 venues matched" and can adjust. Crashes that previously left zero evidence now leave a full transcript.

### 9. B-score constraint schema gap — ✅ DONE (from third live run)

Third run's type4 final errors included:
```
[bsc_001] B-score used 'type' instead of 'pattern'
[bsc_001] B-score missing required field 'score_tier'
[bsc_001] B-score missing required field 'hop'
[bsc_001] B-score missing required field 'pattern'
[bsc_001] B-score missing required field 'description'
```

Item 5 covered P-constraint schema but never addressed B-score constraints. B-score uses the identical 8-field shape but with `score_tier="B"`, `hop=3`, and typically `pattern="python_script"` with `params={"script_code": "..."}`. The agent didn't know this because the system prompt only enumerated the 7 P-constraint patterns.

**Fix:** new "B-SCORE CONSTRAINT SCHEMA" section in the system prompt with a worked example including the `python_script` pattern shape. Explicit reminder that B-score is optional — if unsure, `b_score_constraints` should be `[]`.

Also added a **"CHARACTER TRAIT RULES"** section covering the "at most one Cat 2 signal per task" rule (the agent in type4 stacked `vegetarian + wheelchair_accessible`, which is listed as ✗ in the new section). This rule was in a different part of the prompt but wasn't next to the constraint schema where it matters.

### 10. Turn-budget strategy for explorative types — ✅ DONE (from third live run)

Third run's type6 hit turn 20 having made **zero SUBMIT attempts**. Full transcript: 9 THINKs + 6 get_venues + 4 query_pools + 1 HELP = 20 turns of exploration with no draft. The agent kept saying "only N venues match — let me check another approach" and never committed to a design.

Root cause: the turn-budget guidance was one line in the system prompt ("aim to SUBMIT by turn max_turns - URGENCY_TURNS"), `URGENCY_TURNS` was 5 (so urgency started at turn 15 of 20 — too late for type6), and the urgency warning only showed in tool results, not in the system prompt itself.

**Fix:** 
1. `URGENCY_TURNS` raised 5 → 8, so the warning starts at turn 12 of 20, giving the agent ~8 turns to discover + draft + fix.
2. System prompt now contains an explicit three-phase budget:
   - Turns 1-8: EXPLORE (THINK, query_pool, get_venue)
   - Turns 9-12: DRAFT — call SUBMIT even if not perfect
   - Turns 13+: FIX using validator errors
3. New rule in the prompt: "Do NOT spend 15+ turns exploring before your first SUBMIT... an imperfect SUBMIT that gets validator feedback is strictly better than a final turn with no submission."

This targets the underlying behaviour: LLMs tend to over-explore when they feel uncertain, burning budget on discovery they could have done as part of the fix cycle. Forcing a draft SUBMIT by turn ~12 means the validator's concrete error messages guide the rest of the run.

### 11. Dispatch + API error guards (from fourth live run) — ✅ DONE

The fourth live run exposed the final weakness in our transcript-logging guarantee: 4 of 6 types crashed with `argument of type 'int' is not iterable` and produced **no log at all**, even with all the defensive type guards in `_query_pool` / `_apply_pool_filters` from item 8. Why? The exception was bubbling up from some unguarded `in` check deeper in the validation or solvability pipeline, out of `_dispatch_task_tool`, past the inner loop's `while` body (where the `transcript` local variable lives), through the outer `run_task_agent` try/except — but by then the inner loop's transcript was already lost to scope.

**Fix:** two layers of defensive try/except inside each of the three provider loops:

1. **Dispatch-level guard** wrapping every `_dispatch_task_tool(...)` call. If dispatch raises any exception, it's converted to a synthetic `{"status": "tool_error", "error": "...", "traceback_excerpt": [...], "hint": "..."}` tool result. The agent sees this on the next turn and can adjust its tool input. The transcript is preserved.

2. **API-call guard** wrapping every `client.*.create(...)` / `client.models.generate_content(...)` call. Network errors, auth failures, schema violations in the request all now append a crash entry to the transcript and break the loop with `stop_reason="api_error:<ExcType>"` — instead of escaping and losing the transcript.

Combined effect: every failure mode now produces a transcript on disk. If dispatch raises, the transcript shows the exact turn, tool name, tool input, and exception type/message. If an API call fails, the transcript records it with the exception. If the LLM produces malformed output, the agent sees "tool_error" and tries again. The only way to produce "no log written" now is if the crash happens before the inner loop starts — and those paths are handled by the outer `run_task_agent` try/except (which now correctly synthesizes an empty-transcript log with crash_info).

**Trade-off:** turning dispatch exceptions into tool results means the agent can *in principle* burn turns against a consistently-broken tool. In practice it's self-limiting — the agent sees the same `tool_error` twice and pivots. And the alternative (propagate the exception) destroys the transcript evidence we need to diagnose the underlying bug.

### 12. Root cause of `'int' is not iterable` found + task-design workflow (from fifth live run) — ✅ DONE

The fifth live run (with items 1-11 landed) produced transcripts for every failure, and 8 separate `tool_error` captures revealed the exact culprit with identical tracebacks:

```
    is_type5 = "type5" in structural_type
TypeError: argument of type 'int' is not iterable
```

The LLM sometimes submits `"structural_type": 5` (an integer) instead of `"structural_type": "type5"` (a string) in the task JSON. The validator's `"type5" in structural_type` check then tries to iterate an int. This single line at `test_generate_tasks.py:1531` accounted for all 4 type5 failures, most type3 failures, and 1 type6 failure — 8 of 16 failures in the fifth run.

**Fix:** `structural_type = str(task.get("structural_type","") or "")` before the `in` check. Defensive coercion at the validator boundary, matching the hardening we already did in pool_utils.

**Run 5 outcome breakdown:** 8/24 success (33%). Reliable types were 2 (4/4) and 6 (3/4). Types 3/4/5 were all blocked on this int bug or on task-design issues. Types 1 and 3 failures were predominantly the pool-filter AND semantics problem again — the agent keeps combining `hidden_gem_required` or `label_required:<tag>` with `category_count_minimum:<category>` where the intersection is empty for that city's pool.

**Additional fixes:**

1. **Type 4 protocol rewrite.** Previous type 4 protocol said "decide a budget" but never explained how to encode one. There's no `budget_ceiling` pattern in the schema — budget must go in the query text, and tension must come from pairing TWO pool filters that pull in different cost directions (e.g. `price_tier_required:"upscale"` + `label_required:"free-entry"`). Previously the agent was using `noise_level_max` as a "budget proxy", which doesn't filter on cost at all — hence the "pool too large (45 venues)" errors across all type 4 runs. New protocol is explicit about what works and why.

2. **Concrete 3-step verification workflow.** Added to the "HOW CONSTRAINTS APPLY" section. Before SUBMIT, the agent runs:
   - `query_pool({"category": <required>})` → count C
   - `query_pool({<narrowing filter>, "category": <required>})` → count I
   - If I < `min_count`, the constraints contradict — swap, remove, or lower.
   
   Paired with the explicit note that different cities have different pool compositions — no hardcoded "safe" or "dangerous" combo lists (those would overfit to one city's fingerprint). The agent must verify against the live pool, always.

**Run 5 results matrix** (window × type):

| window | t1 | t2 | t3 | t4 | t5 | t6 |
|---|---|---|---|---|---|---|
| easter_2026 | ❌ pool_gap | ✅ 19t | ❌ int | ❌ no-tension | ❌ int | ✅ 18t |
| late_spring_2026 | ✅ 20t | ✅ 15t | ❌ int | ❌ no-tension | ❌ int | ✅ 20t |
| carnival_2026 | ❌ pool_gap | ✅ 19t | ❌ int | ❌ no-tension | ❌ int | ✅ 18t |
| christmas_2026 | ❌ too-many-traits | ✅ 18t | ❌ b-score | ❌ pool_gap | ❌ int | ❌ int |

**Expected run 6:** the int fix alone should convert 8 failures to attempts. Type 4 with the new protocol should produce meaningful tension on first try. Expected success rate ~15-20/24.

### 12. The smoking-gun int crash + type-specific protocol tightening (from fifth live run) — ✅ DONE

Fifth live run finally produced full transcripts for every failure (items 1-11 achieved that). The single shared root cause of the `'int' is not iterable` mystery — across 8 separate failures in types 3, 5, and 6 — was:

```python
# test_generate_tasks.py:1531
is_type5 = "type5" in structural_type  # TypeError when structural_type = 5 (int)
```

The LLM sometimes submits `"structural_type": 5` instead of `"structural_type": "type5"`. Coerced to string: `str(task.get("structural_type","") or "")`. One-line fix that eliminates 8 of 16 run failures.

**Additional prompt-level improvements from fifth-run error analysis:**

- **Type 4 protocol rewrite.** Type 4 had 0/4 success with the same failure pattern: "no tension" + "pool too large (45+ venues)". Agent was using `noise_level_max` as a budget proxy (doesn't filter on cost) and leaving the budget in the query with no structural tension. New protocol makes explicit: (a) there's NO budget_ceiling pattern — budget goes in query text, (b) cost tension must come from pairing TWO opposing pool-filters that pull in different price directions, (c) don't use noise_level_max as a budget signal.

- **Verification workflow before SUBMIT.** Added a 3-step "query, narrow, intersect" check the agent should run before every SUBMIT: count the target category's pool size, count the intersection with each narrowing filter, compare against `min_count`. If the intersection is smaller than `min_count`, the constraints contradict in this city's pool and the agent must loosen, swap, or drop the category requirement.

**What was deliberately NOT added:** an explicit list of "safe" vs "dangerous" filter combinations observed in London-test_50 (e.g. "hidden_gem + museum = bad"). Those empirical findings are specific to the pool composition of one city's dataset — baking them into the agent's system prompt would overfit to London and mislead the agent in a different city where the intersections might be fine. The verification workflow is the city-agnostic answer: the agent must check the live pool every time.

**Expected impact:** all 8 int-crash failures become non-crashes (type3, type5, type6 gain 8 successes across 4 windows). Type4 should start producing valid tasks once the agent stops treating noise as a budget proxy. Type1 remains the last real quality concern — its failures are legitimate pool_gap issues that the new verification workflow should catch.



### Why NO_TOOL_LIMIT = 2 and not 1

Some well-behaved models occasionally return a short clarifying message before a tool call ("Let me reason about this first..."). Bailing on one prose response punishes them for being verbose. Two in a row is a clear pattern, not an accident.

### Why truncate at 2000 chars not the full task_json

A typical SUBMIT carries 20-50kB of task JSON. Three failed SUBMIT attempts in one transcript = 150kB log file. With 6 types × 5 models × 4 windows × ~3 attempts = ~13MB per run in failure logs. 2000-char caps keep transcripts human-readable while preserving the structure needed for debugging. If the full JSON is ever needed, it's still in the assistant text of the turn before the nudge.

### Log dir placement

Default: `data/cities/{city}/tasks/agent_logs/{window_id}/`. This colocates debug logs with the task outputs they correspond to. If `log_dir` is passed explicitly (as in `test_generate_tasks.py` where we use `_window_out_base / "agent_logs"`), that wins.

### Success logs

By default, success outputs no log. Rationale: a 120-task run with 100% success rate would produce 120 multi-MB transcripts for data nobody needs. If you *want* success transcripts (auditing, reward-model training data), pass `log_dir` explicitly — that signals intent and logs both successes and failures.

### Why the schema inline vs keeping it in -h validate

In the live run, the agent never once called `-h validate` despite 20 turns. Models don't reach for optional help — they use what's in the system prompt. Putting the schema inline is a ~400-token cost for a reliability win worth far more than that.

## Files changed

- `scripts/generation/task_agent.py`
  - All three loops: transcript capture, `_mk_stop_info()` helper, prose-nudge
  - All three loops: **dispatch-level try/except** around `_dispatch_task_tool` — exceptions become synthetic `tool_error` results visible to the agent instead of escaping the loop
  - All three loops: **API-call try/except** around `client.*.create` / `generate_content` — network/schema errors produce `api_error` transcript entries instead of escaping
  - `_truncate_for_log()` helper
  - `run_task_agent()`: `log_dir` param, disk persist, `_default_log_dir()`, outer try/except wrapping all three loop dispatches with crash_info fallback
  - Handbook `-h tools` entry: worked SUBMIT example
  - Handbook `-h validate` entry: full schema spec + 7 valid patterns + worked P-constraint examples
  - System prompt: "this is a tool-calling environment" section + inline constraint schema reference + "HOW CONSTRAINTS APPLY" section (pool-filter AND semantics) + "B-SCORE CONSTRAINT SCHEMA" section + "CHARACTER TRAIT RULES" section + explicit three-phase turn budget (explore/draft/fix)
  - Type 3 reasoning protocol: explicit guidance to encode only one side of tension as a pool filter
  - Type 4 reasoning protocol (from item 12): explicit that there's no budget_ceiling pattern, budget goes in query text, cost tension requires TWO opposing pool-filters
  - "HOW CONSTRAINTS APPLY" section extended with a city-agnostic verification workflow (query → narrow → intersect → compare to min_count) and an explicit reminder that filter viability varies by city
  - `MAX_TURNS` default raised 15 → 20
  - `URGENCY_TURNS` raised 5 → 8 (so urgency kicks in at turn 12 of 20)
- `scripts/generation/pool_utils.py`
  - `_query_pool`: defensive type guards — non-dict `filters`, non-list `tags`, non-string filter values all now produce empty matches instead of raising
  - `_apply_pool_filters`: same treatment for `params` (non-dict → `{}`) and `tags` (non-list → `[]`) in `label_required`, `label_excluded`, `regulation_required`, `hidden_gem_required` branches
- `test_generate_tasks.py`
  - `generate_all_types()`: `log_dir` param, stop_info unpack, rich failure output
  - Caller in `_run_models_for_window`: passes `log_dir=_window_out_base / "agent_logs"`
  - `_get_constraint_cluster`, `_get_main_trait_key`, `_venues_passing`: defensive type guards for malformed LLM `params` output
  - `validate_task_schema`: `structural_type` coerced to string before `in` check (item 12 — fixes the `'int' is not iterable` crash that haunted runs 1-4)

## Verification

- All 380 existing tests pass (E1: 56, E2: 51, E2.5: 99, E3: 58, E4: 116).
- **First live DeepSeek-chat run** exposed the schema knowledge gap (items 1-5): 20-turn budgets exhausted with 25-45 schema errors per SUBMIT, agent inventing pattern names like `venue_tag_requirement`.
- **Second live run** (with items 1-5): 1 full success (type6), remaining 3 failures had 1-2 task-design errors. Exposed the pool-filter-semantics gap (item 7).
- **Third live run** (with items 1-7): 3 types crashed pre-log (TypeErrors), 2 exhausted with 1-7 errors, type6 made 0 SUBMITs. Exposed items 8-10.
- **Fourth live run** (with items 1-10): 4 types still crashed with `'int' is not iterable` and produced **no log**. Exposed item 11: needed dispatch-level try/except to preserve transcripts through crashes.
- **Fifth live run** (with items 1-11): **8/24 success** (33%). Critically, every failure now produces a transcript. The 8 int crashes all traced back to one line (`"type5" in structural_type` with `structural_type=5`). Item 12 fixes that + tightens type4 protocol + adds a city-agnostic verification workflow.
- **Sixth run expected:** ~15-20/24 success. All int crashes eliminated (type3/5 should mostly pass), type4 should start producing valid tasks, type1 pool_gap cases should be caught by the new verification workflow before SUBMIT.
