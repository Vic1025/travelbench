"""
scripts/generation/trait_registry.py

Persistent trait registry for a city's task set.

Tracks which character trait keys have been used across ALL tasks generated
for a city, so future generation calls can be told "these are already taken."

The registry lives at:
  data/data1/tasks/unfiltered/{city}_trait_registry.json

Format:
{
  "city": "paris",
  "used_traits": {
    "dietary_veggie":      {"task_id": "par_gen_003", "model": "claude-sonnet-4-5"},
    "mobility_wheelchair": {"task_id": "par_gen_007", "model": "gpt-5.4"},
    ...
  },
  "used_clusters": {
    "cat2/dietary":      {"task_id": "par_gen_003", "model": "claude-sonnet-4-5"},
    "cat5/seek_hidden":  {"task_id": "par_gen_001", "model": "gpt-5.4-mini"},
    ...
  }
}

The generation prompt receives a plain-English summary:
  "Already used in this city's task set (do NOT reuse):
     - vegetarian/vegan diet (par_gen_003)
     - hidden gems / no tourist traps (par_gen_001)
   Available Cat 2 traits: halal/kosher, wheelchair, dog-friendly
   Available Cat 5 traits: iconic sights (first time classics)"
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


def _registry_path(city: str, task_dir: Path = None) -> Path:
    if task_dir is None:
        task_dir = ROOT / "data" / "data1" / "tasks" / "unfiltered"
    return task_dir / f"{city}_trait_registry.json"


def load_registry(city: str, task_dir: Path = None) -> dict:
    """Load existing registry, or return empty one."""
    path = _registry_path(city, task_dir)
    if path.exists():
        return json.loads(path.read_text())
    return {
        "city":         city,
        "used_traits":  {},   # trait_key → {task_id, model}
        "used_clusters": {},  # "cat/cluster" → {task_id, model}
    }


def save_registry(registry: dict, task_dir: Path = None) -> None:
    path = _registry_path(registry["city"], task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2))


def register_task(task: dict, model: str, registry: dict,
                  get_trait_key_fn, get_cluster_fn) -> list[str]:
    """
    Register all trait keys and clusters from a task into the registry.
    Returns list of conflicts (traits already registered by a different task).
    """
    task_id = task.get("task_id", "?")
    conflicts = []

    for c in task.get("rubric", {}).get("personal_constraints", []):
        key = get_trait_key_fn(c)
        cl  = get_cluster_fn(c)

        if key:
            if key in registry["used_traits"]:
                prev = registry["used_traits"][key]
                if prev["task_id"] != task_id:
                    conflicts.append(
                        f"Trait '{key}' already used in {prev['task_id']} ({prev['model']})"
                    )
            else:
                registry["used_traits"][key] = {"task_id": task_id, "model": model}

        if cl:
            cl_key = f"{cl[0]}/{cl[1]}"
            if cl_key in registry["used_clusters"]:
                prev = registry["used_clusters"][cl_key]
                if prev["task_id"] != task_id:
                    conflicts.append(
                        f"Cluster '{cl_key}' already used in {prev['task_id']} ({prev['model']})"
                    )
            else:
                registry["used_clusters"][cl_key] = {"task_id": task_id, "model": model}

    return conflicts


# ─────────────────────────────────────────────────────────────────────────────
# Human-readable summary for injection into prompts
# ─────────────────────────────────────────────────────────────────────────────

# All possible trait keys and their human labels
_TRAIT_LABELS = {
    "dietary_veggie":       "vegetarian / vegan / plant-based",
    "dietary_halal_kosher": "halal / kosher",
    "dietary_gluten":       "gluten-free",
    "mobility_wheelchair":  "wheelchair / limited mobility",
    "interest_photography": "photography enthusiast",
    "interest_art":         "art / museum interest",
    "interest_outdoor":     "outdoor / nature",
    "interest_livemusic":   "live music",
    "authenticity_hidden":  "hidden gems / no tourist traps",
    "authenticity_iconic":  "iconic sights / first-time classics",
    "occasion_anniversary": "anniversary / honeymoon",
    "occasion_birthday":    "birthday",
    "occasion_romantic":    "romantic occasion",
    "occasion_business":    "business trip",
}

# All possible cluster keys and their human labels
_CLUSTER_LABELS = {
    "cat2/dietary":        "any dietary restriction (vegetarian, halal, gluten-free...)",
    "cat2/mobility":       "any mobility constraint (wheelchair, limited mobility...)",
    "cat2/allergy":        "any allergy (coeliac, severe allergy...)",
    "cat2/companion":      "travelling with animal (dog, pet...)",
    "cat5/seek_hidden":    "hidden gems / no tourist traps / locals-favourite",
    "cat5/want_classics":  "iconic sights / first-time classics",
}


def build_registry_prompt_block(registry: dict) -> str:
    """
    Build a plain-English block to inject into the generation prompt,
    telling the LLM which traits are already taken and what's still available.
    """
    used_traits   = registry.get("used_traits",   {})
    used_clusters = registry.get("used_clusters", {})

    lines = ["TRAIT REGISTRY — already used in this city's task set:"]

    if not used_traits and not used_clusters:
        lines.append("  (none yet — all traits available)")
    else:
        lines.append("  Do NOT reuse any of these in the new tasks:")
        for key, meta in sorted(used_traits.items()):
            label = _TRAIT_LABELS.get(key, key)
            lines.append(f"    ✗ {label}  (used in {meta['task_id']})")

    # What's still available in Cat 2 and Cat 5 (most commonly overused)
    used_c2_clusters = {k.split("/")[1] for k in used_clusters if k.startswith("cat2/")}
    used_c5_clusters = {k.split("/")[1] for k in used_clusters if k.startswith("cat5/")}

    all_c2 = {"dietary", "mobility", "allergy", "companion"}
    all_c5 = {"seek_hidden", "want_classics"}

    avail_c2 = all_c2 - used_c2_clusters
    avail_c5 = all_c5 - used_c5_clusters

    lines.append("")
    lines.append("  Still available:")
    if avail_c2:
        c2_map = {
            "dietary":   "halal/kosher, gluten-free, vegetarian (dietary restrictions)",
            "mobility":  "wheelchair / limited mobility",
            "allergy":   "severe allergy / coeliac",
            "companion": "travelling with dog",
        }
        for cl in sorted(avail_c2):
            lines.append(f"    Cat 2 {cl}: {c2_map.get(cl, cl)}")
    else:
        lines.append("    Cat 2: all clusters used — avoid Cat 2 traits entirely")

    if avail_c5:
        c5_map = {
            "seek_hidden":   "hidden gems / no tourist traps",
            "want_classics": "iconic sights / first-time classics",
        }
        for cl in sorted(avail_c5):
            lines.append(f"    Cat 5 {cl}: {c5_map.get(cl, cl)}")
    else:
        lines.append("    Cat 5: all authenticity clusters used — use Cat 6/7 instead")

    return "\n".join(lines)


def scan_existing_tasks(city: str, task_dir: Path = None,
                         get_trait_key_fn = None,
                         get_cluster_fn   = None) -> dict:
    """
    Rebuild a registry by scanning all existing task JSONs for a city.
    Useful for initialising the registry from already-generated tasks.
    """
    if task_dir is None:
        task_dir = ROOT / "data" / "data1" / "tasks" / "unfiltered"

    registry = load_registry(city, task_dir)

    # Scan all subdirs and root for task files
    import glob as _glob
    patterns = [
        str(task_dir / f"{city[:3]}_*.json"),
        str(task_dir / "*" / f"{city[:3]}_*.json"),
    ]
    found = []
    for pat in patterns:
        found.extend(_glob.glob(pat))

    for path in sorted(found):
        try:
            task = json.loads(Path(path).read_text())
            model = Path(path).parent.name
            if model == "unfiltered":
                model = "unknown"
            if get_trait_key_fn and get_cluster_fn:
                register_task(task, model, registry, get_trait_key_fn, get_cluster_fn)
        except Exception:
            pass

    return registry
