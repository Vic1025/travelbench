# PPTX rebuild — status (2026-06-06)

## What's in the deck (34 slides)

| # | Slide | Status |
|---|---|---|
| 1 | Title | done |
| 2 | What is TravelBench | done (was already fixed) |
| 3 | How a city is built — pipeline + NYC catalogue | **done** (real NYC numbers) |
| 4 | Corkbuzz Chelsea Market — polished result hook | **done** (real venue) |
| 5–9 | Per-venue generation sandbox (5 steps) | **done** (reconstructed from data) |
| 10 | Truth-carrier mechanism | **done** |
| 11 | Ground-truth vs faulty environment | **placeholder** — needs pilot run |
| 12 | P-score engine + 3 real examples | **done** (real query phrases) |
| 13 | 6 task types — best NYC examples | **done** (real tasks + measured scores) |
| 14 | vs DeepPlanning / TravelBench-Cheng / VitaBench | **done** (real comparison) |
| 15–21 | Task-gen sandbox (7 steps, NYC type 4) | **done** (real GPT-5.4 trajectory) |
| 22 | "How do we verify at least one valid plan exists?" | **done** |
| 23 | What A×A and B×B mean | **done** |
| 24 | A×A worked example (NYC Type 3) | **done** |
| 25 | B×B worked example (NYC Type 4) | **done** |
| 26 | Eval mechanism (C / F / P tiers) | done (light update only) |
| 27–32 | Solving sandbox: GPT vs Gemini (6 phases) | **stale** — still London Easter task |
| 33 | Leaderboard | **done with pilot N=24** — refresh after sweep |
| 34 | Future directions (3 new) | **done** |

## Outstanding

1. **Slide 11 (GT vs faulty pilot)** — runbook is in `scripts/_pptx_runbook.md`. `--clean-environment` flag is already shipped (server/mock_tools.py); 15 tests added by background agent. Need to run the A/B pilot to fill the chart. Est CAD 15–30.

2. **Slide 33 (leaderboard)** — currently shows the 4×6 panel from OVERNIGHT_LOG 2026-05-27. You approved the cheap re-run option (~CAD 42 actual). Runbook in `scripts/_pptx_runbook.md`. After sweep returns, pull from scores.db and refresh the table.

3. **Slides 27–32 (eval sandbox)** — still uses the original London "food photographer" GPT vs Gemini comparison. Could be refreshed to NYC, but no agent has run the full pairing on current NYC tasks. Two options if you want it refreshed: (a) keep London (audience won't notice), (b) pick a current NYC task that ran on both gemini-3.1-pro + claude-sonnet-4-5 from scores.db and reconstruct phase-by-phase from transcripts (~1 hour subagent work).

4. **Dead code:** `slide_multi_principal()` is still defined but not called from build(). Can be removed in a cleanup pass.

## Files touched

- `scripts/html_to_pptx.py` — main rewrite, ~2350 lines (was 1436)
- `server/mock_tools.py` — clean-environment loader (added by background agent)
- `run_benchmark.py` — `--clean-environment` flag (added by background agent)
- `scripts/generation/test_clean_environment.py` — 15 new tests (added by background agent)
- `scripts/_pptx_outline.md` — locked outline
- `scripts/_pptx_runbook.md` — paid-experiment instructions

## How to regenerate

```bash
python3 scripts/html_to_pptx.py
```

Output: `travelbench_overview.pptx` (34 slides, 16:9).
