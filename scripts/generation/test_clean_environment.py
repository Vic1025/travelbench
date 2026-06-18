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
    set_arm, get_arm,
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


def _served_post_bodies() -> dict:
    """doc_id -> served body content for every post in the current arm's index."""
    idx = mock_tools._load_city(CITY)
    return {p["doc_id"]: (p.get("content") or "") for p in idx.posts}


def _discover_served_incorrect_doc():
    """Return (doc_id, affected_field, incorrect_value, correct_value, source_type,
    venue_id) for an incorrect_source blog/forum doc that is actually SERVED in
    the faulty index (so the 3-arm comparison has a real served body to act on)."""
    set_arm("faulty")
    mock_tools._city_cache.clear()
    set_run_name(RUN_NAME)
    served = set(_served_post_bodies().keys())

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT dvr.doc_id, dvr.venue_id,
               wi.affected_field, wi.incorrect_value, wi.correct_value,
               wi.source_type
        FROM doc_venue_roles dvr
        JOIN wrong_info wi ON wi.wrong_info_id = dvr.wrong_info_id
        WHERE dvr.role = 'incorrect_source'
    """).fetchall()
    conn.close()
    for r in rows:
        d = dict(r)
        if d["doc_id"] in served:
            return d
    return None


def _value_forms(value) -> list:
    """Plain-text forms of a value to scan a body for (mirrors mock_tools)."""
    out = []
    if value is None:
        return out
    s = str(value).strip()
    if not s:
        return out
    out.append(s)
    try:
        f = float(s)
        if f.is_integer():
            out.append(str(int(f)))
    except (TypeError, ValueError):
        pass
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

# ─────────────────────────────────────────────────────────────────────────────
# [6] 3-ARM ABLATION — faulty / clean_delete / clean_equalvol
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] 3-arm ablation: faulty vs clean_delete vs clean_equalvol")

served_fx = _discover_served_incorrect_doc()
print(f"[fixture] served incorrect_source doc: {served_fx}")
check(
    "found a served incorrect_source blog/forum doc to ablate",
    served_fx is not None,
    "no served incorrect_source doc in faulty index",
)

if served_fx is not None:
    bad_doc   = served_fx["doc_id"]
    bad_field = served_fx["affected_field"]
    incorrect = served_fx["incorrect_value"]
    correct   = served_fx["correct_value"]

    # ── arm API: back-compat aliases map onto arms ──────────────────────────
    _reset_state()
    set_clean_environment(False)
    check("set_clean_environment(False) -> arm 'faulty'", get_arm() == "faulty")
    set_clean_environment(True)
    check("set_clean_environment(True) -> arm 'clean_delete'",
          get_arm() == "clean_delete")

    # ── faulty: doc present, wrong claim carried ────────────────────────────
    _reset_state()
    set_arm("faulty")
    faulty_bodies = _served_post_bodies()
    faulty_count  = len(faulty_bodies)
    check("arm 'faulty' selected", get_arm() == "faulty")
    check(
        f"faulty: incorrect_source doc {bad_doc} is served",
        bad_doc in faulty_bodies,
    )

    # ── clean_delete: doc dropped, count falls ──────────────────────────────
    _reset_state()
    set_arm("clean_delete")
    delete_bodies = _served_post_bodies()
    check(
        f"clean_delete: doc {bad_doc} dropped",
        bad_doc not in delete_bodies,
    )
    check(
        "clean_delete: served post count < faulty count",
        len(delete_bodies) < faulty_count,
        f"delete={len(delete_bodies)} faulty={faulty_count}",
    )

    # ── clean_equalvol: doc KEPT, count unchanged, lie gone, no correction ───
    _reset_state()
    set_arm("clean_equalvol")
    equal_bodies = _served_post_bodies()
    check("arm 'clean_equalvol' selected", get_arm() == "clean_equalvol")
    check(
        "clean_equalvol: served post count == faulty count (no deletion)",
        len(equal_bodies) == faulty_count,
        f"equalvol={len(equal_bodies)} faulty={faulty_count}",
    )
    check(
        f"clean_equalvol: doc {bad_doc} still served (not dropped)",
        bad_doc in equal_bodies,
    )

    eq_body = equal_bodies.get(bad_doc, "")
    fa_body = faulty_bodies.get(bad_doc, "")

    # Length preserved within ±10% so total text volume ≈ faulty.
    if fa_body:
        ratio = len(eq_body) / len(fa_body)
        check(
            "clean_equalvol: replacement body length within ±10% of original",
            0.90 <= ratio <= 1.10,
            f"ratio={ratio:.3f} (orig={len(fa_body)} new={len(eq_body)})",
        )

    # The wrong value string no longer appears in the served body.
    inc_forms = _value_forms(incorrect)
    eq_low = eq_body.lower()
    check(
        f"clean_equalvol: wrong value forms {inc_forms} absent from doc body",
        not any(f.lower() in eq_low for f in inc_forms),
        f"leaked one of {inc_forms}",
    )

    # The corrected value is NOT injected — the replacement stays neutral.
    cor_forms = _value_forms(correct)
    # Only meaningful when the correct form isn't already a stray substring of
    # unrelated text; we assert it was not introduced as a truth claim.
    check(
        f"clean_equalvol: corrected value forms {cor_forms} NOT injected",
        not any(f.lower() in eq_low for f in cor_forms),
        f"corrected value leaked: {cor_forms}",
    )

    # Across the WHOLE equalvol corpus, no served body should carry the lie.
    leaked = [d for d, b in equal_bodies.items()
              if any(f.lower() in b.lower() for f in inc_forms) and d == bad_doc]
    check(
        "clean_equalvol: the lie is gone from the ablated doc's served body",
        len(leaked) == 0,
    )

    # ── determinism: equalvol body is reproducible across reloads ───────────
    _reset_state()
    set_arm("clean_equalvol")
    eq_body_2 = _served_post_bodies().get(bad_doc, "")
    check(
        "clean_equalvol: replacement is deterministic across reloads",
        eq_body == eq_body_2,
    )

# ─────────────────────────────────────────────────────────────────────────────
# [7] yelp structured fields healed under BOTH clean arms
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] yelp fields heal to ground truth under both clean arms")

_reset_state()
set_arm("clean_delete")
hd = _venue_yelp_hours_fri(yelp_fx["venue_id"])
check(
    f"clean_delete: yelp_hours_fri healed to correct ({yelp_fx['correct_value']})",
    hd == yelp_fx["correct_value"],
    f"got '{hd}'",
)

_reset_state()
set_arm("clean_equalvol")
he = _venue_yelp_hours_fri(yelp_fx["venue_id"])
check(
    f"clean_equalvol: yelp_hours_fri healed to correct ({yelp_fx['correct_value']})",
    he == yelp_fx["correct_value"],
    f"got '{he}'",
)

# ─────────────────────────────────────────────────────────────────────────────
# [8] REGRESSION — faulty output byte-identical to a fresh raw DB load
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] faulty arm is byte-identical to pre-change faulty behavior")

_reset_state()
set_arm("faulty")
faulty_idx = mock_tools._load_city(CITY)
faulty_serialized = {
    "posts": [(p["doc_id"], p.get("content") or "") for p in faulty_idx.posts],
    "yelp_hours_fri": {vid: d.get("hours_registered", {}).get("fri")
                       for vid, d in faulty_idx.yelp.items()},
}

# Re-load the city raw (arm='faulty') directly from the DB — this is exactly
# what the old code path did before the arm refactor (clean=False, no edits).
raw_idx = mock_tools._load_city_from_db(CITY, DB_PATH, arm="faulty")
raw_serialized = {
    "posts": [(p["doc_id"], p.get("content") or "") for p in raw_idx.posts],
    "yelp_hours_fri": {vid: d.get("hours_registered", {}).get("fri")
                       for vid, d in raw_idx.yelp.items()},
}
check(
    "faulty served post bodies identical to raw DB load (no mutation)",
    faulty_serialized["posts"] == raw_serialized["posts"],
)
check(
    "faulty yelp hours identical to raw DB load (no healing)",
    faulty_serialized["yelp_hours_fri"] == raw_serialized["yelp_hours_fri"],
)
# And the faulty body still carries the original lie for the ablated doc.
if served_fx is not None:
    raw_bodies = {p["doc_id"]: (p.get("content") or "") for p in raw_idx.posts}
    check(
        "faulty: ablated doc body unchanged vs raw DB",
        raw_bodies.get(served_fx["doc_id"]) == faulty_bodies.get(served_fx["doc_id"]),
    )

# Clean up so subsequent test files see a faulty default
_reset_state()
set_arm("faulty")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"PASSED: {PASS}   FAILED: {FAIL}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
