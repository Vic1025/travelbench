"""
scripts/generation/db.py

SQLite database foundation for TravelBench venue auto-generation pipeline.
Creates and manages all 11 tables. Never exposes ground truth to planning agent.

Tables:
  venues, hours_overrides, tags, ticket_availability, yelp_listings,
  source_docs, doc_venue_refs, doc_venue_roles, wrong_info,
  official_site_docs, city_config
"""

import sqlite3
import random
import string
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "travelbench.db"

# Per-city DB root
_CITIES_ROOT = Path(__file__).parent.parent.parent / "data" / "cities"


def get_city_db_path(city: str, run_name: str | None = None) -> Path:
    """
    Return the SQLite DB path for a specific city (and optional run).
    Creates the directory automatically on first use.

    Default:  data/cities/{city}/travelbench.db
    With run: data/cities/{city}/runs/{run_name}/travelbench.db
    """
    if run_name:
        path = _CITIES_ROOT / city / "runs" / run_name / "travelbench.db"
    else:
        path = _CITIES_ROOT / city / "travelbench.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# ID GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def _random_id(length: int) -> str:
    """Generate a random alphanumeric ID with mixed case."""
    chars = string.ascii_letters + string.digits
    # Ensure no leading digit (cleaner for readability)
    first = random.choice(string.ascii_letters)
    rest = ''.join(random.choices(chars, k=length - 1))
    return first + rest


def new_venue_id() -> str:
    """7-char random venue ID. e.g. 'xK4mR9q'"""
    return _random_id(7)


def new_doc_id() -> str:
    """8-char random document ID. e.g. 'pR7nKx2m'"""
    return _random_id(8)


def new_wrong_info_id() -> str:
    """8-char random wrong-info entry ID."""
    return _random_id(8)


def new_page_id() -> str:
    """8-char random page ID for in-flight drafts."""
    return _random_id(8)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """

-- City configuration. One row per city.
CREATE TABLE IF NOT EXISTS city_config (
    city                TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    country             TEXT NOT NULL,
    centre_lat          REAL NOT NULL,
    centre_lng          REAL NOT NULL,
    radius_km           REAL NOT NULL,
    local_cuisine_label TEXT NOT NULL,
    task_dates          TEXT NOT NULL,   -- JSON array of date strings
    districts           TEXT,            -- JSON array of district names
    cuisine_variety     TEXT,            -- JSON array of cuisine types
    weather_notes       TEXT,
    bbox_min_lat        REAL,            -- bounding box for Overpass queries
    bbox_min_lng        REAL,
    bbox_max_lat        REAL,
    bbox_max_lng        REAL,
    seasonal_windows    TEXT DEFAULT '[]', -- JSON array of window objects
    tag_vocabulary      TEXT DEFAULT '[]', -- P6-T1: city-specific tag extension (JSON array)
    boundary_geojson    TEXT DEFAULT ''    -- P6-T21: city's actual boundary polygon from OSM
);

-- Ground truth venue records. Never exposed to planning agent.
CREATE TABLE IF NOT EXISTS venues (
    venue_id                        TEXT PRIMARY KEY,
    city                            TEXT NOT NULL,
    name                            TEXT,
    category                        TEXT,
    district                        TEXT,
    address                         TEXT,
    lat                             REAL,
    lng                             REAL,
    -- Hours: "HH:MM-HH:MM" or null (closed). Split service: "HH:MM-HH:MM,HH:MM-HH:MM"
    hours_mon                       TEXT,
    hours_tue                       TEXT,
    hours_wed                       TEXT,
    hours_thu                       TEXT,
    hours_fri                       TEXT,
    hours_sat                       TEXT,
    hours_sun                       TEXT,
    -- Costs
    avg_cost_local                    REAL,
    lunch_cost_local                  REAL,           -- food/drink venues only
    dinner_cost_local                 REAL,           -- food/drink venues only
    price_tier                      TEXT,
    -- Visit
    recommended_visit_minutes       INTEGER,
    booking_required                INTEGER DEFAULT 0,  -- 0/1
    has_official_site               INTEGER DEFAULT 0,  -- 0/1
    -- Character
    outdoor_sensitivity             TEXT DEFAULT 'indoor',  -- indoor|outdoor
    recommended_pace                TEXT,           -- relaxed|moderate|intense
    traffic_tier                    TEXT,
    local_cuisine                   INTEGER,        -- 0/1, food venues only
    cuisine                         TEXT,           -- P6-T1b: required for restaurants
                                                    -- (must be in UNIVERSAL_CUISINES);
                                                    -- optional elsewhere.
    -- Search simulation
    total_results                   INTEGER DEFAULT 0,
    yelp_popularity_score           REAL DEFAULT 0.5,
    -- Generation metadata
    has_wrong_info_planned          INTEGER DEFAULT 0,  -- 0/1 from orchestrator brief
    -- Computed post-generation
    venue_difficulty_score          REAL,           -- null until computed
    -- Time window (sparse — most venues null)
    recommended_time_window_end     TEXT,           -- "HH:MM" or null
    recommended_time_window_reason  TEXT,
    -- Regulations (flat columns for clean querying)
    pet_friendly                    INTEGER DEFAULT 0,
    wheelchair_accessible           INTEGER DEFAULT 1,
    parking_nearby                  INTEGER DEFAULT 0,
    age_restriction                 INTEGER,        -- null or minimum age int
    dress_code                      TEXT,           -- null or description
    photography_allowed             INTEGER DEFAULT 1,
    noise_level                     TEXT DEFAULT 'moderate',
    reservation_required            INTEGER,
    outside_food_allowed            INTEGER DEFAULT 0,
    family_friendly                 INTEGER DEFAULT 1,
    food_available                  INTEGER DEFAULT 0,
    -- Seasonal window annotation (populated by annotate_venues_for_window.py)
    window_flags                    TEXT DEFAULT '{}',  -- JSON: {window_id: {flag: value}}
    -- Page tracking
    page_status                     TEXT NOT NULL DEFAULT 'draft',  -- draft|committed|verified
    FOREIGN KEY (city) REFERENCES city_config(city)
);

-- Date-specific hour exceptions. Evaluator checks this before weekday columns.
CREATE TABLE IF NOT EXISTS hours_overrides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id        TEXT NOT NULL,
    date            TEXT,
    override_type   TEXT NOT NULL,  -- closed|modified
    hours           TEXT,           -- "HH:MM-HH:MM" only if modified
    reason          TEXT,           -- e.g. "Christmas Day"
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);

-- Inverted tag index. Primary search index for both agents.
-- All search goes through this table, not via JSON on venue row.
CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag             TEXT NOT NULL,
    city            TEXT NOT NULL,
    venue_id        TEXT NOT NULL,
    yelp_visible    INTEGER NOT NULL DEFAULT 0,  -- 0/1
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
    FOREIGN KEY (city) REFERENCES city_config(city),
    UNIQUE(tag, venue_id)
);

-- Ticket availability by date.
CREATE TABLE IF NOT EXISTS ticket_availability (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id         TEXT NOT NULL,
    date             TEXT NOT NULL,      -- "YYYY-MM-DD"
    status           TEXT NOT NULL,      -- available|limited|sold_out
    slots_available  INTEGER,            -- null = unlimited / not tracked
    sold_out         INTEGER DEFAULT 0,  -- 0/1
    price_local        REAL,               -- null = free / unknown
    notes            TEXT,               -- e.g. "Advance booking only"
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
    UNIQUE(venue_id, date)
);

-- Events: special occurrences at venues (concerts, exhibitions, seasonal specials)
-- Affects hours, access, or pricing on specific date ranges.
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id        TEXT NOT NULL,
    city            TEXT NOT NULL,
    window_id       TEXT,               -- which seasonal window this event belongs to
    name            TEXT NOT NULL,
    description     TEXT,
    start_date      TEXT NOT NULL,      -- "YYYY-MM-DD"
    end_date        TEXT NOT NULL,      -- "YYYY-MM-DD"
    affects_hours   INTEGER DEFAULT 0,  -- 0/1: event changes opening hours
    affects_access  INTEGER DEFAULT 0,  -- 0/1: event requires booking/ticket
    sold_out        INTEGER DEFAULT 0,  -- 0/1: fully booked for this period
    modified_hours  TEXT,               -- e.g. "10:00-22:00" if affects_hours=1
    price_local       REAL,               -- ticket price if affects_access=1
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);

-- Travel time matrix between venue pairs. Season-independent — physical
-- distance doesn't change. Computed once per city / per distinct seasonal pool.
CREATE TABLE IF NOT EXISTS travel_matrix (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    city            TEXT NOT NULL,
    venue_id_a      TEXT NOT NULL,
    venue_id_b      TEXT NOT NULL,
    walk_minutes    REAL,
    transit_minutes REAL,
    cycling_minutes REAL,
    taxi_minutes    REAL,   -- approx: transit × 0.85 (overrideable per city)
    distance_km     REAL,
    FOREIGN KEY (venue_id_a) REFERENCES venues(venue_id),
    FOREIGN KEY (venue_id_b) REFERENCES venues(venue_id),
    UNIQUE(venue_id_a, venue_id_b)
);

-- Yelp listings. Planning agent visible via search_yelp.
-- Separate table so search_yelp never accidentally exposes ground truth.
CREATE TABLE IF NOT EXISTS yelp_listings (
    venue_id                TEXT PRIMARY KEY,
    city                    TEXT NOT NULL,
    name                    TEXT,
    category                TEXT,
    district                TEXT,
    stars                   REAL,
    review_count            INTEGER,
    -- Potentially incorrect hours (may differ from venues table)
    yelp_hours_mon          TEXT,
    yelp_hours_tue          TEXT,
    yelp_hours_wed          TEXT,
    yelp_hours_thu          TEXT,
    yelp_hours_fri          TEXT,
    yelp_hours_sat          TEXT,
    yelp_hours_sun          TEXT,
    -- Potentially incorrect cost/price overlay (may differ from venues table).
    -- NULL = serve the venues GT value; non-NULL = serve this lie (b1.5).
    yelp_avg_cost_local     REAL,
    yelp_price_tier         TEXT,
    yelp_booking_required   INTEGER,
    top_review_snippet      TEXT,
    last_activity_date      TEXT,   -- "YYYY-MM-DD" when owner last updated or last review
    official_url            TEXT,   -- null if no official site
    yelp_popularity_score   REAL DEFAULT 0.5,
    page_status             TEXT NOT NULL DEFAULT 'draft',
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);

-- Blog and forum posts.
CREATE TABLE IF NOT EXISTS source_docs (
    doc_id          TEXT PRIMARY KEY,
    city            TEXT NOT NULL,
    doc_type        TEXT,
    title           TEXT,
    author          TEXT,
    source_name     TEXT,
    date            TEXT,
    likes           INTEGER NOT NULL DEFAULT 0,
    saves           INTEGER NOT NULL DEFAULT 0,
    view_count      INTEGER NOT NULL DEFAULT 0,
    body            TEXT,
    mentioned_regulations TEXT,  -- JSON array: ["pet_friendly", "photography_allowed", ...]
    mentioned_tags  TEXT NOT NULL DEFAULT '[]',  -- P6-T2-B: JSON array of tag strings the body addresses
    persona         TEXT NOT NULL DEFAULT '',    -- P6-T16: one of 7 canonical personas
    tone            TEXT NOT NULL DEFAULT '',    -- P6-T16: open vocab; canonical 10 or invented
    page_status     TEXT NOT NULL DEFAULT 'draft',
    FOREIGN KEY (city) REFERENCES city_config(city)
);

-- Which venues does each source doc mention. Flat join.
CREATE TABLE IF NOT EXISTS doc_venue_refs (
    doc_id      TEXT NOT NULL,
    venue_id    TEXT NOT NULL,
    PRIMARY KEY (doc_id, venue_id),
    FOREIGN KEY (doc_id) REFERENCES source_docs(doc_id),
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);

-- Role of each doc relative to each venue's wrong-info entry.
-- A doc can be truth_carrier for venue A and neutral for venue B.
CREATE TABLE IF NOT EXISTS doc_venue_roles (
    doc_id          TEXT NOT NULL,
    venue_id        TEXT NOT NULL,
    role            TEXT NOT NULL,  -- incorrect_source|truth_carrier|neutral
    wrong_info_id   TEXT,           -- nullable FK, null for neutral
    PRIMARY KEY (doc_id, venue_id),
    FOREIGN KEY (doc_id) REFERENCES source_docs(doc_id),
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
    FOREIGN KEY (wrong_info_id) REFERENCES wrong_info(wrong_info_id)
);

-- Wrong information entries. One row per wrong-info entry.
-- Venues with no wrong info simply have no rows here.
CREATE TABLE IF NOT EXISTS wrong_info (
    wrong_info_id       TEXT PRIMARY KEY,
    venue_id            TEXT NOT NULL,
    affected_field      TEXT NOT NULL,  -- e.g. "hours_fri"
    incorrect_value         TEXT NOT NULL,  -- what the wrong source shows
    correct_value       TEXT NOT NULL,  -- what ground truth says
    source_type         TEXT NOT NULL,  -- yelp|blog|forum
    wrong_info_category TEXT NOT NULL,  -- temporal_decay|propagation_error|conditional|subjective
    origin_story        TEXT NOT NULL,  -- one sentence explaining how this mistake entered this source
    -- Corruption-pipeline metadata (additive; all nullable). Older rows have NULL.
    seed_used           TEXT,           -- seed string used to generate this corruption
    detectability       INTEGER,        -- difficulty signal: how hard to detect the flaw
    repairability       REAL,           -- difficulty signal: how recoverable the truth is
    structure           TEXT,           -- flaw-structure label: minority_truth|copying_bloc|omission|
                                        --   entity_resolution|stale_authority|no_truth
    profile_id          TEXT,           -- corruption profile that produced this entry
    mask_id             TEXT,           -- mask identifier within the profile
    suppress_authority  INTEGER DEFAULT 0,  -- 0/1: mock_tools.get_official_site drops/staleifies
                                            --   the official value for this venue's trapped field
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);

-- Corruption pipeline run provenance. One row per corruption run.
CREATE TABLE IF NOT EXISTS corruption_runs (
    run_id              TEXT PRIMARY KEY,
    master_seed         TEXT,
    profile_id          TEXT,
    profile_version     TEXT,
    corruptor_version   TEXT,
    gt_hash             TEXT,
    created_at          TEXT
);

-- Official site documents. One-to-one with venues where has_official_site=true.
-- Always authoritative. Planning agent accesses via fetch_url(url), not by venue_id.
CREATE TABLE IF NOT EXISTS official_site_docs (
    venue_id            TEXT PRIMARY KEY,
    city                TEXT NOT NULL,
    url                 TEXT,
    retrieved_date      TEXT,   -- when we fetched it
    last_updated        TEXT,   -- when venue last updated the page (e.g. "© 2009", nullable)
    body                TEXT,
    -- Correct hours (always matches ground truth)
    hours_mon           TEXT,
    hours_tue           TEXT,
    hours_wed           TEXT,
    hours_thu           TEXT,
    hours_fri           TEXT,
    hours_sat           TEXT,
    hours_sun           TEXT,
    -- JSON blobs acceptable here — evaluator reads as blob, no per-field querying
    full_regulations    TEXT,   -- JSON
    mentioned_regulations TEXT,  -- JSON array: ["pet_friendly", ...] regulations explicitly mentioned
    full_labels         TEXT,   -- comma-separated string
    ticket_availability TEXT,   -- JSON
    active_event        TEXT,   -- JSON or null
    page_status         TEXT NOT NULL DEFAULT 'draft',
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);

-- Draft pages in-flight. Tracks what the agent is currently working on.
-- Rows deleted on SUBMIT or VERIFY failure.
CREATE TABLE IF NOT EXISTS draft_pages (
    page_id         TEXT PRIMARY KEY,
    venue_id        TEXT,           -- null for city_config pages
    entity_type     TEXT NOT NULL,  -- venue|yelp_listing|source_doc|official_site_doc
    page_status     TEXT NOT NULL DEFAULT 'draft',  -- draft|committed
    created_at      TEXT NOT NULL
);
"""


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION & INIT
# ─────────────────────────────────────────────────────────────────────────────

def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled. Applies pending migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    Idempotent schema migrations for columns/tables added after initial DB creation.
    Safe to run on every connection — only alters if the column/table is missing.
    Skips entirely on new/empty databases (init_db handles those).
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # Skip on empty DB — init_db will create everything from scratch
    if "venues" not in tables:
        return

    venue_cols = {r[1] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}

    # window_flags — added in A6.5
    if "window_flags" not in venue_cols:
        conn.execute("ALTER TABLE venues ADD COLUMN window_flags TEXT DEFAULT '{}'")

    # local_cuisine — may be absent in older schemas
    if "local_cuisine" not in venue_cols:
        conn.execute("ALTER TABLE venues ADD COLUMN local_cuisine INTEGER DEFAULT 0")

    # P6-T1b: cuisine — required at COMMIT for restaurants (enforced in tool_COMMIT)
    if "cuisine" not in venue_cols:
        conn.execute("ALTER TABLE venues ADD COLUMN cuisine TEXT")
        venue_cols = {r[1] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}

    # Currency rename (P8): drop misleading _usd suffix in favour of _local.
    # DB values are already in the city's local currency (e.g. GBP for London);
    # we're just fixing the column label to stop lying about USD.
    # Idempotent via column-existence check.
    _rename_pairs = [
        ("avg_cost_usd",    "avg_cost_local"),
        ("lunch_cost_usd",  "lunch_cost_local"),
        ("dinner_cost_usd", "dinner_cost_local"),
    ]
    for old, new in _rename_pairs:
        if old in venue_cols and new not in venue_cols:
            conn.execute(f"ALTER TABLE venues RENAME COLUMN {old} TO {new}")
    # Refresh after any rename
    venue_cols = {r[1] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}

    # events table — added in A9
    if "events" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                venue_id        TEXT NOT NULL,
                city            TEXT NOT NULL,
                window_id       TEXT,
                name            TEXT NOT NULL,
                description     TEXT,
                start_date      TEXT NOT NULL,
                end_date        TEXT NOT NULL,
                affects_hours   INTEGER DEFAULT 0,
                affects_access  INTEGER DEFAULT 0,
                sold_out        INTEGER DEFAULT 0,
                modified_hours  TEXT,
                price_local       REAL,
                FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
            )
        """)

    # ticket_availability extended columns — added in A8
    ta_cols = {r[1] for r in conn.execute("PRAGMA table_info(ticket_availability)").fetchall()}
    if ta_cols:  # table exists but may have old schema
        # Currency rename for ticket_availability.price_usd → price_local
        if "price_usd" in ta_cols and "price_local" not in ta_cols:
            conn.execute("ALTER TABLE ticket_availability RENAME COLUMN price_usd TO price_local")
            ta_cols = {r[1] for r in conn.execute("PRAGMA table_info(ticket_availability)").fetchall()}

        for col, defn in [
            ("slots_available", "INTEGER"),
            ("sold_out",        "INTEGER DEFAULT 0"),
            ("price_local",       "REAL"),
            ("notes",           "TEXT"),
        ]:
            if col not in ta_cols:
                conn.execute(f"ALTER TABLE ticket_availability ADD COLUMN {col} {defn}")

    # events table — price_usd → price_local rename for existing tables
    if "events" in tables:
        events_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "price_usd" in events_cols and "price_local" not in events_cols:
            conn.execute("ALTER TABLE events RENAME COLUMN price_usd TO price_local")

    # P6-T1: city_config.tag_vocabulary — per-city extension to the universal
    # tag whitelist. JSON array of strings; default '[]'.
    if "city_config" in tables:
        cc_cols = {r[1] for r in conn.execute("PRAGMA table_info(city_config)").fetchall()}
        if "tag_vocabulary" not in cc_cols:
            conn.execute("ALTER TABLE city_config ADD COLUMN tag_vocabulary TEXT DEFAULT '[]'")

        # P6-T21: city's actual boundary polygon (geojson). Used by venue
        # COMMIT for point-in-polygon checks — replaces the leaky 15-km bbox
        # approach. JSON-serialized geojson dict; '' if Nominatim didn't
        # return a polygon (older configs).
        cc_cols = {r[1] for r in conn.execute("PRAGMA table_info(city_config)").fetchall()}
        if "boundary_geojson" not in cc_cols:
            conn.execute("ALTER TABLE city_config ADD COLUMN boundary_geojson TEXT DEFAULT ''")

    # P6-T2-B: source_docs.mentioned_tags — JSON array of tag strings the body
    # addresses. Mirrors mentioned_regulations. VERIFY uses it to enforce that
    # every yelp_visible=0 tag surfaces in at least one source doc.
    if "source_docs" in tables:
        sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(source_docs)").fetchall()}
        if "mentioned_tags" not in sd_cols:
            conn.execute("ALTER TABLE source_docs ADD COLUMN mentioned_tags TEXT NOT NULL DEFAULT '[]'")

        # P6-T16: persona + tone declared by the agent. Persona is closed-vocab
        # (one of 7 canonical); tone is open-vocab (10 canonical + invented).
        # Both required at COMMIT.
        sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(source_docs)").fetchall()}
        if "persona" not in sd_cols:
            conn.execute("ALTER TABLE source_docs ADD COLUMN persona TEXT NOT NULL DEFAULT ''")
        if "tone" not in sd_cols:
            conn.execute("ALTER TABLE source_docs ADD COLUMN tone TEXT NOT NULL DEFAULT ''")

    # b1.5: yelp_listings cost/price overlay columns. Nullable; when NULL the
    # serving path falls back to the venues GT value (so existing data is
    # unchanged). When set, search_yelp prefers the overlay (the served lie),
    # mirroring how yelp_hours_* overlays the GT hours.
    if "yelp_listings" in tables:
        yl_cols = {r[1] for r in conn.execute("PRAGMA table_info(yelp_listings)").fetchall()}
        for col, defn in [
            ("yelp_avg_cost_local",   "REAL"),
            ("yelp_price_tier",       "TEXT"),
            ("yelp_booking_required", "INTEGER"),
        ]:
            if col not in yl_cols:
                conn.execute(f"ALTER TABLE yelp_listings ADD COLUMN {col} {defn}")

    # Corruption pipeline: additive wrong_info columns + corruption_runs table.
    # All columns nullable (suppress_authority defaults 0); existing inserts use
    # explicit column lists and reads use SELECT *, so older rows are unaffected.
    if "wrong_info" in tables:
        wi_cols = {r[1] for r in conn.execute("PRAGMA table_info(wrong_info)").fetchall()}
        for col, defn in [
            ("seed_used",          "TEXT"),
            ("detectability",      "INTEGER"),
            ("repairability",      "REAL"),
            ("structure",          "TEXT"),
            ("profile_id",         "TEXT"),
            ("mask_id",            "TEXT"),
            ("suppress_authority", "INTEGER DEFAULT 0"),
        ]:
            if col not in wi_cols:
                conn.execute(f"ALTER TABLE wrong_info ADD COLUMN {col} {defn}")

    # corruption_runs — run provenance for the corruption pipeline.
    if "corruption_runs" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS corruption_runs (
                run_id              TEXT PRIMARY KEY,
                master_seed         TEXT,
                profile_id          TEXT,
                profile_version     TEXT,
                corruptor_version   TEXT,
                gt_hash             TEXT,
                created_at          TEXT
            )
        """)

    conn.commit()


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create all tables if they don't exist. Returns open connection."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def reset_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Drop and recreate all tables. USE WITH CAUTION — destroys all data."""
    if db_path.exists():
        db_path.unlink()
    return init_db(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_VENUE_FIELDS = [
    "venue_id", "city", "name", "category", "district", "lat", "lng",
    "avg_cost_local", "price_tier", "recommended_visit_minutes",
    "booking_required", "has_official_site", "outdoor_sensitivity",
    "recommended_pace", "traffic_tier", "total_results",
    "yelp_popularity_score", "pet_friendly", "wheelchair_accessible",
    "parking_nearby", "photography_allowed", "noise_level",
    "reservation_required", "outside_food_allowed", "family_friendly",
    "food_available",
]

REQUIRED_YELP_FIELDS = [
    "venue_id", "city", "name", "category", "district",
    "stars", "review_count", "yelp_popularity_score",
]

REQUIRED_SOURCE_DOC_FIELDS = [
    "doc_id", "city", "doc_type", "title", "author",
    "source_name", "date", "body",
    "persona", "tone",   # P6-T16 — keep in sync with REQUIRED_ON_COMMIT in agent_tools
]

REQUIRED_OFFICIAL_SITE_FIELDS = [
    "venue_id", "city", "url", "retrieved_date", "body",
]

REQUIRED_FIELDS = {
    "venue": REQUIRED_VENUE_FIELDS,
    "yelp_listing": REQUIRED_YELP_FIELDS,
    "source_doc": REQUIRED_SOURCE_DOC_FIELDS,
    "official_site_doc": REQUIRED_OFFICIAL_SITE_FIELDS,
}


def get_null_fields(conn: sqlite3.Connection, entity_type: str, record_id: str) -> list[str]:
    """Return list of required fields that are currently null for this record."""
    required = REQUIRED_FIELDS.get(entity_type, [])
    table_map = {
        "venue": ("venues", "venue_id"),
        "yelp_listing": ("yelp_listings", "venue_id"),
        "source_doc": ("source_docs", "doc_id"),
        "official_site_doc": ("official_site_docs", "venue_id"),
    }
    table, id_col = table_map[entity_type]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {id_col} = ?", (record_id,)
    ).fetchone()
    if row is None:
        return required
    return [f for f in required if row[f] is None]


if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        print("Resetting database...")
        conn = reset_db()
        print(f"✓ Database reset at {DB_PATH}")
    else:
        conn = init_db()
        print(f"✓ Database ready at {DB_PATH}")
    conn.close()
