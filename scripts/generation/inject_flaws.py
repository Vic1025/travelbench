"""scripts/generation/inject_flaws.py

b1 corruption operator (STRUCTURED FIELDS ONLY).

Places seeded, load-bearing, FALSE-POSITIVE flaws on the venue fields that a
task's BINDING constraints actually read, so they bite the GT-based scorer.
Validity is gated by the existing flaw_certifier. Fully reproducible.

Pipeline:
  1. COPY src DB -> dst DB (never mutate src; canonical GT stays clean).
  2. LOAD tasks + binding constraints; build the load-bearing target set
     = (constraint field) x (venues in that task's candidate pool),
       restricted to structured fields a mask can corrupt.
  3. For each target, pick the matching mask; sub-seed = hash(master_seed,
     venue_id, field). Compute the false-positive wrong value.
  4. APPLY served corruption:
       - hours_*  -> set yelp_listings.yelp_<field> to the wrong value AND
                     establish a truth_carrier doc role so the certifier sees
                     a contradicting truth (detectability >= 1).
       - non-hours -> the certifier reads only doc claims for these fields, so
                      establish an incorrect_source role (the lie) + a
                      truth_carrier role (the truth) from existing docs.
       Write a wrong_info row with full provenance.
  5. VALIDITY GATE: certify; keep only if valid (detectability>=1 and
     0<repairability<1). suppress_authority per profile, but never if it would
     drop repairability to 0 (then skip-suppress or skip-flaw; logged).
  6. Record ONE corruption_runs row. Emit flaw_plan.json next to dst.

Determinism / reproducibility:
  * Target order is sorted by (venue_id, field) — order-independent.
  * Sub-seeds are blake2b hashes of (master_seed, venue_id, field).
  * created_at is passed in (no datetime.now()).
  * Running twice with same (src, seed, profile) yields identical dst overlay
    (asserted via overlay_hash).

GT immutability: the dst `venues` table keeps TRUE values. Only yelp_listings
(served) + wrong_info (overlay) + doc_venue_roles (evidence wiring) carry/expose
the lie. The evaluator scores against `venues` and is untouched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.generation.db import get_connection
from scripts.generation import flaw_certifier as fc
from scripts.generation import flaw_masks as fm

CORRUPTOR_VERSION = "b1.v1"

# Structured fields a mask can corrupt (the only fields we trap).
_STRUCTURED_FIELDS = {
    "avg_cost_local", "price_tier", "booking_required",
    "recommended_visit_minutes",
} | set(fm.HOURS_FIELDS)

_HOURS_FIELDS = set(fm.HOURS_FIELDS)

# b1.5: non-hours structured fields served from a yelp_<field> overlay column
# on yelp_listings (preferred over the immutable venues GT in search_yelp).
_COST_PRICE_OVERLAY = {"avg_cost_local", "price_tier", "booking_required"}


def _overlay_typed(field: str, wrong_value):
    """Coerce a (TEXT) wrong value to the type the yelp_<field> overlay column
    expects: REAL for cost, INTEGER for booking, TEXT for price_tier."""
    if field == "avg_cost_local":
        try:
            return float(wrong_value)
        except (TypeError, ValueError):
            return wrong_value
    if field == "booking_required":
        s = str(wrong_value).strip().lower()
        return 1 if s in ("1", "true", "yes") else 0
    return str(wrong_value)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILES
# ─────────────────────────────────────────────────────────────────────────────
#
# A profile parameterises a corruption run. profile = {
#   "profile_id": str, "profile_version": str,
#   "suppress_official": bool,   # suppress official authority where present
#   "max_flaws": int|None,       # cap on kept flaws (None = unlimited)
# }
DEFAULT_PROFILE = {
    "profile_id": "b1_default",
    "profile_version": "1",
    "suppress_official": False,
    "max_flaws": None,
}


# ─────────────────────────────────────────────────────────────────────────────
# SEEDING
# ─────────────────────────────────────────────────────────────────────────────

def _sub_seed(master_seed: str, venue_id: str, field: str) -> int:
    """Deterministic, order-independent per-(venue,field) sub-seed."""
    h = hashlib.blake2b(
        f"{master_seed}|{venue_id}|{field}".encode("utf-8"), digest_size=8
    )
    return int.from_bytes(h.digest(), "big")


def _new_id(prefix: str, master_seed: str, venue_id: str, field: str) -> str:
    """Deterministic ID derived from the sub-seed (so re-runs reproduce)."""
    h = hashlib.blake2b(
        f"{prefix}|{master_seed}|{venue_id}|{field}".encode("utf-8"),
        digest_size=6,
    )
    return prefix[0].upper() + h.hexdigest()[:9]


def _gt_hash(conn: sqlite3.Connection) -> str:
    """Hash of the source venues table (canonical GT) for provenance."""
    rows = conn.execute("SELECT * FROM venues ORDER BY venue_id").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM venues LIMIT 1").description]
    h = hashlib.blake2b(digest_size=16)
    for r in rows:
        h.update("|".join(str(r[c]) for c in cols).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# BINDING-CONSTRAINT FIELD EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
#
# A binding constraint READS one or more venue fields. We map a constraint to
# the structured venue field(s) it depends on:
#   * hard_constraints type 'hours_check'  -> ALL hours_<dow> (any scheduled
#     venue's hours are read by the GT scorer).
#   * personal_constraints:
#       - condition.field == 'price_tier'                  -> price_tier
#       - condition.field == 'recommended_visit_minutes'   -> recommended_visit_minutes
#       - condition.field == 'booking_required' / 'reservation_required'
#                                                           -> booking_required
#       - aggregation.sum == 'estimated_cost_local'        -> avg_cost_local
#       - aggregation.field / condition.field on cost      -> avg_cost_local
#   * tag / district / category conditions are NOT structured-corruptible here
#     (b1 scope) and are ignored.

# Map a constraint-referenced field name to a corruptible venue column.
_FIELD_ALIAS = {
    "estimated_cost_local": "avg_cost_local",
    "avg_cost_local": "avg_cost_local",
    "cost": "avg_cost_local",
    "price_tier": "price_tier",
    "recommended_visit_minutes": "recommended_visit_minutes",
    "booking_required": "booking_required",
    "reservation_required": "booking_required",
}


def _constraint_fields(task: Dict) -> set:
    """Return the set of corruptible venue fields the task's binding
    constraints read. Includes hours for any task with an hours_check.
    """
    fields: set = set()
    rubric = task.get("rubric", {}) or {}

    # Hard constraints: an hours_check makes hours load-bearing for every
    # scheduled venue.
    for hc in rubric.get("hard_constraints", []) or []:
        if hc.get("type") == "hours_check":
            fields.update(_HOURS_FIELDS)

    # Personal constraints: inspect condition + aggregation.
    for pc in rubric.get("personal_constraints", []) or []:
        cond = pc.get("condition", {}) or {}
        agg = pc.get("aggregation", {})

        # condition.field
        cfield = cond.get("field")
        if cfield in _FIELD_ALIAS:
            fields.add(_FIELD_ALIAS[cfield])

        if isinstance(agg, dict):
            # aggregation.sum (e.g. budget ceiling on estimated_cost_local)
            sfield = agg.get("sum")
            if sfield in _FIELD_ALIAS:
                fields.add(_FIELD_ALIAS[sfield])
            # aggregation.field used by count_distinct etc.
            afield = agg.get("field")
            if afield in _FIELD_ALIAS:
                fields.add(_FIELD_ALIAS[afield])

    return fields & _STRUCTURED_FIELDS


def _task_pool_venue_ids(task: Dict, all_pool: List[Dict]) -> List[str]:
    """Venues in this task's candidate pool.

    The candidate pool is the city's universal pool after the task's own
    universal/exclusion filters (same notion generate_task uses). We
    additionally always include any required_venue_ids.
    """
    try:
        from scripts.generation.generate_task import _apply_pool_filters
        narrowed = _apply_pool_filters(all_pool, task)
    except Exception:
        narrowed = all_pool
    vids = {v["venue_id"] for v in narrowed}
    for rvid in (task.get("rubric", {}) or {}).get("required_venue_ids", []) or []:
        vids.add(rvid)
    return sorted(vids)


# ─────────────────────────────────────────────────────────────────────────────
# TASK LOADING
# ─────────────────────────────────────────────────────────────────────────────

def _load_tasks(task_paths: List[Path]) -> List[Dict]:
    tasks = []
    for p in task_paths:
        try:
            tasks.append(json.loads(Path(p).read_text()))
        except Exception:
            continue
    return tasks


def discover_task_files(city: str, run_name: str) -> List[Path]:
    """Find canonical task JSONs for a city/run.

    Canonical tasks live under tasks/runs/{run}/{window}/{model}/*.json
    (the agent_logs/ siblings are NOT canonical). We pick the per-model
    task files and de-duplicate by task_id.
    """
    root = (Path(__file__).parent.parent.parent / "data" / "cities" / city /
            "tasks" / "runs" / run_name)
    if not root.exists():
        return []
    out = []
    for p in sorted(root.rglob("*.json")):
        if "agent_logs" in p.parts:
            continue
        out.append(p)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE WIRING (structured only)
# ─────────────────────────────────────────────────────────────────────────────

def _venue_docs(conn: sqlite3.Connection, venue_id: str) -> List[str]:
    """doc_ids that mention this venue, deterministic order."""
    rows = conn.execute(
        "SELECT DISTINCT doc_id FROM doc_venue_roles WHERE venue_id = ? "
        "ORDER BY doc_id",
        (venue_id,),
    ).fetchall()
    return [r["doc_id"] for r in rows]


def _official_hours_truth(conn: sqlite3.Connection, venue_id: str, field: str) -> bool:
    """Does the official site carry a (truthful) value for this hours field?"""
    if field not in _HOURS_FIELDS:
        return False
    has = conn.execute(
        "SELECT has_official_site FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if not has or not has["has_official_site"]:
        return False
    off = conn.execute(
        f"SELECT {field} AS v FROM official_site_docs WHERE venue_id = ?",
        (venue_id,),
    ).fetchone()
    return off is not None and off["v"] is not None and str(off["v"]).strip() != ""


# ─────────────────────────────────────────────────────────────────────────────
# SERVABILITY GATE ("has teeth" invariant)
# ─────────────────────────────────────────────────────────────────────────────
#
# A flaw only has teeth if the value the PLANNING AGENT actually sees (via the
# real serving path in server/mock_tools) reflects the lie rather than the GT.
#
# The bug this guards against: inject() overwrites yelp_listings.yelp_<field>
# for hours_* (which search_yelp DOES serve, via the `hours` dict), but for
# avg_cost_local / price_tier / booking_required the served path in
# mock_tools.tool_search_yelp sources from the GROUND-TRUTH `venues` table (and
# in fact does not even surface those fields). The operator keeps `venues`
# immutable, so those lies are invisible to the agent — they have no teeth.
#
# We therefore probe the REAL serving path (mock_tools.tool_search_yelp against
# the dst DB) for every kept flaw and reject any whose served value == GT as
# `not_servable`, rolling back its staged changes.
#
# DEFERRED (v1 known limitation): cost/price/booking servability requires either
#   (a) a served-cost overlay in mock_tools — PREFER a `yelp_avg_cost_local` /
#       `yelp_price_tier` column on yelp_listings over the venues value — or
#   (b) NL evidence carrying the lie in the b3 blog/forum corpus.
# We do NOT implement that overlay here; we only gate honestly so these flaws
# are dropped as not_servable rather than passing the certifier with no teeth.

def _served_yelp_value(city: str, dst_db_path: str, venue_id: str,
                       venue_name: str, field: str):
    """Return the AGENT-VISIBLE value for (venue_id, field) via the REAL serving
    path: mock_tools.tool_search_yelp against the dst DB. Returns the served
    value as a string normalised to compare against the GT string, or None if
    the field is not surfaced by the serving path at all (i.e. not servable).

    We point mock_tools at the dst DB by monkeypatching _get_db_path and clearing
    the city cache (mirroring scripts/generation/test_authority_suppression.py).
    The caller is responsible for having COMMITTED the staged changes first,
    since mock_tools opens its own connection and only sees committed state.
    """
    from server import mock_tools
    from server.mock_tools import set_run_name, tool_search_yelp

    orig_get_db_path = mock_tools._get_db_path
    orig_arm = mock_tools.get_arm()
    try:
        mock_tools._get_db_path = lambda c: Path(dst_db_path)
        mock_tools.set_arm("faulty")            # served = as-generated (the lie)
        mock_tools._city_cache.clear()
        set_run_name(None)                      # path is monkeypatched

        res = tool_search_yelp(venue_name, city, top_k=50)
        match = next((r for r in res.get("results", [])
                      if r["venue_id"] == venue_id), None)
        if match is None:
            # Fall back to an empty query (returns all) in case name search
            # failed to surface the venue.
            res = tool_search_yelp("", city, top_k=10_000)
            match = next((r for r in res.get("results", [])
                          if r["venue_id"] == venue_id), None)
        if match is None:
            return None

        if field in _HOURS_FIELDS:
            day = field[len("hours_"):]
            hrs = match.get("hours", {}) or {}
            parts = hrs.get(day)
            if not parts:
                return None
            # search_yelp serves hours as ["HH:MM", "HH:MM"]; recompose to the
            # canonical "HH:MM-HH:MM" string the GT/wrong_info uses.
            if isinstance(parts, (list, tuple)) and len(parts) == 2:
                return f"{parts[0]}-{parts[1]}"
            return str(parts)

        # Non-hours structured fields (avg_cost_local / price_tier /
        # booking_required) are surfaced by tool_search_yelp (b1.5), served from
        # the yelp_* overlay when set else the venues GT. Normalise the served
        # value to the GT string convention so the gate compares like-for-like:
        #   - booking_required: served as bool True/False; GT is INTEGER 0/1.
        #   - avg_cost_local:   served as float; GT is REAL (float).
        #   - price_tier:       plain string.
        if field not in match:
            return None
        served = match.get(field)
        if served is None:
            return None
        if field == "booking_required":
            return "1" if bool(served) else "0"
        return str(served)
    finally:
        mock_tools._get_db_path = orig_get_db_path
        mock_tools.set_arm(orig_arm)
        mock_tools._city_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# CORE
# ─────────────────────────────────────────────────────────────────────────────

def _pick_mask(field: str, venue: Dict, master_seed: str) -> Optional[Dict]:
    """Pick a deterministic mask for (field, venue): first matching mask
    (in library order) whose predicate passes.
    """
    candidates = fm.masks_for_field(field)
    for m in candidates:
        try:
            if m["predicate"](venue):
                return m
        except Exception:
            continue
    return None


def inject(src_db_path: str, dst_db_path: str, master_seed: str,
           profile: Optional[Dict] = None,
           *, created_at: str = "1970-01-01T00:00:00Z",
           task_files: Optional[List[Path]] = None,
           city: str = "new_york", run_name: str = "test_70",
           write_plan: bool = True) -> Dict[str, Any]:
    """Run the operator. Returns the flaw_plan summary dict."""
    profile = dict(profile or DEFAULT_PROFILE)
    profile_id = profile["profile_id"]
    profile_version = profile.get("profile_version", "1")
    suppress_official = bool(profile.get("suppress_official", False))
    max_flaws = profile.get("max_flaws")

    src_db_path = str(src_db_path)
    dst_db_path = str(dst_db_path)

    # 1) COPY src -> dst (never mutate src).
    Path(dst_db_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_db_path, dst_db_path)
    # Copy any WAL/SHM sidecars so the dst reflects committed src state fully.
    for ext in ("-wal", "-shm"):
        side = Path(src_db_path + ext)
        if side.exists():
            shutil.copyfile(str(side), dst_db_path + ext)

    conn = get_connection(Path(dst_db_path))   # applies migrations on dst
    conn.row_factory = sqlite3.Row

    run_id = _new_id("run", master_seed, profile_id, profile_version)
    gt_hash = _gt_hash(conn)

    # 2) LOAD tasks + binding constraints; build load-bearing target set.
    from scripts.generation.pool_utils import load_venue_pool
    all_pool = load_venue_pool(city, db_path=Path(dst_db_path))
    pool_by_id = {v["venue_id"]: v for v in all_pool}

    if task_files is None:
        task_files = discover_task_files(city, run_name)
    tasks = _load_tasks(task_files)

    # target = (venue_id, field). Use a dict to dedupe; value tracks origin tasks.
    targets: Dict[Tuple[str, str], List[str]] = {}
    for t in tasks:
        fields = _constraint_fields(t)
        if not fields:
            continue
        vids = _task_pool_venue_ids(t, all_pool)
        for vid in vids:
            if vid not in pool_by_id:
                continue
            for f in fields:
                targets.setdefault((vid, f), []).append(t.get("task_id", "?"))

    target_list = sorted(targets.keys())   # order-independent

    # Track placed (venue,field) to keep evidence sources disjoint: one flaw
    # per (venue,field), and never collide with a pre-existing wrong_info row
    # on the same (venue,field).
    existing = conn.execute(
        "SELECT venue_id, affected_field FROM wrong_info"
    ).fetchall()
    occupied = {(r["venue_id"], r["affected_field"]) for r in existing}

    kept: List[Dict] = []
    rejected: List[Dict] = []
    # (venue_id, doc_id) pairs consumed as evidence — kept disjoint across flaws.
    consumed_docs: set = set()

    for (venue_id, field) in target_list:
        origin_tasks = sorted(set(targets[(venue_id, field)]))
        if (venue_id, field) in occupied:
            rejected.append({
                "venue_id": venue_id, "field": field, "mask_id": None,
                "reason": "occupied: existing flaw on this (venue,field) — disjoint scope",
                "tasks": origin_tasks,
            })
            continue

        venue = pool_by_id[venue_id]
        mask = _pick_mask(field, venue, master_seed)
        if mask is None:
            rejected.append({
                "venue_id": venue_id, "field": field, "mask_id": None,
                "reason": "no matching mask / predicate failed",
                "tasks": origin_tasks,
            })
            continue

        # Read the TRUE value from the canonical venues table.
        true_val = conn.execute(
            f"SELECT {field} AS v FROM venues WHERE venue_id = ?", (venue_id,)
        ).fetchone()
        true_value = true_val["v"] if true_val else None

        wrong_value = mask["value_fn"](true_value)
        if wrong_value is None:
            rejected.append({
                "venue_id": venue_id, "field": field, "mask_id": mask["id"],
                "reason": f"value_fn produced no change for true_value={true_value!r}",
                "tasks": origin_tasks,
            })
            continue

        correct_value = str(true_value)

        if max_flaws is not None and len(kept) >= max_flaws:
            rejected.append({
                "venue_id": venue_id, "field": field, "mask_id": mask["id"],
                "reason": "max_flaws reached",
                "tasks": origin_tasks,
            })
            continue

        # --- Stage the served corruption + evidence wiring (savepoint) -------
        wrong_info_id = _new_id("wi", master_seed, venue_id, field)
        is_hours = field in _HOURS_FIELDS
        source_type = "yelp" if is_hours else "blog"

        consumed_snapshot = set(consumed_docs)   # restore on rollback
        conn.execute("SAVEPOINT flaw")
        try:
            # Insert wrong_info row first (referenced by doc_venue_roles FK).
            suppress_flag = 1 if (suppress_official and is_hours
                                  and _official_hours_truth(conn, venue_id, field)) else 0
            conn.execute(
                """INSERT INTO wrong_info
                   (wrong_info_id, venue_id, affected_field, incorrect_value,
                    correct_value, source_type, wrong_info_category, origin_story,
                    seed_used, structure, profile_id, mask_id, suppress_authority)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (wrong_info_id, venue_id, field, str(wrong_value), correct_value,
                 source_type, "propagation_error",
                 f"b1 operator: mask {mask['id']} on {field}",
                 str(_sub_seed(master_seed, venue_id, field)),
                 mask["structure"], profile_id, mask["id"], suppress_flag),
            )

            if is_hours:
                # Served lie on yelp.
                conn.execute(
                    f"UPDATE yelp_listings SET yelp_{field} = ? WHERE venue_id = ?",
                    (str(wrong_value), venue_id),
                )
                # Truth evidence: prefer official (already truthful); else flip
                # a neutral doc to truth_carrier so detectability >= 1.
                has_official = _official_hours_truth(conn, venue_id, field)
                if not has_official:
                    _assign_truth_carrier(conn, venue_id, wrong_info_id, consumed_docs)
            else:
                # b1.5: served lie on the yelp_* cost/price overlay column so
                # search_yelp serves the lie (preferred over the immutable
                # venues GT). Analogous to hours writing yelp_hours_*.
                if field in _COST_PRICE_OVERLAY:
                    overlay_col = f"yelp_{field}"
                    conn.execute(
                        f"UPDATE yelp_listings SET {overlay_col} = ? WHERE venue_id = ?",
                        (_overlay_typed(field, wrong_value), venue_id),
                    )
                # Certifier reads the yelp overlay claim + doc claims for these
                # fields: wire one incorrect_source (the lie) + one
                # truth_carrier so detectability/repairability are well-posed.
                _assign_doc_evidence(conn, venue_id, wrong_info_id, consumed_docs)

            # --- VALIDITY GATE ------------------------------------------------
            wi_row = conn.execute(
                "SELECT * FROM wrong_info WHERE wrong_info_id = ?", (wrong_info_id,)
            ).fetchone()
            claims = fc.load_flaw_evidence(conn, wi_row)
            cert = fc.certify(claims)

            # If suppressing official dropped repairability to 0, un-suppress
            # and re-certify rather than discard.
            if suppress_flag and not (cert["detectability"] >= 1
                                      and 0.0 < cert["repairability"] < 1.0):
                conn.execute(
                    "UPDATE wrong_info SET suppress_authority = 0 WHERE wrong_info_id = ?",
                    (wrong_info_id,),
                )
                suppress_flag = 0
                # Note: certifier here treats official as always-present truth;
                # suppression is enforced downstream by mock_tools. We re-cert to
                # confirm validity without relying on suppression.
                wi_row = conn.execute(
                    "SELECT * FROM wrong_info WHERE wrong_info_id = ?", (wrong_info_id,)
                ).fetchone()
                claims = fc.load_flaw_evidence(conn, wi_row)
                cert = fc.certify(claims)

            valid = (cert["detectability"] >= 1 and
                     0.0 < cert["repairability"] < 1.0)

            if not valid:
                conn.execute("ROLLBACK TO flaw")
                conn.execute("RELEASE flaw")
                consumed_docs = consumed_snapshot
                rejected.append({
                    "venue_id": venue_id, "field": field, "mask_id": mask["id"],
                    "reason": (f"certifier gate failed: detectability="
                               f"{cert['detectability']}, repairability="
                               f"{cert['repairability']}"),
                    "tasks": origin_tasks,
                })
                continue

            # Persist certifier signals on the wrong_info row.
            conn.execute(
                "UPDATE wrong_info SET detectability = ?, repairability = ? "
                "WHERE wrong_info_id = ?",
                (int(cert["detectability"]), float(cert["repairability"]),
                 wrong_info_id),
            )
            conn.execute("RELEASE flaw")
            occupied.add((venue_id, field))
            kept.append({
                "wrong_info_id": wrong_info_id,
                "venue_id": venue_id, "field": field, "mask_id": mask["id"],
                "structure": mask["structure"], "source_type": source_type,
                "incorrect_value": str(wrong_value),
                "correct_value": correct_value,
                "detectability": int(cert["detectability"]),
                "repairability": float(cert["repairability"]),
                "suppress_authority": int(suppress_flag),
                "tasks": origin_tasks,
            })
        except Exception as e:  # noqa: BLE001
            conn.execute("ROLLBACK TO flaw")
            conn.execute("RELEASE flaw")
            consumed_docs = consumed_snapshot
            rejected.append({
                "venue_id": venue_id, "field": field, "mask_id": mask["id"],
                "reason": f"exception: {type(e).__name__}: {e}",
                "tasks": origin_tasks,
            })

    # ── SERVABILITY GATE ("has teeth") ───────────────────────────────────────
    # Commit the staged flaws so the REAL serving path (mock_tools, which opens
    # its own connection) sees committed state, then probe each kept flaw via
    # tool_search_yelp. A flaw whose AGENT-VISIBLE value still equals GT has no
    # teeth — reject it as `not_servable` and roll back its staged changes.
    #
    # In v1 this drops ALL cost/price/booking flaws (tool_search_yelp sources
    # those from the immutable `venues` GT and does not even surface them), while
    # hours_* flaws survive (search_yelp serves yelp_hours_* via its `hours`
    # dict). See the DEFERRED note above and in flaw_plan.json.
    conn.commit()
    not_servable_by_field: Dict[str, int] = {}
    survivors: List[Dict] = []
    for k in kept:
        venue_id = k["venue_id"]
        field = k["field"]
        venue_name = pool_by_id.get(venue_id, {}).get("name", "")
        gt_val = conn.execute(
            f"SELECT {field} AS v FROM venues WHERE venue_id = ?", (venue_id,)
        ).fetchone()
        gt_str = str(gt_val["v"]) if gt_val else None

        served = _served_yelp_value(city, dst_db_path, venue_id, venue_name, field)
        # Servable iff the served value EXISTS and differs from GT (the lie is
        # actually visible to the planning agent).
        is_servable = served is not None and str(served) != gt_str
        k["served_value"] = served
        k["servable"] = bool(is_servable)
        if is_servable:
            survivors.append(k)
            continue

        # Not servable → roll back this flaw's staged changes.
        not_servable_by_field[field] = not_servable_by_field.get(field, 0) + 1
        _rollback_flaw(conn, k["wrong_info_id"], venue_id, field)
        occupied.discard((venue_id, field))
        rejected.append({
            "venue_id": venue_id, "field": field, "mask_id": k["mask_id"],
            "reason": (f"not_servable: served value {served!r} == GT {gt_str!r} "
                       f"(lie not visible via search_yelp serving path)"),
            "served_value": served,
            "tasks": k.get("tasks", []),
        })
    kept = survivors
    conn.commit()

    # 6) Record ONE corruption_runs row.
    conn.execute(
        """INSERT OR REPLACE INTO corruption_runs
           (run_id, master_seed, profile_id, profile_version,
            corruptor_version, gt_hash, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, master_seed, profile_id, profile_version,
         CORRUPTOR_VERSION, gt_hash, created_at),
    )
    conn.commit()

    overlay_hash = _overlay_hash(conn)

    plan = {
        "run_id": run_id,
        "master_seed": master_seed,
        "profile": profile,
        "corruptor_version": CORRUPTOR_VERSION,
        "gt_hash": gt_hash,
        "overlay_hash": overlay_hash,
        "created_at": created_at,
        "n_tasks": len(tasks),
        "n_targets": len(target_list),
        "n_kept": len(kept),
        "n_rejected": len(rejected),
        "not_servable_by_field": not_servable_by_field,
        "servability_note": (
            "b1.5: cost/price/booking servability IS implemented. The operator "
            "writes the lie into a yelp_<field> overlay column on yelp_listings "
            "(yelp_avg_cost_local / yelp_price_tier / yelp_booking_required) and "
            "search_yelp serves the overlay (preferred over the immutable venues "
            "GT) and surfaces avg_cost_local/price_tier/booking_required in its "
            "results. hours_* remain served from yelp_hours_*. A flaw is still "
            "rejected as not_servable only if its served value happens to equal "
            "GT (e.g. value_fn produced no effective change at serve time)."
        ),
        "kept": kept,
        "rejected": rejected,
    }

    conn.close()

    if write_plan:
        plan_path = Path(dst_db_path).parent / "flaw_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2))
        plan["_plan_path"] = str(plan_path)

    return plan


def _rollback_flaw(conn: sqlite3.Connection, wrong_info_id: str,
                   venue_id: str, field: str) -> None:
    """Undo a single flaw's staged changes (used by the servability gate after
    commit). Restores the served yelp_listings value from GT (hours only),
    reverts any doc_venue_roles wired to this flaw back to 'neutral', and deletes
    the wrong_info row. Must run before the doc_venue_roles delete because of the
    wrong_info_id FK direction (roles reference wrong_info).
    """
    # Revert evidence wiring first (rows reference wrong_info_id).
    conn.execute(
        "UPDATE doc_venue_roles SET role = 'neutral', wrong_info_id = NULL "
        "WHERE wrong_info_id = ?",
        (wrong_info_id,),
    )
    # Restore the served yelp value for the columns the operator overwrites.
    # hours_*: write GT back onto yelp_hours_* (hours are served from that cell).
    # cost/price/booking: NULL the yelp_<field> overlay so the serve path falls
    # back to the immutable venues GT value.
    if field in _HOURS_FIELDS:
        gt = conn.execute(
            f"SELECT {field} AS v FROM venues WHERE venue_id = ?", (venue_id,)
        ).fetchone()
        if gt is not None:
            conn.execute(
                f"UPDATE yelp_listings SET yelp_{field} = ? WHERE venue_id = ?",
                (gt["v"], venue_id),
            )
    elif field in _COST_PRICE_OVERLAY:
        conn.execute(
            f"UPDATE yelp_listings SET yelp_{field} = NULL WHERE venue_id = ?",
            (venue_id,),
        )
    conn.execute(
        "DELETE FROM wrong_info WHERE wrong_info_id = ?", (wrong_info_id,)
    )


def _free_neutral_docs(conn: sqlite3.Connection, venue_id: str,
                       consumed: set) -> List[str]:
    """Neutral docs for this venue not already consumed by another flaw
    (disjoint evidence sources), deterministic order.
    """
    docs = conn.execute(
        "SELECT doc_id FROM doc_venue_roles WHERE venue_id = ? AND role = 'neutral' "
        "ORDER BY doc_id",
        (venue_id,),
    ).fetchall()
    return [d["doc_id"] for d in docs if (venue_id, d["doc_id"]) not in consumed]


def _assign_truth_carrier(conn: sqlite3.Connection, venue_id: str,
                          wrong_info_id: str, consumed: set) -> None:
    """Flip the first free neutral doc for this venue to a truth_carrier tied
    to wrong_info_id (deterministic). No-op if none free and no official truth
    — caller handles the consequence via the gate. Records consumed doc.
    """
    free = _free_neutral_docs(conn, venue_id, consumed)
    if not free:
        return
    doc_id = free[0]
    conn.execute(
        "UPDATE doc_venue_roles SET role = 'truth_carrier', wrong_info_id = ? "
        "WHERE venue_id = ? AND doc_id = ?",
        (wrong_info_id, venue_id, doc_id),
    )
    consumed.add((venue_id, doc_id))


def _assign_doc_evidence(conn: sqlite3.Connection, venue_id: str,
                         wrong_info_id: str, consumed: set) -> None:
    """For non-hours fields, wire one incorrect_source (the lie) + one
    truth_carrier (the truth) from the venue's FREE neutral docs (disjoint
    across flaws). Deterministic: first free -> incorrect_source, second ->
    truth_carrier. Raises if fewer than 2 free docs (caught -> rejected).
    """
    free = _free_neutral_docs(conn, venue_id, consumed)
    if len(free) < 2:
        raise ValueError(
            f"venue {venue_id} has <2 free neutral docs for disjoint evidence wiring"
        )
    incorrect_doc, truth_doc = free[0], free[1]
    conn.execute(
        "UPDATE doc_venue_roles SET role = 'incorrect_source', wrong_info_id = ? "
        "WHERE venue_id = ? AND doc_id = ?",
        (wrong_info_id, venue_id, incorrect_doc),
    )
    conn.execute(
        "UPDATE doc_venue_roles SET role = 'truth_carrier', wrong_info_id = ? "
        "WHERE venue_id = ? AND doc_id = ?",
        (wrong_info_id, venue_id, truth_doc),
    )
    consumed.add((venue_id, incorrect_doc))
    consumed.add((venue_id, truth_doc))


def _overlay_hash(conn: sqlite3.Connection) -> str:
    """Hash of everything the operator may have changed in dst: the b1
    wrong_info rows, the served yelp hours, and doc_venue_roles. Used to
    assert reproducibility across runs.
    """
    h = hashlib.blake2b(digest_size=16)

    wi = conn.execute(
        "SELECT wrong_info_id, venue_id, affected_field, incorrect_value, "
        "correct_value, source_type, structure, profile_id, mask_id, "
        "seed_used, detectability, repairability, suppress_authority "
        "FROM wrong_info WHERE mask_id IS NOT NULL ORDER BY wrong_info_id"
    ).fetchall()
    for r in wi:
        h.update(("|".join(str(x) for x in tuple(r))).encode("utf-8"))
        h.update(b"\n")

    yelp_cols = ", ".join(
        f"yelp_{f}" for f in sorted(_HOURS_FIELDS | _COST_PRICE_OVERLAY)
    )
    yl = conn.execute(
        f"SELECT venue_id, {yelp_cols} FROM yelp_listings ORDER BY venue_id"
    ).fetchall()
    for r in yl:
        h.update(("|".join(str(x) for x in tuple(r))).encode("utf-8"))
        h.update(b"\n")

    dvr = conn.execute(
        "SELECT doc_id, venue_id, role, wrong_info_id FROM doc_venue_roles "
        "ORDER BY doc_id, venue_id"
    ).fetchall()
    for r in dvr:
        h.update(("|".join(str(x) for x in tuple(r))).encode("utf-8"))
        h.update(b"\n")

    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="b1 flaw injector (structured fields).")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--seed", default="b1-seed-001")
    ap.add_argument("--city", default="new_york")
    ap.add_argument("--run-name", default="test_70")
    ap.add_argument("--suppress-official", action="store_true")
    ap.add_argument("--created-at", default="1970-01-01T00:00:00Z")
    args = ap.parse_args()

    prof = dict(DEFAULT_PROFILE)
    prof["suppress_official"] = args.suppress_official
    plan = inject(args.src, args.dst, args.seed, prof,
                  created_at=args.created_at, city=args.city,
                  run_name=args.run_name)
    print(f"run_id={plan['run_id']}  targets={plan['n_targets']}  "
          f"kept={plan['n_kept']}  rejected={plan['n_rejected']}")
    print(f"overlay_hash={plan['overlay_hash']}")
    print(f"plan: {plan.get('_plan_path')}")
