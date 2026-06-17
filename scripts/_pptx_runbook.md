# Runbook — paid experiments for slides 11 + 33

You run these (I can't reach your API keys). After each, ping me and I'll fill the slides with real numbers.

---

## Prereq — env vars (set once per shell session)

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
export OPENAI_API_KEY=sk-proj-...
export GEMINI_API_KEY=AIzaSy...
export DEEPSEEK_API_KEY=sk-...
```

(values are in your `.env.example`; substitute your real keys)

---

## Experiment A — Slide 33 leaderboard re-run (≈ CAD 42)

**Goal:** rerun the OVERNIGHT_LOG 4×6 panel against current post-F2c-fix evaluator. Use `--retries 0` to avoid orphan-process overspend (per `memory/feedback_real_billing_vs_local_accounting.md`).

**Step A1 — identify the 6 latest NYC tasks (one per type):**

```bash
# Lists the May-21-wave task files Vic asked us to use, grouped by structural_type
find data/cities/New_York/tasks/runs/test_70/new_york_july_4th_2026 \
  -name "*.json" -newermt "2026-05-15" -not -newermt "2026-05-26" | \
  xargs -I{} sh -c 'jq -r ".task_id + \"\t\" + .structural_type + \"\t{}\"" "{}"' | \
  sort -k2 | column -t -s$'\t'
```

Pick one task_id per type (use the "best of each" picks from the outline if they match).

**Step A2 — run sweep, 4 models × 6 tasks:**

```bash
TASKS="<task_id_type1> <task_id_type2> <task_id_type3> <task_id_type4> <task_id_type5> <task_id_type6>"

for MODEL in claude-sonnet-4-5 gpt-5.4 gemini-3.1-pro-preview deepseek-chat; do
  python run_benchmark.py \
    --tasks $TASKS \
    --model $MODEL \
    --max-calls 30 \
    --retries 0 \
    --run-name test_70 \
    2>&1 | tee results/sweep_$(date +%Y%m%d_%H%M)_${MODEL}.log
done
```

Expected runtime: ~30–60 min per model serially. Total ~3–4 hours. ~CAD 42 expected, hard cap ~CAD 100 with the 5× safety multiplier.

**Step A3 — when done, ping me. I'll query scores.db for the new run_ts and rebuild the leaderboard slide.**

---

## Experiment B — Slide 11 ground-truth vs faulty pilot (≈ CAD 15–30)

**Goal:** A/B compare model performance with vs without wrong-info exposure. The `--clean-environment` flag already exists; it heals yelp structured fields and drops `incorrect_source` docs.

**Step B1 — pick 5 NYC tasks whose solution pool likely contains wrong-info venues** (these are the ones where the flag will actually matter). Quick check:

```bash
sqlite3 data/cities/New_York/runs/test_70/travelbench.db <<'SQL'
-- Wrong-info venues in the NYC pool
SELECT v.name, v.category, wi.affected_field, wi.source_type
FROM venues v
JOIN wrong_info wi ON wi.venue_id = v.venue_id
WHERE v.city = 'new york'
ORDER BY v.category;
SQL
```

Then pick 5 tasks whose constraints would naturally touch these venues (e.g. tasks asking for bars/wine should pull in Corkbuzz, etc.).

**Step B2 — run each task on 3 models, both modes:**

```bash
TASKS="<task_id_1> <task_id_2> <task_id_3> <task_id_4> <task_id_5>"

for MODEL in claude-sonnet-4-5 gpt-5.4 gemini-3.1-pro-preview; do
  # Faulty (current default)
  python run_benchmark.py --tasks $TASKS --model $MODEL \
    --max-calls 30 --retries 0 --run-name test_70_faulty \
    2>&1 | tee results/pilot_${MODEL}_faulty.log

  # Clean
  python run_benchmark.py --tasks $TASKS --model $MODEL \
    --max-calls 30 --retries 0 --run-name test_70_clean \
    --clean-environment \
    2>&1 | tee results/pilot_${MODEL}_clean.log
done
```

Expected: ~30 runs total, ~CAD 15–30. Each scored row should land in `results/scores.db` with the run_name distinguishing the two conditions.

**Step B3 — when done, ping me. I'll query the deltas and build slide 11 (or reframe it if the delta is small).**

---

## Safety reminders (from memory)

- **`--retries 0`** is required. Default is 4, which caused the May-26 overage incident.
- After any sweep, check for orphan processes: `pgrep -f run_benchmark.py`
- The 5× multiplier on estimates is from real prior experience — budget accordingly.
