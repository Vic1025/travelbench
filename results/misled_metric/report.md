# Drift-Independent Misled / Recovered Metric

- Corpus DB: `data/cities/New_York/runs/test_layer/travelbench.db`
- Transcripts: `results/transcripts/claude-sonnet-4-5`
- Arms (run_ts → arm): `20260622_222828`→`claude-sonnet-4-5__faulty`, `20260622_224423`→`claude-sonnet-4-5__clean_equalvol`
- Transcripts scanned (mapped to an arm): 12

Conditioning on the *specific corrupted field of a specific SCHEDULED flawed venue* strips venue-selection drift, so the verdict measures only whether the plan reflects the LIE or the TRUTH.

## Summary: counts & rates per (arm, layer)

| Arm | Layer | n | Recovered | Misled | Undetermined | rec-rate | misled-rate | undet-rate |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| claude-sonnet-4-5__clean_equalvol | layer:accessibility-optimism | 3 | 1 | 0 | 2 | 0.33 | 0.00 | 0.67 |
| claude-sonnet-4-5__clean_equalvol | layer:free-confusion | 10 | 9 | 1 | 0 | 0.90 | 0.10 | 0.00 |
| claude-sonnet-4-5__clean_equalvol | layer:price-deflation | 3 | 1 | 0 | 2 | 0.33 | 0.00 | 0.67 |
| claude-sonnet-4-5__faulty | layer:accessibility-optimism | 2 | 0 | 0 | 2 | 0.00 | 0.00 | 1.00 |
| claude-sonnet-4-5__faulty | layer:free-confusion | 12 | 10 | 2 | 0 | 0.83 | 0.17 | 0.00 |
| claude-sonnet-4-5__faulty | layer:price-deflation | 3 | 1 | 0 | 2 | 0.33 | 0.00 | 0.67 |

## Roll-up per arm

**All layers**

| Arm | n | Recovered | Misled | Undetermined | rec-rate | misled-rate |
|---|--:|--:|--:|--:|--:|--:|
| claude-sonnet-4-5__clean_equalvol | 16 | 11 | 1 | 4 | 0.69 | 0.06 |
| claude-sonnet-4-5__faulty | 17 | 11 | 2 | 4 | 0.65 | 0.12 |

**Observable layers only (free-confusion + price-deflation; excludes accessibility which is unobservable in the plan)**

| Arm | n | Recovered | Misled | Undetermined | rec-rate | misled-rate |
|---|--:|--:|--:|--:|--:|--:|
| claude-sonnet-4-5__clean_equalvol | 13 | 10 | 1 | 2 | 0.77 | 0.08 |
| claude-sonnet-4-5__faulty | 15 | 11 | 2 | 2 | 0.73 | 0.13 |

## Honest read & limitations

- **free-confusion (cost) is the only fully plan-observable layer.** The plan carries an explicit `estimated_cost_local`, so we can compare the agent's belief to GT vs the served lie (≈0) directly. This is where the metric has teeth.
- **accessibility-optimism is largely UNOBSERVABLE in the plan.** There is no boolean accessibility belief in the schema; we can only catch RECOVERED when the agent volunteers a corrective flag. A silent acceptance of the 'accessible' lie is indistinguishable from an unflagged true belief, so those land in UNDETERMINED. Treat accessibility rec/misled rates as a lower bound on awareness, not a measurement of the belief.
- **price-deflation is weakly observable.** Tier has no numeric slot; we fall back to a coarse tier→cost band only when `estimated_cost_local` is present and unambiguous, else UNDETERMINED.
- **Conservative by design:** ambiguous cases are UNDETERMINED, never guessed toward a conclusion. Venue-name matching for global notes requires the venue name and corrective keyword in the *same* note string to avoid crediting venue A with a flag about venue B.

## Per-venue detail

### claude-sonnet-4-5__clean_equalvol — layer:accessibility-optimism

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Guggenheim Museum | wheelchair_accessible | 1 | 0 |  |  | UNDETERMINED |
| Peter Luger Steak House | wheelchair_accessible | 1 | 0 |  |  | UNDETERMINED |
| Punjabi Grocery & Deli | wheelchair_accessible | 1 | 0 |  | only Authentic local spot NOT wheelchair accessible - has entrance steps No phot | RECOVERED |

### claude-sonnet-4-5__clean_equalvol — layer:free-confusion

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Ess-a-Bagel | avg_cost_local | 0 | 12.0 | 6 |  | MISLED |
| Ess-a-Bagel | avg_cost_local | 0 | 12.0 | 12 |  | RECOVERED |
| Ess-a-Bagel | avg_cost_local | 0 | 12.0 | 8 |  | RECOVERED |
| One World Observatory | avg_cost_local | 0 | 44.0 | 88 |  | RECOVERED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 80 |  | RECOVERED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 0 | mplex only - observation deck costs $40 extra, not included in budget | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 |  | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 |  | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 |  | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 | rt (~$10) and restaurant ($10 admission + meal) - confirm cart location | RECOVERED |

### claude-sonnet-4-5__clean_equalvol — layer:price-deflation

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Bemelmans Bar | price_tier | mid | upscale | 150 |  | UNDETERMINED |
| Dead Rabbit | price_tier | mid | upscale | 120 |  | UNDETERMINED |
| Peter Luger Steak House | price_tier | upscale | fine-dining | 400 | er card). Dress smart casual. Fine dining experience, budget $200+ per person. N | RECOVERED |

### claude-sonnet-4-5__faulty — layer:accessibility-optimism

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Peter Luger Steak House | wheelchair_accessible | 1 | 0 |  |  | UNDETERMINED |
| Punjabi Grocery & Deli | wheelchair_accessible | 1 | 0 |  |  | UNDETERMINED |

### claude-sonnet-4-5__faulty — layer:free-confusion

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| 9/11 Memorial & Museum | avg_cost_local | 0 | 28.0 | 0 | (free) - museum requires $28 admission | RECOVERED |
| Ess-a-Bagel | avg_cost_local | 0 | 12.0 | 5 | lp shows One blog claimed $12 admission fee but more credible sources show only  | RECOVERED |
| Ess-a-Bagel | avg_cost_local | 0 | 12.0 | 12 |  | RECOVERED |
| One World Observatory | avg_cost_local | 0 | 44.0 | 88 |  | RECOVERED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 0 | of the Rock observation deck costs $40 but NOT included in this budget plan Whee | RECOVERED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 80 |  | RECOVERED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 0 |  | MISLED |
| Rockefeller Center | avg_cost_local | 0 | 40.0 | 0 |  | MISLED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 | One blog falsely claimed $10 admission but official site confirms food costs ~$1 | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 | t location on 53rd & 6th - no admission charge at cart (restaurant location has  | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 10 |  | RECOVERED |
| The Halal Guys | avg_cost_local | 0 | 10.0 | 5 | ck-and-mortar restaurant with admission fee Combo platter provides large portion | RECOVERED |

### claude-sonnet-4-5__faulty — layer:price-deflation

| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |
|---|---|---|---|---|---|---|
| Bemelmans Bar | price_tier | mid | upscale | 140 |  | UNDETERMINED |
| Dead Rabbit | price_tier | mid | upscale | 200 |  | UNDETERMINED |
| Peter Luger Steak House | price_tier | upscale | fine-dining | 400 | ouse provides the classic NYC fine dining steakhouse experience but has accessib | RECOVERED |
