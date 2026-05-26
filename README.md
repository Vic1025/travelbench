# TravelBench

A benchmark for evaluating LLM travel-planning agents on **multi-source information
synthesis** under realistic noise.

Most planning benchmarks evaluate agents on clean, well-structured tasks. Real travel
planning happens against messy, partially-wrong, multi-source information — Yelp
listings that haven't been updated since 2019, blog posts with stale prices, forum
threads where the corrections come three replies deep. TravelBench evaluates how
planning agents handle that mess.

## What gets tested

A planning agent receives a natural-language trip request (`"my partner and I are
celebrating our anniversary in NYC for the July 4th weekend…"`) and produces a
structured day-by-day itinerary. It has access to four mock tools that mirror
real-world surfaces:

- **`search_yelp`** — venue listings (may contain stale hours, wrong prices)
- **`search_blogs_and_forums`** — user-generated content (mixed quality, some carry corrections)
- **`fetch_url`** — official sites (authoritative when present, but not always available)
- **`estimate_travel`** — travel-time queries

Generated tasks span **6 structural difficulty types**:

| Type | What it tests |
|---|---|
| `type1_cascading_requirements` | Multiple constraints that progressively narrow the feasible set |
| `type2_subset_selection` | Picking the best subset of activities under a time ceiling |
| `type3_competing_requirements` | Two preferences that pull the schedule in different directions |
| `type4_precision_allocation` | Tight per-day budget that forces wise allocation |
| `type5_hard_feasibility` | Constraint intersections that leave very few feasible venues |
| `type6_context_window_tension` | Persona vs. seasonal-window collisions (closed venues, sold-out events) |

Each plan is scored on **four tiers**:

- **C-score** (correctness) — BFCL-style deduction; hard gates on tool-output shape and schedule structure
- **F-score** (feasibility) — deduction-based: hours, travel time, regulation compliance, ceiling overruns
- **P-score** (personal-constraint satisfaction) — explicit personal constraints stated in the query
- **B-score** (bonus) — discretionary signals: proactive use of underused tools, doc-appearance bonus, etc.

## Architecture

```
                                 ┌─────────────────────────┐
                                 │  research_city.py       │ ← OSM Overpass + Nominatim
                                 │  (city geodata + polys) │   (city polygon, districts)
                                 └────────────┬────────────┘
                                              │
                                              ▼
                                 ┌──────────────────────────┐
                                 │  populate_seasonal_      │
                                 │  windows.py              │ ← per-window weather notes
                                 │  (2-4 windows per city)  │
                                 └────────────┬─────────────┘
                                              │
                                              ▼
                                 ┌──────────────────────────┐
                                 │  generate_venue.py       │ ← LLM agent w/ 11 tools
                                 │  (per-venue gen + VERIFY)│   CREATE_PAGE / FILL / COMMIT /
                                 └────────────┬─────────────┘   ADD_WRONG_INFO / VERIFY / SUBMIT
                                              │
                                              ▼
                                 ┌──────────────────────────┐
                                 │  generate_events.py +    │ ← per-venue events bound
                                 │  populate_ticket_avail.py│   to seasonal windows
                                 └────────────┬─────────────┘
                                              │
                                              ▼
                          data/cities/<city>/travelbench.db ← single per-city SQLite
                                              │
                                              ▼
                                 ┌──────────────────────────┐
                                 │  test_generate_tasks.py  │ ← multi-model agent loop
                                 │  (task gen per window)   │   produces 6 tasks per window
                                 └────────────┬─────────────┘
                                              │
                                              ▼
                                 ┌──────────────────────────┐
                                 │  run_benchmark.py        │ ← planning agent + mock
                                 │  (planning agent + eval) │   tools + 4-tier scorer
                                 └──────────────────────────┘
```

## Quickstart

### Install

```bash
pip install -r requirements.txt
# Core: anthropic, Babel
# Optional for other model providers: openai, google-generativeai
```

### Inspect the reference corpus (no API key needed)

```bash
# Browse the included London / New York corpora
python scripts/generation/inspect_db.py --city london
python scripts/generation/inspect_db.py --city new_york

# Read one venue end-to-end (venue row + tags + yelp + source_docs + wrong_info)
python scripts/analysis/fetch_venue.py heHfpkV   # Borough Market in London test_70
```

### Generate tasks against an existing corpus

```bash
# All 4 models in parallel, all 6 types, one seasonal window
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GOOGLE_API_KEY=...
python test_generate_tasks.py \
    --city new_york --run-name test_70 \
    --window new_york_july_4th_2026
```

### Run a planning agent against generated tasks

```bash
python run_benchmark.py \
    --city new_york --run-name test_70 \
    --window new_york_july_4th_2026 \
    --model claude-sonnet-4-5 --api-key $ANTHROPIC_API_KEY
```

### Generate a fresh city corpus (full pipeline)

```bash
# 1. Research city: OSM polygon + Nominatim + Overpass district children
python scripts/generation/research_city.py --city paris --api-key $ANTHROPIC_API_KEY

# 2. Populate 2-4 seasonal windows (each with per-window weather)
python scripts/generation/populate_seasonal_windows.py --city paris

# 3. Generate ~70 venues using an LLM agent loop
python scripts/generation/generate_city_venues.py --city paris --api-key $ANTHROPIC_API_KEY

# 4. Generate per-window events for each venue
python scripts/generation/generate_events.py --city paris

# 5. Validate the city corpus
python scripts/generation/validate_city.py --city paris
```

## Repository layout

```
travelbench/
├── README.md                       you are here
├── LICENSE                         MIT
├── requirements.txt
│
├── scripts/
│   ├── generation/                 city + venue + task generation pipeline
│   │   ├── research_city.py            OSM polygon + districts
│   │   ├── populate_seasonal_windows.py  2-4 windows per city, per-window weather
│   │   ├── generate_venue.py           per-venue LLM agent (11 tools)
│   │   ├── generate_city_venues.py     orchestrator for venue pool
│   │   ├── generate_events.py          per-venue seasonal events
│   │   ├── agent_tools.py              the 11 tools + VERIFY checks
│   │   ├── handbook.py                 agent-facing tool docs + vocab
│   │   ├── task_agent.py               task-generation agent
│   │   ├── validate_city.py            post-generation corpus validator
│   │   ├── db.py                       schema + migrations
│   │   ├── inspect_db.py               CLI to browse a city corpus
│   │   └── test_*.py                   ~25 test suites
│   ├── analysis/
│   │   ├── fetch_venue.py              single-venue inspector
│   │   └── venue_diversity_score.py    corpus diversity metrics
│   └── migration/                  one-shot vocab / data migrations
│
├── server/
│   └── mock_tools.py               the 4 planning-agent tools (search_yelp, etc.)
│
├── agents/
│   └── runner.py / gpt_runner.py / gemini_runner.py    planning-agent runners
│
├── eval/
│   └── evaluator.py                C / F / P / B scoring
│
├── data/
│   └── cities/
│       ├── london/                 reference corpus (test_70)
│       └── New_York/               reference corpus (test_70)
│
├── run_benchmark.py                run a planning agent against tasks
├── test_generate_tasks.py          generate tasks across models / windows
│
├── results/
│   └── transcripts/                per-model planning runs (committed sample)
│
├── dashboard/
│   └── index.html                  static results viewer
│
└── docs/                           design docs, phase TODOs, audit reports
    ├── DESIGN.md                       canonical feature design
    ├── DESIGN_DECISIONS.md             locked design facts
    ├── TODO_PHASE6.md                  current phase work tracker
    ├── PHASE7_FUTURE_WORK.md           deferred / future phase items
    ├── CLAUDE_GUIDE.md                 LLM-collaborator working instructions
    ├── DATA_FORMATS.md                 all agreed schemas
    └── …                               (per-phase TODOs + per-feature design docs)
```

## Project status

| Phase | Focus | Status |
|---|---|---|
| Phase 1–2 | Paris hand-crafted dataset; evaluator; C/F/P scoring | ✅ Complete (Paris JSON pipeline retired in Phase 6) |
| Phase 3 | Auto-generation pipeline for any city; B-score; multi-model task gen | ✅ Complete |
| Phase 4 | Quality improvements, validation, constraint vocab; London corpus | ✅ Complete |
| Phase 5 | Full benchmark loop; per-city SQLite DBs; transport activities | ✅ Complete |
| Phase 5.5 | Constraint diversity (V), wrong-info scaling (W), doc count (D), AxA/BxB audit | ✅ Complete |
| Phase 5.10 | C-score redesign (BFCL-style); F-score deduction model; composite formula removed | ✅ Complete |
| **Phase 6 (current)** | Data quality, source-doc coverage, geographic scope fix, atomic wrong-info workflow | 🚧 ~95% complete (see [`docs/TODO_PHASE6.md`](docs/TODO_PHASE6.md)) |
| Phase 7 (planned) | Semantic source-doc quality detection, temporal-arc reasoning, wrong-info redesign | 📋 Tracked in [`docs/PHASE7_FUTURE_WORK.md`](docs/PHASE7_FUTURE_WORK.md) |

## Reference corpora included

| City | Venues | Source docs | Tasks | Status |
|---|---:|---:|---:|---|
| London (`test_70`) | 70 | 230 | 24 (4 windows × 6 types) | Generated with pre-Phase 6 fixes — has known issues now resolved |
| New York (`test_70`) | 70 | 252 | 18 | Generated with Phase 6 fixes — clean reference |

See [`docs/TODO_PHASE6.md`](docs/TODO_PHASE6.md) for the audit log on each.

## Tests

```bash
# Run the active test sweep (25 suites)
for f in scripts/generation/test_*.py scripts/migration/test_*.py; do
  python "$f" > /dev/null && echo "PASS $f" || echo "FAIL $f"
done

# Single suite
python scripts/generation/test_agent_tools.py    # 161 cases — agent tools + VERIFY checks
```

## Design philosophy

A few principles that shaped this project — most visible in the
[design decisions log](docs/DESIGN_DECISIONS.md):

- **The benchmark generates its own data.** No hand-curating venue databases — LLM
  agents generate venues, source docs, events, and tasks. Quality is enforced via
  validation at COMMIT (`agent_tools.py`) and via post-generation auditing
  (`validate_city.py`).
- **Wrong information is first-class.** Every wrong-info entry is generated with a
  plausible "origin story" and embedded in source docs (one carrying the wrong
  value, one carrying the correction). The benchmark explicitly rewards agents
  for cross-referencing and finding the truth.
- **Forward-only fixes.** Each phase ships new validation rules that apply only to
  new generation runs. Older corpora are kept as historical references.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Built with [Claude Code](https://docs.claude.com/en/docs/claude-code) as the primary
collaborator. Open data sources used: OpenStreetMap (Nominatim + Overpass) for
city geodata, Open-Meteo for per-window weather notes.
