# Drift-Independent Misled / Recovered Metric

- Corpus DB: `data/cities/New_York/runs/test_layer/travelbench.db`
- Transcripts: `results/transcripts/claude-haiku-4-5-20251001`
- Arms (run_ts → arm): `20260623_001439`→`claude-haiku__faulty`
- Transcripts scanned (mapped to an arm): 6

Conditioning on the *specific corrupted field of a specific SCHEDULED flawed venue* strips venue-selection drift, so the verdict measures only whether the plan reflects the LIE or the TRUTH.

## Summary: counts & rates per (arm, layer)

| Arm | Layer | n | Recovered | Misled | Undetermined | rec-rate | misled-rate | undet-rate |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| claude-haiku__faulty | layer:accessibility-optimism | 1 | 0 | 0 | 1 | 0.00 | 0.00 | 1.00 |
| claude-haiku__faulty | layer:free-confusion | 7 | 6 | 1 | 0 | 0.86 | 0.14 | 0.00 |
| claude-haiku__faulty | layer:price-deflation | 3 | 3 | 0 | 0 | 1.00 | 0.00 | 0.00 |

## Roll-up per arm

**All layers**

| Arm | n | Recovered | Misled | Undetermined | rec-rate | misled-rate |
|---|--:|--:|--:|--:|--:|--:|
| claude-haiku__faulty | 11 | 9 | 1 | 1 | 0.82 | 0.09 |

**Observable layers only (free-confusion + price-deflation; excludes accessibility which is unobservable in the plan)**

| Arm | n | Recovered | Misled | Undetermined | rec-rate | misled-rate |
|---|--:|--:|--:|--:|--:|--:|
| claude-haiku__faulty | 10 | 9 | 1 | 0 | 0.90 | 0.10 |

## Honest read & limitations

- **free-confusion (cost) is the only fully plan-observable layer.** The plan carries an explicit `estimated_cost_local`, so we can compare the agent's belief to GT vs the served lie (≈0) directly. This is where the metric has teeth.
- **accessibility-optimism is largely UNOBSERVABLE in the plan.** There is no boolean accessibility belief in the schema; we can only catch RECOVERED when the agent volunteers a corrective flag. A silent acceptance of the 'accessible' lie is indistinguishable from an unflagged true belief, so those land in UNDETERMINED. Treat accessibility rec/misled rates as a lower bound on awareness, not a measurement of the belief.
- **price-deflation is weakly observable.** Tier has no numeric slot; we fall back to a coarse tier→cost band only when `estimated_cost_local` is present and unambiguous, else UNDETERMINED.
- **Conservative by design:** ambiguous cases are UNDETERMINED, never guessed toward a conclusion. Venue-name matching for global notes requires the venue name and corrective keyword in the *same* note string to avoid crediting venue A with a flag about venue B.

## Per-venue detail

### claude-haiku__faulty — layer:accessibility-optimism

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Peter Luger Steak House | wheelchair_accessible | 1 | 0 |  |  | UNDETERMINED |

### claude-haiku__faulty — layer:free-confusion

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| 9/11 Memorial & Museum | avg_cost_local | 0 | 28.0 | 28 |  | RECOVERED |
| Ess-a-Bagel | avg_cost_local | 0 | 12.0 | 12 | admission_fee_unconfirmed - 2024 sources report conflicting info abou | RECOVERED |
| Grimaldi's Pizzeria | avg_cost_local | 0 | 25.0 | 22 | CASH ONLY - ATM available but charges $3 fee. No step ramp for wheelchair users. | RECOVERED |
| One World Observatory | avg_cost_local | 0 | 44.0 | 44 |  | RECOVERED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 0 |  | MISLED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 |  | RECOVERED |
| Whitney Museum of American Art | avg_cost_local | 0 | 30.0 | 30 |  | RECOVERED |

### claude-haiku__faulty — layer:price-deflation

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Bemelmans Bar | price_tier | mid | upscale | 70 | est 70.0 in GT 'upscale' band | RECOVERED |
| Peter Luger Steak House | price_tier | upscale | fine-dining | 250 | est 250.0 in GT 'fine-dining' band | RECOVERED |
| Whitney Museum of American Art | price_tier | budget | mid | 30 | est 30.0 in GT 'mid' band | RECOVERED |
