"""
scripts/generation/compute_task_difficulty.py

Computes difficulty scores for generated tasks.

Three axes (all stored on the task JSON):
  1. constraint_complexity: derived from constraint count, hop depth, structural type
  2. avg_venue_difficulty:  mean venue_difficulty_score over P-score-filtered pool
  3. pool_size_difficulty:  how narrow the filtered pool is (narrow = harder)

Combined: 50% constraint_complexity + 25% avg_venue_difficulty + 25% pool_size_difficulty

Usage:
  python scripts/generation/compute_task_difficulty.py --task-file path/to/task.json
  python scripts/generation/compute_task_difficulty.py --task-dir path/to/tasks/
"""

import json
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.compute_venue_difficulty import compute_all_venue_difficulties


# ─────────────────────────────────────────────────────────────────────────────
# AXIS 1: CONSTRAINT COMPLEXITY
# ─────────────────────────────────────────────────────────────────────────────

STRUCTURAL_TYPE_WEIGHT = {
    "type1_hidden_requirements":    0.4,
    "type2_subset_selection":       0.5,
    "type3_competing_requirements": 0.6,
    "type4_precision_allocation":   0.5,
    "type5_hard_feasibility":       0.7,
    "type6_context_window_tension": 0.6,
}

HOP_WEIGHT = {1: 0.2, 2: 0.4, 3: 0.6}


def compute_constraint_complexity(task: dict) -> float:
    """
    Compute constraint_complexity score (0.0–1.0).

    Factors:
    - Hop depth distribution across P+B constraints
    - Constraint count (more = harder, up to 6)
    - Structural type weight
    - B-score bonus if any B-score constraints present
    - Secondary structural type +0.1 if present
    """
    rubric       = task.get("rubric", {})
    p_constraints = rubric.get("personal_constraints", [])
    b_constraints = rubric.get("b_score_constraints", [])

    hop_scores = []
    for c in p_constraints:
        hop = c.get("hop", 1)
        hop_scores.append(HOP_WEIGHT.get(hop, 0.2))
    for c in b_constraints:
        hop_scores.append(HOP_WEIGHT.get(3, 0.6))  # B-score always hop-3

    hop_complexity   = sum(hop_scores) / max(len(hop_scores), 1) if hop_scores else 0.2
    count_score      = min(1.0, (len(p_constraints) + len(b_constraints)) / 6)
    stype            = task.get("structural_type", "")
    stype_score      = STRUCTURAL_TYPE_WEIGHT.get(stype, 0.4)
    s2_bonus         = 0.1 if task.get("structural_type_secondary") else 0.0

    complexity = (
        0.35 * hop_complexity +
        0.25 * count_score +
        0.30 * stype_score +
        0.10 * (1.0 if b_constraints else 0.0)
    ) + s2_bonus

    return round(min(1.0, complexity), 3)


# ─────────────────────────────────────────────────────────────────────────────
# AXIS 3: POOL SIZE DIFFICULTY
# ─────────────────────────────────────────────────────────────────────────────

def _pool_size_difficulty_score(n: int) -> float:
    """
    Convert filtered pool size to difficulty score.

    Narrow pool = harder for agent to find valid options.
    > 25: hard fail at SUBMIT time (this score never actually appears)

    | Pool size | Score |
    |-----------|-------|
    | ≤ 3       | 1.0   |
    | 4–8       | 0.75  |
    | 9–15      | 0.5   |
    | 16–25     | 0.25  |
    | > 25      | 0.0 (shouldn't reach here) |
    """
    if n <= 3:   return 1.0
    if n <= 8:   return 0.75
    if n <= 15:  return 0.5
    if n <= 25:  return 0.25
    return 0.0   # > 25 shouldn't pass solvability gate


# ─────────────────────────────────────────────────────────────────────────────
# AXIS 2: AVG VENUE DIFFICULTY  (filtered pool mean — no random sampling)
# ─────────────────────────────────────────────────────────────────────────────

def compute_avg_venue_difficulty(task: dict, city: str,
                                  db_path: Path = None) -> tuple[float | None, int]:
    """
    Compute avg_venue_difficulty and filtered pool size for a task.

    Applies _apply_pool_filters to get the P-score-filtered pool,
    then averages venue_difficulty_score across all venues in that pool.

    Returns (avg_difficulty, pool_size).
    avg_difficulty is None if venue_difficulty_score not yet computed.
    pool_size is always returned (used for pool_size_difficulty axis).
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    if db_path is None:
        db_path = get_city_db_path(city)
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT venue_id, venue_difficulty_score, category, traffic_tier, "
        "has_wrong_info_planned, pet_friendly, wheelchair_accessible, "
        "photography_allowed, family_friendly, age_restriction, noise_level, "
        "price_tier, avg_cost_local, booking_required "
        "FROM venues WHERE city = ? AND page_status = 'verified'",
        (city,)
    ).fetchall()

    # Fetch tags for each venue
    tag_map: dict[str, list[str]] = {}
    for row in rows:
        vid  = row["venue_id"]
        tags = [r["tag"] for r in conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
        ).fetchall()]
        tag_map[vid] = tags

    conn.close()

    if not rows:
        return None, 0

    # Build pool list with tags attached
    venue_pool = []
    for row in rows:
        v = dict(row)
        v["tags"] = tag_map.get(v["venue_id"], [])
        venue_pool.append(v)

    # Apply P-score filters using the shared helper
    try:
        from scripts.generation.pool_utils import _apply_pool_filters
        filtered = _apply_pool_filters(venue_pool, task)
    except ImportError:
        filtered = venue_pool  # fallback: no filtering

    pool_size = len(filtered)

    # Compute mean venue_difficulty_score over filtered pool
    scored = [v["venue_difficulty_score"] for v in filtered
              if v.get("venue_difficulty_score") is not None]
    if not scored:
        return None, pool_size

    return round(sum(scored) / len(scored), 3), pool_size


# ─────────────────────────────────────────────────────────────────────────────
# COMBINED DIFFICULTY ANNOTATION
# ─────────────────────────────────────────────────────────────────────────────

def annotate_task_difficulty(task: dict, city: str,
                              db_path: Path = None) -> dict:
    """
    Compute and attach all three difficulty axes to a task dict.

    Adds:
      task["constraint_complexity"]   float 0.0-1.0
      task["avg_venue_difficulty"]    float 0.0-1.0 or None
      task["pool_size_difficulty"]    float 0.0-1.0
      task["filtered_pool_size"]      int
      task["difficulty_combined"]     float 0.0-1.0
      task["difficulty"]              "easy" | "medium" | "hard"

    Weights: 50% constraint_complexity + 25% avg_venue + 25% pool_size

    Returns modified task dict (in-place).
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    if db_path is None:
        db_path = get_city_db_path(city)
    cc              = compute_constraint_complexity(task)
    avd, pool_size  = compute_avg_venue_difficulty(task, city, db_path=db_path)
    psd             = _pool_size_difficulty_score(pool_size)

    task["constraint_complexity"] = cc
    task["avg_venue_difficulty"]  = avd
    task["pool_size_difficulty"]  = psd
    task["filtered_pool_size"]    = pool_size

    # Combined score — if avg_venue_difficulty unavailable, split 50/50 cc/psd
    if avd is not None:
        combined = round(0.50 * cc + 0.25 * avd + 0.25 * psd, 3)
    else:
        combined = round(0.50 * cc + 0.50 * psd, 3)

    task["difficulty_combined"] = combined

    # Update difficulty label
    if combined < 0.35:
        task["difficulty"] = "easy"
    elif combined < 0.60:
        task["difficulty"] = "medium"
    else:
        task["difficulty"] = "hard"

    return task


def annotate_task_file(task_path: Path, city: str,
                        db_path: Path = None,
                        dry_run: bool = False) -> dict:
    """Load, annotate, and optionally write back a task JSON file."""
    task = json.loads(task_path.read_text())
    annotate_task_difficulty(task, city, db_path=db_path)
    if not dry_run:
        task_path.write_text(json.dumps(task, indent=2))
    return task


def annotate_task_directory(task_dir: Path, city: str,
                             db_path: Path = None,
                             dry_run: bool = False) -> list[dict]:
    """Annotate all task JSON files in a directory."""
    tasks = []
    for path in sorted(task_dir.glob("*.json")):
        try:
            task  = annotate_task_file(path, city, db_path=db_path, dry_run=dry_run)
            tasks.append(task)
            cc    = task.get("constraint_complexity", "?")
            avd   = task.get("avg_venue_difficulty", "?")
            psd   = task.get("pool_size_difficulty", "?")
            psz   = task.get("filtered_pool_size", "?")
            comb  = task.get("difficulty_combined", "?")
            print(f"  {path.name}: cc={cc} avd={avd} psd={psd} (pool={psz}) "
                  f"combined={comb} → {task.get('difficulty')}")
        except Exception as e:
            print(f"  ❌ {path.name}: {e}")
    return tasks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute task difficulty scores")
    parser.add_argument("--city",      required=True)
    parser.add_argument("--run-name", default=None,
                        help="Run name for separate DB (e.g. test_30). Default: production DB.")
    parser.add_argument("--task-file", type=Path, default=None)
    parser.add_argument("--task-dir",  type=Path, default=None)
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--db",        type=Path, default=DB_PATH)
    args = parser.parse_args()

    if args.task_file:
        task = annotate_task_file(
            args.task_file, args.city, db_path=args.db or get_city_db_path(args.city, run_name=getattr(args,'run_name',None)), dry_run=args.dry_run
        )
        print(json.dumps({
            "task_id":              task.get("task_id"),
            "constraint_complexity": task.get("constraint_complexity"),
            "avg_venue_difficulty":  task.get("avg_venue_difficulty"),
            "pool_size_difficulty":  task.get("pool_size_difficulty"),
            "filtered_pool_size":    task.get("filtered_pool_size"),
            "difficulty_combined":   task.get("difficulty_combined"),
            "difficulty":            task.get("difficulty"),
        }, indent=2))
    elif args.task_dir:
        tasks = annotate_task_directory(
            args.task_dir, args.city, db_path=args.db or get_city_db_path(args.city, run_name=getattr(args,'run_name',None)), dry_run=args.dry_run
        )
        print(f"\n✅ Annotated {len(tasks)} tasks")
    else:
        print("Provide --task-file or --task-dir")
        sys.exit(1)

