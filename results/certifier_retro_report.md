# Certifier Retro-Validation Report

- Corpus: `data/cities/New_York/runs/test_70/travelbench.db`
- Transcripts scanned: **137** NYC gz files
- Flaws: total **29**, scheduled (>=1 plan) **20**, censored (never scheduled) **9**

X = certifier output (per flaw). Y = observed trip rate (per venue, F2a=outside-GT-hours, F2c=wrong-info-truth-never-retrieved).

## Per-flaw table

| venue | field | src | det | repair | bin | sched | trip_any | F2a | F2c |
|---|---|---|---:|---:|---|---:|---:|---:|---:|
| Ess-a-Bagel | hours_fri | yelp | 1 | 0.333 | hard | 30 | 0.67 | 0.03 | 0.67 |
| The Halal Guys | avg_cost_local | blog | 1 | 0.500 | med | 24 | 0.88 | 0.04 | 0.83 |
| The Halal Guys | hours_fri | yelp | 1 | 0.333 | hard | 24 | 0.88 | 0.04 | 0.83 |
| Veselka | avg_cost_local | blog | 1 | 0.500 | med | 23 | 0.70 | 0.00 | 0.70 |
| Taverna Kyclades | hours_mon | blog | 1 | 0.333 | hard | 19 | 0.63 | 0.00 | 0.63 |
| El Malecón | recommended_visit_minutes | blog | 1 | 0.500 | med | 12 | 0.83 | 0.00 | 0.83 |
| Ops | hours_sun | blog | 2 | 0.667 | easy | 9 | 0.78 | 0.22 | 0.78 |
| Roberta's | hours_fri | blog | 2 | 0.667 | easy | 7 | 0.86 | 0.14 | 0.86 |
| Prospect Park | hours_fri | blog | 2 | 0.667 | easy | 6 | 0.83 | 0.00 | 0.83 |
| Flushing Meadows-Corona Park | hours_sat | blog | 2 | 0.667 | easy | 5 | 1.00 | 0.20 | 1.00 |
| The Ramble, Central Park | hours_sun | blog | 2 | 0.667 | easy | 5 | 0.80 | 0.00 | 0.80 |
| Diner | recommended_visit_minutes | blog | 1 | 0.500 | med | 4 | 1.00 | 0.25 | 1.00 |
| Smorgasburg LIC Winter Pop-Up | avg_cost_local | blog | 1 | 0.500 | med | 3 | 0.00 | 0.00 | 0.00 |
| Tacos El Bronco | hours_fri | yelp | 1 | 0.333 | hard | 3 | 0.67 | 0.00 | 0.67 |
| Wave Hill | avg_cost_local | blog | 1 | 0.500 | med | 3 | 1.00 | 0.67 | 0.33 |
| Westlight Bar | hours_sun | blog | 3 | 0.833 | easy | 3 | 0.67 | 0.33 | 0.33 |
| Xi'an Famous Foods | hours_fri | blog | 3 | 0.833 | easy | 3 | 1.00 | 0.33 | 0.67 |
| Corkbuzz Chelsea Market | hours_fri | yelp | 1 | 0.333 | hard | 1 | 1.00 | 0.00 | 1.00 |
| Studio Museum in Harlem | recommended_visit_minutes | blog | 1 | 0.500 | med | 1 | 0.00 | 0.00 | 0.00 |
| Sunny's Bar | hours_sat | blog | 1 | 0.333 | hard | 1 | 1.00 | 0.00 | 1.00 |
| Angel's Share | reservation_required | blog | 1 | 0.500 | med | 0 | - | - | - |
| Bemelmans Bar | hours_fri | blog | 1 | 0.333 | hard | 0 | - | - | - |
| Black Flamingo | hours_fri | yelp | 1 | 0.333 | hard | 0 | - | - | - |
| Snug Harbor Cultural Center | hours_sat | yelp | 1 | 0.333 | hard | 0 | - | - | - |
| The Bronx Museum of the Arts | booking_required | blog | 1 | 0.500 | med | 0 | - | - | - |
| The Brooklyn Inn | hours_fri | yelp | 1 | 0.333 | hard | 0 | - | - | - |
| The Ear Inn | booking_required | blog | 1 | 0.500 | med | 0 | - | - | - |
| Trini-Pak Roti Shop | hours_fri | yelp | 1 | 0.333 | hard | 0 | - | - | - |
| Uptown Lounge | hours_fri | yelp | 1 | 0.333 | hard | 0 | - | - | - |

## Correlations (Spearman over scheduled flaws)

| relationship | result | expectation |
|---|---|---|
| repairability vs trip_rate_any | rho=+0.081, p=0.733, n=20 | NEGATIVE |
| detectability vs trip_rate_any | rho=+0.107, p=0.652, n=20 | NEGATIVE |
| repairability vs trip_rate_F2a | rho=+0.482, p=0.031, n=20 | NEGATIVE |
| detectability vs trip_rate_F2a | rho=+0.442, p=0.051, n=20 | NEGATIVE |
| repairability vs trip_rate_F2c | rho=-0.068, p=0.775, n=20 | NEGATIVE |
| detectability vs trip_rate_F2c | rho=+0.031, p=0.897, n=20 | NEGATIVE |
| repairability vs trip_any (weighted by sched) | rho=+0.349, p=0.000, n=186 | NEGATIVE |
| detectability vs trip_any (weighted by sched) | rho=+0.177, p=0.016, n=186 | NEGATIVE |

- AUC (has-teeth = tripped_any>0, scored by -repairability): **0.472** (class balance pos/neg = (18, 2)).

## Repairability distribution (all 29 flaws)

- min=0.333, median=0.500, max=0.833
- distinct values (4): 0.333, 0.500, 0.667, 0.833

## Censored flaws (scheduled == 0)

| venue | field | src | det | repair |
|---|---|---|---:|---:|
| Angel's Share | reservation_required | blog | 1 | 0.500 |
| Bemelmans Bar | hours_fri | blog | 1 | 0.333 |
| Black Flamingo | hours_fri | yelp | 1 | 0.333 |
| Snug Harbor Cultural Center | hours_sat | yelp | 1 | 0.333 |
| The Bronx Museum of the Arts | booking_required | blog | 1 | 0.500 |
| The Brooklyn Inn | hours_fri | yelp | 1 | 0.333 |
| The Ear Inn | booking_required | blog | 1 | 0.500 |
| Trini-Pak Roti Shop | hours_fri | yelp | 1 | 0.333 |
| Uptown Lounge | hours_fri | yelp | 1 | 0.333 |

## POWER CAVEAT

These flaws are *engineered clean*: repairability takes only a handful of discrete values (see distribution above), so the X spread is narrow. Many flaw venues are never scheduled by any agent, censoring their Y entirely. Per-flaw n is small. Therefore a **null or non-significant correlation here is INCONCLUSIVE, not refuting** -- the experiment lacks the statistical power and X-variance to falsify the certifier. A significant negative repairability-vs-trip_rate relationship would be encouraging confirmatory evidence; its absence should not be read as the certifier failing.

Note: X is per-FLAW but Y (scheduled/tripped) is measured per-VENUE, so venues with two flaws (e.g. The Halal Guys) reuse the same Y across both rows.

**Direction surprise (F2a).** repairability-vs-trip_rate_F2a comes out POSITIVE (rho~+0.48, p~0.03), the opposite of the hypothesized negative. Inspecting the cells, this is a confound, not a refutation: F2a only fires when the agent schedules OUTSIDE ground-truth hours, which depends on the *geometry* of the planted hour shift (a 1-2h temporal-decay nudge rarely creates an actual scheduling conflict), not on how many sources contradict the lie. The low-repairability hours flaws happen to be tight 1-2h shifts (seldom tripped) while a few higher-repairability ones have wider/binding windows. F2c (truth-never-retrieved), which more directly reflects detection effort, shows essentially no relationship (rho~0, p high) -- consistent with the power caveat rather than a signal in either direction.
