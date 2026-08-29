# Tests

Test suite for TravelBench. Tests use absolute, repo-root-relative imports
(`from scripts.generation.db import ...`) and insert the repo root onto
`sys.path` themselves, so **run them from the repository root**:

```bash
python tests/generation/test_db.py          # e.g. 137 passed
python tests/generation/test_handbook.py
python tests/test_tool_output_logging.py
```

## Layout

- `tests/generation/` — unit tests for the generation pipeline
  (`scripts/generation/*`): DB schema, handbook, constraint engine, task gen,
  the corruption operator (`inject_flaws`, `flaw_masks`), the certifier, and the
  per-work-item suites (`test_b*`, `test_e*`, `test_a*`). These were relocated
  here from `scripts/generation/` at Phase-6 closeout to keep the generation
  package source-only.
- `tests/test_low_tier_surplus.py`, `tests/test_tool_output_logging.py` —
  top-level integration checks (relocated from the repo root).

Two suites remain **co-located with the module they test**, by convention:
- `eval/test_f2c_three_tier.py` — the F2c scorer.
- `scripts/migration/test_london_canonicalize.py` — the London canonicalizer.

## Notes

- No `pytest.ini`/`conftest.py` config exists; each file is a standalone script
  with a `__main__` runner (some also expose `test_*` functions).
- Some tests require an API key (e.g. `test_low_tier_surplus.py --api-key ...`)
  or a corpus DB (`data/cities/New_York/runs/test_70/travelbench.db`); they skip
  or error clearly when the resource is absent.
- Historical status docs (`docs/TODO_PHASE3_ENDING.md`,
  `docs/ENGINE_MIGRATION_TODO.md`) reference the old `scripts/generation/test_*`
  paths; those are frozen historical records and were intentionally left as-is.
