"""
recovery_probe.py — cheap truth-recovery probe.

Tests whether the model is fooled more by a CORRELATED WRONG-MAJORITY (copying bloc)
than by an ISOLATED single fault. Pure truth-recovery, no planning/venue-selection.

Conditions per (venue, field):
  CLEAN     : 3 sources all state GT.
  ISO       : 1 WRONG + 2 GT  (truth = isolated, majority correct).
  BLOC      : 3 WRONG (identical) + 1 GT  (truth = minority, no tell).
  BLOC_TELL : same as BLOC but the 3 wrong sources also share an identical oddly-specific
              claim about a DIFFERENT venue (detectable copy signature); lone GT independent.

Run: python3 scripts/recovery_probe.py
"""
import os, sys, json, re, time
from pathlib import Path
from datetime import datetime

import anthropic

ROOT = Path(__file__).parent.parent
OUT = ROOT / "results" / "recovery_probe"
OUT.mkdir(parents=True, exist_ok=True)

MODEL = "claude-sonnet-4-5"
REPEATS = 2

# ── 8 hand-picked CLEAN venues (has_wrong_info_planned=0). GT = venues-table value. ──
# 4 numeric (avg_cost_local), 4 hours_fri.
VENUES = [
    # field=cost
    {"name": "Katz's Delicatessen", "field": "average cost per person (USD)",
     "ftype": "cost", "gt": 25, "wrong": 12},
    {"name": "Blue Note Jazz Club", "field": "average cost per person (USD)",
     "ftype": "cost", "gt": 65, "wrong": 32},
    {"name": "Guggenheim Museum", "field": "average cost per person (USD)",
     "ftype": "cost", "gt": 25, "wrong": 13},
    {"name": "Peter Luger Steak House", "field": "average cost per person (USD)",
     "ftype": "cost", "gt": 100, "wrong": 50},
    # field=hours (Friday). wrong = widen close by 2-3h.
    {"name": "American Museum of Natural History", "field": "Friday opening hours",
     "ftype": "hours", "gt": "10:00-17:30", "wrong": "10:00-20:00"},
    {"name": "New York Transit Museum", "field": "Friday opening hours",
     "ftype": "hours", "gt": "10:00-16:00", "wrong": "10:00-19:00"},
    {"name": "Nom Wah Tea Parlor", "field": "Friday opening hours",
     "ftype": "hours", "gt": "10:30-21:30", "wrong": "10:30-00:00"},
    {"name": "Joe's Shanghai", "field": "Friday opening hours",
     "ftype": "hours", "gt": "11:00-21:45", "wrong": "11:00-00:30"},
]

# An unrelated venue + oddly-specific copy-signature claim for BLOC_TELL.
COPY_SIG = ('It also notes (word-for-word the same line seen in the other two): '
            '"The Liberty View Diner on Pier 17 serves exactly 47 varieties of pancake '
            'and closes for a 12-minute break at 3:48pm daily."')


def fmt(v, ftype):
    if ftype == "cost":
        return f"about ${v} per person"
    return f"open {v} on Fridays"


def build_snippets(v, condition):
    name, field, ftype = v["name"], v["field"], v["ftype"]
    gt, wrong = v["gt"], v["wrong"]
    gtxt, wtxt = fmt(gt, ftype), fmt(wrong, ftype)

    if condition == "CLEAN":
        return [
            f"Yelp listing: {name} is {gtxt}.",
            f"Blog 'NYC Eats Daily': We confirmed that {name} is {gtxt}.",
            f"Forum user @localguide: Visited recently — {name} is {gtxt}.",
        ]
    if condition == "ISO":
        return [
            f"Blog 'CityWanderer': One report says {name} is {wtxt}.",
            f"Yelp listing: {name} is {gtxt}.",
            f"Forum user @localguide: Visited recently — {name} is {gtxt}.",
        ]
    if condition == "BLOC":
        return [
            f"Yelp listing: {name} is {wtxt}.",
            f"Blog 'CityWanderer': {name} is {wtxt}.",
            f"Forum user @tripfan88: {name} is {wtxt}.",
            f"Forum user @localguide (independent visit log): {name} is {gtxt}.",
        ]
    if condition == "BLOC_TELL":
        return [
            f"Yelp listing: {name} is {wtxt}. {COPY_SIG}",
            f"Blog 'CityWanderer': {name} is {wtxt}. {COPY_SIG}",
            f"Forum user @tripfan88: {name} is {wtxt}. {COPY_SIG}",
            f"Forum user @localguide (independent first-hand visit log): {name} is {gtxt}.",
        ]
    raise ValueError(condition)


def build_prompt(v, condition):
    snips = build_snippets(v, condition)
    lines = "\n".join(f"- {s}" for s in snips)
    return (
        f"Sources about {v['name']}:\n{lines}\n\n"
        f"Based ONLY on these sources, what is the true {v['field']} of {v['name']}? "
        f"Reply STRICT JSON only: "
        f'{{"value": <value or "unknown">, "confidence": <0-1>, "why": "<one sentence>"}}'
    )


def parse(text):
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ── classification ──
def num(x):
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = re.search(r'(\d+(?:\.\d+)?)', x.replace(",", ""))
        if m:
            return float(m.group(1))
    return None


def _to_24h(hh, mm, ampm):
    h = int(hh); m = int(mm) if mm else 0
    ampm = (ampm or "").lower()
    if ampm == "pm" and h != 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def norm_hours(x):
    if not isinstance(x, str):
        return None
    # capture HH(:MM)?(am/pm)? tokens
    toks = re.findall(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|AM|PM)?', x)
    toks = [t for t in toks if t[0] != ""]
    if len(toks) >= 2:
        a = _to_24h(*toks[0])
        b = _to_24h(*toks[-1])
        return f"{a}-{b}"
    return None


def classify(resp, v):
    if resp is None:
        return "other"
    val = resp.get("value")
    if isinstance(val, str) and val.strip().lower() == "unknown":
        return "abstained"
    ftype = v["ftype"]
    if ftype == "cost":
        n = num(val)
        if n is None:
            return "other"
        if abs(n - v["gt"]) <= max(1.0, 0.05 * v["gt"]):
            return "recovered"
        if abs(n - v["wrong"]) <= max(1.0, 0.05 * v["wrong"]):
            return "misled"
        return "other"
    else:
        h = norm_hours(val)
        if h is None:
            return "other"
        if h == v["gt"]:
            return "recovered"
        if h == norm_hours(v["wrong"]):
            return "misled"
        # close-time tolerance: match by close time
        gt_close = v["gt"].split("-")[1]
        w_close = v["wrong"].split("-")[1]
        h_close = h.split("-")[1]
        if h_close == gt_close:
            return "recovered"
        if h_close == w_close:
            return "misled"
        return "other"


CONDITIONS = ["CLEAN", "ISO", "BLOC", "BLOC_TELL"]


def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("No ANTHROPIC_API_KEY", file=sys.stderr); sys.exit(1)
    client = anthropic.Anthropic(api_key=key)

    raw = []
    for v in VENUES:
        for cond in CONDITIONS:
            prompt = build_prompt(v, cond)
            for rep in range(REPEATS):
                r = client.messages.create(
                    model=MODEL, max_tokens=300, temperature=0,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(b.text for b in r.content if hasattr(b, "text"))
                resp = parse(text)
                label = classify(resp, v)
                rec = {
                    "venue": v["name"], "field": v["field"], "ftype": v["ftype"],
                    "gt": v["gt"], "wrong": v["wrong"], "condition": cond, "repeat": rep,
                    "raw_text": text, "parsed": resp, "label": label,
                    "confidence": (resp or {}).get("confidence"),
                    "why": (resp or {}).get("why"),
                }
                raw.append(rec)
                print(f"{cond:10s} {v['name'][:26]:26s} rep{rep} -> {label} "
                      f"(val={resp.get('value') if resp else '?'})")

    (OUT / "raw_responses.json").write_text(json.dumps(raw, indent=2))

    # aggregate
    agg = {}
    for cond in CONDITIONS:
        rows = [r for r in raw if r["condition"] == cond]
        n = len(rows)
        counts = {k: sum(1 for r in rows if r["label"] == k)
                  for k in ["recovered", "misled", "abstained", "other"]}
        misled_conf = [r["confidence"] for r in rows
                       if r["label"] == "misled" and isinstance(r["confidence"], (int, float))]
        agg[cond] = {
            "n": n,
            "recovered": counts["recovered"] / n,
            "misled": counts["misled"] / n,
            "abstained": counts["abstained"] / n,
            "other": counts["other"] / n,
            "counts": counts,
            "mean_conf_when_misled": (sum(misled_conf) / len(misled_conf)) if misled_conf else None,
        }
    (OUT / "summary.json").write_text(json.dumps(agg, indent=2))

    # markdown
    md = ["# Recovery Probe Results", "",
          f"Model: `{MODEL}` | temp=0 | {len(VENUES)} venues x {len(CONDITIONS)} conditions x {REPEATS} repeats = {len(raw)} calls",
          f"Run: {datetime.utcnow().isoformat()}Z", "",
          "| Condition | n | recovered | misled | abstained | other | mean_conf(misled) |",
          "|---|---|---|---|---|---|---|"]
    for cond in CONDITIONS:
        a = agg[cond]
        mc = f"{a['mean_conf_when_misled']:.2f}" if a["mean_conf_when_misled"] is not None else "-"
        md.append(f"| {cond} | {a['n']} | {a['recovered']:.0%} | {a['misled']:.0%} | "
                  f"{a['abstained']:.0%} | {a['other']:.0%} | {mc} |")
    (OUT / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
