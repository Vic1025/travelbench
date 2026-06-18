"""scripts/generation/flaw_masks.py

A small, deterministic library of *flaw masks* for the b1 corruption operator
(structured fields only).

A MASK describes a recipe for a single, load-bearing, FALSE-POSITIVE flaw:
the corrupted value makes a venue *look better* than it really is against the
binding constraint that reads its field, so the GT-based scorer (which reads
the clean `venues` table) penalises an agent that trusts the served lie.

Mask shape (a plain dict, built by `make_mask`):
    {
      "id":                     str,            # stable mask identifier
      "predicate":              callable,       # venue_row(dict) -> bool
      "target_field":           str,            # structured venue column
      "direction":              "false_positive",
      "structure":              "minority_truth" | "stale_authority",
      "value_fn":               callable,       # true_value -> wrong_value (str|None)
      "target_repairability_band": (lo, hi),    # advisory band on certified repairability
    }

Determinism:
  * value_fn is a pure function of the true value only (no RNG, no clock).
  * Cost-down halves the cost (rounded) and would not change a value that is
    already 0 (returns None -> caller skips).
  * Hours-widen opens earlier / closes later by a fixed amount, clamped to a
    valid 24h clock. Non-canonical "HH:MM-HH:MM" strings return None (skip).
  * booking_required True->False is a constant flip.

Direction = false_positive means, for the trapped field:
  * cost-DOWN: lower avg_cost_local  -> looks affordable (beats a budget ceiling)
  * hours-WIDEN: open earlier / close later -> looks open when it is not
  * booking->False: walk-in looks fine when a booking is really required

`structure` labels how the truth is recoverable, matching the certifier's
vocabulary stored on wrong_info.structure:
  * minority_truth  -> the served surface (yelp/blog) lies; a minority of
                       sources still carry the truth (truth_carrier doc).
  * stale_authority -> an authoritative source (official site) carries truth
                       but is old/low-salience; the lie dominates by count.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

DIRECTION = "false_positive"

# Canonical hours format the masks understand: "HH:MM-HH:MM" (24h).
# Split-service ("HH:MM-HH:MM,HH:MM-HH:MM"), "closed", and 12h am/pm strings
# are intentionally NOT widened — value_fn returns None and the caller skips.


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (pure)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_hhmm(s: str) -> Optional[int]:
    """'HH:MM' -> minutes since midnight, or None if not parseable."""
    try:
        hh, mm = s.split(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def _fmt_hhmm(mins: int) -> str:
    mins %= (24 * 60)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _is_simple_range(value: str) -> bool:
    """True only for a single canonical 'HH:MM-HH:MM' range."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if "," in v or "-" not in v:
        return False
    parts = v.split("-")
    if len(parts) != 2:
        return False
    return _parse_hhmm(parts[0]) is not None and _parse_hhmm(parts[1]) is not None


# ─────────────────────────────────────────────────────────────────────────────
# value_fn implementations (true_value -> wrong_value | None)
# ─────────────────────────────────────────────────────────────────────────────

def cost_halve(true_value) -> Optional[str]:
    """avg_cost_local DOWN: halve and round. Skip free/zero/unparseable."""
    try:
        c = float(true_value)
    except (TypeError, ValueError):
        return None
    if c <= 0:
        return None
    wrong = round(c * 0.5)
    if wrong < 0:
        return None
    if wrong >= c:           # rounding must actually lower the cost
        return None
    # Preserve REAL-valued column convention (venues.avg_cost_local is REAL).
    return f"{float(wrong)}"


def price_tier_down(true_value) -> Optional[str]:
    """price_tier DOWN one rung (looks cheaper). Skip if already cheapest."""
    order = ["free", "budget", "mid", "upscale", "fine-dining", "luxury"]
    if true_value not in order:
        return None
    i = order.index(true_value)
    if i == 0:
        return None
    return order[i - 1]


def hours_open_earlier(true_value, *, by_hours: int = 2) -> Optional[str]:
    """Open earlier by `by_hours` (looks open earlier). Clamp at 00:00."""
    if not _is_simple_range(true_value):
        return None
    open_s, close_s = true_value.strip().split("-")
    o, c = _parse_hhmm(open_s), _parse_hhmm(close_s)
    if o is None or c is None:
        return None
    new_o = max(0, o - by_hours * 60)
    if new_o >= o:           # already at/near midnight, no widening possible
        return None
    return f"{_fmt_hhmm(new_o)}-{_fmt_hhmm(c)}"


def hours_close_later(true_value, *, by_hours: int = 2) -> Optional[str]:
    """Close later by `by_hours` (looks open later). Clamp at 24:00."""
    if not _is_simple_range(true_value):
        return None
    open_s, close_s = true_value.strip().split("-")
    o, c = _parse_hhmm(open_s), _parse_hhmm(close_s)
    if o is None or c is None:
        return None
    # If close < open the range already wraps past midnight; don't touch it.
    if c <= o:
        return None
    new_c = min(24 * 60, c + by_hours * 60)
    if new_c <= c:
        return None
    # Render 24*60 as "24:00" (a valid closing time), not "00:00" (which
    # _fmt_hhmm would wrap to). Matches the DB's "00:00-24:00" convention.
    close_str = "24:00" if new_c == 24 * 60 else _fmt_hhmm(new_c)
    return f"{open_s}-{close_str}"


def booking_to_false(true_value) -> Optional[str]:
    """booking_required True(1) -> False(0). Skip if already not required."""
    try:
        b = int(true_value)
    except (TypeError, ValueError):
        return None
    if b != 1:
        return None
    return "0"


# ─────────────────────────────────────────────────────────────────────────────
# Predicates (venue_row -> bool)
# ─────────────────────────────────────────────────────────────────────────────

_UPSCALE_TIERS = {"upscale", "fine-dining", "luxury"}


def _is_upscale(v: Dict) -> bool:
    if v.get("price_tier") in _UPSCALE_TIERS:
        return True
    # Fallback: above-typical cost even if tier label missing.
    c = v.get("avg_cost_local")
    try:
        return c is not None and float(c) >= 40.0
    except (TypeError, ValueError):
        return False


def _has_cost(v: Dict) -> bool:
    c = v.get("avg_cost_local")
    try:
        return c is not None and float(c) > 0
    except (TypeError, ValueError):
        return False


def _has_price_tier(v: Dict) -> bool:
    return v.get("price_tier") in {"budget", "mid", "upscale", "fine-dining", "luxury"}


def _booking_required(v: Dict) -> bool:
    try:
        return int(v.get("booking_required") or 0) == 1
    except (TypeError, ValueError):
        return False


def _always(_v: Dict) -> bool:
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Mask factory + library
# ─────────────────────────────────────────────────────────────────────────────

def make_mask(mask_id: str, predicate: Callable, target_field: str,
              structure: str, value_fn: Callable,
              target_repairability_band: Tuple[float, float]) -> Dict:
    return {
        "id": mask_id,
        "predicate": predicate,
        "target_field": target_field,
        "direction": DIRECTION,
        "structure": structure,
        "value_fn": value_fn,
        "target_repairability_band": target_repairability_band,
    }


# Hours fields the WIDEN masks may attach to.
HOURS_FIELDS = (
    "hours_mon", "hours_tue", "hours_wed", "hours_thu",
    "hours_fri", "hours_sat", "hours_sun",
)


def build_masks() -> List[Dict]:
    """Return the canonical mask library (deterministic order)."""
    masks: List[Dict] = [
        # 1) cost-DOWN on upscale venues — beats a budget ceiling falsely.
        make_mask(
            "cost_down_upscale",
            predicate=lambda v: _is_upscale(v) and _has_cost(v),
            target_field="avg_cost_local",
            structure="minority_truth",
            value_fn=cost_halve,
            target_repairability_band=(0.2, 0.8),
        ),
        # 2) cost-DOWN on any priced venue (broader fallback).
        make_mask(
            "cost_down_any",
            predicate=_has_cost,
            target_field="avg_cost_local",
            structure="minority_truth",
            value_fn=cost_halve,
            target_repairability_band=(0.2, 0.8),
        ),
        # 3) price_tier DOWN one rung — looks within a tier ceiling.
        make_mask(
            "price_tier_down",
            predicate=_has_price_tier,
            target_field="price_tier",
            structure="minority_truth",
            value_fn=price_tier_down,
            target_repairability_band=(0.2, 0.8),
        ),
        # 4) hours WIDEN — close later (looks open in the evening).
        make_mask(
            "hours_close_later",
            predicate=_always,
            target_field="__hours__",   # resolved to a concrete hours_<dow>
            structure="stale_authority",
            value_fn=hours_close_later,
            target_repairability_band=(0.5, 0.9),
        ),
        # 5) hours WIDEN — open earlier (looks open in the morning).
        make_mask(
            "hours_open_earlier",
            predicate=_always,
            target_field="__hours__",
            structure="stale_authority",
            value_fn=hours_open_earlier,
            target_repairability_band=(0.5, 0.9),
        ),
        # 6) booking_required True->False — walk-in looks fine.
        make_mask(
            "booking_required_off",
            predicate=_booking_required,
            target_field="booking_required",
            structure="minority_truth",
            value_fn=booking_to_false,
            target_repairability_band=(0.2, 0.8),
        ),
    ]
    return masks


# Index helpers used by the operator.
def masks_for_field(field: str) -> List[Dict]:
    """Return masks whose target_field matches `field`.

    Hours masks declare target_field='__hours__' and match any hours_<dow>.
    """
    out = []
    for m in build_masks():
        tf = m["target_field"]
        if tf == field:
            out.append(m)
        elif tf == "__hours__" and field in HOURS_FIELDS:
            out.append(m)
    return out
