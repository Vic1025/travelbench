"""
scripts/analysis/fetch_venue.py

Print the full audit payload for one venue out of a TravelBench city DB:
venue row, tags (visible + hidden), yelp_listing, every source_doc,
official_site_doc, wrong_info entries.

Usage:
  python scripts/analysis/fetch_venue.py <venue_id>
  python scripts/analysis/fetch_venue.py <venue_id> --db <path>

Defaults to London test_70 (data/cities/london/runs/test_70/travelbench.db).
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DEFAULT_DB = ROOT / "data/cities/london/runs/test_70/travelbench.db"


# Columns we surface for the venue row — skip pure boilerplate (FKs, NULL flags).
VENUE_KEY_COLS = [
    "venue_id", "city", "name", "category", "district", "lat", "lng",
    "hours_mon", "hours_tue", "hours_wed", "hours_thu",
    "hours_fri", "hours_sat", "hours_sun",
    "avg_cost_local", "lunch_cost_local", "dinner_cost_local", "price_tier",
    "recommended_visit_minutes", "booking_required", "has_official_site",
    "outdoor_sensitivity", "recommended_pace", "traffic_tier",
    "cuisine", "local_cuisine",
    "total_results", "yelp_popularity_score", "venue_difficulty_score",
    "has_wrong_info_planned",
    "pet_friendly", "wheelchair_accessible", "parking_nearby",
    "photography_allowed", "noise_level", "reservation_required",
    "outside_food_allowed", "family_friendly", "food_available",
    "age_restriction", "dress_code",
    "recommended_time_window_end", "recommended_time_window_reason",
    "window_flags",
    "page_status",
]


def _line(ch="─", n=78):
    return ch * n


def _print_kv(key, val, width=32):
    if val is None:
        val_str = "(null)"
    elif isinstance(val, (dict, list)):
        val_str = json.dumps(val, ensure_ascii=False)
    else:
        val_str = str(val)
    print(f"  {key:<{width}} {val_str}")


def _print_block_header(title):
    print()
    print(_line("═"))
    print(f" {title}")
    print(_line("═"))


def _print_section(title):
    print()
    print(_line("─"))
    print(f" {title}")
    print(_line("─"))


def _pretty_json(raw, indent=4):
    """Try to parse + re-emit; otherwise return raw stringified."""
    if raw in (None, ""):
        return "(empty)"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return json.dumps(parsed, ensure_ascii=False, indent=indent)
    except (TypeError, json.JSONDecodeError):
        return str(raw)


def fetch(conn: sqlite3.Connection, venue_id: str) -> None:
    venue = conn.execute(
        "SELECT * FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if venue is None:
        print(f"venue_id '{venue_id}' not found in this DB.")
        sys.exit(1)
    v = dict(venue)

    _print_block_header(f"VENUE  {v.get('name')}  ({venue_id})")
    for col in VENUE_KEY_COLS:
        if col in v:
            _print_kv(col, v[col])

    # ── tags ────────────────────────────────────────────────────────────────
    tag_rows = conn.execute(
        "SELECT tag, yelp_visible FROM tags WHERE venue_id = ? ORDER BY yelp_visible DESC, tag",
        (venue_id,)
    ).fetchall()
    visible = [r["tag"] for r in tag_rows if r["yelp_visible"] == 1]
    hidden  = [r["tag"] for r in tag_rows if r["yelp_visible"] == 0]
    _print_section(f"TAGS  ({len(tag_rows)} total — {len(visible)} yelp-visible, {len(hidden)} hidden)")
    print(f"  yelp_visible : {', '.join(visible) if visible else '(none)'}")
    print(f"  hidden       : {', '.join(hidden)  if hidden  else '(none)'}")

    # ── yelp_listing ───────────────────────────────────────────────────────
    yelp = conn.execute(
        "SELECT * FROM yelp_listings WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    _print_section("YELP LISTING")
    if yelp is None:
        print("  (no yelp_listing row)")
    else:
        for k, val in dict(yelp).items():
            if k in ("venue_id",):
                continue
            _print_kv(k, val)

    # ── hours_overrides ─────────────────────────────────────────────────────
    overrides = conn.execute(
        "SELECT date, override_type, reason, hours_value FROM hours_overrides "
        "WHERE venue_id = ? ORDER BY date",
        (venue_id,)
    ).fetchall() if _table_has_column(conn, "hours_overrides", "hours_value") else conn.execute(
        "SELECT date, override_type, reason FROM hours_overrides "
        "WHERE venue_id = ? ORDER BY date",
        (venue_id,)
    ).fetchall()
    _print_section(f"HOURS OVERRIDES  ({len(overrides)})")
    if not overrides:
        print("  (none)")
    else:
        for o in overrides:
            d = dict(o)
            print(f"  {d.get('date')}  type={d.get('override_type')}  "
                  f"reason={d.get('reason')}"
                  + (f"  hours={d.get('hours_value')}" if 'hours_value' in d else ""))

    # ── source docs ─────────────────────────────────────────────────────────
    docs = conn.execute(
        """SELECT s.* FROM source_docs s
           JOIN doc_venue_refs r ON s.doc_id = r.doc_id
           WHERE r.venue_id = ?
           ORDER BY s.date""",
        (venue_id,)
    ).fetchall()
    _print_section(f"SOURCE DOCS  ({len(docs)})")
    if not docs:
        print("  (none)")
    for i, d in enumerate(docs, 1):
        dd = dict(d)
        # Role for this venue (if any)
        role_row = conn.execute(
            "SELECT role, wrong_info_id FROM doc_venue_roles "
            "WHERE doc_id = ? AND venue_id = ?",
            (dd["doc_id"], venue_id)
        ).fetchone()
        role_str = (f"{role_row['role']}"
                    + (f" (wrong_info={role_row['wrong_info_id']})"
                       if role_row and role_row["wrong_info_id"] else "")
                    ) if role_row else "(not registered in doc_venue_roles)"

        print()
        print(f"  ── [{i}/{len(docs)}]  doc_id={dd['doc_id']}  "
              f"{dd.get('doc_type')}  {dd.get('date')}  role={role_str}")
        _print_kv("title", dd.get("title"), width=28)
        _print_kv("author", dd.get("author"), width=28)
        _print_kv("source_name", dd.get("source_name"), width=28)
        _print_kv("likes / saves / views",
                  f"{dd.get('likes')} / {dd.get('saves')} / {dd.get('view_count')}",
                  width=28)
        _print_kv("page_status", dd.get("page_status"), width=28)
        _print_kv("mentioned_regulations",
                  _pretty_json(dd.get("mentioned_regulations"), indent=None),
                  width=28)
        _print_kv("mentioned_tags",
                  _pretty_json(dd.get("mentioned_tags"), indent=None),
                  width=28)
        body = dd.get("body") or ""
        print("    body:")
        for line in body.splitlines():
            print(f"      {line}")
        if not body:
            print("      (empty)")

    # ── official site doc ──────────────────────────────────────────────────
    site = conn.execute(
        "SELECT * FROM official_site_docs WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    _print_section("OFFICIAL SITE DOC")
    if site is None:
        print("  (no official_site_docs row)")
    else:
        sd = dict(site)
        for k in ("url", "retrieved_date", "last_updated", "page_status"):
            _print_kv(k, sd.get(k))
        for k in ("hours_mon","hours_tue","hours_wed","hours_thu",
                  "hours_fri","hours_sat","hours_sun"):
            _print_kv(k, sd.get(k))
        _print_kv("mentioned_regulations",
                  _pretty_json(sd.get("mentioned_regulations"), indent=None))
        _print_kv("full_regulations",
                  _pretty_json(sd.get("full_regulations"), indent=None))
        _print_kv("full_labels", sd.get("full_labels"))
        _print_kv("ticket_availability",
                  _pretty_json(sd.get("ticket_availability"), indent=None))
        _print_kv("active_event",
                  _pretty_json(sd.get("active_event"), indent=None))
        body = sd.get("body") or ""
        print("  body:")
        for line in body.splitlines():
            print(f"    {line}")
        if not body:
            print("    (empty)")

    # ── wrong info ─────────────────────────────────────────────────────────
    wis = conn.execute(
        "SELECT * FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchall()
    _print_section(f"WRONG INFO  ({len(wis)})")
    if not wis:
        print("  (none)")
    for i, w in enumerate(wis, 1):
        wd = dict(w)
        print(f"\n  ── wrong_info[{i}]  id={wd['wrong_info_id']}")
        for k in ("affected_field", "incorrect_value", "correct_value",
                  "source_type", "wrong_info_category", "origin_story"):
            _print_kv(k, wd.get(k), width=22)

    # ── events ─────────────────────────────────────────────────────────────
    events = conn.execute(
        "SELECT * FROM events WHERE venue_id = ?", (venue_id,)
    ).fetchall() if _table_exists(conn, "events") else []
    _print_section(f"EVENTS  ({len(events)})")
    if not events:
        print("  (none)")
    for e in events:
        ed = dict(e)
        print(f"\n  {ed.get('name')}  ({ed.get('start_date')} → {ed.get('end_date')})")
        for k in ("description", "window_id", "affects_hours", "affects_access",
                  "sold_out", "modified_hours", "price_local"):
            _print_kv(k, ed.get(k), width=22)

    # ── ticket availability ────────────────────────────────────────────────
    if _table_exists(conn, "ticket_availability"):
        tas = conn.execute(
            "SELECT * FROM ticket_availability WHERE venue_id = ? ORDER BY date",
            (venue_id,)
        ).fetchall()
        if tas:
            _print_section(f"TICKET AVAILABILITY  ({len(tas)})")
            for t in tas:
                td = dict(t)
                print(f"  {td.get('date')}  slots={td.get('slots_available')}  "
                      f"sold_out={td.get('sold_out')}  price={td.get('price_local')}  "
                      f"notes={td.get('notes')}")

    print()


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _table_has_column(conn, table, col):
    if not _table_exists(conn, table):
        return False
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    ap.add_argument("venue_id", help="venue_id to fetch")
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"path to travelbench.db (default: {DEFAULT_DB})")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        fetch(conn, args.venue_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
