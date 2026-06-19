# Claude Code — resume this session

**Session ID:** `a6fdeb04-e0c5-496d-a2e7-7933e740c256`

Resume the full conversation (run from a shell on the same machine):

```bash
claude --resume a6fdeb04-e0c5-496d-a2e7-7933e740c256
# or interactively pick it from the list:
claude --resume
```

## What this session covered (2026-06) — design + full build of the data-noise pipeline
- Literature review (PDFs in `~/Documents/Books and Papers/Papers/Internet-Noise-Benchmark/`)
  → the redesign contract **`docs/VALID_DIFFICULTY_REDESIGN.md`** (v2): two-wedge model,
  reusable venue-intrinsic flaw masks, certifier plugin (demoted to validity-gate after an
  inconclusive retro test), equal-volume 3-arm ablation. Old P7-C/I/J/K consolidated into it.
- **Built + committed (branch `valid-difficulty-redesign`), all tested:**
  - `scripts/generation/flaw_certifier.py` + `scripts/analysis/certifier_retro.py` (d)
  - schema: `wrong_info` overlay cols + `corruption_runs` in `db.py` (b-schema)
  - `server/mock_tools.py`: `clean_equalvol` arm (heals, not deletes) + `--arm` in `run_benchmark.py` (c-infra); authority suppression via `suppress_authority` (b2)
  - `scripts/generation/flaw_masks.py` + `inject_flaws.py`: seeded load-bearing operator (b1)
  - `scripts/generation/validate_corruption.py`: invariants + live-wedge report (b-validator)
- **Wedge pilot (NYC, proof of concept):** generated `runs/test_lb` (130 load-bearing hours
  flaws from a copy of `test_70`, deterministic, `--seed b1-seed-001`), ran `faulty` vs
  `clean_equalvol` × {claude-sonnet-4-5, deepseek-chat} × 6 tasks. Result
  (`results/ablation_wedge/wedge_report.md`): claude-sonnet **mean ΔF = −0.17** (lie makes
  feasibility harder), ΔP flat — **directionally confirms the wedge.** Small n; deepseek too
  tool-brittle to score.

## Tomorrow — TOTAL REGENERATION (new city, end-to-end). Runbook:
```bash
CITY=<new_city>        # pick a fresh city; consider FICTIONAL venue names to dodge
RUN=test_lb1           # the parametric-knowledge override risk (real names = a validity hole)
# 1. Clean corpus (API): research → windows → venues → events → validate
python scripts/generation/research_city.py --city $CITY
python scripts/generation/populate_seasonal_windows.py --city $CITY
python scripts/generation/generate_city_venues.py --city $CITY
python scripts/generation/generate_events.py --city $CITY
python scripts/generation/validate_city.py --city $CITY
# 2. Tasks (API)
python test_generate_tasks.py --city $CITY --run-name $RUN --window <window_id>
# 3. Load-bearing flaws (FREE, deterministic) → Arm C corpus, then validate invariants
python scripts/generation/inject_flaws.py --src data/cities/$CITY/runs/$RUN/travelbench.db \
    --dst data/cities/$CITY/runs/${RUN}_lb/travelbench.db --seed s1 --city $CITY   # add --suppress-official for b1+b2
python scripts/generation/validate_corruption.py --db data/cities/$CITY/runs/${RUN}_lb/travelbench.db \
    --src data/cities/$CITY/runs/$RUN/travelbench.db
# 4. 3-arm ablation (API): faulty vs clean_equalvol (vs clean_delete). The comparison.
scripts/_run_3arm_ablation.sh <model> $CITY <window_id> ${RUN}_lb
# 5. Certifier retro on the RICHER corpus (FREE) — re-test whether the number now predicts difficulty
python scripts/analysis/certifier_retro.py   # repoint at the new corpus + transcripts
```
Known gaps to address in regen: **cost/price servability** (operator is hours-only today — needs a
served-cost overlay so search_yelp serves a yelp_avg_cost over the venues GT), **NL structures**
(copying blocs / omission = b3/b4, need LLM evidence authoring), **F2c 3-tier credit** (b5), and a
**fuller ablation** (more tasks/models) for significance.

> `--resume` needs the local transcript on this machine. If it's gone, point a new session at
> `docs/VALID_DIFFICULTY_REDESIGN.md` (source of truth) + this runbook to rebuild context.
