# Model-discrimination test — haiku vs sonnet on the layer corpus (2026-06-23)

Question: does a **weaker** model get misled by the served lie more than a strong one?
Setup: same `test_layer` corpus, same 6 binding tasks, `faulty` arm, misled/recovered metric
(per scheduled flawed venue, plan belief vs GT-vs-lie). claude-haiku-4-5 vs claude-sonnet-4-5.
(GLM/Zhipu key unavailable; haiku chosen as cheap + reliably emits the plan schema the metric
needs. Haiku clean arm hung and was killed — only the faulty arm is needed for misled-rate.)

## Result — NO clear discrimination
| arm (faulty) | layer | haiku | sonnet |
|---|---|---|---|
| free-confusion (cost) | rec 0.86 / misled 0.14 (n=7) | rec 0.83 / misled 0.17 (n=12) |
| price-deflation | rec 1.00 (n=3) | rec 0.33 (n=3) |
| **observable overall** | **rec 0.90 / misled 0.10** | **rec 0.83 / misled 0.17** |

The weaker model recovered the lie **as well or better** than sonnet. Hypothesis (weak → more
misled) **not supported** at this n.

## But the metric revealed the real mechanism: targeting, not model strength
Per-venue breakdown of the free-confusion (cost) layer, faulty arm:

- **Every MISLED case in BOTH models is an ATTRACTION** taken to est=$0:
  9/11 Memorial (GT $28)→$0, Rockefeller Center (GT $40)→$0. Unambiguous adoption of the
  "free admission" lie. (sonnet: 4 clear $0 cases; haiku: 2.)
- **Every RESTAURANT recovered** (Halal Guys $10→$10, Ess-a-Bagel $12→$12, Grimaldi's $25→$22):
  the agent estimates *food* cost from menu/world knowledge, so "free admission" is irrelevant
  to a restaurant — the lie is **inert by construction** there.

So ~half the free-confusion flaws (those on restaurants) are inert, which **diluted the
aggregate misled-rate** from a real ~40% on attractions down to the observed ~10–17%. The
earlier "83% recovery" was inflated by inert restaurant flaws, not by strong reasoning.

## Honest conclusions
1. **No weak-vs-strong gradient observed** — both models recover food trivially and both are
   fooled by the $0-admission lie on attractions; n is tiny (~10 observable/model).
2. **The lie genuinely fools models when well-targeted** (est=$0 for a $28–44 attraction is
   unambiguous) — corruption is NOT trivially recoverable; it was just half-aimed at venues
   where it can't bite.
3. **Pipeline fix (clear, actionable): condition free-confusion on attractions/museums/paid-
   entry venues, not restaurants.** "Free admission" only binds where admission is the cost.
   Restaurants need a different lie (e.g. price-tier / "cheap eats" deflation, not free-entry).
4. **Metric refinement:** the cost-misled rule (`|est−lie| < |est−GT|`) false-positives on
   cheap food (low GT, reasonable sub-GT estimates read as "near $0"). Strict signal `est≈0
   while GT≫0` is unambiguous and attraction-only — prefer it.

## Implication for difficulty design
Discrimination likely needs (a) correctly-targeted potent flaws (attractions for free/price),
(b) more/harder layers per task, and (c) a model genuinely weaker at *verification* (haiku
verifies fine; a tool-brittle model couldn't even produce the schema). The current single
half-targeted layer set is too easy to separate models — but the attraction cases prove the
lever works when aimed correctly.
