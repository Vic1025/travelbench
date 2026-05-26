"""
scripts/generation/generate_events.py

A9 — Events system.

Generates plausible events for venues within each seasonal window.
Events represent special occurrences (exhibitions, concerts, seasonal menus,
holiday specials) that affect hours, access, or pricing.

In dry-run / stub mode: generates deterministic plausible events.
With API key: uses LLM to generate contextually authentic events.

Events are stored in the `events` table and returned by
get_official_site(venue_id, city, date) when queried.

Design:
  - Not every venue gets an event (typically 30-40% of venues per window)
  - High-traffic venues more likely to have events
  - Events that affect_access create additional F-score traps
  - Events during anchor event dates are more plausible

Usage:
  python scripts/generation/generate_events.py --city london --dry-run
  python scripts/generation/generate_events.py --city london --api-key KEY
"""

import json
import random
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.populate_seasonal_windows import get_windows

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# ─────────────────────────────────────────────────────────────────────────────
# EVENT STUBS — deterministic plausible events per category
# ─────────────────────────────────────────────────────────────────────────────

# category → list of (name_template, description_template, affects_hours,
#                      affects_access, sold_out_chance, price_modifier)
STUB_EVENTS = {
    "museum": [
        ("Special Exhibition: {window_label}",
         "A temporary exhibition running throughout {window_label}. "
         "Extended evening hours on select dates.",
         1, 1, 0.3, 1.5),
        ("Members' Preview Evening",
         "Private evening viewing for members and ticket holders. "
         "Pre-booking essential.",
         1, 1, 0.6, 0.0),
    ],
    "attraction": [
        ("Seasonal Event: {window_label}",
         "Special programming for {window_label}. "
         "Pre-booking strongly recommended.",
         0, 1, 0.4, 1.2),
        ("Guided Tour Series",
         "Daily guided tours available throughout this period. "
         "Limited to 15 participants per session.",
         0, 1, 0.2, 0.8),
    ],
    "restaurant": [
        ("Special Menu: {window_label}",
         "A seasonal menu created for {window_label}. "
         "Advance booking required.",
         0, 1, 0.3, 1.3),
        ("Extended Hours",
         "Extended opening hours during {window_label}.",
         1, 0, 0.0, 1.0),
    ],
    "bar": [
        ("Live Music Series",
         "Live performances throughout {window_label}. "
         "Entry by ticket only on event nights.",
         1, 1, 0.5, 1.0),
        ("Special Event Night",
         "One-off special event. Advance ticket purchase required.",
         1, 1, 0.7, 1.0),
    ],
    "cafe": [
        ("Pop-up Collaboration",
         "Guest chef / roaster collaboration for {window_label}. "
         "Walk-ins welcome but queues expected.",
         0, 0, 0.0, 1.1),
    ],
    "park": [
        ("Seasonal Installation",
         "A temporary art installation or seasonal display. "
         "Free to visit.",
         0, 0, 0.0, 0.0),
    ],
    "neighbourhood": [
        ("Local Festival",
         "Neighbourhood festivities throughout {window_label}. "
         "Some roads may be closed.",
         0, 0, 0.0, 0.0),
    ],
}

DEFAULT_EVENT = ("Special Event",
                 "A special event running during {window_label}.",
                 0, 0, 0.0, 1.0)


def _stub_event_for_venue(venue: dict, window: dict, rng: random.Random) -> dict | None:
    """
    Generate a stub event for a venue during a window.
    Returns None if this venue should not have an event.
    """
    category = venue.get("category", "museum")
    traffic  = venue.get("traffic_tier", "mid")

    # Probability of having an event
    prob = {"high": 0.55, "mid": 0.35, "low": 0.15}.get(traffic, 0.3)
    if rng.random() > prob:
        return None

    # Pick an event template
    templates = STUB_EVENTS.get(category, [DEFAULT_EVENT])
    template  = rng.choice(templates)
    name_tmpl, desc_tmpl, affects_hours, affects_access, sold_out_chance, price_mod = template

    label = window.get("label", "this period")
    name  = name_tmpl.format(window_label=label)
    desc  = desc_tmpl.format(window_label=label)

    # Event spans the full window
    dates = window.get("dates", [])
    if not dates:
        return None

    # sold_out: use anchor event dates for realism
    anchor_dates = {e["date"] for e in window.get("anchor_events", [])}
    any_anchor   = bool(anchor_dates.intersection(dates))
    sold_out     = 1 if (any_anchor and rng.random() < sold_out_chance) else 0

    # Price
    base_price = venue.get("avg_cost_local") or 0.0
    price = round(base_price * price_mod, 2) if price_mod > 0 else None

    # Modified hours for affects_hours events
    modified_hours = None
    if affects_hours:
        modified_hours = "10:00-21:00" if rng.random() < 0.5 else "11:00-20:00"

    return {
        "venue_id":      venue["venue_id"],
        "city":          venue["city"],
        "window_id":     window["window_id"],
        "name":          name,
        "description":   desc,
        "start_date":    dates[0],
        "end_date":      dates[-1],
        "affects_hours": affects_hours,
        "affects_access": affects_access,
        "sold_out":      sold_out,
        "modified_hours": modified_hours,
        "price_local":     price,
    }


# ─────────────────────────────────────────────────────────────────────────────
# LLM EVENT GENERATION
# ─────────────────────────────────────────────────────────────────────────────

EVENTS_SYSTEM_PROMPT = """You are a data generation agent for TravelBench. Your job is to generate
plausible events for venues during specific seasonal windows.

For each venue provided, decide whether it plausibly has a special event during
the given window period, and if so, generate the event details.

Rules:
- Not every venue needs an event (be selective — 30-40% of venues)
- Events must be plausible for the venue type and seasonal context
- affects_access=1 events must require advance booking (creates F-score traps)
- sold_out events are the most interesting — planning agents must detect these
- modified_hours should be realistic (e.g. "10:00-21:00")

Return ONLY a valid JSON array. Each element is either null (no event) or an event object:
{
  "venue_id": "string",
  "name": "Event name",
  "description": "1-2 sentences describing the event",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "affects_hours": 0 or 1,
  "affects_access": 0 or 1,
  "sold_out": 0 or 1,
  "modified_hours": "HH:MM-HH:MM" or null,
  "price_local": float or null
}"""


def _generate_events_llm(venues: list, window: dict,
                          api_key: str,
                          model: str = "claude-sonnet-4-20250514") -> list[dict | None]:
    """Generate events for a batch of venues using LLM."""
    client = OpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1")

    venue_summaries = [
        f"{v['venue_id']} | {v['name']} | {v['category']} | {v.get('traffic_tier','mid')}"
        for v in venues
    ]

    prompt = f"""Window: {window['label']} ({window['dates'][0]} – {window['dates'][-1]})
Anchor events: {', '.join(e['name'] for e in window.get('anchor_events', []))}

Window character:
{window['character'][:400]}

Venues (venue_id | name | category | traffic_tier):
{chr(10).join(venue_summaries)}

Generate events for these venues during this window. Return a JSON array with one element
per venue (null if no event). Null is expected for most venues."""

    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": EVENTS_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ]
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        events = json.loads(raw)
        if len(events) != len(venues):
            return [None] * len(venues)
        return events
    except Exception as e:
        print(f"  LLM event generation failed: {e}")
        return [None] * len(venues)


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_events(city: str, dry_run: bool = False,
                    api_key: str = None,
                    model: str = "claude-sonnet-4-20250514",
                    seed: int = 42,
                    db_path: Path = DB_PATH) -> dict:
    """
    Generate events for all venues in a city across all seasonal windows.
    """
    rng = random.Random(seed)
    conn = get_connection(db_path)

    venues = conn.execute("""
        SELECT venue_id, city, name, category, traffic_tier, avg_cost_local
        FROM venues
        WHERE city = ? AND page_status = 'verified'
    """, (city,)).fetchall()

    if not venues:
        conn.close()
        return {"city": city, "status": "no_venues", "events": 0}

    windows = get_windows(city, db_path=db_path)
    if not windows:
        conn.close()
        return {"city": city, "status": "no_windows", "events": 0}

    all_events = []
    venue_list = [dict(v) for v in venues]

    for window in windows:
        wid = window["window_id"]
        print(f"  Window: {window['label']}")

        if api_key and HAS_OPENAI:
            # LLM generation in batches of 15
            batch_size = 15
            for i in range(0, len(venue_list), batch_size):
                batch   = venue_list[i:i+batch_size]
                results = _generate_events_llm(batch, window, api_key, model)
                for venue, event in zip(batch, results):
                    if event and isinstance(event, dict):
                        event["venue_id"] = venue["venue_id"]
                        event["city"]     = city
                        event["window_id"] = wid
                        all_events.append(event)
        else:
            # Stub generation
            for venue in venue_list:
                event = _stub_event_for_venue(venue, window, rng)
                if event:
                    all_events.append(event)

        event_count = sum(1 for e in all_events if e.get("window_id") == wid)
        print(f"    → {event_count} events generated")

    if not dry_run and all_events:
        conn.execute(
            "DELETE FROM events WHERE city = ?", (city,)
        )
        conn.executemany("""
            INSERT INTO events
                (venue_id, city, window_id, name, description,
                 start_date, end_date, affects_hours, affects_access,
                 sold_out, modified_hours, price_local)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            (e["venue_id"], e["city"], e.get("window_id"),
             e["name"], e.get("description",""),
             e["start_date"], e["end_date"],
             int(e.get("affects_hours",0)), int(e.get("affects_access",0)),
             int(e.get("sold_out",0)), e.get("modified_hours"),
             e.get("price_local"))
            for e in all_events
        ])
        conn.commit()

    conn.close()

    sold_out = sum(1 for e in all_events if e.get("sold_out"))
    access   = sum(1 for e in all_events if e.get("affects_access"))

    return {
        "city":            city,
        "status":          "ok",
        "total_events":    len(all_events),
        "affects_access":  access,
        "sold_out":        sold_out,
        "windows":         len(windows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate venue events (A9)")
    parser.add_argument("--city",    required=True)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model",   default="claude-sonnet-4-20250514")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed",    type=int, default=42)
    parser.add_argument("--db",      type=Path, default=DB_PATH)
    args = parser.parse_args()

    print(f"\nGenerating events for {args.city}...")
    result = generate_events(
        args.city,
        dry_run=args.dry_run,
        api_key=args.api_key,
        model=args.model,
        seed=args.seed,
        db_path=args.db,
    )

    print(f"\n{'dry-run — nothing written' if args.dry_run else 'Written to DB'}")
    print(f"  Total events:   {result.get('total_events', 0)}")
    print(f"  Affects access: {result.get('affects_access', 0)}")
    print(f"  Sold out:       {result.get('sold_out', 0)}")
