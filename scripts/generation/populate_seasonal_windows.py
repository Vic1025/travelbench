"""
scripts/generation/populate_seasonal_windows.py

Populates seasonal_windows in city_config for London, Hokkaido, and Rio.
Run after research_city has created the city_config row.

Window schema per entry:
{
  "window_id":   str,          -- e.g. "london_easter_2026"
  "label":       str,          -- human-readable name
  "dates":       [str, ...],   -- 7 ISO date strings
  "anchor_events": [           -- culturally significant dates within window
    {"date": str, "name": str, "type": str}
  ],
  "character":   str,          -- description for PLAN_VENUES + task agent
  "conditional_wrong_info_hints": [  -- three trap patterns
    {"type": str, "description": str}
  ],
  "venue_pool_id": str | null  -- null = base city pool; named = distinct pool
}

Usage:
  python scripts/generation/populate_seasonal_windows.py --city london
  python scripts/generation/populate_seasonal_windows.py --city all
"""

import json
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path

# ─────────────────────────────────────────────────────────────────────────────
# WINDOW DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

LONDON_WINDOWS = [
    {
        "window_id": "london_easter_2026",
        "label": "Spring — Easter Week",
        "dates": ["2026-04-02", "2026-04-03", "2026-04-04",
                  "2026-04-05", "2026-04-06", "2026-04-07", "2026-04-08"],
        "anchor_events": [
            {"date": "2026-04-03", "name": "Good Friday",    "type": "bank_holiday"},
            {"date": "2026-04-05", "name": "Easter Sunday",  "type": "public_holiday"},
            {"date": "2026-04-06", "name": "Easter Monday",  "type": "bank_holiday"},
        ],
        "character": (
            "London's first real outdoor weekend of the year. Good Friday (Apr 3) and "
            "Easter Monday (Apr 6) are bank holidays — most venues run reduced hours or "
            "close entirely on those two days, with Saturday and Sunday at peak busyness. "
            "Borough Market and Portobello Road heave with weekend crowds. Parks fill with "
            "families. Popular restaurants run fixed Easter menus requiring advance booking. "
            "The contrast between the two bank-holiday closures and the busy market days "
            "in between is the defining planning challenge."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A Yelp listing or blog written in June shows normal Monday "
                               "hours; the venue is actually closed Easter Monday. The source "
                               "has no awareness of bank holiday closures."
            },
            {
                "type": "access_trap",
                "description": "A source says 'walk-ins always welcome' for a restaurant "
                               "that runs a fixed Easter menu requiring booking 3+ weeks ahead."
            },
            {
                "type": "character_trap",
                "description": "A source describes a normally quiet neighbourhood pub as "
                               "'a relaxed local spot' when it is packed wall-to-wall for "
                               "the Easter bank holiday weekend with no table service."
            },
        ],
        "venue_pool_id": None,  # base London pool
    },
    {
        "window_id": "london_late_spring_2026",
        "label": "Late Spring — Bank Holiday Weekend",
        "dates": ["2026-05-23", "2026-05-24", "2026-05-25",
                  "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"],
        "anchor_events": [
            {"date": "2026-05-25", "name": "Spring Bank Holiday", "type": "bank_holiday"},
        ],
        "character": (
            "London's long May weekend — the unofficial start of summer. Beer gardens "
            "reach peak capacity. The Spring Bank Holiday (Monday May 25) means another "
            "mid-trip closure day for some venues. Chelsea Flower Show falls the week "
            "before, leaving the city in a garden-party mood. Street markets are at full "
            "swing. This is the most 'normal' of London's windows — relatively stable "
            "hours, good weather, busy but manageable crowds. Bank holiday Monday closures "
            "remain a quiet trap."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source shows Monday hours that don't account for the "
                               "Bank Holiday; venue opens 2 hours later or not at all."
            },
            {
                "type": "access_trap",
                "description": "A normally walk-in pub or café operates table service only "
                               "during the busy weekend; source says no booking needed."
            },
            {
                "type": "character_trap",
                "description": "A source describes a rooftop bar as 'often quiet on weekday "
                               "evenings' when the bank holiday weekend makes every evening "
                               "feel like Saturday."
            },
        ],
        "venue_pool_id": None,
    },
    {
        "window_id": "london_carnival_2026",
        "label": "Summer — Notting Hill Carnival",
        "dates": ["2026-08-20", "2026-08-21", "2026-08-22",
                  "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26"],
        "anchor_events": [
            {"date": "2026-08-23", "name": "Notting Hill Carnival Sunday",   "type": "festival"},
            {"date": "2026-08-24", "name": "Notting Hill Carnival Monday",   "type": "bank_holiday"},
        ],
        "character": (
            "Europe's largest street festival transforms an entire London district for two "
            "days. The Notting Hill district is effectively impassable on Sunday and Monday "
            "— roads closed, venues either shut or operating invite-only events. Sound "
            "systems are audible across West London. Outside Notting Hill, the rest of the "
            "city runs normally and is notably quieter than usual. Rooftop bars at peak. "
            "An agent planning a Notting Hill venue on Carnival Sunday or Monday without "
            "checking the official site is making a serious scheduling error."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source written in February shows normal Sunday/Monday hours "
                               "for a Notting Hill venue that is actually closed for carnival weekend."
            },
            {
                "type": "access_trap",
                "description": "A source says a Notting Hill restaurant accepts walk-ins; "
                               "during carnival it operates a ticketed event only."
            },
            {
                "type": "character_trap",
                "description": "A source describes the Notting Hill area as 'a quiet, "
                               "upmarket neighbourhood' — accurate in February, completely "
                               "misleading the weekend of Aug 23-24."
            },
        ],
        "venue_pool_id": None,
    },
    {
        "window_id": "london_christmas_2026",
        "label": "Winter — Christmas Week",
        "dates": ["2026-12-23", "2026-12-24", "2026-12-25",
                  "2026-12-26", "2026-12-27", "2026-12-28", "2026-12-29"],
        "anchor_events": [
            {"date": "2026-12-24", "name": "Christmas Eve",               "type": "cultural"},
            {"date": "2026-12-25", "name": "Christmas Day",               "type": "bank_holiday"},
            {"date": "2026-12-26", "name": "Boxing Day",                  "type": "bank_holiday"},
            {"date": "2026-12-28", "name": "Christmas Substitute Holiday", "type": "bank_holiday"},
        ],
        "character": (
            "Three bank holidays cluster within six days. Most independent venues close "
            "Dec 25-26; many also close Dec 28. Restaurants that do open run fixed "
            "Christmas menus requiring booking weeks ahead. Major tourist attractions "
            "remain open with premium pricing and mandatory pre-booking. Pubs are "
            "exceptionally busy on Christmas Eve and between Christmas and New Year. "
            "Oxford Street and Covent Garden are packed with post-Christmas sales crowds "
            "from Dec 27. An agent using normal operating hours for any day in this window "
            "without checking official sources will produce an unworkable schedule."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A Yelp listing shows standard hours; the venue is closed "
                               "Dec 25, 26, and 28 with no mention of this in the source."
            },
            {
                "type": "access_trap",
                "description": "A source says a restaurant is 'always bookable last-minute'; "
                               "during Christmas week it has been fully booked since October "
                               "with a fixed menu only."
            },
            {
                "type": "character_trap",
                "description": "A source describes a museum as 'calm and uncrowded on "
                               "weekday afternoons' — true in October, completely false "
                               "during the post-Christmas sales period Dec 27-29."
            },
        ],
        "venue_pool_id": None,
    },
]

HOKKAIDO_WINDOWS = [
    {
        "window_id": "hokkaido_snow_festival_2026",
        "label": "Winter — Sapporo Snow Festival Week",
        "dates": ["2026-02-05", "2026-02-06", "2026-02-07",
                  "2026-02-08", "2026-02-09", "2026-02-10", "2026-02-11"],
        "anchor_events": [
            {"date": "2026-02-05", "name": "Sapporo Snow Festival opens",   "type": "festival"},
            {"date": "2026-02-11", "name": "National Foundation Day",        "type": "national_holiday"},
        ],
        "character": (
            "Sapporo hosts one of the world's largest winter festivals — enormous snow and "
            "ice sculptures fill Odori Park and Susukino for a full week, drawing 2 million "
            "visitors. The city is at absolute capacity: hotels booked a year ahead, "
            "restaurants packed every night, the underground shopping network surging with "
            "foot traffic. Ski resorts (Niseko, Furano, Rusutsu) are at peak season "
            "simultaneously — powder conditions at their best, lift queues long. Onsen "
            "ryokans in the mountains require multi-month advance booking. An agent planning "
            "this window without checking official sites for any venue will likely find it "
            "fully booked or running festival-only access."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source written in October shows normal winter ryokan "
                               "availability; during Snow Festival week every room is sold "
                               "out and check-in procedure is different."
            },
            {
                "type": "access_trap",
                "description": "A source says an Odori Park area restaurant accepts walk-ins; "
                               "during festival week it operates fixed seatings only, "
                               "booked in advance."
            },
            {
                "type": "character_trap",
                "description": "A source describes Sapporo's Susukino district as 'quiet "
                               "after midnight'; during Snow Festival week the ice sculpture "
                               "site is illuminated and visited around the clock."
            },
        ],
        "venue_pool_id": "hokkaido_winter",
    },
    {
        "window_id": "hokkaido_lavender_2026",
        "label": "Summer — Lavender Peak",
        "dates": ["2026-07-16", "2026-07-17", "2026-07-18",
                  "2026-07-19", "2026-07-20", "2026-07-21", "2026-07-22"],
        "anchor_events": [
            {"date": "2026-07-20", "name": "Marine Day",                     "type": "national_holiday"},
            {"date": "2026-07-16", "name": "Furano Lavender Festival peak",  "type": "festival"},
        ],
        "character": (
            "The Furano-Biei region turns purple. Farm Tomita is the iconic lavender farm "
            "— free entry but requiring early arrival (before 9am) to avoid hours-long "
            "queues and limited parking. The surrounding countryside is studded with flower "
            "fields of multiple varieties. Roadside farm stands sell fresh produce, lavender "
            "products, and soft-serve ice cream with legendary local queues. Sapporo itself "
            "runs its famous Beer Garden (outdoor season). The patchwork quilt hills of Biei "
            "are best photographed in morning light. An agent scheduling a Furano visit "
            "after noon on a weekend without checking parking and queue information is "
            "heading for a frustrating day."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source shows Farm Tomita opening at 8:30am; during peak "
                               "lavender season it opens at 7:00am and queues form before that."
            },
            {
                "type": "access_trap",
                "description": "A source says 'no booking required' for a Furano restaurant "
                               "popular with lavender tourists; during peak July it requires "
                               "same-day reservation by phone only."
            },
            {
                "type": "character_trap",
                "description": "A source describes the Biei hills as 'peaceful and uncrowded "
                               "in the afternoon' — accurate in October, completely false "
                               "during the lavender festival peak."
            },
        ],
        "venue_pool_id": "hokkaido_summer",
    },
]

RIO_WINDOWS = [
    {
        "window_id": "rio_carnival_2026",
        "label": "Carnival — The Five Days",
        "dates": ["2026-02-12", "2026-02-13", "2026-02-14",
                  "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18"],
        "anchor_events": [
            {"date": "2026-02-14", "name": "Carnival Saturday (Sambadrome opens)",  "type": "festival"},
            {"date": "2026-02-15", "name": "Carnival Sunday",                        "type": "festival"},
            {"date": "2026-02-16", "name": "Carnival Monday",                        "type": "festival"},
            {"date": "2026-02-17", "name": "Carnival Tuesday (peak)",                "type": "festival"},
            {"date": "2026-02-18", "name": "Ash Wednesday (Carnival ends)",          "type": "cultural"},
        ],
        "character": (
            "Rio Carnival 2026 is among the largest on Earth. The Sambadrome runs "
            "competitive samba school parades Sat-Tue night (tickets sell out 6+ months "
            "ahead). Hundreds of blocos (street parties) take over neighbourhoods — "
            "Ipanema, Santa Teresa, Lapa, and Centro each host their own character of "
            "bloco. Normal city function partially suspends: many businesses close Mon-Tue, "
            "public transport runs carnival-special routes, and Lapa's famous nightlife "
            "district becomes a continuous outdoor party. Planning a 'normal' itinerary "
            "during Carnival without acknowledging the festival is a fundamental misread "
            "of the city."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source shows a Santa Teresa restaurant as open Tuesday "
                               "12-22; during Carnival Tuesday it closes entirely because "
                               "the owner is parading in a samba school."
            },
            {
                "type": "access_trap",
                "description": "A source says Sambadrome tickets are available at the gate; "
                               "for Carnival 2026 all sectors are sold out months ahead."
            },
            {
                "type": "character_trap",
                "description": "A source describes Lapa as 'lively on weekends but "
                               "manageable'; during Carnival week Lapa is continuous "
                               "day-and-night street party with capacity crowds."
            },
        ],
        "venue_pool_id": "rio_carnival_override",
    },
    {
        "window_id": "rio_festas_juninas_2026",
        "label": "Festas Juninas Season",
        "dates": ["2026-06-11", "2026-06-12", "2026-06-13",
                  "2026-06-14", "2026-06-15", "2026-06-16", "2026-06-17"],
        "anchor_events": [
            {"date": "2026-06-13", "name": "Festa de Santo António",  "type": "cultural"},
            {"date": "2026-06-24", "name": "Festa de São João (nearby)", "type": "cultural"},
        ],
        "character": (
            "Brazilian winter is Rio's best season for visitors — 20-25°C, low humidity, "
            "clear skies, minimal rain. Festas Juninas bring temporary forró dance stages, "
            "corn cake stalls, and quadrilha performances to neighbourhood squares, "
            "particularly in the North Zone (Tijuca, Madureira) where the tradition is "
            "strongest. Bars and restaurants in Lapa and Santa Teresa run special Festa "
            "Junina menus. This is peak international tourist season for comfort-seekers. "
            "For an agent, the main challenge is that the Festas Juninas events are "
            "neighbourhood-specific and not well-documented online — the best ones are "
            "found via local knowledge, not Yelp."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source shows a Lapa samba club as open Thursday from "
                               "22:00; during Festas Juninas week it opens at 20:00 for "
                               "a special forró night not listed on Yelp."
            },
            {
                "type": "access_trap",
                "description": "A source says a viewpoint restaurant in Santa Teresa has "
                               "'reliable walk-in availability on weeknights'; in June peak "
                               "tourist season it is often fully booked by 18:00."
            },
            {
                "type": "character_trap",
                "description": "A source describes Madureira market as 'a local neighbourhood "
                               "market, not a tourist attraction'; during Festas Juninas it "
                               "hosts one of Rio's largest public celebrations."
            },
        ],
        "venue_pool_id": None,
    },
    {
        "window_id": "rio_summer_peak_2026",
        "label": "Summer Peak — January Heat",
        "dates": ["2026-01-08", "2026-01-09", "2026-01-10",
                  "2026-01-11", "2026-01-12", "2026-01-13", "2026-01-14"],
        "anchor_events": [
            {"date": "2026-01-08", "name": "Post-New Year summer peak begins", "type": "cultural"},
        ],
        "character": (
            "January is Rio's hottest, most humid month and its highest tourist season "
            "outside Carnival. Copacabana and Ipanema beaches are at absolute maximum "
            "capacity on weekends. The famous kiosks operate extended hours. The city is "
            "loud, hot, and vibrant. But the extreme heat (35-40°C with humidity) affects "
            "planning: heavy museum visits in the afternoon are uncomfortable, outdoor "
            "queues are punishing, and the cable car to Sugarloaf can have 2-3 hour waits. "
            "Neighbourhood botequins in shaded streets are more appealing than exposed "
            "viewpoints. An agent who doesn't account for the heat and crowd loading of "
            "January will produce an exhausting schedule."
        ),
        "conditional_wrong_info_hints": [
            {
                "type": "hours_trap",
                "description": "A source written in June shows Sugarloaf cable car as "
                               "'typically 30-minute wait'; in January the wait is 2-3 hours "
                               "and pre-booking online is the only way to avoid it."
            },
            {
                "type": "access_trap",
                "description": "A source says a rooftop bar in Ipanema is 'easy to walk "
                               "into on weekday evenings'; in January peak season it "
                               "operates a cover charge and reservation system."
            },
            {
                "type": "character_trap",
                "description": "A source describes an indoor cultural centre in Flamengo "
                               "as 'a quiet retreat from the city'; in January it becomes "
                               "one of the few air-conditioned refuges and is often at "
                               "capacity by midday."
            },
        ],
        "venue_pool_id": None,
    },
]

CITY_WINDOWS = {
    "london":   LONDON_WINDOWS,
    "hokkaido": HOKKAIDO_WINDOWS,
    "rio":      RIO_WINDOWS,
}


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-GENERATE WINDOWS FOR NEW CITIES
# ─────────────────────────────────────────────────────────────────────────────

_WINDOW_GEN_SYSTEM = (
    "You are a travel research assistant generating benchmark window configurations. "
    "Return only valid JSON. No markdown, no explanation."
)

_WINDOW_GEN_PROMPT = """Generate seasonal windows for TravelBench, a travel planning benchmark.

City: {display_name}, {country}
Hemisphere: {hemisphere}
Timezone: {timezone}
Districts: {districts}

A "window" is one culturally significant week in this city that creates interesting
travel planning challenges. Each window must have a distinct character — different
crowds, closures, events, or seasonal conditions that affect planning.

TARGET: 3-4 windows spread across the year. Return 2 if the city genuinely only
has two distinct seasonal moments worth benchmarking (rare). Never return fewer than 2.

For each window, design 3 wrong-info traps:
- hours_trap: a source doc written outside the window shows wrong hours for this period
- access_trap: a source says walk-in or available when this window requires booking
- character_trap: a source describes the atmosphere as if the window event isn't happening

Return ONLY a JSON array of window objects:
[
  {{
    "window_id": "{city_key}_<slug>_2026",
    "label": "Season — Event Name",
    "dates": ["2026-MM-DD", "2026-MM-DD", "2026-MM-DD", "2026-MM-DD",
              "2026-MM-DD", "2026-MM-DD", "2026-MM-DD"],
    "anchor_events": [
      {{"date": "2026-MM-DD", "name": "Event Name", "type": "festival|bank_holiday|national_holiday|cultural"}}
    ],
    "character": "3-5 sentences describing this week's distinctive atmosphere and planning challenges.",
    "conditional_wrong_info_hints": [
      {{"type": "hours_trap",     "description": "Specific scenario for this city/window."}},
      {{"type": "access_trap",    "description": "Specific scenario for this city/window."}},
      {{"type": "character_trap", "description": "Specific scenario for this city/window."}}
    ],
    "venue_pool_id": null
  }}
]

Rules:
- dates: exactly 7 consecutive ISO date strings, all in 2026
- anchor_events: ≥1 event whose date falls within the window's dates array
- character: must be specific to this city and window — no generic travel copy
- window_id: lowercase, underscores only, ends with _2026
- windows must not overlap (no shared dates)
- spread windows across the year — don't cluster in one season
- venue_pool_id: null unless the city has genuinely separate venue pools per season
  (e.g. a ski resort city where winter and summer venues are completely different)"""


def _stub_windows(city_key: str, city_config: dict) -> list[dict]:
    """
    Fallback: generate minimal but valid stub windows when LLM fails.
    Uses northern-hemisphere assumption unless hemisphere field says 'southern'.
    Produces 3 windows: spring peak, summer peak, winter/holiday.
    """
    southern = str(city_config.get("hemisphere", "")).lower() == "southern"
    name = city_config.get("display_name", city_key.title())

    if southern:
        windows_raw = [
            ("jan_summer", "Summer Peak", "01", "08",
             [{"date": f"2026-01-11", "name": "Summer peak week", "type": "cultural"}],
             f"{name} in high summer. Outdoor venues at capacity, heat affects planning."),
            ("apr_autumn", "Autumn Season", "04", "09",
             [{"date": f"2026-04-11", "name": "Autumn season", "type": "cultural"}],
             f"{name} in autumn. Cooler weather, local cultural season begins."),
            ("jul_winter", "Winter Season", "07", "09",
             [{"date": f"2026-07-11", "name": "Mid-winter", "type": "cultural"}],
             f"{name} mid-winter. Indoor venues preferred, some outdoor closures."),
        ]
    else:
        windows_raw = [
            ("spring_peak", "Spring Peak", "04", "09",
             [{"date": "2026-04-11", "name": "Spring peak", "type": "cultural"}],
             f"{name} in spring. Outdoor venues reopening, parks busy, good weather."),
            ("summer_peak", "Summer Peak", "07", "10",
             [{"date": "2026-07-13", "name": "Summer peak", "type": "cultural"}],
             f"{name} in high summer. Maximum tourist season, long queues, extended hours."),
            ("winter_holiday", "Winter Holiday Season", "12", "23",
             [{"date": "2026-12-25", "name": "Christmas Day", "type": "bank_holiday"}],
             f"{name} during the winter holiday period. Closures, festive events, advance booking required."),
        ]

    windows = []
    for slug, label, month, start_day, anchor_events, character in windows_raw:
        start = f"2026-{month}-{start_day}"
        # Generate 7 consecutive dates
        from datetime import date, timedelta
        d0 = date.fromisoformat(start)
        dates = [(d0 + timedelta(days=i)).isoformat() for i in range(7)]
        windows.append({
            "window_id": f"{city_key}_{slug}_2026",
            "label": label,
            "dates": dates,
            "anchor_events": anchor_events,
            "character": character,
            "conditional_wrong_info_hints": [
                {"type": "hours_trap",
                 "description": f"A source written outside this window shows normal hours; "
                                f"the venue has different hours during {label}."},
                {"type": "access_trap",
                 "description": f"A source says walk-ins welcome; during {label} "
                                f"advance booking is required."},
                {"type": "character_trap",
                 "description": f"A source describes the area as calm and quiet; "
                                f"during {label} it is busy and atmospheric."},
            ],
            "venue_pool_id": None,
        })
    return windows


def _enrich_windows_with_weather(windows: list, centre_lat: float,
                                  centre_lng: float) -> None:
    """P6-T23: add per-window `weather_notes` by calling Open-Meteo for each
    window's own dates. Mutates the windows in place. Skips windows that
    already have non-empty `weather_notes`. Cheap retry: gracefully degrades
    to a stub string when the API is unavailable so the validator can still
    pass."""
    import time as _time
    try:
        from scripts.generation.research_city import _fetch_weather_notes
    except Exception:
        # Helper not importable (offline / circular) — stub everything.
        for w in windows:
            if not w.get("weather_notes"):
                w["weather_notes"] = "Weather data unavailable."
        return

    for i, w in enumerate(windows):
        if w.get("weather_notes"):
            continue
        dates = w.get("dates", [])
        if not dates or not centre_lat:
            w["weather_notes"] = "Weather data unavailable."
            continue
        try:
            w["weather_notes"] = _fetch_weather_notes(
                centre_lat, centre_lng, dates
            ) or "Weather data unavailable."
        except Exception:
            w["weather_notes"] = "Weather data unavailable."
        # Open-Meteo is generous but be polite between back-to-back calls.
        if i < len(windows) - 1:
            _time.sleep(0.3)


def _validate_windows(windows: list) -> list[str]:
    """
    Validate a list of window dicts. Returns list of error strings (empty = ok).
    """
    errors = []
    if not isinstance(windows, list):
        return ["windows must be a JSON array"]
    if len(windows) < 2:
        errors.append(f"need ≥2 windows, got {len(windows)}")
        return errors
    if len(windows) > 4:
        errors.append(f"need ≤4 windows, got {len(windows)}")

    from datetime import date
    all_dates: set[str] = set()

    for i, w in enumerate(windows):
        wid = w.get("window_id", f"window[{i}]")

        # 7 consecutive dates
        dates = w.get("dates", [])
        if len(dates) != 7:
            errors.append(f"{wid}: need exactly 7 dates, got {len(dates)}")
        else:
            try:
                parsed = [date.fromisoformat(d) for d in dates]
                for j in range(1, 7):
                    if (parsed[j] - parsed[j-1]).days != 1:
                        errors.append(f"{wid}: dates are not consecutive")
                        break
            except ValueError as e:
                errors.append(f"{wid}: invalid date format — {e}")

            # Overlap check
            for d in dates:
                if d in all_dates:
                    errors.append(f"{wid}: date {d} overlaps with another window")
            all_dates.update(dates)

        # Anchor events
        anchor = w.get("anchor_events", [])
        if not anchor:
            errors.append(f"{wid}: need ≥1 anchor_event")
        else:
            anchor_dates_in_window = [
                e for e in anchor if e.get("date") in set(dates)
            ]
            if not anchor_dates_in_window:
                errors.append(f"{wid}: no anchor_event date falls within the window dates")

        # Character
        if not w.get("character", "").strip():
            errors.append(f"{wid}: character is empty")

        # Note: weather_notes is populated by _enrich_windows_with_weather
        # AFTER this validator runs. Not checked here so LLM outputs can
        # pass validation without including the field.

        # Wrong info hints
        hints = w.get("conditional_wrong_info_hints", [])
        if len(hints) != 3:
            errors.append(f"{wid}: need exactly 3 conditional_wrong_info_hints, got {len(hints)}")
        else:
            hint_types = {h.get("type") for h in hints}
            required = {"hours_trap", "access_trap", "character_trap"}
            if hint_types != required:
                errors.append(f"{wid}: hints must cover hours_trap/access_trap/character_trap, got {hint_types}")

    return errors


def generate_and_store_windows(city: str, city_config: dict,
                                api_key: str,
                                model: str = "claude-sonnet-4-6",
                                db_path: Path = DB_PATH) -> dict:
    """
    Generate 3-4 seasonal windows for any city via LLM and store in DB.

    Used automatically by the orchestrator for cities not in CITY_WINDOWS.
    Falls back to stub windows if LLM fails after 3 retries.

    Returns:
        {"status": "ok"|"stub"|"error", "windows_written": int, "window_ids": [...]}
    """
    city_key = city.lower().replace(" ", "_")

    # Check city exists in DB
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT city FROM city_config WHERE city = ?", (city_key,)
    ).fetchone()
    conn.close()
    if row is None:
        return {"status": "error", "message": f"City '{city_key}' not in DB. Run research_city first."}

    # Build prompt
    prompt = _WINDOW_GEN_PROMPT.format(
        display_name=city_config.get("display_name", city.title()),
        country=city_config.get("country", ""),
        hemisphere=city_config.get("hemisphere", "northern"),
        timezone=city_config.get("timezone", "UTC"),
        districts=", ".join(city_config.get("districts", [])),
        city_key=city_key,
    )

    windows = None
    last_error = ""

    if api_key:
        try:
            from anthropic import Anthropic as _Anthropic
            client = _Anthropic(api_key=api_key)
        except ImportError:
            return {"status": "error", "message": "anthropic package required"}

        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=4000,
                    system=_WINDOW_GEN_SYSTEM,
                    messages=[{"role": "user", "content": prompt}]
                )
                import re as _re
                raw = resp.content[0].text.strip()
                raw = _re.sub(r'```(?:json)?\s*', '', raw)
                raw = _re.sub(r'```', '', raw).strip()
                candidate = json.loads(raw)
                errs = _validate_windows(candidate)
                if not errs:
                    windows = candidate
                    break
                else:
                    last_error = f"validation: {errs}"
                    print(f"  ⚠ Window gen attempt {attempt+1} failed validation: {errs}")
            except Exception as e:
                last_error = str(e)
                print(f"  ⚠ Window gen attempt {attempt+1} error: {e}")

    if windows is None:
        print(f"  ⚠ LLM window generation failed ({last_error}) — using stub windows")
        windows = _stub_windows(city_key, city_config)

    # P6-T23: per-window weather notes — enrich each window with an
    # Open-Meteo summary computed from its own dates (replaces the
    # vestigial city-level weather_notes).
    centre_lat = float(city_config.get("centre_lat") or 0.0)
    centre_lng = float(city_config.get("centre_lng") or 0.0)
    _enrich_windows_with_weather(windows, centre_lat, centre_lng)

    # Write to DB
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE city_config SET seasonal_windows = ? WHERE city = ?",
        (json.dumps(windows), city_key)
    )
    conn.commit()
    conn.close()

    used_stub = windows is not None and last_error != "" or not api_key
    return {
        "status": "stub" if (not api_key or last_error) else "ok",
        "windows_written": len(windows),
        "window_ids": [w["window_id"] for w in windows],
    }

def populate_windows(city: str, db_path: Path = DB_PATH) -> dict:
    windows = CITY_WINDOWS.get(city)
    if windows is None:
        return {"city": city, "status": "unknown_city",
                "supported": list(CITY_WINDOWS.keys())}

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT city, centre_lat, centre_lng FROM city_config WHERE city = ?",
        (city,)
    ).fetchone()

    if row is None:
        conn.close()
        return {"city": city, "status": "city_not_in_db",
                "note": "Run research_city first to create the city_config row."}

    # P6-T23: per-window weather notes. Deep-copy windows so we don't mutate
    # the module-level CITY_WINDOWS dicts.
    import copy as _copy
    windows = _copy.deepcopy(windows)
    _enrich_windows_with_weather(
        windows, float(row["centre_lat"] or 0.0), float(row["centre_lng"] or 0.0)
    )

    conn.execute(
        "UPDATE city_config SET seasonal_windows = ? WHERE city = ?",
        (json.dumps(windows), city)
    )
    conn.commit()
    conn.close()

    return {
        "city": city,
        "status": "ok",
        "windows_written": len(windows),
        "window_ids": [w["window_id"] for w in windows],
    }


def get_windows(city: str, db_path: Path = DB_PATH) -> list:
    """Read seasonal_windows for a city from DB."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT seasonal_windows FROM city_config WHERE city = ?", (city,)
    ).fetchone()
    conn.close()
    if row is None:
        return []
    try:
        return json.loads(row["seasonal_windows"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def get_window(city: str, window_id: str, db_path: Path = DB_PATH) -> dict | None:
    """Read a single window by ID."""
    windows = get_windows(city, db_path)
    for w in windows:
        if w["window_id"] == window_id:
            return w
    return None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Populate seasonal_windows in city_config"
    )
    parser.add_argument("--city", default="all",
                        help="City to populate, or 'all' for known cities, or any new city name with --generate")
    parser.add_argument("--generate", action="store_true",
                        help="Auto-generate windows for a new city via LLM (requires --api-key)")
    parser.add_argument("--api-key", default=None,
                        help="Anthropic API key (required for --generate)")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    if args.generate:
        if args.city == "all":
            print("--generate requires a specific --city name")
            sys.exit(1)
        from scripts.generation.db import get_connection as _gc
        conn = _gc(args.db)
        row = conn.execute(
            "SELECT * FROM city_config WHERE city = ?",
            (args.city.lower().replace(" ", "_"),)
        ).fetchone()
        conn.close()
        if row is None:
            print(f"City '{args.city}' not in DB. Run research_city first.")
            sys.exit(1)
        city_config = dict(row)
        for col in ("districts", "cuisine_variety", "task_dates"):
            if isinstance(city_config.get(col), str):
                city_config[col] = json.loads(city_config[col] or "[]")
        result = generate_and_store_windows(
            args.city, city_config,
            api_key=args.api_key,
            model=args.model,
            db_path=args.db,
        )
        if result["status"] in ("ok", "stub"):
            label = "✅" if result["status"] == "ok" else "⚠ (stub)"
            print(f"{label} {args.city}: {result['windows_written']} windows written")
            for wid in result["window_ids"]:
                print(f"   {wid}")
        else:
            print(f"❌ {args.city}: {result.get('message', result['status'])}")
        sys.exit(0)

    cities = list(CITY_WINDOWS.keys()) if args.city == "all" else [args.city]
    for city in cities:
        result = populate_windows(city, db_path=args.db)
        if result["status"] == "ok":
            print(f"✅ {city}: {result['windows_written']} windows written")
            for wid in result["window_ids"]:
                print(f"   {wid}")
        else:
            print(f"⚠  {city}: {result['status']} — {result.get('note', '')}")
