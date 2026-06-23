"""scripts/generation/inject_layers.py

LLM layer-authoring sandbox (LAYER_SANDBOX_DESIGN.md + VALID_DIFFICULTY_REDESIGN.md §14).

A *layer* corrupts ONE binding attribute systematically across many venues,
carried by a small set of RECURRING UNRELIABLE SOURCES (the cross-venue tell).
This operator instantiates 3 layers on the real NYC pool, authoring real LLM
prose (incorrect-source + truth-carrier docs) per selected venue.

HONESTY CONTRACT (the test is OF the pipeline):
  Whatever the LLM authors is what we keep. NO manual edits to doc text, NO
  regeneration of "weak" docs, NO dropping flaws because the prose looks
  unconvincing. The only allowed rejection is on FAIRNESS/VALIDITY (a flaw with
  no recovery path: missing incorrect doc, missing truth doc, or unservable
  structured lie) — never to tune difficulty.

What is corrupted, per selected venue (GT `venues` row NEVER mutated):
  1. STRUCTURED served overlay:
       free-confusion        : yelp_avg_cost_local -> 0   + tag 'free-entry' yelp_visible
       accessibility-optimism: tag 'step-free' yelp_visible (served wheelchair signal;
                               the structured wheelchair flag is served from the
                               immutable venues GT, so the tag IS the served signal)
       price-deflation       : yelp_price_tier -> one rung cheaper
  2. INCORRECT doc (1): recurring unreliable author states the wrong claim as plain
     fact + an origin seed.  (LLM, §4(a))
  3. TRUTH doc (1): independent recent visitor states the true value.  (LLM, §4(b))
  4. wrong_info row (direct insert satisfying the ADD_WRONG_INFO invariants: both
     doc roles wired, distinct docs, valid category) + layer provenance
     (structure='layer:<name>', mask_id='layer', seed_used, profile_id).

Determinism: seeded_sample via blake2b(seed|layer|venue); density 0.30; recurring
unreliable identities assigned round-robin over the seeded-sorted selection.

Collisions: one flaw per (venue,field); if two layers target the same (venue,field)
the higher-priority layer wins (free > access > price); the other is dropped+logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.generation.db import get_connection, new_doc_id, new_wrong_info_id

OPERATOR_VERSION = "layer.v1"
# Design default is claude-sonnet-4-20250514 (generate_multi_venue_docs); that id
# 404s on this environment's key. claude-sonnet-4-5-20250929 is the available Sonnet
# (the same line the §8/§12 ablation uses). Override via $LAYER_GEN_MODEL.
GEN_MODEL = os.environ.get("LAYER_GEN_MODEL", "claude-sonnet-4-5-20250929")

PRICE_ORDER = ["budget", "mid", "upscale", "fine-dining"]


# ─────────────────────────────────────────────────────────────────────────────
# LAYER DEFINITIONS  (LAYER_SANDBOX_DESIGN §1)
# ─────────────────────────────────────────────────────────────────────────────
#
# priority: free > access > price  (collision resolution on shared venue+field)
# field:    the wrong_info.affected_field this layer binds (the collision key).
# Each layer has 2-3 recurring (author, source_name) unreliable identities — the
# same source lies across multiple venues = the cross-venue TELL.

LAYERS: List[Dict[str, Any]] = [
    {
        "name": "free-confusion",
        "priority": 0,
        "field": "avg_cost_local",
        "wrong_tag": "free-entry",
        "wrong_claim": "admission is completely free",
        "true_claim_tmpl": "there's a ${cost:.0f} admission charge",
        "origin_seed": ("visited a few years ago, before they introduced an "
                        "entry fee, and it was free back then"),
        "wrong_info_category": "temporal_decay",
        "unreliable_srcs": [
            ("OldTownWanderer", "Frugal NYC Notebook"),
            ("budget_nomad_77", "Free Things To Do Forum"),
            ("ThriftyCityGuides", "ThriftyCityGuides Blog"),
        ],
    },
    {
        "name": "accessibility-optimism",
        "priority": 1,
        "field": "wheelchair_accessible",
        "wrong_tag": "step-free",
        "wrong_claim": "it's step-free and fully wheelchair accessible throughout",
        "true_claim_tmpl": "there are steps at the entrance — it is not step-free",
        "origin_seed": ("walked past the main entrance, which looked level, and "
                        "assumed the whole place was step-free"),
        "wrong_info_category": "subjective",
        "unreliable_srcs": [
            ("AccessOptimist", "Easy Access City Blog"),
            ("rolling_explorer", "Step-Free Travel Forum"),
        ],
    },
    {
        "name": "price-deflation",
        "priority": 2,
        "field": "price_tier",
        "wrong_tag": None,
        "wrong_claim": "it's a {wrong_tier}-priced spot, very reasonable for what you get",
        "true_claim_tmpl": "these days it's firmly {true_tier} — prices have crept up",
        "origin_seed": ("ate here a while back when it was cheaper; prices have "
                        "since climbed a tier but my impression stuck"),
        "wrong_info_category": "temporal_decay",
        "unreliable_srcs": [
            ("StalePriceList", "Old Menu Prices Blog"),
            ("deal_hunter_nyc", "Cheap Eats NYC Forum"),
            ("ValueDiner2019", "ValueDiner Reviews"),
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# SEEDING / SAMPLING
# ─────────────────────────────────────────────────────────────────────────────

def _h(*parts: str) -> int:
    return int.from_bytes(
        hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=8).digest(),
        "big",
    )


def seeded_sample(eligible: List[Dict], seed: str, layer: str,
                  density: float) -> List[Dict]:
    """Deterministic per-venue Bernoulli sample at `density`. A venue is selected
    iff blake2b(seed|layer|venue) mod 10_000 < density*10_000. Order-independent;
    returns selection sorted by venue_id for stable round-robin assignment."""
    cut = int(density * 10_000)
    sel = [v for v in eligible if (_h(seed, layer, v["venue_id"]) % 10_000) < cut]
    return sorted(sel, key=lambda v: v["venue_id"])


# ─────────────────────────────────────────────────────────────────────────────
# ELIGIBILITY  (LAYER_SANDBOX_DESIGN §1)
# ─────────────────────────────────────────────────────────────────────────────

def _eligible(conn: sqlite3.Connection, layer_name: str) -> List[Dict]:
    rows = conn.execute(
        "SELECT venue_id, name, category, district, avg_cost_local, price_tier, "
        "wheelchair_accessible, traffic_tier FROM venues "
        "WHERE city='new_york' AND page_status='verified'"
    ).fetchall()
    venues = [dict(r) for r in rows]
    if layer_name == "free-confusion":
        return [v for v in venues if (v["avg_cost_local"] or 0) > 0]
    if layer_name == "accessibility-optimism":
        return [v for v in venues if not v["wheelchair_accessible"]]
    if layer_name == "price-deflation":
        return [v for v in venues if v["price_tier"] in ("mid", "upscale", "fine-dining")]
    return []


def _cheaper_tier(tier: str) -> Optional[str]:
    try:
        i = PRICE_ORDER.index(tier)
    except ValueError:
        return None
    return PRICE_ORDER[i - 1] if i > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS  (LAYER_SANDBOX_DESIGN §4 — verbatim shape)
# ─────────────────────────────────────────────────────────────────────────────

def _incorrect_prompt(author: str, source_name: str, doc_type: str, persona: str,
                      tone: str, venue: Dict, wrong_claim: str, origin_seed: str) -> str:
    return f"""You are {author}, who writes on {source_name}. Write a realistic {doc_type} \
({persona}, {tone}) about {venue['name']}, a {venue['category']} in {venue['district']}, New York.

Embed this claim NATURALLY, stated as plain fact with no hedging, woven into the post \
(not a bullet): "{wrong_claim}".
The author believes it because: "{origin_seed}".
Do NOT mention anything that contradicts the claim. 350-900 words of body only.

Return JSON: [{{"title":"","author":"{author}","source_name":"{source_name}",\
"date":"YYYY-MM","body":""}}]"""


def _truth_prompt(author2: str, source_name2: str, doc_type: str, venue: Dict,
                  true_claim: str) -> str:
    return f"""You are {author2} on {source_name2}, a recent first-hand visitor. Write a realistic \
{doc_type} about {venue['name']}, a {venue['category']} in {venue['district']}, New York. \
State plainly, as recent fact: "{true_claim}". \
You may note older posts get this wrong. 300-700 words, body only.

Return JSON: [{{"title":"","author":"{author2}","source_name":"{source_name2}",\
"date":"YYYY-MM","body":""}}]"""


# Independent (reliable) truth-carrier identities — distinct from the recurring
# unreliable sources; one per layer, deterministically chosen per venue.
_TRUTH_SRCS = {
    "free-confusion": [
        ("just_visited_ny", "NYC Recent Visits Blog"),
        ("weekend_in_nyc", "Manhattan This Month"),
    ],
    "accessibility-optimism": [
        ("wheels_on_tour", "Access Checked NYC"),
        ("mobility_first_ny", "Real Access Reports"),
    ],
    "price-deflation": [
        ("ate_there_last_week", "Current NYC Eats"),
        ("nyc_bill_watch", "What It Costs Now"),
    ],
}

_PERSONAS = ["local expert", "first-time tourist", "budget traveler",
             "luxury seeker", "family planner", "solo adventurer", "foodie"]
_TONES = ["enthusiastic", "matter-of-fact", "nostalgic", "practical",
          "chatty", "wry observational"]


def _call_llm(prompt: str, api_key: str) -> Optional[Dict]:
    """Call the shared generation LLM; return the first doc dict or None."""
    from scripts.generation.generate_multi_venue_docs import _call_generation_llm
    docs = _call_generation_llm(prompt, GEN_MODEL, api_key)
    if isinstance(docs, list) and docs:
        return docs[0]
    if isinstance(docs, dict):
        return docs
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DOC WRITE  (direct insert satisfying COMMIT's required fields → verified)
# ─────────────────────────────────────────────────────────────────────────────

def _insert_doc(conn: sqlite3.Connection, gen: Dict, author: str, source_name: str,
                doc_type: str, persona: str, tone: str, traffic_tier: str) -> str:
    doc_id = new_doc_id()
    while conn.execute("SELECT 1 FROM source_docs WHERE doc_id=?", (doc_id,)).fetchone():
        doc_id = new_doc_id()
    title = (gen.get("title") or f"{source_name}: a visit").strip()[:200]
    body = (gen.get("body") or "").strip()
    date = (gen.get("date") or "2026-04")
    if len(date) == 7:           # YYYY-MM -> YYYY-MM-DD (FILL date validator shape)
        date = date + "-15"
    # Engagement scaled to traffic tier (COMMIT requires each >= 1).
    eng = {"high": (200, 80, 2500), "mid": (60, 20, 500)}.get(traffic_tier, (20, 6, 100))
    likes, saves, views = eng
    conn.execute(
        """INSERT INTO source_docs
           (doc_id, city, doc_type, title, author, source_name, date,
            likes, saves, view_count, body, mentioned_regulations, mentioned_tags,
            persona, tone, page_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, "new_york", doc_type, title, author, source_name, date,
         likes, saves, views, body, "[]", "[]", persona, tone, "verified"),
    )
    return doc_id


def _embeds(body: str, *needles: str) -> bool:
    """Concept substring check: does the prose contain the claim concept?"""
    b = (body or "").lower()
    return any(n.lower() in b for n in needles if n)


# ─────────────────────────────────────────────────────────────────────────────
# STRUCTURED OVERLAY
# ─────────────────────────────────────────────────────────────────────────────

def _set_visible_tag(conn: sqlite3.Connection, venue_id: str, tag: str) -> None:
    """Add `tag` as a yelp_visible served tag (the wrong structured signal).
    UNIQUE(tag, venue_id): upsert to yelp_visible=1.

    Marks the row injected=1 so the clean ablation arms can exclude this
    layer-planted lie tag while the faulty arm still serves it. We only stamp
    injected=1 when this call actually CREATES the tag (it is genuinely the
    injected lie); if a real pre-existing tag of the same name already sits on
    the venue we leave its injected flag untouched (don't relabel real data)."""
    pre = conn.execute(
        "SELECT 1 FROM tags WHERE tag=? AND venue_id=?", (tag, venue_id)
    ).fetchone()
    if pre is None:
        # Newly injected lie tag → flag it injected=1.
        conn.execute(
            "INSERT INTO tags (tag, city, venue_id, yelp_visible, injected) "
            "VALUES (?,?,?,1,1)",
            (tag, "new_york", venue_id),
        )
    else:
        # Pre-existing real tag → just ensure it's served; don't relabel it.
        conn.execute(
            "UPDATE tags SET yelp_visible=1 WHERE tag=? AND venue_id=?",
            (tag, venue_id),
        )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def inject_layers(src_db: str, dst_db: str, seed: str = "layer-001",
                  density: float = 0.30, api_key: Optional[str] = None,
                  *, write_plan: bool = True) -> Dict[str, Any]:
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    src_db, dst_db = str(src_db), str(dst_db)
    Path(dst_db).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_db, dst_db)
    for ext in ("-wal", "-shm"):
        s = Path(src_db + ext)
        if s.exists():
            shutil.copyfile(str(s), dst_db + ext)

    conn = get_connection(Path(dst_db))
    conn.row_factory = sqlite3.Row

    # ── 1. Plan selections + resolve collisions (free > access > price) ───────
    layer_sel: Dict[str, List[Dict]] = {}
    eligible_counts: Dict[str, int] = {}
    for L in LAYERS:
        elig = _eligible(conn, L["name"])
        eligible_counts[L["name"]] = len(elig)
        layer_sel[L["name"]] = seeded_sample(elig, seed, L["name"], density)

    # Collision map: (venue_id, field) -> winning layer name.
    claimed: Dict[Tuple[str, str], str] = {}
    dropped_collisions: List[Dict] = []
    for L in sorted(LAYERS, key=lambda x: x["priority"]):   # free first
        keep = []
        for v in layer_sel[L["name"]]:
            key = (v["venue_id"], L["field"])
            if key in claimed:
                dropped_collisions.append({
                    "venue_id": v["venue_id"], "field": L["field"],
                    "dropped_layer": L["name"], "kept_layer": claimed[key],
                })
                continue
            claimed[key] = L["name"]
            keep.append(v)
        layer_sel[L["name"]] = keep

    # ── 2. Author + write per layer ──────────────────────────────────────────
    plan_layers: List[Dict] = []
    sample_checks: List[Dict] = []
    n_incorrect_docs = 0
    n_incorrect_docs_embedding_lie = 0

    for L in LAYERS:
        name = L["name"]
        srcs = L["unreliable_srcs"]
        truth_srcs = _TRUTH_SRCS[name]
        selected = layer_sel[name]

        used_sources: Dict[str, int] = {f"{a} / {s}": 0 for a, s in srcs}
        docs_authored = 0
        flaws_written = 0
        validity_rejections: List[Dict] = []
        venues_done: List[Dict] = []

        for idx, v in enumerate(selected):
            vid = v["venue_id"]
            # Round-robin recurring unreliable identity (the cross-venue tell).
            author, source_name = srcs[idx % len(srcs)]
            t_author, t_source = truth_srcs[_h(seed, name, vid) % len(truth_srcs)]
            # Deterministic doc_type/persona/tone (cosmetic — corpus variety).
            doc_type = "forum" if (_h(seed, "dt", vid) % 2) else "blog"
            # forum prose Q&A structure isn't guaranteed from these prompts; keep
            # blog for direct inserts (we bypass COMMIT's forum check, but stay honest).
            doc_type = "blog"
            persona = _PERSONAS[_h(seed, "p", vid) % len(_PERSONAS)]
            tone = _TONES[_h(seed, "t", vid) % len(_TONES)]

            # ----- compute wrong/true values + claims -----
            if name == "free-confusion":
                true_value = v["avg_cost_local"]
                wrong_value = 0
                wrong_claim = L["wrong_claim"]
                true_claim = L["true_claim_tmpl"].format(cost=float(true_value))
                affected_field = "avg_cost_local"
                lie_needles = ["free", "no charge", "no admission", "free admission"]
                truth_needles = [f"${float(true_value):.0f}", "admission",
                                 f"{float(true_value):.0f}"]
            elif name == "accessibility-optimism":
                true_value = 0
                wrong_value = 1
                wrong_claim = L["wrong_claim"]
                true_claim = L["true_claim_tmpl"]
                affected_field = "wheelchair_accessible"
                lie_needles = ["step-free", "wheelchair accessible", "fully accessible",
                               "step free"]
                truth_needles = ["step", "stairs", "not step-free", "not accessible"]
            else:  # price-deflation
                true_tier = v["price_tier"]
                wrong_tier = _cheaper_tier(true_tier)
                if wrong_tier is None:
                    validity_rejections.append({
                        "venue_id": vid, "reason": "no cheaper tier (already budget)"})
                    continue
                true_value = true_tier
                wrong_value = wrong_tier
                wrong_claim = L["wrong_claim"].format(wrong_tier=wrong_tier)
                true_claim = L["true_claim_tmpl"].format(true_tier=true_tier)
                affected_field = "price_tier"
                lie_needles = [wrong_tier, "reasonable", "cheap", "affordable"]
                truth_needles = [true_tier, "prices", "expensive", "pricey", "crept", "climbed"]

            # ----- author the 2 docs (LLM) -----
            try:
                inc_gen = _call_llm(
                    _incorrect_prompt(author, source_name, doc_type, persona, tone,
                                      v, wrong_claim, L["origin_seed"]), api_key)
                truth_gen = _call_llm(
                    _truth_prompt(t_author, t_source, doc_type, v, true_claim), api_key)
            except Exception as e:  # noqa: BLE001
                validity_rejections.append({
                    "venue_id": vid, "reason": f"llm_error: {type(e).__name__}: {e}"})
                continue

            # VALIDITY/FAIRNESS gate: a flaw needs both an incorrect doc and a
            # truth doc (the recovery path). Missing either = no recovery → reject.
            if not inc_gen or not (inc_gen.get("body") or "").strip():
                validity_rejections.append({
                    "venue_id": vid, "reason": "incorrect doc authoring failed (no body)"})
                continue
            if not truth_gen or not (truth_gen.get("body") or "").strip():
                validity_rejections.append({
                    "venue_id": vid, "reason": "truth doc authoring failed (no body) — "
                                               "no recovery path"})
                continue

            inc_body = inc_gen.get("body", "")
            truth_body = truth_gen.get("body", "")

            # ----- write everything atomically -----
            conn.execute("SAVEPOINT lf")
            try:
                inc_doc_id = _insert_doc(conn, inc_gen, author, source_name, doc_type,
                                         persona, tone, v["traffic_tier"])
                # truth doc uses a different persona so within-venue uniqueness
                # would hold if it went through COMMIT; direct insert here.
                t_persona = _PERSONAS[(_h(seed, "p", vid) + 1) % len(_PERSONAS)]
                truth_doc_id = _insert_doc(conn, truth_gen, t_author, t_source, doc_type,
                                           t_persona, tone, v["traffic_tier"])

                conn.execute(
                    "INSERT OR IGNORE INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)",
                    (inc_doc_id, vid))
                conn.execute(
                    "INSERT OR IGNORE INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)",
                    (truth_doc_id, vid))

                wi_id = new_wrong_info_id()
                conn.execute(
                    """INSERT INTO wrong_info
                       (wrong_info_id, venue_id, affected_field, incorrect_value,
                        correct_value, source_type, wrong_info_category, origin_story,
                        seed_used, structure, profile_id, mask_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (wi_id, vid, affected_field, str(wrong_value), str(true_value),
                     "forum" if doc_type == "forum" else "blog",
                     L["wrong_info_category"], L["origin_seed"],
                     str(_h(seed, name, vid)), f"layer:{name}", name, "layer"),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO doc_venue_roles (doc_id, venue_id, role, wrong_info_id) "
                    "VALUES (?,?,?,?)", (inc_doc_id, vid, "incorrect_source", wi_id))
                conn.execute(
                    "INSERT OR REPLACE INTO doc_venue_roles (doc_id, venue_id, role, wrong_info_id) "
                    "VALUES (?,?,?,?)", (truth_doc_id, vid, "truth_carrier", wi_id))

                # STRUCTURED served overlay.
                if name == "free-confusion":
                    conn.execute(
                        "UPDATE yelp_listings SET yelp_avg_cost_local=0 WHERE venue_id=?",
                        (vid,))
                    _set_visible_tag(conn, vid, L["wrong_tag"])
                elif name == "accessibility-optimism":
                    _set_visible_tag(conn, vid, L["wrong_tag"])   # served wheelchair signal
                else:  # price-deflation
                    conn.execute(
                        "UPDATE yelp_listings SET yelp_price_tier=? WHERE venue_id=?",
                        (wrong_value, vid))

                conn.execute("RELEASE lf")
            except Exception as e:  # noqa: BLE001
                conn.execute("ROLLBACK TO lf")
                conn.execute("RELEASE lf")
                validity_rejections.append({
                    "venue_id": vid, "reason": f"write_error: {type(e).__name__}: {e}"})
                continue

            # ----- accounting + embed measurement (honest, no fixing) -----
            docs_authored += 2
            flaws_written += 1
            used_sources[f"{author} / {source_name}"] += 1
            n_incorrect_docs += 1
            embeds_lie = _embeds(inc_body, *lie_needles)
            if embeds_lie:
                n_incorrect_docs_embedding_lie += 1
            venues_done.append({
                "venue_id": vid, "name": v["name"], "field": affected_field,
                "wrong_value": str(wrong_value), "true_value": str(true_value),
                "unreliable_source": f"{author} / {source_name}",
                "truth_source": f"{t_author} / {t_source}",
                "incorrect_doc_id": inc_doc_id, "truth_doc_id": truth_doc_id,
                "incorrect_doc_embeds_wrong_claim": embeds_lie,
                "truth_doc_embeds_true_value": _embeds(truth_body, *truth_needles),
            })

        conn.commit()
        plan_layers.append({
            "layer": name, "field": L["field"],
            "eligible": eligible_counts[name],
            "selected_after_collision": len(selected),
            "flaws_written": flaws_written,
            "docs_authored": docs_authored,
            "unreliable_sources_used": used_sources,
            "validity_rejections": validity_rejections,
            "venues": venues_done,
        })
        print(f"[{name}] eligible={eligible_counts[name]} "
              f"selected={len(selected)} kept(flaws)={flaws_written} "
              f"docs={docs_authored} rejections={len(validity_rejections)}")

    # ── corruption_runs provenance ───────────────────────────────────────────
    gt_hash = hashlib.blake2b(digest_size=16)
    for r in conn.execute("SELECT * FROM venues ORDER BY venue_id").fetchall():
        gt_hash.update("|".join(str(x) for x in tuple(r)).encode("utf-8"))
    conn.execute(
        "INSERT OR REPLACE INTO corruption_runs "
        "(run_id, master_seed, profile_id, profile_version, corruptor_version, gt_hash, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"layer-{seed}", seed, "layer_sandbox", "1", OPERATOR_VERSION,
         gt_hash.hexdigest(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    conn.commit()
    conn.close()

    plan = {
        "operator_version": OPERATOR_VERSION,
        "seed": seed, "density": density, "model": GEN_MODEL,
        "src_db": src_db, "dst_db": dst_db,
        "collision_priority": "free-confusion > accessibility-optimism > price-deflation",
        "dropped_collisions": dropped_collisions,
        "layers": plan_layers,
        "incorrect_docs_total": n_incorrect_docs,
        "incorrect_docs_embedding_lie": n_incorrect_docs_embedding_lie,
        "pct_incorrect_docs_state_lie": (
            round(100.0 * n_incorrect_docs_embedding_lie / n_incorrect_docs, 1)
            if n_incorrect_docs else None),
    }
    if write_plan:
        p = Path(dst_db).parent / "layer_plan.json"
        p.write_text(json.dumps(plan, indent=2))
        plan["_plan_path"] = str(p)
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# SANITY CHECK  (print, don't tune)
# ─────────────────────────────────────────────────────────────────────────────

def sanity_check(dst_db: str, plan: Dict, n_samples: int = 3) -> None:
    """For up to n_samples flawed venues, print GT vs served-overlay vs whether
    the incorrect doc body embeds the wrong claim and the truth doc embeds the
    true value. Verifies the prose actually states the lie (the b1 bug)."""
    conn = get_connection(Path(dst_db))
    conn.row_factory = sqlite3.Row
    print("\n" + "=" * 78)
    print("SANITY CHECK — GT vs served-overlay vs doc-embeds-claim")
    print("=" * 78)
    shown = 0
    for L in plan["layers"]:
        for v in L["venues"]:
            if shown >= n_samples:
                break
            vid = v["venue_id"]
            field = v["field"]
            gt = conn.execute(
                f"SELECT {field} AS val FROM venues WHERE venue_id=?", (vid,)
            ).fetchone()["val"]
            if field == "avg_cost_local":
                ov = conn.execute(
                    "SELECT yelp_avg_cost_local AS v FROM yelp_listings WHERE venue_id=?",
                    (vid,)).fetchone()["v"]
                served = ov
            elif field == "price_tier":
                ov = conn.execute(
                    "SELECT yelp_price_tier AS v FROM yelp_listings WHERE venue_id=?",
                    (vid,)).fetchone()["v"]
                served = ov
            else:  # wheelchair_accessible — served signal is the step-free tag
                tag = conn.execute(
                    "SELECT yelp_visible FROM tags WHERE venue_id=? AND tag='step-free'",
                    (vid,)).fetchone()
                served = f"GT wheelchair={gt}; served 'step-free' tag visible={bool(tag and tag['yelp_visible'])}"
            print(f"\n[{L['layer']}] {v['name']} ({vid}) field={field}")
            print(f"  GT value             : {gt}")
            print(f"  served-overlay value : {served}")
            print(f"  incorrect doc {v['incorrect_doc_id']} embeds WRONG claim : "
                  f"{v['incorrect_doc_embeds_wrong_claim']}")
            print(f"  truth doc     {v['truth_doc_id']} embeds TRUE value : "
                  f"{v['truth_doc_embeds_true_value']}")
            shown += 1
        if shown >= n_samples:
            break
    print("\n" + "-" * 78)
    print(f"INCORRECT docs that actually STATE THE LIE: "
          f"{plan['incorrect_docs_embedding_lie']}/{plan['incorrect_docs_total']} "
          f"= {plan['pct_incorrect_docs_state_lie']}%   (pipeline-quality measure; not fixed)")
    print("-" * 78)
    conn.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/cities/New_York/runs/test_70/travelbench.db")
    ap.add_argument("--dst", default="runs/New_York/test_layer/travelbench.db")
    ap.add_argument("--seed", default="layer-001")
    ap.add_argument("--density", type=float, default=0.30)
    args = ap.parse_args()
    plan = inject_layers(args.src, args.dst, args.seed, args.density)
    print(f"\nplan: {plan.get('_plan_path')}")
    sanity_check(args.dst, plan)
