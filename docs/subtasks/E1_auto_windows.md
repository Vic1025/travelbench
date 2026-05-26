# E1 — Auto-window + event generation pipeline
**Status: ✅ complete**
**Blocks: E2, E5**

---

## Tasks
- [x] `generate_and_store_windows()` in `populate_seasonal_windows.py`
      LLM prompt + validation + fallback stub + DB write
- [x] Wire Step 2 into orchestrator (after research_city, skip if windows exist)
      Dry-run writes stub windows; live run calls LLM (falls back to stub on failure)
- [x] `generate_events_for_windows()` in `generate_city_venues.py`
      Single LLM call, all windows + all briefs
      Returns flat list; orchestrator builds `events_by_venue: dict[int, list[dict]]`
- [x] Add `source_hint` to event schema in `_plan_prompt_with_events` (already present)
- [x] `_build_assignment()` — add `event_briefs` param + EVENTS block
- [x] `run_venue_agent()` — add `event_briefs` param; injects into VERIFY dispatch
- [x] `_generate_one()` — attaches `brief["event_briefs"]` before dispatch
- [x] VERIFY check — `_check_event_mentions()` in `agent_tools.py`;
      each assigned event has ≥1 source_doc mention (hard fail)
- [x] Step 8: `write_planned_events` after venue gen with full event list
      (grouped by window, uses venue_id_map built post-generation)
- [x] `test_b0.py` updated — fixed `_plan_prompt` → `_plan_prompt_with_events`,
      counts updated 40→50
- [x] `test_e1.py` — 56 tests covering all E1 components; all passing

---

## Implementation notes

### What changed vs original design
- `generate_events_for_windows()` is a **new separate function**, not reusing `plan_venues`.
  `plan_venues` still returns events as a side-effect for the planning window only —
  that return value is now discarded (`_, _`). The canonical event source is Step 4.
- Orchestrator step labels updated: 1→2→3→3b→4→5→6→8 (matching E1 spec order)
- Dry-run always writes stub windows for unknown cities so downstream tests work
- `event_briefs` injected into VERIFY via the agent loop's `tool_input` dict,
  same pattern as `db_path`/`city` — agent never sees it, only VERIFY does

### Files changed
- `populate_seasonal_windows.py` — added `generate_and_store_windows()`,
  `_validate_windows()`, `_stub_windows()`, updated CLI
- `generate_city_venues.py` — added `generate_events_for_windows()`,
  `_validate_events()`, `_build_venue_list_for_events()`,
  `_build_window_list_for_events()`; rewired orchestrator Steps 2-8
- `generate_venue.py` — `_build_assignment()` + `run_venue_agent()` gain
  `event_briefs` param; VERIFY call injects event_briefs
- `agent_tools.py` — added `_check_event_mentions()`,
  hooked into `tool_VERIFY` error list
- `test_b0.py` — updated for renamed function + 50-venue counts
- `test_e1.py` — new, 56 tests
