"""
scripts/generation/validate_corruption.py

Independent verification pass over a LOAD-BEARING corpus produced by
`inject_flaws.py` (the b1 corruption operator). Re-derives every invariant the
operator claims to uphold from the dst DB itself — it does NOT trust the
flaw_plan.json — and reports per-task "live wedge" coverage.

This is the corruption analogue of `validate_city.py`: same report shape
(print_report + JSON), same exit-code contract.

OPERATOR FLAWS vs LEGACY FLAWS
  The operator writes `wrong_info` rows with `mask_id` AND `structure` set
  (b1 provenance). Legacy / hand-authored wrong_info rows have mask_id NULL.
  We validate ONLY the operator rows.

HARD INVARIANTS (any failure -> nonzero exit):
  1. RECOVERABLE     — recompute the certifier on the flaw's stored evidence;
                       repairability must be > 0 AND match the stored value
                       within tolerance.
  2. HAS TEETH       — stored detectability >= 1 AND the flaw is SERVABLE: the
                       agent-visible value (mock_tools.tool_search_yelp against
                       this DB, arm 'faulty' — same path test_authority_
                       suppression uses) differs from the GT `venues` value.
  3. PLAUSIBLE       — the corrupted value is type-valid / in-domain:
                         hours_*  -> HH:MM-HH:MM, both within 00:00–24:00, open<close
                         avg_cost -> a number > 0
                         booking  -> in {0,1}
                         price    -> a non-empty string (rung label)
  4. DISJOINT SCOPES — at most one operator flaw per (venue, field); no two
                       operator flaws share an evidence doc (doc_venue_roles).
  5. RECOVERY PATH   — each flaw has >=1 truth_carrier doc OR a non-suppressed
                       official-site field carrying the truth (a reachable GT).

WARNINGS (non-blocking — surfaced, never fail the run):
  6. GT IMMUTABLE & SOLVABLE — with --src: the dst `venues` table hash MUST equal
                       src (HARD: a mismatch means the canonical GT was mutated).
                       Solvability is a heuristic: each affected task's GT pool
                       still holds >=1 feasible venue for its binding field
                       (flag, don't hard-fail, when uncertain).
  7. PARAMETRIC-SAFE — real venue names risk an LLM parametric override of the
                       served lie; we just surface a count.
  8. REPRODUCIBLE    — with --src: read the corruption_runs row (seed/profile),
                       re-run inject_flaws.inject into /tmp, assert the overlay
                       hash matches the corpus under test.

PER-TASK LIVE-WEDGE REPORT
  For each task (discovered the same way the operator discovers them), list its
  binding-constraint fields and whether >=1 in-pool venue carries a SERVABLE
  operator flaw on that field. Summarise: N tasks with >=1 live wedge / total.

Usage:
  python scripts/generation/validate_corruption.py --db <dst.db>
  python scripts/generation/validate_corruption.py --db <dst.db> --src <baseline.db>
  python scripts/generation/validate_corruption.py --db <dst.db> --json
"""

from __future__ import annotations

import sys
import json
import argparse
import re
import sqlite3
import shutil
import tempfile
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.generation import flaw_certifier as fc
from scripts.generation import inject_flaws as inj

# Tolerance for the recomputed-vs-stored repairability comparison.
REPAIR_TOL = 1e-4

_HOURS_FIELDS = set(inj._HOURS_FIELDS)


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _operator_flaws(conn: sqlite3.Connection):
    """Operator-produced rows only: mask_id (and structure) set."""
    return conn.execute(
        "SELECT * FROM wrong_info "
        "WHERE mask_id IS NOT NULL AND structure IS NOT NULL "
        "ORDER BY wrong_info_id"
    ).fetchall()


_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_hhmm(s: str):
    m = _HHMM_RE.match(s.strip())
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= mm < 60):
        return None
    total = h * 60 + mm
    if not (0 <= total <= 24 * 60):
        return None
    return total


def _plausible(field: str, value: str) -> tuple[bool, str]:
    """Type-valid / in-domain check for the corrupted value."""
    if value is None:
        return False, "value is NULL"
    v = str(value).strip()
    if field in _HOURS_FIELDS:
        if "," in v:
            return False, f"split-service interval not allowed: {v!r}"
        if "-" not in v:
            return False, f"hours not HH:MM-HH:MM: {v!r}"
        o, c = v.split("-", 1)
        om, cm = _parse_hhmm(o), _parse_hhmm(c)
        if om is None or cm is None:
            return False, f"hours endpoints not valid HH:MM in 00:00–24:00: {v!r}"
        if not (om < cm):
            return False, f"hours open >= close: {v!r}"
        return True, ""
    if field == "avg_cost_local":
        try:
            f = float(v)
        except ValueError:
            return False, f"cost not numeric: {v!r}"
        if f <= 0:
            return False, f"cost not > 0: {f}"
        return True, ""
    if field == "booking_required":
        if v not in ("0", "1"):
            return False, f"booking not in {{0,1}}: {v!r}"
        return True, ""
    if field == "price_tier":
        if v == "":
            return False, "price_tier empty"
        return True, ""
    # recommended_visit_minutes etc.: require a positive number
    try:
        f = float(v)
        if f <= 0:
            return False, f"value not > 0: {f}"
        return True, ""
    except ValueError:
        return True, ""  # unknown field type — don't penalise


# ─────────────────────────────────────────────────────────────────────────────
# main validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_corruption(db_path: Path, src_path: Path | None = None,
                        city: str = "new_york",
                        run_name: str = "test_70") -> dict:
    issues: list[str] = []       # HARD failures -> nonzero exit
    warnings: list[str] = []     # non-blocking
    stats: dict = {
        "db": str(db_path),
        "src": str(src_path) if src_path else None,
        "city": city,
        "run_name": run_name,
    }

    db_path = Path(db_path)
    if not db_path.exists():
        return {"passed": False, "issues": [f"DB not found: {db_path}"],
                "warnings": [], "stats": stats}

    conn = _open(db_path)
    flaws = _operator_flaws(conn)
    stats["n_operator_flaws"] = len(flaws)
    stats["n_legacy_flaws"] = conn.execute(
        "SELECT COUNT(*) FROM wrong_info WHERE mask_id IS NULL"
    ).fetchone()[0]

    if not flaws:
        conn.close()
        return {"passed": False,
                "issues": ["No operator flaws (mask_id+structure set) found — "
                           "nothing to validate. Was inject_flaws run on this DB?"],
                "warnings": [], "stats": stats}

    # Per-field counters for the report.
    field_counts: dict[str, int] = defaultdict(int)

    # Precompute served values via the REAL serving path once per flaw.
    name_by_vid = {}
    for r in conn.execute("SELECT venue_id, name FROM venues").fetchall():
        name_by_vid[r["venue_id"]] = r["name"]

    # (venue, field) -> servable bool, for the live-wedge report.
    servable_vf: dict[tuple[str, str], bool] = {}

    # disjoint-scope tracking
    vf_seen: dict[tuple[str, str], int] = defaultdict(int)
    doc_owner: dict[str, str] = {}   # doc_id -> wrong_info_id (operator flaws)

    n_recoverable = n_teeth = n_plausible = n_recovery = 0

    for f in flaws:
        wid = f["wrong_info_id"]
        vid = f["venue_id"]
        field = f["affected_field"]
        field_counts[field] += 1
        vf_seen[(vid, field)] += 1

        # ── 1. RECOVERABLE ──────────────────────────────────────────────────
        cert = fc.certify(fc.load_flaw_evidence(conn, f))
        recomputed = cert["repairability"]
        stored = f["repairability"]
        ok_recover = recomputed > 0.0
        if not ok_recover:
            issues.append(
                f"[RECOVERABLE] {wid} ({vid}/{field}): recomputed repairability "
                f"= {recomputed} (must be > 0; no path back to GT)"
            )
        elif stored is not None and abs(float(stored) - recomputed) > REPAIR_TOL:
            issues.append(
                f"[RECOVERABLE] {wid} ({vid}/{field}): stored repairability "
                f"{stored} != recomputed {recomputed} (drifted beyond tol)"
            )
        else:
            n_recoverable += 1

        # ── 2. HAS TEETH (detectability + servable) ─────────────────────────
        det = f["detectability"]
        served = inj._served_yelp_value(
            city, str(db_path), vid, name_by_vid.get(vid, ""), field)
        gt_row = conn.execute(
            f"SELECT {field} AS v FROM venues WHERE venue_id = ?", (vid,)
        ).fetchone()
        gt_str = str(gt_row["v"]) if gt_row else None
        is_servable = served is not None and str(served) != gt_str
        servable_vf[(vid, field)] = is_servable
        if det is None or int(det) < 1:
            issues.append(
                f"[HAS TEETH] {wid} ({vid}/{field}): detectability={det} (< 1)")
        elif not is_servable:
            issues.append(
                f"[HAS TEETH] {wid} ({vid}/{field}): not servable — agent-visible "
                f"value {served!r} == GT {gt_str!r} (lie invisible via search_yelp)")
        else:
            n_teeth += 1

        # ── 3. PLAUSIBLE ────────────────────────────────────────────────────
        ok_plaus, why = _plausible(field, f["incorrect_value"])
        if not ok_plaus:
            issues.append(
                f"[PLAUSIBLE] {wid} ({vid}/{field}): incorrect_value "
                f"{f['incorrect_value']!r} — {why}")
        else:
            n_plausible += 1

        # ── 5. RECOVERY PATH (inferable) ────────────────────────────────────
        n_truth_carriers = conn.execute(
            "SELECT COUNT(*) FROM doc_venue_roles "
            "WHERE wrong_info_id = ? AND role = 'truth_carrier'", (wid,)
        ).fetchone()[0]
        # A non-suppressed official-site field carrying the truth also counts.
        official_truth = (
            int(f["suppress_authority"] or 0) == 0
            and field in _HOURS_FIELDS
            and inj._official_hours_truth(conn, vid, field)
        )
        if n_truth_carriers >= 1 or official_truth:
            n_recovery += 1
        else:
            issues.append(
                f"[RECOVERY PATH] {wid} ({vid}/{field}): no truth_carrier doc and "
                f"no non-suppressed official truth — GT is unreachable")

        # disjoint-scope: record evidence doc ownership
        for d in conn.execute(
            "SELECT doc_id FROM doc_venue_roles WHERE wrong_info_id = ?", (wid,)
        ).fetchall():
            did = d["doc_id"]
            if did in doc_owner and doc_owner[did] != wid:
                issues.append(
                    f"[DISJOINT SCOPES] doc {did} shared by operator flaws "
                    f"{doc_owner[did]} and {wid}")
            else:
                doc_owner[did] = wid

    # ── 4. DISJOINT SCOPES: at most one flaw per (venue,field) ──────────────
    dup_vf = {k: c for k, c in vf_seen.items() if c > 1}
    if dup_vf:
        for (vid, field), c in sorted(dup_vf.items()):
            issues.append(
                f"[DISJOINT SCOPES] {c} operator flaws on the same "
                f"(venue,field)=({vid},{field}) — must be <= 1")

    stats["field_counts"] = dict(field_counts)
    stats["invariant_pass_counts"] = {
        "recoverable": n_recoverable,
        "has_teeth": n_teeth,
        "plausible": n_plausible,
        "recovery_path": n_recovery,
        "total_flaws": len(flaws),
    }

    # ── 6. GT IMMUTABLE & SOLVABLE (needs --src) ────────────────────────────
    if src_path is not None:
        src_path = Path(src_path)
        if not src_path.exists():
            warnings.append(f"--src given but not found: {src_path}")
        else:
            src_conn = _open(src_path)
            dst_gt = inj._gt_hash(conn)
            src_gt = inj._gt_hash(src_conn)
            stats["gt_hash_dst"] = dst_gt
            stats["gt_hash_src"] = src_gt
            if dst_gt != src_gt:
                issues.append(
                    "[GT IMMUTABLE] dst venues hash != src venues hash — the "
                    "canonical GT was mutated by corruption")
            src_conn.close()

    # ── PER-TASK LIVE-WEDGE REPORT ──────────────────────────────────────────
    # Discover tasks the same way the operator does, and compute per-task
    # live-wedge coverage + the (warning-only) solvability heuristic.
    live_wedge, solvability_warnings = _live_wedge_report(
        conn, city, run_name, servable_vf)
    stats["live_wedge"] = live_wedge
    warnings.extend(solvability_warnings)

    # ── 7. PARAMETRIC-SAFE (warn-only) ──────────────────────────────────────
    # Real venue names risk an LLM overriding the served lie from parametric
    # memory. Surface the count of distinct real venues carrying an operator lie.
    real_named = conn.execute(
        "SELECT COUNT(DISTINCT venue_id) FROM wrong_info "
        "WHERE mask_id IS NOT NULL AND structure IS NOT NULL"
    ).fetchone()[0]
    stats["parametric_risk_venues"] = real_named
    if real_named:
        warnings.append(
            f"[PARAMETRIC] {real_named} real venue(s) carry an operator lie — "
            f"a model may override the served value from parametric memory. "
            f"Non-blocking; surfaced for awareness.")

    # ── 8. REPRODUCIBLE (needs --src) ───────────────────────────────────────
    if src_path is not None and Path(src_path).exists():
        repro = _check_reproducible(conn, src_path, city, run_name)
        stats["reproducible"] = repro
        if repro.get("status") == "match":
            pass  # good
        elif repro.get("status") == "mismatch":
            warnings.append(
                f"[REPRODUCIBLE] re-run overlay hash {repro['rerun_hash'][:16]}… "
                f"!= corpus overlay hash {repro['corpus_hash'][:16]}… — "
                f"corpus may not be reproducible from its corruption_runs row")
        else:
            warnings.append(
                f"[REPRODUCIBLE] could not verify: {repro.get('reason')}")

    conn.close()

    passed = len(issues) == 0
    return {
        "passed": passed,
        "issues": issues,
        "warnings": warnings,
        "stats": stats,
    }


def _live_wedge_report(conn: sqlite3.Connection, city: str, run_name: str,
                       servable_vf: dict) -> tuple[dict, list]:
    """Per-task: for each binding-constraint field, is there >=1 in-pool venue
    carrying a SERVABLE operator flaw on that field? Returns (report, warnings).
    """
    warnings: list[str] = []
    from scripts.generation.pool_utils import load_venue_pool

    all_pool = load_venue_pool(city, db_path=Path(conn.execute(
        "PRAGMA database_list").fetchone()[2]))
    task_files = inj.discover_task_files(city, run_name)
    tasks = inj._load_tasks(task_files)

    per_task = []
    n_with_wedge = 0
    for t in tasks:
        tid = t.get("task_id", "?")
        fields = sorted(inj._constraint_fields(t))
        pool_vids = set(inj._task_pool_venue_ids(t, all_pool))
        field_live = {}
        for fld in fields:
            live = any(
                (vid, fld) in servable_vf and servable_vf[(vid, fld)]
                for vid in pool_vids
            )
            field_live[fld] = live
        has_wedge = any(field_live.values())
        if has_wedge:
            n_with_wedge += 1
        per_task.append({
            "task_id": tid,
            "binding_fields": fields,
            "live_fields": sorted(f for f, v in field_live.items() if v),
            "pool_size": len(pool_vids),
            "has_live_wedge": has_wedge,
        })

        # Solvability heuristic (warn-only): for the binding fields the task
        # reads, does the GT pool still hold >=1 venue feasible on that field?
        # GT is untouched by definition (invariant 6), so this is informational;
        # we only flag when a task with binding fields has an EMPTY pool, which
        # would make it unsatisfiable regardless of corruption.
        if fields and len(pool_vids) == 0:
            warnings.append(
                f"[SOLVABLE?] task {tid} reads binding fields {fields} but its "
                f"candidate pool is empty — possibly unsatisfiable (heuristic)")

    return {
        "n_tasks": len(tasks),
        "n_tasks_with_live_wedge": n_with_wedge,
        "per_task": per_task,
    }, warnings


def _check_reproducible(conn: sqlite3.Connection, src_path: Path,
                        city: str, run_name: str) -> dict:
    """Read the corruption_runs row (seed/profile), re-run inject into /tmp, and
    compare overlay hashes.
    """
    cr = conn.execute(
        "SELECT * FROM corruption_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if cr is None:
        return {"status": "unknown", "reason": "no corruption_runs row"}

    seed = cr["master_seed"]
    created_at = cr["created_at"]
    profile = dict(inj.DEFAULT_PROFILE)
    profile["profile_id"] = cr["profile_id"]
    profile["profile_version"] = cr["profile_version"]
    # suppress_official is not stored on the run row; infer from suppress flags.
    sup = conn.execute(
        "SELECT COUNT(*) FROM wrong_info "
        "WHERE mask_id IS NOT NULL AND suppress_authority = 1"
    ).fetchone()[0]
    profile["suppress_official"] = bool(sup)

    # Corpus overlay hash (recompute from this DB, do not trust the plan file).
    corpus_hash = inj._overlay_hash(conn)

    tmp = Path(tempfile.mkdtemp(prefix="valcorr_repro_"))
    try:
        dst = tmp / "rerun.db"
        plan = inj.inject(
            str(src_path), str(dst), seed, profile,
            created_at=created_at, city=city, run_name=run_name,
            write_plan=False)
        rerun_hash = plan["overlay_hash"]
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown", "reason": f"re-run failed: {type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "status": "match" if rerun_hash == corpus_hash else "mismatch",
        "corpus_hash": corpus_hash,
        "rerun_hash": rerun_hash,
        "seed": seed,
        "profile_id": profile["profile_id"],
    }


def print_report(result: dict) -> None:
    stats = result["stats"]
    ipc = stats.get("invariant_pass_counts", {})
    total = ipc.get("total_flaws", 0)

    print(f"\n{'='*64}")
    print(f"VALIDATE CORRUPTION: {stats.get('city','?')}/{stats.get('run_name','?')}")
    print(f"{'='*64}")
    print(f"DB:  {stats.get('db')}")
    if stats.get("src"):
        print(f"SRC: {stats.get('src')}")
    print(f"Operator flaws: {stats.get('n_operator_flaws', 0)}  "
          f"(legacy/ignored: {stats.get('n_legacy_flaws', 0)})")
    if stats.get("field_counts"):
        print(f"By field: {stats['field_counts']}")

    if total:
        print(f"\nHARD invariants (per operator flaw, {total} total):")
        print(f"  1. RECOVERABLE   : {ipc.get('recoverable',0)}/{total} pass")
        print(f"  2. HAS TEETH     : {ipc.get('has_teeth',0)}/{total} pass")
        print(f"  3. PLAUSIBLE     : {ipc.get('plausible',0)}/{total} pass")
        print(f"  4. DISJOINT SCOPE: (corpus-level; see issues)")
        print(f"  5. RECOVERY PATH : {ipc.get('recovery_path',0)}/{total} pass")

    if "gt_hash_dst" in stats:
        same = stats["gt_hash_dst"] == stats.get("gt_hash_src")
        print(f"  6. GT IMMUTABLE  : {'MATCH (GT untouched)' if same else 'MISMATCH'}")
    if "reproducible" in stats:
        r = stats["reproducible"]
        print(f"  8. REPRODUCIBLE  : {r.get('status')}")

    lw = stats.get("live_wedge", {})
    if lw:
        print(f"\nPER-TASK LIVE WEDGES: "
              f"{lw['n_tasks_with_live_wedge']}/{lw['n_tasks']} tasks have "
              f">=1 live wedge")
        for pt in lw.get("per_task", []):
            flag = "✓" if pt["has_live_wedge"] else "·"
            print(f"  {flag} {pt['task_id'][:48]:<48} "
                  f"pool={pt['pool_size']:>3}  "
                  f"live={pt['live_fields'] or '—'}")

    if result["warnings"]:
        print(f"\n⚠  Warnings ({len(result['warnings'])}):")
        for w in result["warnings"]:
            print(f"   • {w}")

    if result["issues"]:
        print(f"\n❌ Issues ({len(result['issues'])}):")
        for i in result["issues"]:
            print(f"   • {i}")
    else:
        print(f"\n✅ All hard invariants passed")

    print(f"\nResult: {'PASSED' if result['passed'] else 'FAILED'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Validate a load-bearing corruption corpus.")
    ap.add_argument("--db", required=True, type=Path,
                    help="dst corpus DB produced by inject_flaws.py")
    ap.add_argument("--src", type=Path, default=None,
                    help="baseline (clean) DB — enables GT-immutability and "
                         "reproducibility checks")
    ap.add_argument("--city", default="new_york")
    ap.add_argument("--run-name", default="test_70")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = validate_corruption(args.db, args.src,
                                 city=args.city, run_name=args.run_name)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
    sys.exit(0 if result["passed"] else 1)
