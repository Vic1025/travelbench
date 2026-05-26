"""
scripts/generation/test_single_venue.py

Test a single venue generation with full interaction visibility.
Shows every tool call, every response, and the final DB state.

Usage:
  python scripts/generation/test_single_venue.py --api-key $DEEPSEEK_API_KEY
  python scripts/generation/test_single_venue.py --api-key KEY --city tokyo --category museum --district Ueno
  python scripts/generation/test_single_venue.py --api-key KEY --model deepseek-reasoner
  python scripts/generation/test_single_venue.py --dry-run   # no API, just check tooling works
"""

import json
import sys
import argparse
import tempfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection, DB_PATH
from scripts.generation.research_city import research_city
from scripts.generation.generate_venue import run_venue_agent


def run_test(city: str, category: str, district: str, traffic_tier: str,
             name: str, character: str,
             api_key: str, model: str,
             use_temp_db: bool, dry_run: bool,
             log_path=None):

    # ── Set up DB ─────────────────────────────────────────────────────────────
    if use_temp_db:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = Path(tmp.name)
        print(f"  Using temp DB: {db_path}")
    else:
        db_path = DB_PATH
        print(f"  Using main DB: {db_path}")

    init_db(db_path)

    # ── Ensure city config exists ─────────────────────────────────────────────
    print(f"\n[1] Ensuring city config for '{city}'...")
    city_config = research_city(
        city_name=city, api_key=api_key, model=model,
        db_path=db_path, dry_run=dry_run
    )
    print(f"    ✓ {city_config['display_name']}, {city_config['country']}")

    # ── Run the venue agent with full verbose output ───────────────────────────
    print(f"\n[2] Running venue agent...")
    print(f"    Name:      {name}")
    print(f"    Category:  {category}")
    print(f"    District:  {district}")
    print(f"    Tier:      {traffic_tier}")
    print(f"    Character: {character}")
    print(f"    Model:     {model}")
    print(f"    Mode:      {'dry-run' if dry_run else 'live LLM'}")
    print(f"\n{'─'*60}")

    if log_path:
        print(f"  Conversation will be logged to: {log_path}")

    start = datetime.now()
    result = run_venue_agent(
        city=city,
        category=category,
        district=district,
        traffic_tier=traffic_tier,
        name=name,
        archetype=character,
        api_key=None if dry_run else api_key,
        model=model,
        db_path=db_path,
        verbose=True,
        log_path=log_path if not dry_run else None,
    )
    elapsed = (datetime.now() - start).total_seconds()

    print(f"{'─'*60}")
    print(f"\n[3] Result:")
    print(f"    Status:  {result['status']}")
    print(f"    Turns:   {result.get('turns', 0)}")
    print(f"    Elapsed: {elapsed:.1f}s")

    if result["status"] != "success":
        print(f"    ✗ FAILED: {result.get('message', 'unknown error')}")
        return False

    venue_id = result["venue_id"]
    print(f"    venue_id: {venue_id}")

    # ── Show what was written to DB ───────────────────────────────────────────
    print(f"\n[4] DB state after generation:")
    conn = get_connection(db_path)

    venue_row = conn.execute(
        "SELECT * FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if venue_row:
        v = dict(venue_row)
        print(f"\n  VENUE:")
        for k, val in v.items():
            if val is not None and val != "" and val != 0:
                print(f"    {k:35s} {val}")

    yelp_row = conn.execute(
        "SELECT * FROM yelp_listings WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if yelp_row:
        y = dict(yelp_row)
        print(f"\n  YELP LISTING:")
        for k, val in y.items():
            if val is not None and val != "" and val != 0:
                print(f"    {k:35s} {val}")

    tags = conn.execute(
        "SELECT tag, yelp_visible FROM tags WHERE venue_id = ?", (venue_id,)
    ).fetchall()
    if tags:
        yelp_tags = [t["tag"] for t in tags if t["yelp_visible"]]
        hidden_tags = [t["tag"] for t in tags if not t["yelp_visible"]]
        print(f"\n  TAGS:")
        print(f"    Yelp visible: {yelp_tags}")
        print(f"    Hidden:       {hidden_tags}")

    docs = conn.execute(
        "SELECT s.doc_id, s.doc_type, s.title, s.date, s.likes, "
        "LENGTH(s.body) as body_len "
        "FROM source_docs s "
        "JOIN doc_venue_refs r ON s.doc_id = r.doc_id "
        "WHERE r.venue_id = ?", (venue_id,)
    ).fetchall()
    if docs:
        print(f"\n  SOURCE DOCS ({len(docs)}):")
        for d in docs:
            print(f"    [{d['doc_type']:6s}] {d['title'][:50]:50s} "
                  f"date={d['date']} likes={d['likes']} body={d['body_len']}chars")

    official = conn.execute(
        "SELECT url, LENGTH(body) as body_len FROM official_site_docs WHERE venue_id = ?",
        (venue_id,)
    ).fetchone()
    if official:
        print(f"\n  OFFICIAL SITE: {official['url']} ({official['body_len']} chars)")

    wrong_info = conn.execute(
        "SELECT * FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchall()
    if wrong_info:
        print(f"\n  WRONG INFO ({len(wrong_info)} entries):")
        for w in wrong_info:
            print(f"    field={w['affected_field']} incorrect='{w['incorrect_value']}' "
                  f"correct='{w['correct_value']}' via {w['source_type']}")
            print(f"    category: {w['wrong_info_category']}")
            print(f"    origin:   {w['origin_story']}")

    conn.close()

    if use_temp_db:
        Path(db_path).unlink()
        print(f"\n  Temp DB cleaned up.")

    print(f"\n✅ Single venue test complete — {venue_id}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test single venue generation with full interaction visibility"
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--city", default="london")
    parser.add_argument("--category", default="cafe",
                        choices=["restaurant", "cafe", "bar", "museum",
                                 "attraction", "park", "neighbourhood"])
    parser.add_argument("--district", default="Shoreditch")
    parser.add_argument("--traffic-tier", default="low",
                        choices=["high", "mid", "low"])
    parser.add_argument("--name", default="Brewed Awakening",
                        help="Exact venue name")
    parser.add_argument("--character", default="A specialty coffee shop in a converted Victorian railway arch, known for single-origin pour-overs and a quiet reading corner",
                        help="One-sentence venue character description")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use stub data, no API calls")
    parser.add_argument("--keep-db", action="store_true",
                        help="Write to main DB instead of temp DB")
    parser.add_argument("--log-dir", default="logs/venue_runs",
                        help="Directory to save conversation logs (default: logs/venue_runs)")
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY")

    from pathlib import Path as _P
    import time as _time
    # Make log_dir absolute: relative to repo root (parent of scripts/)
    _repo_root = _P(__file__).parent.parent.parent
    log_dir = _repo_root / args.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (args.name or "venue").replace(" ", "_").replace("/", "_")[:30]
    log_path = log_dir / f"{safe_name}_{int(_time.time())}.json"
    print(f"  Log will be saved to: {log_path}")

    success = run_test(
        city=args.city,
        category=args.category,
        district=args.district,
        traffic_tier=args.traffic_tier,
        name=args.name,
        character=args.character,
        api_key=api_key,
        model=args.model,
        use_temp_db=not args.keep_db,
        dry_run=args.dry_run or not api_key,
        log_path=log_path,
    )
    sys.exit(0 if success else 1)
