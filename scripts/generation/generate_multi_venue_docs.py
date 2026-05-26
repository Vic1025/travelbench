"""
scripts/generation/generate_multi_venue_docs.py

Multi-venue document generation for TravelBench.

Two phases:
  Phase 1 — Planning (agent loop in doc_agent.py):
    Produces ~20 doc briefs: doc_type, angle, venue_ids, wrong_info_venues, date.

  Phase 2 — Generation (single call per type batch, 3–4 docs per call):
    Each batch call receives city context + full venue details for those venues
    + 3–4 briefs. Returns completed doc content for each brief.
    Docs stored in source_docs table + doc_venue_refs table.

Usage:
  python scripts/generation/generate_multi_venue_docs.py --city london \\
      --model claude-sonnet-4-20250514 --api-key $ANTHROPIC_API_KEY

  # Skip Phase 1, use existing briefs file:
  python scripts/generation/generate_multi_venue_docs.py --city london \\
      --briefs-file briefs_london.json --model ... --api-key ...
"""

from __future__ import annotations

import json
import math
import random
import argparse
import sys
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.pool_utils import load_city_pool, load_venue_pool, load_pool
from scripts.generation.doc_agent import (
    run_doc_planning_agent,
    DOC_TYPES, DOC_TYPE_POPULARITY, STALE_POPULARITY,
)

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic as _anthropic_mod
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    from google import genai as _google_genai
    from google.genai import types as _genai_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False


# ─────────────────────────────────────────────────────────────────────────────
# DB SCHEMA HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_schema(conn):
    """Add doc_subtype and window_id columns to source_docs if not present."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(source_docs)").fetchall()}
    if "doc_subtype" not in existing:
        conn.execute("ALTER TABLE source_docs ADD COLUMN doc_subtype TEXT")
    if "window_id" not in existing:
        conn.execute("ALTER TABLE source_docs ADD COLUMN window_id TEXT")
    conn.commit()


def _next_doc_id(conn, city: str) -> str:
    """Generate next sequential multi-venue doc ID for a city."""
    prefix = f"{city[:3].lower()}_mv_"
    rows   = conn.execute(
        "SELECT doc_id FROM source_docs WHERE doc_id LIKE ?",
        (prefix + "%",)
    ).fetchall()
    n = len(rows) + 1
    return f"{prefix}{n:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# POPULARITY ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

def _assign_popularity(brief: dict) -> dict[str, int]:
    """
    Assign likes, saves, view_count based on doc type and stale flag.
    Uses the ranges from DOC_TYPE_POPULARITY / STALE_POPULARITY.
    """
    rng = random.Random()  # unseeded for variety
    if brief.get("stale"):
        lo_l, hi_l, lo_s, hi_s, lo_v, hi_v = STALE_POPULARITY
    else:
        dt = brief.get("doc_type", "trip_diary")
        lo_l, hi_l, lo_s, hi_s, lo_v, hi_v = DOC_TYPE_POPULARITY.get(
            dt, DOC_TYPE_POPULARITY["trip_diary"]
        )
    return {
        "likes":      rng.randint(lo_l, hi_l),
        "saves":      rng.randint(lo_s, hi_s),
        "view_count": rng.randint(lo_v, hi_v),
    }


def _assign_date(brief: dict) -> str:
    """Return publication date string (YYYY-MM-DD or YYYY-MM)."""
    if brief.get("date"):
        d = str(brief["date"])
        # Ensure it looks like a date
        if len(d) == 4:
            return f"{d}-06-15"
        if len(d) == 7:
            return f"{d}-15"
        return d
    if brief.get("stale"):
        year  = random.randint(2021, 2023)
        month = random.randint(1, 12)
        return f"{year}-{month:02d}-{random.randint(1,28):02d}"
    else:
        year  = random.choice([2024, 2024, 2025])
        month = random.randint(1, 12)
        return f"{year}-{month:02d}-{random.randint(1,28):02d}"


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — GENERATION PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

_DOC_TYPE_INSTRUCTIONS = {
    "trip_diary": """\
Write a first-person blog post about a day/evening visiting these venues in sequence.
Include: times visited, brief personal reactions, any surprises (good or bad).
If any venue has wrong_info=True in its venue data, you may include a subtle factual
error (e.g. incorrect hours, wrong price, closed day) — keep it realistic, not obvious.
Tone: casual, personal, conversational. 400–600 words.""",

    "forum_qa": """\
Write a Q&A forum thread. The question asks for venue recommendations matching a theme.
Each answer recommends 1–2 of the venues with a brief explanation.
If wrong_info=True for a venue, one answer may give incorrect details about it.
Tone: helpful, opinionated, like a real forum (Reddit/TripAdvisor style). 300–500 words.""",

    "listicle": """\
Write a listicle-style article: numbered or bulleted list of these venues under a theme.
One short paragraph per venue: what it is, why it's worth visiting, one key detail.
If wrong_info=True for a venue, the key detail for that venue may be subtly incorrect.
Tone: editorial, punchy, confident. 350–550 words.""",

    "comparison": """\
Write a comparison piece weighing these venues against each other.
Make explicit trade-offs: price, atmosphere, location, suitability for different groups.
If wrong_info=True for a venue, one specific claim about it may be subtly incorrect.
Tone: balanced, analytical but readable. 300–450 words.""",

    "itinerary_guide": """\
Write a structured day-plan itinerary using these venues in geographic sequence.
Include: suggested arrival times, how long to spend, transit notes between venues.
Venues are already selected to be geographically clustered — trust the sequence.
If wrong_info=True for a venue, a timing or transit claim may be subtly incorrect.
Tone: practical, prescriptive, like a travel guide. 300–500 words.""",

    "review_aggregator": """\
Write a meta-summary of what visitors say about this venue or neighbourhood.
Synthesise common praise, common complaints, and any notable surprises.
If wrong_info=True for a venue, the aggregated consensus may include a subtle error.
Tone: authoritative, third-person, like a review digest. 200–350 words.""",

    "city_memoir": """\
Write a loose, literary account of time spent in the city. Venues are mentioned
incidentally — not as the primary subject, but as texture in the story.
If wrong_info=True for a venue, the incidental mention may contain a subtle error.
Tone: reflective, personal, atmospheric — like a travel essay or newsletter. 450–650 words.""",
}


def _build_phase2_prompt(city: str, batch: list[dict],
                          venue_details: dict[str, dict]) -> str:
    """
    Build Phase 2 generation prompt for a batch of same-type briefs.
    Returns the prompt string.
    """
    doc_type = batch[0]["doc_type"]
    instr    = _DOC_TYPE_INSTRUCTIONS.get(doc_type, "Write a travel document.")

    venue_blocks = []
    for vid, v in venue_details.items():
        wi_flag = " [WRONG INFO POSSIBLE]" if v.get("has_wrong_info") else ""
        tags    = ", ".join(v.get("tags", [])[:6])
        regs    = v.get("regulations", {})
        wc      = "yes" if regs.get("wheelchair_accessible") else "no"
        lat, lng = v.get("lat"), v.get("lng")
        coord   = f"{lat:.3f},{lng:.3f}" if lat and lng else "unknown"
        venue_blocks.append(
            f"  [{vid}] {v.get('name','?')} | {v.get('category','?')} | "
            f"{v.get('district','?')} | {v.get('traffic_tier','?')}-traffic | "
            f"${v.get('avg_cost_local',0):.0f}/person | coord: {coord}{wi_flag}\n"
            f"    Tags: {tags or 'none'} | Wheelchair: {wc} | "
            f"Pace: {v.get('recommended_pace','moderate')} | "
            f"Visit: ~{v.get('recommended_visit_minutes',75)}min"
        )

    doc_specs = []
    for i, b in enumerate(batch, 1):
        vids   = b.get("venue_ids", [])
        wi_ids = set(b.get("wrong_info_venues", []))
        pub    = _assign_date(b)
        doc_specs.append(
            f"DOC {i}: {b.get('angle','?')}\n"
            f"  Venues (in order): {', '.join(vids)}\n"
            f"  Wrong-info venues: {', '.join(wi_ids) if wi_ids else 'none'}\n"
            f"  Publication date: {pub}"
        )

    return f"""You are writing {len(batch)} {doc_type.replace('_',' ')} document(s) for {city.title()}.

VENUE DATA:
{chr(10).join(venue_blocks)}

WRITING INSTRUCTIONS:
{instr}

DIFFERENTIATION: These {len(batch)} docs must feel distinct from each other.
Use different voices, perspectives, seasons, or traveller types for each one.

DOCS TO WRITE:
{chr(10).join(doc_specs)}

OUTPUT FORMAT:
Return a JSON array with {len(batch)} objects, one per doc, in order:
[
  {{
    "doc_index": 1,
    "title": "...",
    "author": "...",
    "content": "...",
    "venues_mentioned": ["vid1", "vid2"]
  }},
  ...
]
Return ONLY the JSON array. No markdown fences."""


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — LLM CALL
# ─────────────────────────────────────────────────────────────────────────────

def _call_generation_llm(prompt: str, model: str, api_key: str) -> list[dict]:
    """Call LLM for Phase 2 doc generation. Returns list of doc dicts."""
    raw = ""
    model_lower = model.lower()

    if "claude" in model_lower or "anthropic" in model_lower:
        client = _anthropic_mod.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=model, max_tokens=8000,
            system="You are a travel writer generating benchmark corpus documents.",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()

    elif "gemini" in model_lower:
        client = _google_genai.Client(api_key=api_key)
        resp   = client.models.generate_content(
            model=model, contents=prompt,
            config=_genai_types.GenerateContentConfig(
                system_instruction="You are a travel writer generating benchmark corpus documents.",
                max_output_tokens=8000,
                response_mime_type="application/json",
            )
        )
        raw = (resp.text or "").strip()

    else:
        # OpenAI / DeepSeek
        base_url = "https://api.deepseek.com/v1" if "deepseek" in model_lower else None
        kwargs   = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        resp   = client.chat.completions.create(
            model=model, max_tokens=8000,
            messages=[
                {"role": "system",
                 "content": "You are a travel writer generating benchmark corpus documents."},
                {"role": "user", "content": prompt}
            ]
        )
        raw = resp.choices[0].message.content.strip()

    # Strip fences if present
    if "```" in raw:
        raw = raw.split("```", 1)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    # Find JSON array
    start = raw.find("[")
    end   = raw.rfind("]")
    if start != -1 and end != -1:
        raw = raw[start:end+1]

    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# DB WRITE
# ─────────────────────────────────────────────────────────────────────────────

def _write_doc_to_db(conn, city: str, brief: dict, generated: dict) -> str:
    """Write one generated doc + venue roles to DB. Returns doc_id."""
    doc_id   = _next_doc_id(conn, city)
    pop      = _assign_popularity(brief)
    pub_date = _assign_date(brief)
    doc_type = brief.get("doc_type", "trip_diary")

    # Ensure doc_subtype / window_id columns exist
    existing = {r[1] for r in conn.execute("PRAGMA table_info(source_docs)").fetchall()}
    if "doc_subtype" not in existing:
        conn.execute("ALTER TABLE source_docs ADD COLUMN doc_subtype TEXT")
    if "window_id" not in existing:
        conn.execute("ALTER TABLE source_docs ADD COLUMN window_id TEXT")

    # Schema uses 'body' column for content
    body_col = "body" if "body" in existing else "content"

    conn.execute(f"""
        INSERT INTO source_docs
            (doc_id, city, doc_type, doc_subtype, source_name, author, date,
             title, {body_col}, likes, saves, view_count, page_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        doc_id, city,
        "blog" if doc_type in ("trip_diary","city_memoir","listicle","itinerary_guide",
                               "comparison","review_aggregator") else "forum",
        doc_type,
        f"travelbench_{city}_{doc_type}",
        generated.get("author", "Anonymous"),
        pub_date,
        generated.get("title", brief.get("angle", "")),
        generated.get("content", ""),
        pop["likes"], pop["saves"], pop["view_count"],
        "verified",   # ← must match mock_tools.py query filter
    ))

    # Register in doc_venue_refs (flat join table)
    for vid in brief.get("venue_ids", []):
        conn.execute(
            "INSERT OR IGNORE INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)",
            (doc_id, vid)
        )

    # Register roles in doc_venue_roles
    wi_vids = set(brief.get("wrong_info_venues", []))
    for vid in brief.get("venue_ids", []):
        role = "incorrect_source" if vid in wi_vids else "neutral"
        conn.execute(
            "INSERT OR IGNORE INTO doc_venue_roles (doc_id, venue_id, role, wrong_info_id) "
            "VALUES (?,?,?,NULL)",
            (doc_id, vid, role)
        )

    conn.commit()
    return doc_id


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def generate_multi_venue_docs(
    city:        str,
    model:       str,
    api_key:     str,
    db_path:     Path = None,
    briefs_file: Path | None = None,
    dry_run:     bool = False,
    verbose:     bool = False,
) -> dict:
    """
    Run full multi-venue doc generation for a city.

    Returns summary dict with counts and any errors.
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    import time as _time
    print(f"\n📚 Multi-venue doc generation — {city.title()}")
    print(f"   Model: {model} | dry_run={dry_run}")

    conn = get_connection(db_path)
    _ensure_schema(conn)
    conn.close()

    # Load pool and tasks
    print("\n[1] Loading venue pool and tasks...")
    pool = load_city_pool(city, db_path=db_path)
    print(f"  Pool: {len(pool)} venues")

    # Tasks are context for the planning agent (which venues matter).
    # Try DB table first, then fall back to JSON files on disk.
    tasks = []
    conn = get_connection(db_path)
    if _table_exists(conn, "tasks"):
        task_rows = conn.execute(
            "SELECT task_json FROM tasks WHERE city = ?", (city,)
        ).fetchall()
        for row in task_rows:
            try:
                tasks.append(json.loads(row["task_json"]))
            except Exception:
                pass
    conn.close()

    if not tasks:
        # Scan JSON task files on disk.
        # Tasks live at data/cities/{city}/tasks/... — find the city root
        # by walking up from db_path until we find a 'tasks' sibling.
        _city_root = db_path.parent
        for _ in range(5):
            _tasks_dir = _city_root / "tasks"
            if _tasks_dir.exists():
                break
            _city_root = _city_root.parent
        else:
            _tasks_dir = None

        if _tasks_dir and _tasks_dir.exists():
            for _tf in _tasks_dir.rglob("*.json"):
                if "agent_log" in str(_tf):
                    continue
                try:
                    tasks.append(json.loads(_tf.read_text()))
                except Exception:
                    pass
    print(f"  Tasks: {len(tasks)} loaded for context")

    # Phase 1 — Planning
    _log_dir = db_path.parent / "logs" / "multi_venue_docs"
    _log_dir.mkdir(parents=True, exist_ok=True)

    if briefs_file and briefs_file.exists():
        print(f"\n[2] Loading existing briefs from {briefs_file}...")
        briefs = json.loads(briefs_file.read_text())
        print(f"  Loaded {len(briefs)} briefs")
    else:
        print(f"\n[2] Phase 1 — Planning agent loop ({model})...")
        briefs = run_doc_planning_agent(
            city=city, pool=pool, tasks=tasks,
            model=model, api_key=api_key, verbose=verbose,
        )
        if briefs is None:
            print("  ❌ Planning agent failed (max turns exhausted or no API key)")
            return {"city": city, "docs_written": 0, "errors": ["Phase 1 failed"]}
        print(f"  ✓ {len(briefs)} briefs planned")

        # Save Phase 1 log
        _p1_log = _log_dir / f"phase1_briefs_{int(_time.time())}.json"
        _p1_log.write_text(json.dumps({
            "phase": 1, "city": city, "model": model,
            "n_briefs": len(briefs), "briefs": briefs,
        }, indent=2, ensure_ascii=False))
        print(f"  📝 Phase 1 log: {_p1_log.name}")

        if dry_run:
            print("  (dry-run — saving briefs only)")
            brief_path = Path(f"briefs_{city}.json")
            brief_path.write_text(json.dumps(briefs, indent=2))
            print(f"  Saved: {brief_path}")
            return {"city": city, "briefs": len(briefs), "docs_written": 0, "dry_run": True}

    # Group by doc type for Phase 2 batching
    from collections import defaultdict
    by_type: dict[str, list] = defaultdict(list)
    for b in briefs:
        by_type[b.get("doc_type", "trip_diary")].append(b)

    # Build pool_map for venue detail lookups
    pool_map = {v["venue_id"]: v for v in pool}
    wrong_info_vids = {v["venue_id"] for v in pool if v.get("has_wrong_info")}

    # Phase 2 — Generation
    print(f"\n[3] Phase 2 — Generating docs by type...")
    total_written = 0
    errors        = []
    BATCH_SIZE    = 3

    for doc_type, type_briefs in by_type.items():
        print(f"\n  {doc_type} ({len(type_briefs)} docs)...")

        # Process in batches of BATCH_SIZE
        for batch_start in range(0, len(type_briefs), BATCH_SIZE):
            batch = type_briefs[batch_start:batch_start + BATCH_SIZE]

            # Collect all unique venue details for this batch
            batch_venue_ids = {vid for b in batch for vid in b.get("venue_ids", [])}
            venue_details   = {}
            for vid in batch_venue_ids:
                detail = pool_map.get(vid, {})
                detail_full = {
                    "name":                      detail.get("name", vid),
                    "category":                  detail.get("category", ""),
                    "district":                  detail.get("district", ""),
                    "traffic_tier":              detail.get("traffic_tier", "mid"),
                    "avg_cost_local":              detail.get("avg_cost_local", 0),
                    "tags":                      detail.get("tags", []),
                    "regulations":               {
                        "wheelchair_accessible": bool(detail.get("wheelchair_accessible", 0)),
                        "pet_friendly":          bool(detail.get("pet_friendly", 0)),
                    },
                    "recommended_pace":          detail.get("recommended_pace", "moderate"),
                    "recommended_visit_minutes": detail.get("recommended_visit_minutes", 75),
                    "lat":                       detail.get("lat"),
                    "lng":                       detail.get("lng"),
                    "has_wrong_info":            vid in wrong_info_vids,
                }
                venue_details[vid] = detail_full

            prompt = _build_phase2_prompt(city, batch, venue_details)

            try:
                generated_docs = _call_generation_llm(prompt, model, api_key)
                if not isinstance(generated_docs, list):
                    raise ValueError(f"Expected list, got {type(generated_docs)}")

                # Save Phase 2 batch log
                _p2_log = _log_dir / f"phase2_{doc_type}_{batch_start}_{int(_time.time())}.json"
                _p2_log.write_text(json.dumps({
                    "phase": 2, "doc_type": doc_type, "model": model,
                    "batch_index": batch_start // BATCH_SIZE,
                    "n_briefs": len(batch),
                    "n_generated": len(generated_docs),
                    "briefs": batch,
                    "generated": generated_docs,
                    "prompt_length": len(prompt),
                }, indent=2, ensure_ascii=False))

                conn = get_connection(db_path)
                for i, brief in enumerate(batch):
                    if i < len(generated_docs):
                        gen = generated_docs[i]
                    else:
                        gen = {"title": brief.get("angle",""), "content": "", "author": "Anonymous"}
                        errors.append(f"Missing generated doc {i+1} for {doc_type}")

                    doc_id = _write_doc_to_db(conn, city, brief, gen)
                    total_written += 1
                    print(f"    ✓ {doc_id}: {brief.get('angle','?')[:50]}")
                conn.close()

            except Exception as e:
                errors.append(f"{doc_type} batch {batch_start//BATCH_SIZE + 1}: {e}")
                print(f"    ❌ Batch failed: {e}")
                # Save failure log
                try:
                    _fail_log = _log_dir / f"phase2_FAIL_{doc_type}_{batch_start}_{int(_time.time())}.json"
                    _fail_log.write_text(json.dumps({
                        "phase": 2, "doc_type": doc_type, "model": model,
                        "error": str(e), "briefs": batch,
                        "prompt_length": len(prompt),
                    }, indent=2, ensure_ascii=False))
                except Exception:
                    pass

    # Coverage report
    print(f"\n[4] Coverage report")
    if total_written > 0:
        conn = get_connection(db_path)
        written = conn.execute(
            "SELECT doc_subtype, COUNT(*) as n FROM source_docs "
            "WHERE city = ? AND doc_subtype IS NOT NULL "
            "GROUP BY doc_subtype", (city,)
        ).fetchall()
        conn.close()
        for row in written:
            print(f"  {row['doc_subtype']:20s}: {row['n']} docs")

    venues_covered = {vid for b in briefs for vid in b.get("venue_ids", [])}
    print(f"\n  Venues covered: {len(venues_covered)}/{len(pool)} "
          f"({len(venues_covered)/max(len(pool),1):.0%})")
    print(f"  Total docs written: {total_written}")
    if errors:
        print(f"  Errors: {len(errors)}")
        for e in errors:
            print(f"    - {e}")

    return {
        "city":         city,
        "briefs":       len(briefs),
        "docs_written": total_written,
        "venues_covered": len(venues_covered),
        "errors":       errors,
    }


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate multi-venue docs for a city")
    parser.add_argument("--city",        required=True)
    parser.add_argument("--run-name", default=None,
                        help="Run name for separate DB (e.g. test_30). Default: production DB.")
    parser.add_argument("--model",       default="claude-sonnet-4-20250514")
    parser.add_argument("--api-key",     default=None)
    parser.add_argument("--db",          type=Path, default=None,
                        help="Explicit DB path. If omitted, uses --run-name or default city DB.")
    parser.add_argument("--briefs-file", type=Path, default=None,
                        help="Skip Phase 1 — load briefs from JSON file")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Phase 1 only — save briefs, don't generate content")
    parser.add_argument("--verbose",     action="store_true")
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY") or \
              os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")

    _run = getattr(args,'run_name',None)
    result = generate_multi_venue_docs(
        city=args.city,
        model=args.model,
        api_key=api_key,
        db_path=args.db or get_city_db_path(args.city, run_name=_run),
        briefs_file=args.briefs_file,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print(f"\n✅ Done: {result['docs_written']} docs written for {args.city}")
    if result.get("errors"):
        sys.exit(1)
