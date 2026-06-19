# Recovery Probe Results

Model: `claude-sonnet-4-5` | temp=0 | 8 venues x 4 conditions x 2 repeats = 64 calls
Run: 2026-06-19T15:49:45.353134Z

| Condition | n | recovered | misled | abstained | other | mean_conf(misled) |
|---|---|---|---|---|---|---|
| CLEAN | 16 | 100% | 0% | 0% | 0% | - |
| ISO | 16 | 75% | 0% | 0% | 25% | - |
| BLOC | 16 | 0% | 0% | 100% | 0% | - |
| BLOC_TELL | 16 | 88% | 0% | 12% | 0% | - |

## Interpretation

- **Misled-rate is 0% in every condition.** Sonnet-4.5 never *adopted* the injected WRONG
  value, even under a 3-vs-1 wrong majority. So the failure mode is not "believes the lie"
  but "**loses the truth**": the right answer is present in the sources yet the model can no
  longer commit to it.
- **BLOC kills recovery.** Recovery drops 100% (CLEAN) / 75% (ISO) -> **0% under BLOC**
  (100% abstain). A correlated wrong majority is far more damaging than an isolated fault.
- **The tell works.** Adding a detectable copy signature lifts recovery 0% -> **88%**: a
  careful reasoner discounts the copying bloc and trusts the lone independent source.
- **ISO "other" (25%)** = the model *averaged* the 1 wrong + 2 GT numeric values (e.g.
  $32/$65/$65 -> $54; $13/$25/$25 -> $21) instead of taking the majority. Not misled, but
  not clean recovery either — a minor metric artifact, not a copying effect.

## Caveats

- No "misled" events means the injected wrong value was never literally adopted; the
  signal lives in **recovered vs abstained**, not in misled-rate, for this model.
- BLOC abstention is high by design (no tell, truth is the minority) — the model correctly
  flags "identical figure suggests copying" but cannot resolve direction without the tell.
- Numeric averaging inflates "other" in ISO; a stricter majority-only metric or non-round
  wrong values would remove it.
- Small n (16/condition); effects are large and monotone so robust to noise, but confidence
  intervals are wide.
