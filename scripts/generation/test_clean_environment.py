"""
scripts/generation/test_clean_environment.py

Tests for the --clean-environment flag (server.mock_tools.set_clean_environment).

Verifies that toggling clean mode:
  1. Heals yelp_listings structured fields back to ground truth.
  2. Filters source_docs flagged as incorrect_source out of search_blogs.
  3. Still surfaces truth_carrier docs in clean mode (clean truth, not silence).
  4. Does not affect venues that have no wrong_info row.
  5. Toggles deterministically — flipping back to noisy restores the trap.

Uses the existing New_York test_70 DB so the fixtures cover real data the
benchmark actually runs against. No DB mutation.

Run: python scripts/generation/test_clean_environment.py
"""

import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from server import mock_tools
from server.mock_tools import (
    set_run_name, set_clean_environment, get_clean_environment,
    tool_search_yelp, tool_search_blogs_and_forums, tool_get_official_site,
)

CITY = "new_york"
RUN_NAME = "test_70"
DB_PATH = ROOT / "data" / "cities" / "New_York" / "runs" / RUN_NAME / "travelbench.db"

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))
        FAIL += 1


def _reset_state():
    """Clear cache and flags between tests so each scenario starts cold."""
    mock_tools._city_cache.clear()
    set_run_name(RUN_NAME)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE DISCOVERY
# Pull a real (venue_id, affected_field, incorrect_value, correct_value,
# incorrect_doc_id, truth_doc_id) tuple straight from the DB so the test is
# self-validating against whatever data the run actually has.
# ─────────────────────────────────────────────────────────────────────────────

def _discover_yelp_hours_fixture():
    """Return (vid, field, incorrect, correct, bad_doc_id, truth_doc_id) for a
    venue with yelp wrong_info on hours_fri and both incorrect+truth carriers."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT wi.venue_id, wi.affected_field, wi.incorrect_value, wi.correct_value,
               (SELECT doc_id FROM doc_venue_roles
                  WHERE venue_id = wi.venue_id AND role = 'incorrect_source'
                  LIMIT 1) AS bad_doc,
               (SELECT doc_id FROM doc_venue_roles
                  WHERE venue_id = wi.venue_id AND role = 'truth_carrier'
                  LIMIT 1) AS truth_doc
        FROM wrong_info wi
        WHERE wi.source_type = 'yelp'
          AND wi.affected_field = 'hours_fri'
        LIMIT 1
    """).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def _discover_blog_fixture():
    """Return (vid, bad_doc_id) for a venue whose wrong_info source_type='blog'."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT wi.venue_id,
               (SELECT doc_id FROM doc_venue_roles
                  WHERE venue_id = wi.venue_id AND role = 'incorrect_source'
                  LIMIT 1) AS bad_doc
        FROM wrong_info wi
        WHERE wi.source_type IN ('blog', 'forum')
        LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None


def _venue_yelp_hours_fri(vid: str) -> str:
    """Read the venue's hours_fri straight out of search_yelp output."""
    yelp_r = tool_search_yelp("", CITY, top_k=10_000)
    for r in yelp_r.get("results", []):
        if r["venue_id"] == vid:
            hours = r.get("hours", {}).get("fri")
            if hours is None:
                return ""
            if isinstance(hours, list):
                return "-".join(str(x) for x in hours)
            return str(hours)
    return "VENUE_NOT_FOUND"


def _all_blog_doc_ids() -> set:
    """Collect every doc_id returned across a broad blog search."""
    out = set()
    # query="" + venue_name=None falls back to ranked_search on empty tokens
    # which returns nothing, so we drive the index by pulling its docs directly.
    idx = mock_tools._load_city(CITY)
    for doc_id in idx.post_idx._docs.keys():
        out.add(doc_id)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== TEST: clean-environment flag in mock_tools ===\n")

if not DB_PATH.exists():
    print(f"❌ Test DB not found: {DB_PATH}")
    print("   (expected from the New_York test_70 corpus)")
    sys.exit(1)

yelp_fx = _discover_yelp_hours_fixture()
blog_fx = _discover_blog_fixture()

print(f"[fixture] yelp_hours_fri: {yelp_fx}")
print(f"[fixture] blog:          {blog_fx}")

if yelp_fx is None or yelp_fx["bad_doc"] is None or yelp_fx["truth_doc"] is None:
    print("❌ Could not find a yelp hours_fri fixture with both carrier roles")
    sys.exit(1)

# ── [1] Yelp field heals from incorrect to correct ───────────────────────────
print("\n[1] yelp_listings field heals from incorrect to correct value")
_reset_state()

set_clean_environment(False)
hours_noisy = _venue_yelp_hours_fri(yelp_fx["venue_id"])
check("noisy mode flag reads False", get_clean_environment() is False)
check(
    f"noisy mode: venue {yelp_fx['venue_id']} yelp_hours_fri == incorrect "
    f"({yelp_fx['incorrect_value']})",
    hours_noisy == yelp_fx["incorrect_value"],
    f"got '{hours_noisy}'",
)

set_clean_environment(True)
hours_clean = _venue_yelp_hours_fri(yelp_fx["venue_id"])
check("clean mode flag reads True", get_clean_environment() is True)
check(
    f"clean mode: venue {yelp_fx['venue_id']} yelp_hours_fri == correct "
    f"({yelp_fx['correct_value']})",
    hours_clean == yelp_fx["correct_value"],
    f"got '{hours_clean}'",
)
check(
    "clean and noisy yelp_hours_fri differ for this trap venue",
    hours_clean != hours_noisy,
    f"clean='{hours_clean}', noisy='{hours_noisy}'",
)

# ── [2] search_blogs drops incorrect_source docs in clean mode ───────────────
print("\n[2] search_blogs_and_forums result set differs between modes")
_reset_state()

set_clean_environment(False)
noisy_docs = _all_blog_doc_ids()

_reset_state()
set_clean_environment(True)
clean_docs = _all_blog_doc_ids()

# Pull the full incorrect_source set straight from the DB so we compare the
# right invariant: clean_docs ⊆ noisy_docs AND clean_docs ∩ incorrect == ∅.
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
incorrect_doc_ids = {
    r["doc_id"] for r in conn.execute(
        "SELECT DISTINCT doc_id FROM doc_venue_roles WHERE role='incorrect_source'"
    ).fetchall()
}
conn.close()

dropped = noisy_docs - clean_docs
check("noisy has >= clean (clean cannot add docs)", clean_docs.issubset(noisy_docs))
check(
    "every dropped doc is flagged incorrect_source",
    dropped.issubset(incorrect_doc_ids),
    f"unexpected drops: {sorted(dropped - incorrect_doc_ids)[:3]}",
)
check(
    "clean drops at least one incorrect_source doc",
    len(dropped) > 0,
    f"dropped count = {len(dropped)}",
)
check(
    "no incorrect_source doc survives in clean mode",
    len(clean_docs & incorrect_doc_ids) == 0,
    f"leaked: {sorted(clean_docs & incorrect_doc_ids)[:3]}",
)

# ── [3] truth-carrier doc still present in clean mode ────────────────────────
print("\n[3] truth_carrier doc is still returned in clean mode")
check(
    f"truth_carrier doc {yelp_fx['truth_doc']} present in clean index",
    yelp_fx["truth_doc"] in clean_docs,
    "truth carrier got dropped — clean mode should keep it",
)
check(
    f"incorrect_source doc {yelp_fx['bad_doc']} absent from clean index",
    yelp_fx["bad_doc"] not in clean_docs,
    "incorrect_source doc leaked into clean mode",
)
check(
    f"truth_carrier doc {yelp_fx['truth_doc']} also present in noisy index",
    yelp_fx["truth_doc"] in noisy_docs,
    "sanity: noisy mode also keeps the truth carrier",
)

# ── [4] non-trap venues are untouched by clean mode ──────────────────────────
print("\n[4] venues without wrong_info are unaffected by clean mode")
_reset_state()

# Pick a venue whose ID is NOT in wrong_info
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
control = conn.execute("""
    SELECT v.venue_id
    FROM venues v
    JOIN yelp_listings y ON y.venue_id = v.venue_id
    LEFT JOIN wrong_info wi ON wi.venue_id = v.venue_id
    WHERE LOWER(v.city) = ? AND v.page_status='verified'
      AND wi.venue_id IS NULL AND y.yelp_hours_fri IS NOT NULL
    LIMIT 1
""", (CITY,)).fetchone()
conn.close()
control_vid = control["venue_id"] if control else None

set_clean_environment(False)
ctrl_noisy = _venue_yelp_hours_fri(control_vid) if control_vid else None
set_clean_environment(True)
ctrl_clean = _venue_yelp_hours_fri(control_vid) if control_vid else None
check(
    f"control venue {control_vid}: hours_fri identical in both modes",
    ctrl_noisy == ctrl_clean,
    f"noisy='{ctrl_noisy}' clean='{ctrl_clean}'",
)

# ── [5] toggle is deterministic — flipping back restores the trap ────────────
print("\n[5] toggle is deterministic across flips")
_reset_state()

set_clean_environment(False)
h1 = _venue_yelp_hours_fri(yelp_fx["venue_id"])
set_clean_environment(True)
h2 = _venue_yelp_hours_fri(yelp_fx["venue_id"])
set_clean_environment(False)
h3 = _venue_yelp_hours_fri(yelp_fx["venue_id"])

check("flip noisy→clean→noisy: noisy values match", h1 == h3,
      f"h1='{h1}' h3='{h3}'")
check("flip noisy→clean→noisy: clean differs from noisy", h2 != h1)

# Clean up so subsequent test files see a noisy default
_reset_state()
set_clean_environment(False)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"PASSED: {PASS}   FAILED: {FAIL}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
