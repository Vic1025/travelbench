# Phase 5 — Benchmark Pipeline Upgrade

> ⚠ **Archived phase TODO.** Content below preserved for historical record.
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) (Phase 6 work tracker).
> **Phase 7 candidates:** [docs/PHASE7_FUTURE_WORK.md](PHASE7_FUTURE_WORK.md).

The full loop: task → solving agent → tools → plan → evaluator → scores.

---

## ✅ COMPLETED

### B1 — Task format mismatch ✅
Compute `start_day_of_week` from `start_date`. `user_profile` optional.

### B2 — `load_task` hardcoded path ✅
City-aware search: `data/cities/{city}/tasks/` → legacy → full scan.

### B3 — City case sensitivity ✅
Lowercase city everywhere.

### B4 — F-score hours format ✅
Normalize DB strings to list format.

### B5 — Ghost tools removed ✅
Deleted `get_weather_forecast` and `find_nearby_venues` from evaluator.

### B6 — Category enum synced ✅
Removed `shop`/`hotel`, added `neighbourhood`.

### B7 — System prompt city-aware ✅
Dynamic `_build_system_prompt(task, city_config)`. Seasonal context injected.

### B9 — `run_benchmark.py` task discovery ✅
`--city` auto-discovers tasks. `--window` filters. `--tasks` for explicit IDs.

### B12 — Date-based time_window ✅
Detects YYYY-MM-DD format, matches against `_day_date`.

### B13 — `cuisine_label` count_distinct ✅
Tag fallback for count_distinct when flat field doesn't exist.

### B14 — Transport activities + multi-mode travel ✅
**Files:** `agents/runner.py`, `eval/evaluator.py`, `server/mock_tools.py`

Transport is now an explicit activity type with its own format:
```json
{"time_start": "14:30", "time_end": "15:00", "activity_type": "transport",
 "mode": "transit", "from_venue_id": "abc", "to_venue_id": "def",
 "estimated_cost_local": 3}
```

Changes across the pipeline:
- **System prompt**: transport format documented, modes listed, old "Do NOT include
  transport" rule removed
- **C-score**: `transport` added to VALID_ACTIVITY_TYPES, field checks adapted
  (mode/from_venue_id/to_venue_id instead of venue_id/venue_name), venue_id
  validation skipped for transport
- **F-score gap check**: rewritten — when transport exists between venue pair,
  validates duration >= matrix time for stated mode. Without transport, falls back
  to implicit walking gap check (backward compatible).
- **F-score hours/duration**: transport activities skipped (no venue to check)
- **P-score**: transport filtered from all_activities before constraint evaluation
- **B-score**: unaffected (filters by venue_id presence, transport has none)
- **Mock tools**: `get_travel_time` now returns walking_minutes, transit_minutes,
  cycling_minutes from DB. Schema description updated.
- **Matrix loading**: `_load_city_from_db` now loads all three transport modes
  as dict `{"walking": N, "transit": N, "cycling": N}` instead of flat int.

---

## 🟢 CAN DEFER

### B8 — Bucket C pattern handlers — dead code
Four legacy handlers never triggered. Leave for later.

### B10 — Solving agent Anthropic-only
Need multi-model for benchmarking non-Anthropic models. ~80 lines.

### B11 — Wrong-info three-tier scoring
Binary → three-tier. ~40 lines.

---

## Status

**Done:** B1-B7, B9, B12-B14 (11/14)
**Deferred:** B8, B10, B11 (3/14)

---

## Also pending from Phase 4

- **P3** — Negative regulation visibility (design blocked)
- **P14** — Type 2 required-venue feasibility (not blocking)
- **Multi-venue docs** — ✅ Pipeline working, run completed for London
