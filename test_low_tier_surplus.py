"""
Quick test: does the LLM actually produce more low-tier venues when we ask for them?

Usage:
  python test_low_tier_surplus.py --api-key sk-ant-...

What it does:
  One API call using the existing plan_venues prompt with n_venues=60
  (13 high + 21 mid + 26 low instead of the default 13 + 21 + 16 = 50).
  Prints the tier/category/district distribution so you can see if the LLM
  obeys the low-tier target or steals slots from high/mid.
"""

import argparse
import json
import sys
from pathlib import Path
from collections import Counter

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from scripts.generation.research_city import STUB_CONFIGS
from scripts.generation.generate_city_venues import plan_venues


def main():
    parser = argparse.ArgumentParser(description="Test low-tier surplus behavior")
    parser.add_argument("--api-key", required=True, help="Anthropic API key")
    parser.add_argument("--n", type=int, default=60, help="Total venues to request (default 60)")
    parser.add_argument("--model", default="claude-sonnet-4-5", help="Planning model")
    args = parser.parse_args()

    cfg = STUB_CONFIGS["london"]

    # Show what the prompt will ask for
    _n_high = max(3, round(args.n * 0.26))
    _n_mid  = max(5, round(args.n * 0.42))
    _n_low  = args.n - _n_high - _n_mid
    print(f"\n{'='*60}")
    print(f"Requesting {args.n} venues: {_n_high} high + {_n_mid} mid + {_n_low} low")
    print(f"{'='*60}\n")

    # One API call
    briefs, _ = plan_venues(
        city_config=cfg,
        api_key=args.api_key,
        model=args.model,
        n_venues=args.n,
        dry_run=False,
    )

    # Analyze results
    tiers = Counter(b.get("traffic_tier", "?") for b in briefs)
    cats  = Counter(b.get("category", "?") for b in briefs)
    dists = Counter(b.get("district", "?") for b in briefs)
    paces = Counter(b.get("recommended_pace", "?") for b in briefs)

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(briefs)} venues returned")
    print(f"{'='*60}")

    print(f"\nTraffic tiers (requested: high={_n_high}, mid={_n_mid}, low={_n_low}):")
    for tier in ["high", "mid", "low"]:
        actual = tiers.get(tier, 0)
        target = {"high": _n_high, "mid": _n_mid, "low": _n_low}[tier]
        delta  = actual - target
        flag   = " ✓" if abs(delta) <= 2 else f" ⚠ ({'+' if delta > 0 else ''}{delta})"
        print(f"  {tier:5s}: {actual:3d} (target {target}){flag}")

    print(f"\nCategories:")
    for cat, count in cats.most_common():
        print(f"  {cat:15s}: {count}")

    print(f"\nDistricts ({len(dists)} unique):")
    for dist, count in dists.most_common():
        print(f"  {dist:20s}: {count}")

    print(f"\nPace: {dict(paces)}")

    # Check for the concern: did high/mid get robbed?
    high_delta = tiers.get("high", 0) - _n_high
    mid_delta  = tiers.get("mid", 0) - _n_mid
    low_delta  = tiers.get("low", 0) - _n_low

    print(f"\n{'='*60}")
    print("VERDICT:")
    if high_delta < -2 or mid_delta < -2:
        print(f"  ⚠ HIGH/MID ROBBED: high {high_delta:+d}, mid {mid_delta:+d}")
        print(f"  → Separate call approach recommended")
    elif low_delta < -5:
        print(f"  ⚠ LOW UNDER-PRODUCED: got {tiers.get('low',0)}, wanted {_n_low}")
        print(f"  → LLM ignoring low target; separate call needed")
    else:
        print(f"  ✓ Distribution acceptable: high {high_delta:+d}, mid {mid_delta:+d}, low {low_delta:+d}")
        print(f"  → Single-call surplus approach works")
    print(f"{'='*60}\n")

    # Dump full briefs for inspection
    out_path = Path("test_low_tier_result.json")
    with open(out_path, "w") as f:
        json.dump(briefs, f, indent=2)
    print(f"Full briefs saved to {out_path}")


if __name__ == "__main__":
    main()
