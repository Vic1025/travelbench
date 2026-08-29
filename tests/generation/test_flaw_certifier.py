"""Self-contained unit tests for flaw_certifier.certify() on real NYC flaws.

Reads the real corpus DB. Skips gracefully if the DB is missing.
Run: python3 scripts/generation/test_flaw_certifier.py
"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.generation.flaw_certifier import load_flaw_evidence, certify  # noqa: E402

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..",
    "data", "cities", "New_York", "runs", "test_70", "travelbench.db",
)


def _open():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _flaw(conn, wrong_info_id):
    return conn.execute(
        "SELECT * FROM wrong_info WHERE wrong_info_id = ?", (wrong_info_id,)
    ).fetchone()


def _flaws_for_venue(conn, venue_id):
    return conn.execute(
        "SELECT * FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchall()


def test_ess_a_bagel(conn):
    # gzSEluN hours_fri: yelp WRONG, 1 incorrect blog + 1 truth blog, no official.
    row = _flaw(conn, "ZgtjSkbZ")
    assert row is not None and row["venue_id"] == "gzSEluN"
    claims = load_flaw_evidence(conn, row)
    cert = certify(claims)
    # yelp carries incorrect 06:00-18:00, GT 06:00-16:00 -> yelp is wrong
    yelp = [c for c in claims if c["surface"] == "yelp"]
    assert len(yelp) == 1 and yelp[0]["is_correct"] is False, "yelp must be wrong here"
    assert cert["detectability"] >= 1, cert
    assert 0.0 < cert["repairability"] < 1.0, cert
    assert cert["valid"] is True, cert
    assert cert["has_official"] is False, cert
    # wrong side heavier: 1 correct (blog) vs 2 wrong (blog + yelp)
    assert cert["n_wrong"] > cert["n_correct"], cert
    return cert


def test_westlight(conn):
    # zjuQnie hours_sun: yelp CORRECT, 1 incorrect blog + 1 truth forum, official=GT.
    row = _flaw(conn, "JL7V2sNX")
    assert row is not None and row["venue_id"] == "zjuQnie"
    claims = load_flaw_evidence(conn, row)
    cert = certify(claims)
    yelp = [c for c in claims if c["surface"] == "yelp"]
    assert len(yelp) == 1 and yelp[0]["is_correct"] is True, "yelp must be correct here"
    assert cert["has_official"] is True, cert
    assert cert["detectability"] >= 1, cert
    assert 0.0 < cert["repairability"] < 1.0, cert
    assert cert["valid"] is True, cert
    return cert


def test_halal_guys(conn):
    # ROn06pn has 2 flaws -- both must load & certify without error.
    flaws = _flaws_for_venue(conn, "ROn06pn")
    assert len(flaws) == 2, f"expected 2 flaws, got {len(flaws)}"
    for f in flaws:
        claims = load_flaw_evidence(conn, f)
        cert = certify(claims)
        assert isinstance(cert["repairability"], float)
        assert cert["detectability"] >= 0
    return [f["affected_field"] for f in flaws]


def test_relative_repairability(ess, west):
    # Westlight (yelp correct + official) should be strictly more repairable
    # than Ess-a-Bagel (yelp wrong, no official).
    assert west["repairability"] > ess["repairability"], (west, ess)


def test_b15_cost_flaw():
    """b1.5: a cost flaw (served from the yelp_avg_cost_local overlay) yields a
    well-posed certification: detectability>=1 and 0<repairability<1. Runs the
    operator on a /tmp copy so the real corpus DB is never mutated."""
    import shutil, tempfile
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from scripts.generation.inject_flaws import inject, DEFAULT_PROFILE

    tmp = tempfile.mkdtemp(prefix="fc_b15_")
    try:
        dst = os.path.join(tmp, "dst.db")
        plan = inject(DB_PATH, dst, "b1-seed-001", DEFAULT_PROFILE,
                      created_at="2026-01-01T00:00:00Z", write_plan=False)
        cost_kept = [k for k in plan["kept"] if k["field"] == "avg_cost_local"]
        assert cost_kept, "b1.5: expected >=1 kept avg_cost_local flaw"
        dconn = sqlite3.connect(dst); dconn.row_factory = sqlite3.Row
        try:
            k = cost_kept[0]
            row = _flaw(dconn, k["wrong_info_id"])
            claims = load_flaw_evidence(dconn, row)
            cert = certify(claims)
            # The yelp overlay carries the lie -> at least one wrong claim.
            yelp = [c for c in claims if c["surface"] == "yelp"]
            assert yelp and yelp[0]["is_correct"] is False, \
                ("yelp overlay must carry the cost lie", yelp)
            assert cert["detectability"] >= 1, cert
            assert 0.0 < cert["repairability"] < 1.0, cert
            assert cert["valid"] is True, cert
            return cert
        finally:
            dconn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if not os.path.exists(DB_PATH):
        print(f"SKIP: corpus DB missing at {DB_PATH}")
        return 0
    conn = _open()
    try:
        ess = test_ess_a_bagel(conn)
        print(f"PASS test_ess_a_bagel  -> {ess}")
        west = test_westlight(conn)
        print(f"PASS test_westlight    -> {west}")
        fields = test_halal_guys(conn)
        print(f"PASS test_halal_guys   -> fields={fields}")
        test_relative_repairability(ess, west)
        print(f"PASS test_relative_repairability "
              f"(west={west['repairability']} > ess={ess['repairability']})")
        cost = test_b15_cost_flaw()
        print(f"PASS test_b15_cost_flaw -> {cost}")
    finally:
        conn.close()
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
