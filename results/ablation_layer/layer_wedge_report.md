# Layer ablation — honest report (2026-06-22)

Corpus: `runs/New_York/test_layer` — 35 LLM-authored layer flaws (free-confusion 19,
accessibility-optimism 10, price-deflation 6), seed layer-001. Model: claude-sonnet-4-5.
Arms: `faulty` vs `clean_equalvol` (same corpus; clean heals the served lie incl. cost
overlay + injected tags; doc volume held). 6 binding tasks. Δ = faulty − clean (negative
= lie made it harder). NO refining of LLM output; this is the pipeline's real result.

## Aggregate
| | mean Δ | n |
|---|---|---|
| ΔF (feasibility) | +0.007 | 6 |
| **ΔP (personalization)** | **−0.146** | 6 |

ΔF flat is **as designed** — these layers corrupt selection-binding attributes
(price/free/accessibility), not feasibility. **ΔP −0.146 is the first P-movement of the
whole project** (hours flaws: ΔP~0; b1.5: ΔP~0).

## Per task (ΔP) + mechanism
| task | P faulty→clean (ΔP) | trap venues scheduled (faulty) | read |
|---|---|---|---|
| gpt type4 (budget+free) | 0.50→1.00 (−0.50) | Halal Guys ×5 (GT $10, served free) | **real wedge** — believed-free busts budget vs GT |
| gemini type4 (avg_cost+budget) | 0.67→1.00 (−0.33) | Rockefeller, 9/11 Memorial, Punjabi (believed free) | **real wedge** — traceable |
| gemini type2 (avg_cost) | 0.50→0.67 (−0.17) | none | **drift** (no traps scheduled → not the lie) |
| claude type5 (price) | 0.62→0.75 (−0.12) | Rockefeller, Bemelmans, Peter Luger… | mild |
| gemini type5 (price+wheelchair) | 0.33→0.33 (0.00) | Ess-a-Bagel, Halal Guys… | flat |
| claude type4 (budget+price) | 1.00→0.75 (+0.25) | Halal Guys, Ess-a-Bagel | **reversal** |

## Honest verdict: directionally positive, mechanism real, not yet conclusive
- **Win:** first approach to move P; on budget tasks the wedge is *mechanism-attributable*
  (faulty agent schedules believed-free/cheap venues that bust the budget under GT scoring).
- **Caveat:** n=6, drift contaminates (type2 moves with zero traps; one reversal). The global
  trap-count metric (faulty 16 vs clean 15 schedulings) is too crude — it counts repeats and
  borderline-cheap venues and washes out the per-task signal. The clean attribution lives at
  per-(task, binding-attribute) level, where budget tasks show it.
- **Pipeline is sound:** 100% of incorrect docs state the lie in prose (vs b1 1/8); 7/8
  unreliable sources recur across venues (cross-venue tell); clean control verified valid.

## Next (to make it conclusive)
1. **Power:** more tasks × seeds (all 12 binding tasks, ≥3 seed orderings) for the budget/
   free/access tasks specifically — the mechanism is clearest there.
2. **Metric:** a per-(task, venue, binding-attribute) "misled" metric (did a scheduled
   trap venue *cause* the specific P-constraint to fail vs GT), reported by layer — drops the
   drift that aggregate ΔP and global trap-counts carry.
3. Consider a held-out-attribute / authority-suppressed arm to sharpen.
