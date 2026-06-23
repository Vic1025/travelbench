#!/usr/bin/env python3
"""
scripts/analysis/misled_metric.py

Drift-independent "misled / recovered" metric for served-lie (layer) flaws.

Idea
----
The raw "did the agent's plan get worse" signal is dominated by venue-selection
drift: which venues the agent happens to schedule changes run-to-run for reasons
unrelated to the served lie.  This metric removes that drift by conditioning on
the *specific corrupted field* of a *specific flawed venue* that the agent
actually SCHEDULED, and asking only: does the plan reflect the LIE or the TRUTH?

For each (scheduled flawed venue, corrupted field) we emit a verdict:
  - RECOVERED    : plan belief tracks ground truth, or a corrective flag is present
  - MISLED       : plan belief tracks the served lie, no corrective flag
  - UNDETERMINED : the plan does not expose the relevant belief (honest abstain)

Layers handled (structure LIKE 'layer:%'):
  - layer:free-confusion        (avg_cost_local, lie≈0, GT>0)  -> observable via cost
  - layer:price-deflation       (price_tier)                   -> mostly UNDETERMINED
  - layer:accessibility-optimism(wheelchair_accessible)        -> UNDETERMINED unless flagged

Data sources
------------
  Corpus DB:    wrong_info rows where structure LIKE 'layer:%'
  Transcripts:  results/transcripts/<model>/*.json.gz  (parsed_plan + flags)
  Arm mapping:  results/ablation_layer/<arm>/<run_ts>_summary_*.json
                (a transcript's run_ts suffix -> which arm/run produced it)

This script makes NO API calls and runs only over existing artifacts.

Usage
-----
  python scripts/analysis/misled_metric.py \
      --db data/cities/New_York/runs/test_layer/travelbench.db \
      --transcripts-dir results/transcripts/claude-sonnet-4-5 \
      --ablation-dir results/ablation_layer \
      --model claude-sonnet-4-5 \
      --out results/misled_metric/report.md
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ─────────────────────────────────────────────────────────────────────────────
# wrong_info (served lies) for layer flaws
# ─────────────────────────────────────────────────────────────────────────────

def load_layer_flaws(db_path: str) -> dict:
    """
    Returns {venue_id: {field: {lie, gt, structure, venue_name}}}
    for wrong_info rows whose structure starts with 'layer:'.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT wi.venue_id, wi.affected_field, wi.incorrect_value,
               wi.correct_value, wi.structure, v.name AS venue_name
        FROM wrong_info wi
        LEFT JOIN venues v ON v.venue_id = wi.venue_id
        WHERE wi.structure LIKE 'layer:%'
        """
    ).fetchall()
    conn.close()
    flaws: dict = defaultdict(dict)
    for r in rows:
        flaws[r["venue_id"]][r["affected_field"]] = {
            "lie":         r["incorrect_value"],
            "gt":          r["correct_value"],
            "structure":   r["structure"],
            "venue_name":  r["venue_name"] or r["venue_id"],
        }
    return flaws


# ─────────────────────────────────────────────────────────────────────────────
# Arm mapping:  run_ts -> arm name   (from <arm>/<run_ts>_summary_*.json)
# ─────────────────────────────────────────────────────────────────────────────

def load_arm_map(ablation_dir: str, model: str) -> dict:
    """
    Returns {run_ts: arm_name}.  Each arm subdir holds one *_summary_*.json
    that carries a 'run_ts' field; that run_ts identifies all transcripts in
    that arm/run.
    """
    arm_map: dict = {}
    for arm_path in sorted(glob.glob(os.path.join(ablation_dir, "*"))):
        if not os.path.isdir(arm_path):
            continue
        arm = os.path.basename(arm_path)
        for sm in glob.glob(os.path.join(arm_path, "*summary*.json")):
            try:
                d = json.load(open(sm))
            except Exception:
                continue
            run_ts = d.get("run_ts")
            if run_ts:
                arm_map[run_ts] = arm
    return arm_map


# Pull the trailing run_ts (YYYYMMDD_HHMMSS) out of a transcript filename.
_RUN_TS_RE = re.compile(r"(\d{8}_\d{6})\.json\.gz$")


def transcript_run_ts(path: str) -> str | None:
    m = _RUN_TS_RE.search(os.path.basename(path))
    return m.group(1) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Belief / flag extraction from a parsed_plan
# ─────────────────────────────────────────────────────────────────────────────

# Corrective-signal regexes for the free-confusion (cost) layer.
_COST_RECOVERY_PATTERNS = [
    r"misinformation",
    r"not free",
    r"actually",
    r"admission",
    r"\bcharge",
    r"\bfee\b",
    r"older posts?",
    r"not \$?0\b",
    r"entrance fee",
    r"costs? \$",
]
_COST_RECOVERY_RE = re.compile("|".join(_COST_RECOVERY_PATTERNS), re.IGNORECASE)

# price-deflation corrective vocabulary
_PRICE_RECOVERY_RE = re.compile(
    r"pricier|upscale|expensive|fine[- ]?dining|higher price|"
    r"more expensive|premium|not budget|costlier|actually mid",
    re.IGNORECASE,
)

# accessibility corrective vocabulary
_ACCESS_RECOVERY_RE = re.compile(
    r"\bstep\b|stairs|not accessible|not step[- ]?free|wheelchair|"
    r"inaccessible|small step|has a step",
    re.IGNORECASE,
)

# Rough tier -> typical cost band, for the (rare) case where a price_tier flaw's
# belief is observable through estimated_cost_local.
_TIER_BAND = {
    "budget":      (0, 15),
    "mid":         (15, 40),
    "upscale":     (40, 80),
    "fine-dining": (80, 300),
}


def iter_scheduled_activities(parsed_plan: dict):
    for day in parsed_plan.get("days", []) or []:
        for act in day.get("activities", []) or []:
            yield act


def gather_plan_context(parsed_plan: dict) -> dict:
    """Pre-compute per-venue activities + the list of individual note strings.

    We keep notes as a *list of separate strings* (each unresolved_flag, plus
    the planning_notes split into sentences) rather than one joined blob.  This
    is load-bearing: a corrective phrase only counts for a venue if the venue
    name and the corrective keyword appear in the *same* note string — otherwise
    a flag about venue B ("Dead Rabbit ... not wheelchair accessible") would be
    falsely credited to an unrelated scheduled venue A whose name happens to sit
    elsewhere in the blob.
    """
    by_venue: dict = defaultdict(list)
    for act in iter_scheduled_activities(parsed_plan):
        vid = act.get("venue_id")
        if vid:
            by_venue[vid].append(act)
    notes = list(parsed_plan.get("unresolved_flags", []) or [])
    pn = parsed_plan.get("planning_notes", "") or ""
    notes += [s.strip() for s in re.split(r"(?<=[.!?])\s+", pn) if s.strip()]
    return {"by_venue": by_venue, "notes": notes}


def _matches_venue(text: str, venue_name: str) -> bool:
    if not text or not venue_name:
        return False
    # match on the venue's leading token(s); names like "Katz's Delicatessen"
    head = venue_name.split("(")[0].strip()
    if head and head.lower() in text.lower():
        return True
    first = head.split()[0] if head.split() else ""
    # require the first token to be reasonably specific (>=4 chars) to match
    return bool(first) and len(first) >= 4 and first.lower() in text.lower()


def _venue_scoped_note(notes: list, venue_name: str, pattern: re.Pattern):
    """
    Return (excerpt) for the first note string that mentions BOTH the venue
    name AND the corrective pattern, else "".  Requiring co-occurrence in the
    same string prevents crediting venue A with a corrective phrase that is
    actually about venue B.
    """
    for note in notes:
        if _matches_venue(note, venue_name):
            m = pattern.search(note)
            if m:
                return note[max(0, m.start() - 30):m.end() + 60].strip()
    return ""


def classify_free_confusion(acts: list, flaw: dict, notes: list) -> tuple:
    """avg_cost_local layer.  Returns (verdict, agent_est, flag_excerpt)."""
    try:
        gt = float(flaw["gt"])
    except (TypeError, ValueError):
        gt = None
    try:
        lie = float(flaw["lie"])
    except (TypeError, ValueError):
        lie = 0.0

    # collect a cost belief + any flag text on the scheduled activities
    est = None
    flag_blob = []
    for a in acts:
        c = a.get("estimated_cost_local")
        if c is not None and est is None:
            est = c
        flag_blob.extend(a.get("flags", []) or [])
    flag_text = " ".join(flag_blob)

    # corrective signal: per-activity flag (venue-scoped by construction) OR a
    # venue-scoped global note (venue name + keyword in the SAME string).
    flag_excerpt = ""
    m = _COST_RECOVERY_RE.search(flag_text)
    if m:
        flag_excerpt = flag_text[max(0, m.start() - 30):m.end() + 60].strip()
    else:
        flag_excerpt = _venue_scoped_note(notes, flaw["venue_name"], _COST_RECOVERY_RE)
    corrective = bool(flag_excerpt)

    # numeric belief: est closer to GT than to the lie?
    near_gt = near_lie = None
    if est is not None and gt is not None:
        try:
            ec = float(est)
            near_gt = abs(ec - gt) < abs(ec - lie)
            near_lie = not near_gt
        except (TypeError, ValueError):
            pass

    if near_gt or corrective:
        return "RECOVERED", est, flag_excerpt
    if near_lie and not corrective:
        return "MISLED", est, ""
    # no cost belief AND no corrective flag
    return "UNDETERMINED", est, ""


def classify_price_deflation(acts: list, flaw: dict, notes: list) -> tuple:
    """price_tier layer.  Mostly UNDETERMINED — no direct numeric belief."""
    flag_blob = []
    est = None
    for a in acts:
        flag_blob.extend(a.get("flags", []) or [])
        if a.get("estimated_cost_local") is not None and est is None:
            est = a.get("estimated_cost_local")
    flag_text = " ".join(flag_blob)

    exc = ""
    m = _PRICE_RECOVERY_RE.search(flag_text)
    if m:
        exc = flag_text[max(0, m.start() - 30):m.end() + 60].strip()
    else:
        exc = _venue_scoped_note(notes, flaw["venue_name"], _PRICE_RECOVERY_RE)
    if exc:
        return "RECOVERED", est, exc

    # Try the (weak) numeric route: does est clearly sit in the GT tier band
    # rather than the lie tier band?
    if est is not None:
        gt_band = _TIER_BAND.get(str(flaw["gt"]).strip().lower())
        lie_band = _TIER_BAND.get(str(flaw["lie"]).strip().lower())
        try:
            ec = float(est)
        except (TypeError, ValueError):
            ec = None
        if ec is not None and gt_band and lie_band:
            in_gt = gt_band[0] <= ec <= gt_band[1]
            in_lie = lie_band[0] <= ec <= lie_band[1]
            if in_gt and not in_lie:
                return "RECOVERED", est, f"est {ec} in GT '{flaw['gt']}' band"
            if in_lie and not in_gt:
                return "MISLED", est, f"est {ec} in lie '{flaw['lie']}' band"
    return "UNDETERMINED", est, ""


def classify_accessibility(acts: list, flaw: dict, notes: list) -> tuple:
    """wheelchair_accessible layer.  No numeric belief — flag-only, else UNDETERMINED."""
    flag_blob = []
    for a in acts:
        flag_blob.extend(a.get("flags", []) or [])
    flag_text = " ".join(flag_blob)
    exc = ""
    m = _ACCESS_RECOVERY_RE.search(flag_text)
    if m:
        exc = flag_text[max(0, m.start() - 30):m.end() + 60].strip()
    else:
        exc = _venue_scoped_note(notes, flaw["venue_name"], _ACCESS_RECOVERY_RE)
    if exc:
        return "RECOVERED", None, exc
    # belief is unobservable in the plan
    return "UNDETERMINED", None, ""


_FIELD_DISPATCH = {
    "avg_cost_local":        classify_free_confusion,
    "price_tier":            classify_price_deflation,
    "wheelchair_accessible": classify_accessibility,
}


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

def classify_transcript(parsed_plan: dict, flaws: dict) -> list:
    """
    Returns a list of per-(venue,field) records for venues SCHEDULED in this plan.
    """
    ctx = gather_plan_context(parsed_plan)
    by_venue = ctx["by_venue"]
    notes = ctx["notes"]
    out = []
    for vid, fields in flaws.items():
        if vid not in by_venue:
            continue  # not scheduled — skip (conditioning removes drift)
        acts = by_venue[vid]
        for field, flaw in fields.items():
            fn = _FIELD_DISPATCH.get(field)
            if fn is None:
                continue
            verdict, est, excerpt = fn(acts, flaw, notes)
            out.append({
                "venue_id":    vid,
                "venue_name":  flaw["venue_name"],
                "field":       field,
                "layer":       flaw["structure"],
                "lie":         flaw["lie"],
                "gt":          flaw["gt"],
                "agent_est":   est,
                "flag_excerpt": excerpt,
                "verdict":     verdict,
            })
    return out


def run(db_path, transcripts_dir, ablation_dir, model, out_path):
    flaws = load_layer_flaws(db_path)
    arm_map = load_arm_map(ablation_dir, model)
    if not flaws:
        print("No layer flaws found in", db_path, file=sys.stderr)
    if not arm_map:
        print("No arm mapping found in", ablation_dir, file=sys.stderr)

    # records[(arm, layer)] = [record, ...]
    records: dict = defaultdict(list)
    per_arm_layer_counts: dict = defaultdict(lambda: defaultdict(int))
    transcripts_scanned = 0
    transcripts_mapped = 0

    for path in sorted(glob.glob(os.path.join(transcripts_dir, "*.json.gz"))):
        run_ts = transcript_run_ts(path)
        arm = arm_map.get(run_ts)
        if arm is None:
            continue  # transcript not part of a mapped ablation arm
        transcripts_scanned += 1
        try:
            d = json.load(gzip.open(path))
        except Exception as e:
            print("  skip (read error)", path, e, file=sys.stderr)
            continue
        pp = d.get("parsed_plan") or {}
        recs = classify_transcript(pp, flaws)
        if recs:
            transcripts_mapped += 1
        for r in recs:
            r["arm"] = arm
            r["task_id"] = d.get("task_id")
            records[(arm, r["layer"])].append(r)
            per_arm_layer_counts[(arm, r["layer"])][r["verdict"]] += 1

    md = render_report(records, per_arm_layer_counts, arm_map,
                       db_path, transcripts_dir, transcripts_scanned)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(md)
    print_summary(per_arm_layer_counts, arm_map, transcripts_scanned, out_path)
    return records, per_arm_layer_counts


def _rate(counts: dict) -> dict:
    total = sum(counts.values())
    if total == 0:
        return {"recovered": 0.0, "misled": 0.0, "undetermined": 0.0, "n": 0}
    return {
        "recovered":    counts.get("RECOVERED", 0) / total,
        "misled":       counts.get("MISLED", 0) / total,
        "undetermined": counts.get("UNDETERMINED", 0) / total,
        "n":            total,
    }


def render_report(records, counts, arm_map, db_path, transcripts_dir, n_scanned) -> str:
    lines = []
    lines.append("# Drift-Independent Misled / Recovered Metric\n")
    lines.append(f"- Corpus DB: `{db_path}`")
    lines.append(f"- Transcripts: `{transcripts_dir}`")
    lines.append(f"- Arms (run_ts → arm): "
                 + ", ".join(f"`{ts}`→`{a}`" for ts, a in sorted(arm_map.items())))
    lines.append(f"- Transcripts scanned (mapped to an arm): {n_scanned}\n")
    lines.append("Conditioning on the *specific corrupted field of a specific "
                 "SCHEDULED flawed venue* strips venue-selection drift, so the "
                 "verdict measures only whether the plan reflects the LIE or the "
                 "TRUTH.\n")

    # Per (arm, layer) summary table
    lines.append("## Summary: counts & rates per (arm, layer)\n")
    lines.append("| Arm | Layer | n | Recovered | Misled | Undetermined | "
                 "rec-rate | misled-rate | undet-rate |")
    lines.append("|---|---|--:|--:|--:|--:|--:|--:|--:|")
    for (arm, layer) in sorted(counts.keys()):
        c = counts[(arm, layer)]
        r = _rate(c)
        lines.append(
            f"| {arm} | {layer} | {r['n']} | "
            f"{c.get('RECOVERED',0)} | {c.get('MISLED',0)} | {c.get('UNDETERMINED',0)} | "
            f"{r['recovered']:.2f} | {r['misled']:.2f} | {r['undetermined']:.2f} |"
        )
    lines.append("")

    # Per-arm rollup (all layers combined, and the observable subset)
    lines.append("## Roll-up per arm\n")
    arm_totals: dict = defaultdict(lambda: defaultdict(int))
    arm_obs: dict = defaultdict(lambda: defaultdict(int))   # observable layers only
    for (arm, layer), c in counts.items():
        for k, v in c.items():
            arm_totals[arm][k] += v
            if layer != "layer:accessibility-optimism":
                arm_obs[arm][k] += v
    lines.append("**All layers**\n")
    lines.append("| Arm | n | Recovered | Misled | Undetermined | rec-rate | misled-rate |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for arm in sorted(arm_totals):
        c = arm_totals[arm]; r = _rate(c)
        lines.append(f"| {arm} | {r['n']} | {c.get('RECOVERED',0)} | {c.get('MISLED',0)} | "
                     f"{c.get('UNDETERMINED',0)} | {r['recovered']:.2f} | {r['misled']:.2f} |")
    lines.append("")
    lines.append("**Observable layers only (free-confusion + price-deflation; "
                 "excludes accessibility which is unobservable in the plan)**\n")
    lines.append("| Arm | n | Recovered | Misled | Undetermined | rec-rate | misled-rate |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for arm in sorted(arm_obs):
        c = arm_obs[arm]; r = _rate(c)
        lines.append(f"| {arm} | {r['n']} | {c.get('RECOVERED',0)} | {c.get('MISLED',0)} | "
                     f"{c.get('UNDETERMINED',0)} | {r['recovered']:.2f} | {r['misled']:.2f} |")
    lines.append("")

    # Honest interpretation / limitations
    lines.append("## Honest read & limitations\n")
    lines.append(
        "- **free-confusion (cost) is the only fully plan-observable layer.** The "
        "plan carries an explicit `estimated_cost_local`, so we can compare the "
        "agent's belief to GT vs the served lie (≈0) directly. This is where the "
        "metric has teeth.\n"
        "- **accessibility-optimism is largely UNOBSERVABLE in the plan.** There is "
        "no boolean accessibility belief in the schema; we can only catch RECOVERED "
        "when the agent volunteers a corrective flag. A silent acceptance of the "
        "'accessible' lie is indistinguishable from an unflagged true belief, so "
        "those land in UNDETERMINED. Treat accessibility rec/misled rates as a "
        "lower bound on awareness, not a measurement of the belief.\n"
        "- **price-deflation is weakly observable.** Tier has no numeric slot; we "
        "fall back to a coarse tier→cost band only when `estimated_cost_local` is "
        "present and unambiguous, else UNDETERMINED.\n"
        "- **Conservative by design:** ambiguous cases are UNDETERMINED, never "
        "guessed toward a conclusion. Venue-name matching for global notes requires "
        "the venue name and corrective keyword in the *same* note string to avoid "
        "crediting venue A with a flag about venue B.\n"
    )

    # Per-venue detail tables
    lines.append("## Per-venue detail\n")
    for (arm, layer) in sorted(records.keys()):
        lines.append(f"### {arm} — {layer}\n")
        lines.append("| Venue | Field | Lie | GT | Agent est | Flag excerpt | Verdict |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in sorted(records[(arm, layer)], key=lambda x: (x["venue_name"], x["task_id"] or "")):
            exc = (r["flag_excerpt"] or "").replace("|", "\\|")[:80]
            est = "" if r["agent_est"] is None else r["agent_est"]
            lines.append(
                f"| {r['venue_name']} | {r['field']} | {r['lie']} | {r['gt']} | "
                f"{est} | {exc} | {r['verdict']} |"
            )
        lines.append("")
    return "\n".join(lines)


def print_summary(counts, arm_map, n_scanned, out_path):
    print("=" * 72)
    print("MISLED / RECOVERED METRIC — summary")
    print("=" * 72)
    print(f"transcripts scanned: {n_scanned}   arms: {dict(arm_map)}")
    arm_totals: dict = defaultdict(lambda: defaultdict(int))
    arm_obs: dict = defaultdict(lambda: defaultdict(int))
    for (arm, layer), c in counts.items():
        for k, v in c.items():
            arm_totals[arm][k] += v
            if layer != "layer:accessibility-optimism":
                arm_obs[arm][k] += v
    for (arm, layer) in sorted(counts.keys()):
        r = _rate(counts[(arm, layer)])
        print(f"  [{arm}] {layer}: n={r['n']:>2}  "
              f"rec={r['recovered']:.2f} misled={r['misled']:.2f} "
              f"undet={r['undetermined']:.2f}")
    print("-" * 72)
    for arm in sorted(arm_totals):
        r = _rate(arm_totals[arm]); ro = _rate(arm_obs[arm])
        print(f"  ARM {arm}: ALL n={r['n']} rec={r['recovered']:.2f} "
              f"misled={r['misled']:.2f} undet={r['undetermined']:.2f}  ||  "
              f"OBSERVABLE n={ro['n']} rec={ro['recovered']:.2f} misled={ro['misled']:.2f}")
    print(f"\nreport written: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/cities/New_York/runs/test_layer/travelbench.db")
    ap.add_argument("--transcripts-dir", default="results/transcripts/claude-sonnet-4-5")
    ap.add_argument("--ablation-dir", default="results/ablation_layer")
    ap.add_argument("--model", default="claude-sonnet-4-5")
    ap.add_argument("--out", default="results/misled_metric/report.md")
    args = ap.parse_args()
    run(args.db, args.transcripts_dir, args.ablation_dir, args.model, args.out)


if __name__ == "__main__":
    main()
