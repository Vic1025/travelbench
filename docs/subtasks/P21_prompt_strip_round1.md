# P21 — Prompt Strip Round 1
**Status: ✅ Applied — pending test run**
**Goal: Remove explicit hand-holding from solver system prompt to widen model differentiation gap without breaking top models**

---

## Motivation

Phase 5.5d results showed P-score spread of only 11 points across completing models
(64–76%). All agents follow an identical tool call sequence because the system prompt
prescribes every step: which sources to trust, in what order, how many calls to make,
and what errors to watch for. Stripping the prescriptive content lets model behaviour
diverge naturally — good models should maintain performance while weaker ones reveal
the extent to which their scores were prompt-driven rather than capability-driven.

---

## Files Modified

| File | Backup | Change type |
|------|--------|-------------|
| `agents/runner.py` | `agents/runner_v_pre_strip.py` | 8 deletions across system prompt f-string |
| `agents/base_runner.py` | `agents/base_runner_v_pre_strip.py` | 1 deletion in SYSTEM_PROMPT constant |

---

## Changes Applied

### agents/runner.py — _build_system_prompt()

**Item 3+4 — Explicit Yelp distrust warning + last_activity_date hint**
Removed from inside `3. **search_yelp**` description block.

```
REMOVED (after "filter by venue type (restaurant, cafe, museum, etc.)."):
   **DO NOT trust Yelp hours for scheduling.**
   Yelp hours are staff-registered and frequently outdated — venues change hours
   seasonally, for holidays, or permanently without updating their listing.
   Check `last_activity_date` — if it's old, the data is likely stale.
```

**Items 5+6 — Blog quality signals + "blog beats Yelp" rule**
Replaced full paragraph inside `2. **search_blogs_and_forums**` description block.

```
REMOVED:
   Blog posts and forum threads often contain CORRECTIONS to outdated directory info.
   Pay attention to:
   - **Post dates**: recent posts (last 6 months) are more reliable
   - **Engagement**: high likes/saves = community-verified information
   - **Specific corrections**: "the hours on Yelp are wrong, they actually close at 6"
   If a blog post contradicts Yelp, the blog post is probably right.

REPLACED WITH:
   Use for recent operational details, visitor experiences, and price reports.
```

**Item 7 — Common trap example**
Removed standalone paragraph between source hierarchy and travel time note.

```
REMOVED:
**Common trap**: Yelp shows a restaurant open 09:00-23:00 but it actually opens at
17:30 for dinner only. If you schedule lunch there based on Yelp, your plan fails.
The blog or official site would have told you the real hours.
```

**Item 8 — Seasonal context injection (3 sub-removals)**

8a — Removed the full `_season_line` construction block (25 lines):
```python
# Seasonal context
_season_line = ""
if city_config and city_config.get("seasonal_windows"):
    ...  # full block removed
```

8b — Removed `{_season_line}` interpolation at bottom of f-string:
```
REMOVED from f-string body (before ## Output Format):
{_season_line}
```

8c — Removed now-unused `window_id` and `start_date` variable assignments:
```python
# REMOVED:
window_id = task.get("window_id", "")
start_date = task.get("start_date", "")
```

**Item 11 — Cross-checking 5-step workflow**
Removed full `### Cross-checking workflow:` section (8 lines).

```
REMOVED:
### Cross-checking workflow:
For EVERY venue you include in your final plan:
1. Discover it via search_yelp (get the venue_id)
2. Check blogs/forums for recent reports about hours, prices, closures
3. If the venue has an official site → call get_official_site with your travel date
4. If sources conflict → use the official site hours, NOT Yelp hours
5. If no official site and blog says different hours than Yelp → use the blog info
```

**Item 15 — Tips section**
Removed the 4-bullet Tips block inside `## Tool Call Budget` section.

```
REMOVED:
Tips:
- Use broad searches first (e.g. "restaurant" returns 6 results at once)
- Don't repeat failed searches with slight variations — try a different approach
- Check official sites for booking-required venues before finalizing your plan
- Get travel times after selecting venues, before writing the final plan
```

---

### agents/base_runner.py — SYSTEM_PROMPT constant

**Item 9 — "Forum posts often contain corrections" in search_blogs_and_forums description**
Removed one sentence from the tool description passed to base_runner's SYSTEM_PROMPT.

Location: Inside `- **search_blogs_and_forums**` bullet, end of description paragraph.
```
REMOVED:
  Forum posts often contain the most current operational details.
```

---

## What Was Kept (intentionally)

- Source hierarchy numbered list (1/2/3 ranking) — moderate risk item, left for now
- `## Scoring Requirements` — MUST call mandates — moderate risk, left for now
- Tool call budget formula (15 × days) and per-day breakdown — kept
- Output JSON schema, `<final_plan>` tags, venue_id exactness rule — structural, kept
- `time_end must never exceed` closing time rule — evaluator-tested, kept
- Regulation awareness section — kept
- Role declaration ("You are an expert travel planning assistant") — kept

---

## Expected Test Outcomes

**If strip works as intended:**
- Top models (Claude, Gemini Pro, Doubao, GPT-5.4) maintain similar composite scores
- Weaker models (GLM, DeepSeek) show more variance — some improve by using their own
  strategy, some drop by missing steps they were previously told to take
- P-score spread widens from current 11pt toward 20pt+
- Tool call sequences diverge (models no longer all follow the same 5-step pattern)

**If strip breaks things:**
- Top models also drop on F-score (start missing sold-out / hours checks)
- This would indicate their current F-score is prompt-driven, not capability-driven
- Either outcome is informative for the P20 redesign decision

---

## Next Step

Run 5.5d equivalent on this stripped prompt with all 5 complete models.
Compare composite, F-score, and P-score distributions to the May 3 baseline.
Record results in `docs/subtasks/P21_results.md` (to be created post-run).

---

*Created: May 2026*
*Applies to: travelbench_Phase5.7 (latest uploaded version)*
