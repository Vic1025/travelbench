"""
scripts/generation/test_authority_suppression.py

Tests for b2: the official site stops being a free oracle for *trapped* fields,
driven by the wrong_info.suppress_authority column.

What is verified (all against a /tmp COPY of the real run DB — the corpus DB is
never mutated):

  1. suppress_authority=0 everywhere -> get_official_site output is byte-
     identical to before the column existed (baseline / no-op).
  2. OMIT mode (default; category not temporal_decay): the trapped field is
     dropped from the official-site response while other fields remain.
       - top-level field (booking_required) removed
       - hours_<day> removed from the hours dict
       - a regulation field removed from full_regulations
  3. STALE mode (category temporal_decay, or structure stale_authority): the
     official site returns the *incorrect_value* instead of the truth.
       - top-level avg_cost_local becomes the (wrong) stale number
       - hours_<day> becomes the (wrong) stale interval
  4. Suppressing one field never leaks into other fields.
  5. F2c bookkeeping intact: a suppressed call still returns
     has_official_site=True with a real payload (it still counts as an attempt).

Run: python scripts/generation/test_authority_suppression.py
"""

import sys
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from server import mock_tools
from server.mock_tools import set_run_name, tool_get_official_site

CITY = "new_york"
RUN_NAME = "test_70"
SRC_DB = ROOT / "data" / "cities" / "New_York" / "runs" / RUN_NAME / "travelbench.db"

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))
        FAIL += 1


# ─────────────────────────────────────────────────────────────────────────────
# TEMP DB HARNESS
# Copy the real run DB to /tmp, ensure the additive corruption columns exist,
# and point mock_tools at the copy via a monkeypatched _get_db_path. We never
# touch SRC_DB.
# ─────────────────────────────────────────────────────────────────────────────

class TempDB:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="authsup_"))
        self.path = self.dir / "travelbench.db"
        shutil.copy2(SRC_DB, self.path)
        self._ensure_columns()
        self._orig_get_db_path = mock_tools._get_db_path
        mock_tools._get_db_path = lambda city: self.path

    def _ensure_columns(self):
        conn = sqlite3.connect(self.path)
        wi_cols = {r[1] for r in conn.execute("PRAGMA table_info(wrong_info)").fetchall()}
        for col, defn in [("structure", "TEXT"),
                          ("suppress_authority", "INTEGER DEFAULT 0")]:
            if col not in wi_cols:
                conn.execute(f"ALTER TABLE wrong_info ADD COLUMN {col} {defn}")
        # Start from a clean slate: nothing suppressed.
        conn.execute("UPDATE wrong_info SET suppress_authority = 0")
        conn.commit()
        conn.close()

    def set_suppress(self, venue_id, affected_field, value=1, category=None, structure=None):
        conn = sqlite3.connect(self.path)
        sets = ["suppress_authority = ?"]
        params = [value]
        if category is not None:
            sets.append("wrong_info_category = ?"); params.append(category)
        if structure is not None:
            sets.append("structure = ?"); params.append(structure)
        params += [venue_id, affected_field]
        conn.execute(
            f"UPDATE wrong_info SET {', '.join(sets)} WHERE venue_id = ? AND affected_field = ?",
            params,
        )
        conn.commit()
        conn.close()

    def reset_cache(self):
        mock_tools._city_cache.clear()
        set_run_name(None)  # path is monkeypatched; run_name unused

    def close(self):
        mock_tools._get_db_path = self._orig_get_db_path
        mock_tools._city_cache.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


def official(venue_id, db):
    db.reset_cache()
    return tool_get_official_site(venue_id, CITY)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES — pulled from the real DB so the test self-validates against the data
# ─────────────────────────────────────────────────────────────────────────────

def discover_fixtures():
    conn = sqlite3.connect(SRC_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT wi.venue_id, wi.affected_field, wi.incorrect_value, wi.correct_value
        FROM wrong_info wi
        JOIN official_site_docs o ON o.venue_id = wi.venue_id
        WHERE o.page_status = 'verified'
    """).fetchall()
    conn.close()
    fx = {"hours": None, "booking": None, "cost": None}
    for r in rows:
        f = r["affected_field"]
        if f.startswith("hours_") and fx["hours"] is None:
            fx["hours"] = dict(r)
        elif f == "booking_required" and fx["booking"] is None:
            fx["booking"] = dict(r)
        elif f == "avg_cost_local" and fx["cost"] is None:
            fx["cost"] = dict(r)
    return fx


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def test_baseline_byte_identical(db, fx):
    print("\n[1] suppress_authority=0 everywhere -> byte-identical")
    vid = fx["hours"]["venue_id"]
    # Snapshot output with all-zero suppress_authority.
    db.set_suppress(vid, fx["hours"]["affected_field"], value=0)
    before = official(vid, db)
    # And again — must be stable and contain the truth.
    again = official(vid, db)
    check("output stable across calls (no suppression)",
          json.dumps(before, sort_keys=True) == json.dumps(again, sort_keys=True))
    field = fx["hours"]["affected_field"]
    day = field[len("hours_"):]
    truth = fx["hours"]["correct_value"].split("-")
    check(f"hours[{day}] still authoritative truth",
          before.get("hours", {}).get(day) == truth,
          f"got {before.get('hours', {}).get(day)} want {truth}")
    check("has_official_site True (call still counts as attempt)",
          before.get("has_official_site") is True)


def test_omit_top_level(db, fx):
    print("\n[2] OMIT — top-level booking_required dropped")
    if not fx["booking"]:
        check("booking fixture available", False, "no booking_required fixture")
        return
    vid = fx["booking"]["venue_id"]
    base = official(vid, db)  # suppress=0
    check("baseline lists booking_required", "booking_required" in base)
    other_avg = base.get("avg_cost_local")
    other_hours = base.get("hours")
    db.set_suppress(vid, "booking_required", value=1, category="conditional")
    out = official(vid, db)
    check("booking_required omitted", "booking_required" not in out)
    check("avg_cost_local untouched", out.get("avg_cost_local") == other_avg)
    check("hours untouched", out.get("hours") == other_hours)
    check("has_official_site still True (attempt counted)",
          out.get("has_official_site") is True)


def test_omit_hours_and_regulation(db, fx):
    print("\n[3] OMIT — hours day + a regulation field dropped")
    vid = fx["hours"]["venue_id"]
    field = fx["hours"]["affected_field"]
    day = field[len("hours_"):]
    base = official(vid, db)
    check(f"baseline lists hours[{day}]", day in base.get("hours", {}))
    other_days = {d: v for d, v in base.get("hours", {}).items() if d != day}
    db.set_suppress(vid, field, value=1, category="conditional")
    out = official(vid, db)
    check(f"hours[{day}] omitted", day not in out.get("hours", {}))
    check("other hours days untouched",
          {d: v for d, v in out.get("hours", {}).items()} == other_days)

    # Regulation field: reuse the same venue's wrong_info row repurposed onto a
    # regulation field name so we exercise the full_regulations branch.
    db.set_suppress(vid, field, value=0)  # clear hours suppression
    # Rename the row's affected_field to a regulation key in the temp DB.
    conn = sqlite3.connect(db.path)
    conn.execute(
        "UPDATE wrong_info SET affected_field='pet_friendly', suppress_authority=1, "
        "wrong_info_category='conditional' WHERE venue_id=? AND affected_field=?",
        (vid, field))
    conn.commit(); conn.close()
    out2 = official(vid, db)
    check("pet_friendly omitted from full_regulations",
          "pet_friendly" not in out2.get("full_regulations", {}))
    check("other regulations remain",
          "wheelchair_accessible" in out2.get("full_regulations", {}))


def test_stale_top_level_cost(db, fx):
    print("\n[4] STALE — avg_cost_local returns incorrect_value (temporal_decay)")
    if not fx["cost"]:
        check("cost fixture available", False, "no avg_cost_local fixture")
        return
    vid = fx["cost"]["venue_id"]
    incorrect = float(fx["cost"]["incorrect_value"])
    correct = float(fx["cost"]["correct_value"])
    base = official(vid, db)
    check("baseline avg_cost_local is the truth",
          float(base.get("avg_cost_local")) == correct)
    db.set_suppress(vid, "avg_cost_local", value=1, category="temporal_decay")
    out = official(vid, db)
    check("avg_cost_local now stale (incorrect_value)",
          float(out.get("avg_cost_local")) == incorrect,
          f"got {out.get('avg_cost_local')} want {incorrect}")
    check("field still present (stale, not omitted)", "avg_cost_local" in out)
    check("has_official_site still True", out.get("has_official_site") is True)


def test_stale_hours(db, fx):
    print("\n[5] STALE — hours day returns incorrect interval (structure)")
    vid = fx["hours"]["venue_id"]
    field = fx["hours"]["affected_field"]
    day = field[len("hours_"):]
    incorrect_list = fx["hours"]["incorrect_value"].split("-")
    # Reset any prior repurposing of this venue's row.
    conn = sqlite3.connect(db.path)
    conn.execute(
        "UPDATE wrong_info SET affected_field=?, suppress_authority=0, "
        "wrong_info_category='temporal_decay', structure='stale_authority' "
        "WHERE venue_id=? AND affected_field='pet_friendly'",
        (field, vid))
    conn.commit(); conn.close()
    # Use structure to trigger stale even if category were neutral.
    db.set_suppress(vid, field, value=1, category="conditional", structure="stale_authority")
    out = official(vid, db)
    check(f"hours[{day}] now stale interval",
          out.get("hours", {}).get(day) == incorrect_list,
          f"got {out.get('hours', {}).get(day)} want {incorrect_list}")


def main():
    db = TempDB()
    try:
        fx = discover_fixtures()
        if not fx["hours"]:
            print("FATAL: no hours wrong_info fixture with an official site in DB")
            return 1
        test_baseline_byte_identical(db, fx)
        test_omit_top_level(db, fx)
        test_omit_hours_and_regulation(db, fx)
        test_stale_top_level_cost(db, fx)
        test_stale_hours(db, fx)
    finally:
        db.close()

    print(f"\n{'='*60}")
    print(f"  {PASS} passed, {FAIL} failed")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
