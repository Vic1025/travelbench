"""
scripts/migration/london_canonicalize.py

P6-T1 / P6-T1b — Slice 3: one-shot migration over London `test_70` to
(a) map all off-vocab tags onto the canonical vocab + city extension,
(b) backfill the `venues.cuisine` column on all 26 restaurants.

Two-stage with a human-in-the-loop review gate:

  # Stage 1: generate the mapping JSON for review (no DB writes).
  python scripts/migration/london_canonicalize.py --plan \\
      --api-key $DEEPSEEK_KEY

  # ...human reviews scripts/migration/london_canonicalize_plan.json...

  # Stage 2: apply the (possibly hand-edited) plan to the DB.
  python scripts/migration/london_canonicalize.py --apply

Both stages accept --db (path to the city DB; defaults to London test_70)
and --plan-file (path to the JSON; defaults next to this script).
"""

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.generation.db import get_connection
from scripts.generation.handbook import (
    UNIVERSAL_CORE_TAGS,
    UNIVERSAL_CUISINES,
    get_combined_vocab,
)
from scripts.generation.agent_tools import _canon_tag

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# Default paths
_DEFAULT_DB        = ROOT / "data" / "cities" / "london" / "runs" / "test_70" / "travelbench.db"
_DEFAULT_PLAN_FILE = Path(__file__).parent / "london_canonicalize_plan.json"
_DEFAULT_CITY      = "london"


# ─────────────────────────────────────────────────────────────────────────────
# Read-side helpers — used by both --plan and --apply integrity check
# ─────────────────────────────────────────────────────────────────────────────

def _load_tag_inventory(conn: sqlite3.Connection, city: str) -> list[tuple[str, int]]:
    """All distinct tags for the city, with usage counts, ordered desc."""
    return [(r["tag"], r["n"]) for r in conn.execute(
        "SELECT tag, COUNT(*) AS n FROM tags WHERE city = ? "
        "GROUP BY tag ORDER BY n DESC, tag", (city,)
    ).fetchall()]


def _load_restaurant_cuisine_state(conn: sqlite3.Connection, city: str,
                                     cuisine_vocab: frozenset) -> list[dict]:
    """Return restaurants + their cuisine-vocab tag intersection."""
    rows = conn.execute(
        "SELECT venue_id, name, cuisine FROM venues "
        "WHERE city = ? AND category = 'restaurant' AND page_status = 'verified' "
        "ORDER BY name", (city,)
    ).fetchall()
    out = []
    for r in rows:
        vid = r["venue_id"]
        tag_rows = conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
        ).fetchall()
        tags = [t["tag"] for t in tag_rows]
        cuisine_tags = [t for t in tags if t in cuisine_vocab]
        out.append({
            "venue_id":      vid,
            "name":          r["name"],
            "current_cuisine": r["cuisine"],
            "tags":          tags,
            "cuisine_tags_matched": cuisine_tags,
        })
    return out


def _get_city_extension(conn: sqlite3.Connection, city: str) -> list[str]:
    """Read current city_config.tag_vocabulary (returns [] if missing)."""
    row = conn.execute(
        "SELECT tag_vocabulary FROM city_config WHERE city = ?", (city,)
    ).fetchone()
    if not row or not row["tag_vocabulary"]:
        return []
    try:
        v = json.loads(row["tag_vocabulary"])
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# DeepSeek LLM calls
# ─────────────────────────────────────────────────────────────────────────────

def _deepseek(api_key: str, model: str) -> "OpenAI":
    if not HAS_OPENAI:
        raise RuntimeError("openai package required for --plan mode")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def _strip_json(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",          "", raw.strip(), flags=re.MULTILINE)
    return raw.strip()


def _call_llm_tag_mapping(off_vocab: list[tuple[str, int]],
                           universal_core: frozenset,
                           universal_cuisines: frozenset,
                           api_key: str, model: str) -> dict:
    """Single DeepSeek call → {old_tag: {action, ...}}. Retries on JSON error."""
    client = _deepseek(api_key, model)
    vocab_listing = sorted(universal_core | universal_cuisines)
    off_listing   = [f"{t!r} ({n})" for t, n in off_vocab]
    prompt = (
        "You are canonicalising a city's tag pool against a fixed vocabulary.\n\n"
        f"CANONICAL VOCABULARY ({len(vocab_listing)} tags — all of these are valid):\n"
        f"{', '.join(vocab_listing)}\n\n"
        f"OFF-VOCAB TAGS (tag, usage count) — propose an action for each:\n"
        f"{', '.join(off_listing)}\n\n"
        "For each off-vocab tag, return one of these action objects:\n"
        "  {\"action\": \"rename\", \"target\": <vocab-tag>}\n"
        "     — semantic near-duplicate; map to the closest canonical tag.\n"
        "  {\"action\": \"split\", \"targets\": [<vocab-tag>, ...]}\n"
        "     — compound concept; decompose into 2-3 canonical tags.\n"
        "  {\"action\": \"drop\", \"reason\": \"...\"}\n"
        "     — singleton, district name, venue-name, or otherwise unsalvageable.\n"
        "  {\"action\": \"extend\", \"reason\": \"...\"}\n"
        "     — genuine city-specific concept worth canonicalising in the\n"
        "       city's tag_vocabulary extension. Use sparingly.\n\n"
        "Rules:\n"
        "  - Every 'target' or 'targets' entry MUST be in the canonical vocabulary.\n"
        "  - Prefer rename > split > extend > drop.\n"
        "  - District names (soho, central-london, borough-market) → drop.\n"
        "  - Venue-name-like singletons (bombay-cafe, crown-jewels) → drop.\n"
        "  - Cuisine adjacent (steakhouse, tapas, sushi) → rename to closest cuisine.\n"
        "  - Compound mush like 'history-royal-heritage' → rename or split.\n\n"
        "Return ONLY a JSON object mapping each off-vocab tag string to its "
        "action object. No prose, no markdown fences.\n"
        "Example:\n"
        "{\n"
        "  \"reservation-required\": {\"action\": \"rename\", \"target\": \"booking-required\"},\n"
        "  \"architecture-city-skyline\": {\"action\": \"split\", \"targets\": [\"architecture\", \"views\"]},\n"
        "  \"bombay-cafe\": {\"action\": \"drop\", \"reason\": \"singleton, venue-name\"}\n"
        "}\n"
    )
    last_err = None
    for max_tok in (2000, 3000, 4500):
        try:
            resp = client.chat.completions.create(
                model=model, max_tokens=max_tok, stream=False,
                messages=[
                    {"role": "system", "content": "Return only valid JSON."},
                    {"role": "user",   "content": prompt},
                ],
            )
            raw = _strip_json(resp.choices[0].message.content or "")
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed:
                # Attach the usage count for downstream consumers
                counts = dict(off_vocab)
                for tag, action in parsed.items():
                    if isinstance(action, dict):
                        action["instances"] = counts.get(tag, 0)
                return parsed
        except Exception as e:
            last_err = e
            print(f"  ⚠ tag-mapping LLM attempt failed ({type(e).__name__}: {e})")
    raise RuntimeError(f"LLM tag-mapping call failed after retries: {last_err}")


def _call_llm_cuisine_pick(venue_name: str, venue_tags: list[str],
                            cuisine_vocab: frozenset,
                            api_key: str, model: str) -> tuple[str, str]:
    """Pick the closest cuisine for a restaurant with 0 or 2+ cuisine-tag intersections."""
    client = _deepseek(api_key, model)
    vocab_listing = sorted(cuisine_vocab)
    prompt = (
        f"Restaurant: {venue_name}\n"
        f"Existing tags: {', '.join(venue_tags) if venue_tags else '(none)'}\n\n"
        f"Pick the single closest cuisine from this fixed vocabulary "
        f"(no other values allowed):\n"
        f"{', '.join(vocab_listing)}\n\n"
        f"Use 'fusion' as a catch-all only if none of the others fits.\n"
        f"Return ONLY JSON: {{\"cuisine\": \"<value>\", \"reasoning\": \"<short>\"}}"
    )
    last_err = None
    for max_tok in (200, 400):
        try:
            resp = client.chat.completions.create(
                model=model, max_tokens=max_tok, stream=False,
                messages=[
                    {"role": "system", "content": "Return only valid JSON."},
                    {"role": "user",   "content": prompt},
                ],
            )
            raw = _strip_json(resp.choices[0].message.content or "")
            parsed = json.loads(raw)
            c = parsed.get("cuisine")
            if isinstance(c, str) and c in cuisine_vocab:
                return c, parsed.get("reasoning", "")
        except Exception as e:
            last_err = e
    # Fallback: fusion (the catch-all)
    return "fusion", f"LLM failed ({last_err}); defaulted to fusion"


# ─────────────────────────────────────────────────────────────────────────────
# --plan
# ─────────────────────────────────────────────────────────────────────────────

def run_plan(db_path: Path, plan_file: Path, city: str,
             api_key: str, model: str) -> dict:
    conn = get_connection(db_path)

    # 1. Tag inventory
    inventory = _load_tag_inventory(conn, city)
    print(f"\n[1/3] Loaded {len(inventory)} distinct tags ({sum(n for _,n in inventory)} instances)")

    # Partition by vocab membership (using current vocab from handbook + city extension)
    city_ext_now = _get_city_extension(conn, city)
    base_vocab   = UNIVERSAL_CORE_TAGS | UNIVERSAL_CUISINES | frozenset(city_ext_now)
    off_vocab = [(t, n) for t, n in inventory if t not in base_vocab]
    in_core = sum(n for t,n in inventory if t in UNIVERSAL_CORE_TAGS)
    in_cui  = sum(n for t,n in inventory if t in UNIVERSAL_CUISINES)
    in_ext  = sum(n for t,n in inventory if t in frozenset(city_ext_now))
    off_n   = sum(n for _,n in off_vocab)
    print(f"      In universal core: {in_core} instances, "
          f"in cuisines: {in_cui}, in existing extension: {in_ext}, "
          f"off-vocab: {off_n}")

    # 2. LLM mapping for off-vocab
    if off_vocab:
        print(f"\n[2/3] Calling DeepSeek for tag mapping ({len(off_vocab)} off-vocab tags)...")
        tag_actions = _call_llm_tag_mapping(
            off_vocab, UNIVERSAL_CORE_TAGS, UNIVERSAL_CUISINES, api_key, model)
        print(f"      Got {len(tag_actions)} actions")
    else:
        tag_actions = {}
        print("\n[2/3] No off-vocab tags; skipping LLM mapping")

    # Compute city extension proposal from `extend` actions
    city_extension = sorted({
        t for t, a in tag_actions.items()
        if isinstance(a, dict) and a.get("action") == "extend"
    })

    # 3. Cuisine backfill
    print(f"\n[3/3] Computing cuisine backfill...")
    restaurants = _load_restaurant_cuisine_state(conn, city, UNIVERSAL_CUISINES)
    cuisine_backfill = {}
    n_tag_direct = n_llm = 0
    for r in restaurants:
        if r["current_cuisine"] in UNIVERSAL_CUISINES:
            # Already set correctly; skip
            continue
        matches = r["cuisine_tags_matched"]
        if len(matches) == 1:
            cuisine_backfill[r["venue_id"]] = {
                "name":   r["name"],
                "source": "tag",
                "cuisine": matches[0],
                "tags_seen": r["tags"],
            }
            n_tag_direct += 1
        else:
            picked, reasoning = _call_llm_cuisine_pick(
                r["name"], r["tags"], UNIVERSAL_CUISINES, api_key, model)
            cuisine_backfill[r["venue_id"]] = {
                "name":   r["name"],
                "source": "llm",
                "cuisine": picked,
                "tags_seen": r["tags"],
                "reasoning": reasoning,
            }
            n_llm += 1
    print(f"      {n_tag_direct} restaurants backfilled directly from tags")
    print(f"      {n_llm} restaurants needed LLM cuisine pick")

    conn.close()

    # Summary
    summary = {
        "tags_renamed":  sum(1 for a in tag_actions.values()
                              if isinstance(a, dict) and a.get("action") == "rename"),
        "tags_split":    sum(1 for a in tag_actions.values()
                              if isinstance(a, dict) and a.get("action") == "split"),
        "tags_dropped":  sum(1 for a in tag_actions.values()
                              if isinstance(a, dict) and a.get("action") == "drop"),
        "tags_extended": sum(1 for a in tag_actions.values()
                              if isinstance(a, dict) and a.get("action") == "extend"),
        "restaurants_with_cuisine_set": len(cuisine_backfill),
    }

    plan = {
        "db_path":      str(db_path),
        "city":         city,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vocab_snapshot": {
            "universal_core_count":     len(UNIVERSAL_CORE_TAGS),
            "universal_cuisines_count": len(UNIVERSAL_CUISINES),
        },
        "tag_actions":      tag_actions,
        "city_extension":   city_extension,
        "cuisine_backfill": cuisine_backfill,
        "summary":          summary,
    }

    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print(f"\n✅ Plan written: {plan_file}")
    print(f"   Summary: {summary}")
    print(f"   City extension proposal ({len(city_extension)}): {city_extension}")
    print(f"\n   Review the JSON, then run with --apply.")
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# --apply
# ─────────────────────────────────────────────────────────────────────────────

def _validate_plan(plan: dict) -> list[str]:
    """Return list of error messages; empty list = valid."""
    errors = []
    extension = frozenset(plan.get("city_extension") or [])
    vocab = get_combined_vocab(list(extension))
    tag_actions = plan.get("tag_actions") or {}
    if not isinstance(tag_actions, dict):
        errors.append("tag_actions is not a dict")
        return errors

    for old_tag, action in tag_actions.items():
        if not isinstance(action, dict):
            errors.append(f"action for {old_tag!r} is not a dict")
            continue
        kind = action.get("action")
        if kind == "rename":
            target = action.get("target")
            if not target or target not in vocab:
                errors.append(
                    f"rename:{old_tag!r} target {target!r} not in combined vocab")
        elif kind == "split":
            targets = action.get("targets") or []
            if not isinstance(targets, list) or not targets:
                errors.append(f"split:{old_tag!r} has empty/invalid targets")
            else:
                for t in targets:
                    if t not in vocab:
                        errors.append(
                            f"split:{old_tag!r} target {t!r} not in combined vocab")
        elif kind in ("drop", "extend"):
            pass
        else:
            errors.append(f"action for {old_tag!r} has unknown kind {kind!r}")

    cuisine_backfill = plan.get("cuisine_backfill") or {}
    for vid, info in cuisine_backfill.items():
        c = info.get("cuisine")
        if not c or c not in UNIVERSAL_CUISINES:
            errors.append(f"cuisine_backfill[{vid}] cuisine={c!r} not in vocab")
    return errors


def _apply_plan(conn: sqlite3.Connection, plan: dict) -> dict:
    """Atomic application. Returns a diff summary."""
    city = plan.get("city", _DEFAULT_CITY)
    tag_actions     = plan.get("tag_actions") or {}
    city_extension  = plan.get("city_extension") or []
    cuisine_backfill = plan.get("cuisine_backfill") or {}

    n_rename = n_split = n_drop = n_extend = 0
    n_tag_rows_changed = 0

    # Process tag actions
    for old_tag, action in tag_actions.items():
        kind = action.get("action")

        if kind == "rename":
            target = action.get("target")
            target = _canon_tag(target)
            # Find venues that currently carry the old tag
            old_rows = conn.execute(
                "SELECT venue_id, yelp_visible FROM tags WHERE tag = ? AND city = ?",
                (old_tag, city)
            ).fetchall()
            if not old_rows:
                continue
            # Insert renamed rows (INSERT OR IGNORE → no double on collision)
            for r in old_rows:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) "
                    "VALUES (?, ?, ?, ?)",
                    (target, city, r["venue_id"], r["yelp_visible"])
                )
            # Delete the old rows
            conn.execute("DELETE FROM tags WHERE tag = ? AND city = ?",
                         (old_tag, city))
            n_rename += 1
            n_tag_rows_changed += len(old_rows)

        elif kind == "split":
            targets = [_canon_tag(t) for t in action.get("targets") or []]
            old_rows = conn.execute(
                "SELECT venue_id, yelp_visible FROM tags WHERE tag = ? AND city = ?",
                (old_tag, city)
            ).fetchall()
            for r in old_rows:
                for t in targets:
                    conn.execute(
                        "INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) "
                        "VALUES (?, ?, ?, ?)",
                        (t, city, r["venue_id"], r["yelp_visible"])
                    )
            conn.execute("DELETE FROM tags WHERE tag = ? AND city = ?",
                         (old_tag, city))
            n_split += 1
            n_tag_rows_changed += len(old_rows)

        elif kind == "drop":
            res = conn.execute("DELETE FROM tags WHERE tag = ? AND city = ?",
                               (old_tag, city))
            n_drop += 1
            n_tag_rows_changed += res.rowcount or 0

        elif kind == "extend":
            # Tag stays as-is; city_extension covers it.
            n_extend += 1

    # Persist the city extension
    conn.execute(
        "UPDATE city_config SET tag_vocabulary = ? WHERE city = ?",
        (json.dumps(sorted(set(city_extension))), city)
    )

    # Cuisine backfill
    n_cuisine_set = 0
    for vid, info in cuisine_backfill.items():
        c = info.get("cuisine")
        res = conn.execute(
            "UPDATE venues SET cuisine = ? WHERE venue_id = ? AND city = ?",
            (c, vid, city)
        )
        if res.rowcount and res.rowcount > 0:
            n_cuisine_set += 1

    return {
        "tags_renamed":      n_rename,
        "tags_split":        n_split,
        "tags_dropped":      n_drop,
        "tags_extended":     n_extend,
        "tag_rows_changed":  n_tag_rows_changed,
        "city_extension_size": len(set(city_extension)),
        "cuisine_set_count": n_cuisine_set,
    }


def _integrity_check(conn: sqlite3.Connection, city: str) -> list[str]:
    """Post-apply checks. Returns list of issue strings (empty = clean)."""
    issues = []
    ext = _get_city_extension(conn, city)
    vocab = get_combined_vocab(ext)

    # All tags on-vocab
    off = [r["tag"] for r in conn.execute(
        "SELECT DISTINCT tag FROM tags WHERE city = ?", (city,)
    ).fetchall() if r["tag"] not in vocab]
    if off:
        issues.append(f"{len(off)} tags still off-vocab: {sorted(off)[:10]}")

    # All restaurants have on-vocab cuisine
    missing = list(conn.execute(
        "SELECT venue_id, name FROM venues WHERE city = ? "
        "AND category = 'restaurant' AND page_status = 'verified' "
        "AND (cuisine IS NULL OR cuisine = '')", (city,)
    ).fetchall())
    if missing:
        issues.append(f"{len(missing)} restaurants without cuisine: "
                       f"{[r['name'] for r in missing]}")
    bad = list(conn.execute(
        "SELECT venue_id, name, cuisine FROM venues WHERE city = ? "
        "AND category = 'restaurant' AND page_status = 'verified' "
        "AND cuisine IS NOT NULL", (city,)
    ).fetchall())
    off_cuisine = [(r["name"], r["cuisine"]) for r in bad
                    if r["cuisine"] not in UNIVERSAL_CUISINES]
    if off_cuisine:
        issues.append(f"{len(off_cuisine)} restaurants with off-vocab cuisine: "
                       f"{off_cuisine[:5]}")
    return issues


def run_apply(db_path: Path, plan_file: Path) -> dict:
    if not plan_file.exists():
        raise FileNotFoundError(
            f"Plan file not found: {plan_file}\n"
            f"Run --plan first."
        )
    plan = json.loads(plan_file.read_text())
    city = plan.get("city", _DEFAULT_CITY)

    errors = _validate_plan(plan)
    if errors:
        print("❌ Plan validation failed:")
        for e in errors:
            print(f"   - {e}")
        raise SystemExit(1)

    # Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".bak.{ts}.db")
    shutil.copy2(db_path, backup_path)
    print(f"\n📦 Backup: {backup_path}")

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN")
        summary = _apply_plan(conn, plan)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n✅ Applied plan:")
    for k, v in summary.items():
        print(f"   {k}: {v}")

    # Post-apply integrity
    conn = get_connection(db_path)
    try:
        issues = _integrity_check(conn, city)
    finally:
        conn.close()
    if issues:
        print(f"\n⚠ Integrity check found {len(issues)} issue(s):")
        for i in issues:
            print(f"   - {i}")
        raise SystemExit(2)
    print(f"\n✅ Integrity check clean.")
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="London tag/cuisine canonicalisation migration (P6-T1 Slice 3)"
    )
    p.add_argument("--plan",  action="store_true",
                    help="Generate the mapping JSON (no DB writes)")
    p.add_argument("--apply", action="store_true",
                    help="Apply the (reviewed) plan JSON to the DB")
    p.add_argument("--db",       type=Path, default=_DEFAULT_DB,
                    help=f"DB path (default: {_DEFAULT_DB})")
    p.add_argument("--plan-file", type=Path, default=_DEFAULT_PLAN_FILE,
                    help=f"Plan JSON path (default: {_DEFAULT_PLAN_FILE})")
    p.add_argument("--city", default=_DEFAULT_CITY,
                    help=f"City key (default: {_DEFAULT_CITY})")
    p.add_argument("--api-key", default=None,
                    help="DeepSeek API key (required for --plan)")
    p.add_argument("--model",   default="deepseek-chat")
    args = p.parse_args()

    if args.plan == args.apply:
        p.error("Provide exactly one of --plan or --apply")

    if args.plan:
        if not args.api_key:
            p.error("--plan requires --api-key (DeepSeek)")
        run_plan(args.db, args.plan_file, args.city, args.api_key, args.model)
    else:
        run_apply(args.db, args.plan_file)


if __name__ == "__main__":
    main()
