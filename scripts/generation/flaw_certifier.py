"""Flaw Certifier v0 -- pure, dependency-free module.

Given a `wrong_info` row from the TravelBench corpus DB, this module assembles
the set of evidentiary *claims* about the (venue, affected_field) pair, then
computes a small vector of structural properties X that we hypothesize predict
how hard the planted flaw is for an agent to detect / repair.

The certifier does NOT touch generation or scoring. It is read-only analysis
tooling: it reads the already-stored corpus and emits numbers.

A *claim* is one source asserting a value for the affected field:
    {
        "source_id":  str,          # doc_id, "yelp:<venue>", or "official:<venue>"
        "surface":    str,          # 'yelp' | 'blog' | 'forum' | 'official'
        "value":      str,          # the asserted value (stringified)
        "is_correct": bool,         # value == ground-truth correct_value
        "weight":     float,        # default weight (uniform-ish)
        "weight_eng": float,        # engagement-weighted variant (NOT default)
    }

Source-of-truth conventions (verified against the NYC corpus):
  * Yelp serves only the `hours_*` fields. When wrong_info.source_type=='yelp'
    the yelp listing carries the INCORRECT value; when 'blog'/'forum' the yelp
    listing carries the CORRECT (ground-truth) value. We never assume -- we read
    the actual yelp_<field> cell and compare to correct_value.
  * doc_venue_roles rows with role 'incorrect_source' assert the wrong value;
    'truth_carrier' assert the correct value; 'neutral' are ignored for value.
  * official_site_docs hours always match ground truth, but are frequently null;
    only counted when has_official_site AND the field cell is populated.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List, Optional


# Fields that a Yelp listing actually carries (yelp_<field> columns exist).
_YELP_SERVED_FIELDS = {
    "hours_mon", "hours_tue", "hours_wed", "hours_thu",
    "hours_fri", "hours_sat", "hours_sun",
}

# Fields official_site_docs carries as per-field columns (hours only).
_OFFICIAL_SERVED_FIELDS = {
    "hours_mon", "hours_tue", "hours_wed", "hours_thu",
    "hours_fri", "hours_sat", "hours_sun",
}

OFFICIAL_WEIGHT = 3.0
BASE_WEIGHT = 1.0


def _norm(v: Any) -> Optional[str]:
    """Normalize a cell to a comparable string (None stays None)."""
    if v is None:
        return None
    return str(v).strip()


def _values_equal(a: Any, b: Any) -> bool:
    na, nb = _norm(a), _norm(b)
    if na is None or nb is None:
        return False
    return na == nb


def load_flaw_evidence(conn: sqlite3.Connection, wrong_info_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the list of claims for one wrong_info row.

    `wrong_info_row` must expose: wrong_info_id, venue_id, affected_field,
    incorrect_value, correct_value, source_type. A sqlite3.Row works directly.
    """
    venue_id = wrong_info_row["venue_id"]
    field = wrong_info_row["affected_field"]
    wrong_info_id = wrong_info_row["wrong_info_id"]
    correct_value = wrong_info_row["correct_value"]

    claims: List[Dict[str, Any]] = []

    # --- Yelp claim (only for fields Yelp serves) -------------------------
    if field in _YELP_SERVED_FIELDS:
        col = "yelp_" + field
        row = conn.execute(
            f"SELECT {col} AS v FROM yelp_listings WHERE venue_id = ?",
            (venue_id,),
        ).fetchone()
        if row is not None and row["v"] is not None:
            yval = row["v"]
            claims.append({
                "source_id": f"yelp:{venue_id}",
                "surface": "yelp",
                "value": _norm(yval),
                "is_correct": _values_equal(yval, correct_value),
                "weight": BASE_WEIGHT,
                "weight_eng": BASE_WEIGHT,  # yelp has no engagement signal here
            })

    # --- Doc claims (incorrect_source / truth_carrier) --------------------
    # Match wrong_info_id where the column is populated; a doc tied to a
    # different wrong_info_id for the same venue is not evidence for THIS field.
    doc_rows = conn.execute(
        """
        SELECT dvr.doc_id, dvr.role, dvr.wrong_info_id,
               sd.doc_type, sd.likes
        FROM doc_venue_roles dvr
        JOIN source_docs sd ON sd.doc_id = dvr.doc_id
        WHERE dvr.venue_id = ?
          AND dvr.role IN ('incorrect_source', 'truth_carrier')
        """,
        (venue_id,),
    ).fetchall()

    for d in doc_rows:
        # If the role row names a wrong_info_id, it must match ours.
        if d["wrong_info_id"] is not None and d["wrong_info_id"] != wrong_info_id:
            continue
        is_correct = (d["role"] == "truth_carrier")
        value = correct_value if is_correct else wrong_info_row["incorrect_value"]
        surface = d["doc_type"] or "blog"
        likes = d["likes"] if d["likes"] is not None else 0
        claims.append({
            "source_id": d["doc_id"],
            "surface": surface,
            "value": _norm(value),
            "is_correct": is_correct,
            "weight": BASE_WEIGHT,
            "weight_eng": BASE_WEIGHT + math.log1p(max(0, likes)),
        })

    # --- Official claim (only when site exists AND field cell populated) ---
    if field in _OFFICIAL_SERVED_FIELDS:
        has_site_row = conn.execute(
            "SELECT has_official_site FROM venues WHERE venue_id = ?",
            (venue_id,),
        ).fetchone()
        has_site = bool(has_site_row["has_official_site"]) if has_site_row else False
        if has_site:
            off = conn.execute(
                f"SELECT {field} AS v FROM official_site_docs WHERE venue_id = ?",
                (venue_id,),
            ).fetchone()
            if off is not None and off["v"] is not None and _norm(off["v"]) != "":
                claims.append({
                    "source_id": f"official:{venue_id}",
                    "surface": "official",
                    "value": _norm(off["v"]),
                    # official always reflects ground truth
                    "is_correct": True,
                    "weight": OFFICIAL_WEIGHT,
                    "weight_eng": OFFICIAL_WEIGHT,
                })

    return claims


def certify(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the certifier vector X from a list of claims.

    Returns:
        valid          -- bool, the flaw is well-posed (refutable but not trivial)
        detectability  -- int, # of correct (truth-asserting) independent claims
        repairability  -- float in [0,1], correct_mass / total_mass
        difficulty_bin -- 'easy'|'med'|'hard' (meaningful only where valid)
        n_correct      -- int, # correct claims
        n_wrong        -- int, # wrong claims
        has_official   -- bool, an official claim is present
        wrong_surface  -- str|None, surface(s) carrying the lie (sorted, joined)
    """
    correct_mass = sum(c["weight"] for c in claims if c["is_correct"])
    wrong_mass = sum(c["weight"] for c in claims if not c["is_correct"])
    correct_mass_eng = sum(c["weight_eng"] for c in claims if c["is_correct"])
    wrong_mass_eng = sum(c["weight_eng"] for c in claims if not c["is_correct"])

    n_correct = sum(1 for c in claims if c["is_correct"])
    n_wrong = sum(1 for c in claims if not c["is_correct"])

    detectability = n_correct  # independent sources contradicting the lie

    total = correct_mass + wrong_mass
    repairability = (correct_mass / total) if total > 0 else 0.0

    total_eng = correct_mass_eng + wrong_mass_eng
    repairability_eng = (correct_mass_eng / total_eng) if total_eng > 0 else 0.0

    valid = (detectability >= 1) and (0.0 < repairability < 1.0)

    if repairability >= 0.66:
        difficulty_bin = "easy"
    elif repairability >= 0.4:
        difficulty_bin = "med"
    else:
        difficulty_bin = "hard"

    has_official = any(c["surface"] == "official" for c in claims)
    wrong_surfaces = sorted({c["surface"] for c in claims if not c["is_correct"]})
    wrong_surface = ",".join(wrong_surfaces) if wrong_surfaces else None

    return {
        "valid": valid,
        "detectability": detectability,
        "repairability": round(repairability, 4),
        "repairability_eng": round(repairability_eng, 4),
        "difficulty_bin": difficulty_bin,
        "n_correct": n_correct,
        "n_wrong": n_wrong,
        "correct_mass": correct_mass,
        "wrong_mass": wrong_mass,
        "has_official": has_official,
        "wrong_surface": wrong_surface,
    }


def open_corpus(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def certify_all(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Load every wrong_info row, certify it, and return joined records."""
    rows = conn.execute(
        """
        SELECT w.*, v.name AS venue_name
        FROM wrong_info w
        JOIN venues v ON v.venue_id = w.venue_id
        ORDER BY v.name
        """
    ).fetchall()
    out = []
    for r in rows:
        claims = load_flaw_evidence(conn, r)
        cert = certify(claims)
        rec = {
            "wrong_info_id": r["wrong_info_id"],
            "venue_id": r["venue_id"],
            "venue_name": r["venue_name"],
            "affected_field": r["affected_field"],
            "source_type": r["source_type"],
            "category": r["wrong_info_category"],
            "n_claims": len(claims),
            **cert,
        }
        out.append(rec)
    return out


def _main() -> None:
    import argparse

    default_db = "data/cities/New_York/runs/test_70/travelbench.db"
    ap = argparse.ArgumentParser(description="Certify all flaws in a corpus DB.")
    ap.add_argument("--db", default=default_db)
    args = ap.parse_args()

    conn = open_corpus(args.db)
    recs = certify_all(conn)

    hdr = ["venue", "field", "src", "det", "repair", "bin", "valid", "nC", "nW", "off", "wsurf"]
    print(f"{hdr[0]:<26} {hdr[1]:<22} {hdr[2]:<5} {hdr[3]:>3} {hdr[4]:>7} "
          f"{hdr[5]:<5} {hdr[6]:<5} {hdr[7]:>3} {hdr[8]:>3} {hdr[9]:>3} {hdr[10]}")
    print("-" * 110)
    for r in recs:
        print(f"{r['venue_name'][:25]:<26} {r['affected_field']:<22} "
              f"{r['source_type']:<5} {r['detectability']:>3} "
              f"{r['repairability']:>7.3f} {r['difficulty_bin']:<5} "
              f"{str(r['valid']):<5} {r['n_correct']:>3} {r['n_wrong']:>3} "
              f"{('Y' if r['has_official'] else '-'):>3} {r['wrong_surface'] or '-'}")
    print(f"\nTotal flaws: {len(recs)}")


if __name__ == "__main__":
    _main()
