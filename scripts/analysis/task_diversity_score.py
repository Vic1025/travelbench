"""
scripts/analysis/task_diversity_score.py

Task diversity saturation analysis — at what point do generated tasks start
repeating constraint patterns and venue combinations?

Three metrics computed as each task is added:
  1. Pairwise constraint Jaccard similarity (higher = more repetition)
  2. Venue coverage fraction (fraction of pool touched by at least one task)
  3. Structural type entropy (max = log2(6) ≈ 2.58 for all 6 types equal)

Usage:
  python scripts/analysis/task_diversity_score.py --city london
  python scripts/analysis/task_diversity_score.py --city london --window lon_carnival_2026
  python scripts/analysis/task_diversity_score.py --city london --csv out.csv

Task files are loaded from:
  data/data1/tasks/unfiltered/<model>/<task>.json  (legacy path)
  OR passed explicitly via --task-dir
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, get_city_db_path

ROOT = Path(__file__).parent.parent.parent


def _constraint_pattern_set(task: dict) -> frozenset[str]:
    """Return frozenset of constraint patterns in a task."""
    pcs = task.get("rubric", {}).get("personal_constraints", [])
    return frozenset(c.get("pattern", "") for c in pcs if c.get("pattern"))


def _venue_ids_in_task(task: dict, pool_ids: set[str]) -> set[str]:
    """Return pool venue_ids referenced or filtered by this task."""
    # required_venue_ids
    vids = set(task.get("rubric", {}).get("required_venue_ids", []))

    # Venues that survive _apply_pool_filters (approximate: use label_required params)
    pcs = task.get("rubric", {}).get("personal_constraints", [])
    for c in pcs:
        pattern = c.get("pattern", "")
        params  = c.get("params", {})
        if pattern == "label_required":
            label = params.get("required_label", "")
            if label:
                vids.add(f"label:{label}")  # placeholder — real venue lookup needs DB

    return vids & pool_ids if vids & pool_ids else vids


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union > 0 else 0.0


def _type_entropy(type_counts: Counter) -> float:
    total = sum(type_counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for n in type_counts.values():
        if n > 0:
            p = n / total
            entropy -= p * math.log2(p)
    return round(entropy, 3)


def load_tasks(city: str, window_id: str | None,
                task_dir: Path | None) -> list[dict]:
    """Load task JSONs for a city, optionally filtered by window_id."""
    if task_dir:
        search_dirs = [task_dir]
    else:
        # Default: data/data1/tasks/unfiltered/<model>/
        unfiltered = ROOT / "data" / "data1" / "tasks" / "unfiltered"
        if unfiltered.exists():
            search_dirs = [d for d in unfiltered.iterdir() if d.is_dir()]
        else:
            search_dirs = []

    tasks = []
    for d in search_dirs:
        for f in sorted(d.glob("*.json")):
            try:
                t = json.loads(f.read_text())
                if t.get("city", "").lower() != city.lower():
                    continue
                if window_id and t.get("window_id") != window_id:
                    continue
                t["_source_file"] = str(f)
                tasks.append(t)
            except Exception:
                pass

    return tasks


def load_pool_ids(city: str, db_path: Path) -> set[str]:
    """Load all verified venue IDs for a city from DB."""
    if not db_path.exists():
        return set()
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT venue_id FROM venues WHERE city = ? AND page_status = 'verified'",
            (city,)
        ).fetchall()
        conn.close()
        return {r["venue_id"] for r in rows}
    except Exception:
        return set()


def analyse_task_diversity(
    city: str,
    window_id: str | None = None,
    task_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict:
    """
    Compute task diversity metrics as tasks are added one by one.
    Returns dict with curve and summary.
    """
    if db_path is None:
        db_path = get_city_db_path(city)

    tasks    = load_tasks(city, window_id, task_dir)
    pool_ids = load_pool_ids(city, db_path)

    if not tasks:
        return {"error": f"No tasks found for city='{city}'"
                         + (f" window='{window_id}'" if window_id else "")}

    ALL_TYPES = ["type1_cascading_requirements", "type2_subset_selection",
                 "type3_competing_requirements", "type4_precision_allocation",
                 "type5_hard_feasibility", "type6_context_window_tension"]

    curve        = []
    type_counts  = Counter()
    covered_vids: set[str] = set()
    pattern_sets: list[frozenset] = []
    pool_size    = len(pool_ids) or 1

    for i, task in enumerate(tasks, 1):
        stype   = task.get("structural_type", "?")
        type_counts[stype] += 1

        pset = _constraint_pattern_set(task)
        pattern_sets.append(pset)

        vids = _venue_ids_in_task(task, pool_ids)
        covered_vids.update(vids)

        # Mean pairwise Jaccard across all pairs so far
        if len(pattern_sets) >= 2:
            pairs = [(pattern_sets[a], pattern_sets[b])
                     for a in range(len(pattern_sets))
                     for b in range(a + 1, len(pattern_sets))]
            mean_jaccard = sum(_jaccard(a, b) for a, b in pairs) / len(pairs)
        else:
            mean_jaccard = 0.0

        # Marginal Jaccard gain (similarity of newest task vs all previous)
        if len(pattern_sets) >= 2:
            marginal_jaccard = sum(
                _jaccard(pset, pattern_sets[j])
                for j in range(len(pattern_sets) - 1)
            ) / (len(pattern_sets) - 1)
        else:
            marginal_jaccard = 0.0

        entropy         = _type_entropy(type_counts)
        venue_coverage  = len(covered_vids & pool_ids) / pool_size if pool_ids else 0.0

        curve.append({
            "n":               i,
            "task_id":         task.get("task_id", "?"),
            "structural_type": stype,
            "patterns":        sorted(pset),
            "mean_jaccard":    round(mean_jaccard, 3),
            "marginal_jaccard": round(marginal_jaccard, 3),
            "venue_coverage":  round(venue_coverage, 3),
            "type_entropy":    entropy,
            "type_counts":     dict(type_counts),
        })

    # Find saturation point — where mean_jaccard exceeds 0.4 and stays there
    sat_point = None
    for point in curve:
        if point["mean_jaccard"] > 0.4 and sat_point is None:
            sat_point = point["n"]

    # Find knee — largest single-step drop in marginal gain
    # (where adding another task gives the least new diversity)
    knee = None
    if len(curve) >= 4:
        gains = [(p["n"], p["marginal_jaccard"]) for p in curve[1:]]
        max_jump = 0.0
        for j in range(1, len(gains)):
            jump = gains[j][1] - gains[j-1][1]  # rising marginal_jaccard = less gain
            if jump > max_jump:
                max_jump = jump
                knee = gains[j][0]

    return {
        "city":           city,
        "window_id":      window_id,
        "total_tasks":    len(tasks),
        "pool_size":      len(pool_ids),
        "final_jaccard":  curve[-1]["mean_jaccard"] if curve else 0,
        "final_coverage": curve[-1]["venue_coverage"] if curve else 0,
        "final_entropy":  curve[-1]["type_entropy"] if curve else 0,
        "saturation_point": sat_point,
        "knee":           knee,
        "curve":          curve,
        "type_counts":    dict(type_counts),
    }


def print_report(result: dict) -> None:
    if "error" in result:
        print(f"Error: {result['error']}")
        return

    city    = result["city"]
    wid     = result["window_id"] or "all windows"
    total   = result["total_tasks"]
    pool    = result["pool_size"]
    jaccard = result["final_jaccard"]
    cov     = result["final_coverage"]
    ent     = result["final_entropy"]
    sat     = result["saturation_point"]
    knee    = result["knee"]
    curve   = result["curve"]

    MAX_ENTROPY = math.log2(6)

    print(f"\n{'='*60}")
    print(f"TASK DIVERSITY — {city.upper()} / {wid} ({total} tasks)")
    print(f"{'='*60}")
    print(f"  Pool size:           {pool} venues")
    print(f"  Final mean Jaccard:  {jaccard:.3f}  (target < 0.40)")
    print(f"  Venue coverage:      {cov:.0%}  of pool touched")
    print(f"  Type entropy:        {ent:.3f}  (max = {MAX_ENTROPY:.2f})")
    print(f"  Saturation point:    {'task ~' + str(sat) if sat else 'not reached (Jaccard never exceeded 0.40)'}")
    print(f"  Diversity knee:      {'task ~' + str(knee) if knee else 'N/A'}")

    # Curve table — every 6 tasks (one full type cycle)
    STEP = 6
    print(f"\n  Diversity curve (every {STEP} tasks):")
    print(f"  {'N':>4}  {'Jaccard':>8}  {'Coverage':>9}  {'Entropy':>8}  {'Assessment'}")
    print(f"  {'─'*4}  {'─'*8}  {'─'*9}  {'─'*8}  {'─'*20}")
    for point in curve:
        if point["n"] % STEP == 0 or point["n"] == total:
            j   = point["mean_jaccard"]
            c   = point["venue_coverage"]
            e   = point["type_entropy"]
            if j < 0.25:   assess = "high diversity"
            elif j < 0.35: assess = "good"
            elif j < 0.45: assess = "moderate repeat"
            else:           assess = "high repeat ⚠"
            print(f"  {point['n']:>4}  {j:>8.3f}  {c:>8.0%}  {e:>8.3f}  {assess}")

    # Structural type distribution
    print(f"\n  Structural type distribution:")
    for stype, n in sorted(result["type_counts"].items(), key=lambda x: -x[1]):
        bar = "█" * n
        print(f"    {stype:40s}: {n:3d}  {bar}")

    # Constraint pattern frequency
    from collections import Counter
    all_patterns: list[str] = []
    for point in curve:
        all_patterns.extend(point["patterns"])
    pat_counts = Counter(all_patterns)
    print(f"\n  Most frequent constraint patterns:")
    for pat, n in pat_counts.most_common(8):
        print(f"    {pat:35s}: {n}")

    # Recommendation
    print(f"\n  RECOMMENDATION:")
    if sat:
        print(f"    Target ~{sat} tasks/window for this pool size ({pool} venues)")
        print(f"    Adding more tasks beyond ~{sat} produces mostly repeated patterns")
    else:
        print(f"    {total} tasks hasn't reached saturation (Jaccard < 0.40)")
        print(f"    Can likely generate {total + 6}–{total + 12} more before repetition sets in")


def write_csv(result: dict, csv_path: Path) -> None:
    if "error" in result or not result.get("curve"):
        return
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "n", "task_id", "structural_type", "patterns",
            "mean_jaccard", "marginal_jaccard", "venue_coverage", "type_entropy"
        ])
        writer.writeheader()
        for p in result["curve"]:
            writer.writerow({
                "n":               p["n"],
                "task_id":         p["task_id"],
                "structural_type": p["structural_type"],
                "patterns":        "|".join(p["patterns"]),
                "mean_jaccard":    p["mean_jaccard"],
                "marginal_jaccard": p["marginal_jaccard"],
                "venue_coverage":  p["venue_coverage"],
                "type_entropy":    p["type_entropy"],
            })
    print(f"\n  CSV written: {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task diversity saturation analysis")
    parser.add_argument("--city",     required=True)
    parser.add_argument("--run-name", default=None,
                        help="Run name for separate DB (e.g. test_30). Default: production DB.")
    parser.add_argument("--window",   default=None,  help="Filter to specific window_id")
    parser.add_argument("--task-dir", type=Path, default=None,
                        help="Directory containing task JSON files")
    parser.add_argument("--db",       type=Path, default=None)
    parser.add_argument("--csv",      type=Path, default=None)
    args = parser.parse_args()

    result = analyse_task_diversity(
        city=args.city,
        window_id=args.window,
        task_dir=args.task_dir,
        db_path=args.db or get_city_db_path(args.city, run_name=getattr(args,'run_name',None)),
    )
    print_report(result)
    if args.csv:
        write_csv(result, args.csv)
