"""Convert travelbench_overview.html into a native .pptx deck.

Native-rebuild mode: PowerPoint text boxes / shapes / tables, dark theme
that mirrors the HTML's palette. Custom web fonts (DM Serif Display,
DM Mono, Syne) fall back to system equivalents (Georgia, Consolas, Calibri).
"""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from lxml import etree


# Palette (from HTML :root)
BG       = RGBColor(0x0D, 0x0D, 0x12)
SURFACE  = RGBColor(0x13, 0x13, 0x1A)
SURFACE2 = RGBColor(0x1A, 0x1A, 0x24)
BORDER   = RGBColor(0x2A, 0x2A, 0x3A)
ACCENT   = RGBColor(0x6C, 0x8E, 0xFF)  # blue
ACCENT2  = RGBColor(0xFF, 0x6C, 0x8E)  # pink/bad
ACCENT3  = RGBColor(0x6C, 0xFF, 0xC8)  # green/good
TEXT     = RGBColor(0xE8, 0xE8, 0xF0)
MUTED    = RGBColor(0x78, 0x78, 0xA0)
WARN     = RGBColor(0xFF, 0xA8, 0x4A)
PURPLE   = RGBColor(0xC8, 0x7E, 0xFF)

SERIF = "Georgia"
MONO  = "Consolas"
SANS  = "Calibri"

# 16:9
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


# ─────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────

def set_slide_bg(slide, color):
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_rect(slide, left, top, width, height, *, fill=SURFACE, line=BORDER,
             line_width=0.75, rounded=True, line_color=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(shape_type, left, top, width, height)
    if rounded:
        shp.adjustments[0] = 0.06
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line_color is None:
        line_color = line
    if line_color is False:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line_color
        shp.line.width = Pt(line_width)
    shp.shadow.inherit = False
    return shp


def add_text(slide, left, top, width, height, text, *,
             font=SANS, size=12, color=TEXT, bold=False, italic=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    """`text` can be:
       - str: one paragraph
       - list of paragraphs, each either a str or a list of (text, attrs) runs
    """
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    tf.word_wrap = True
    tf.vertical_anchor = anchor

    if isinstance(text, str):
        paragraphs = [text]
    else:
        paragraphs = text

    base = dict(font=font, size=size, color=color, bold=bold, italic=italic)
    for i, para in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if isinstance(para, str):
            _add_run(p, para, base)
        elif isinstance(para, list):
            for run in para:
                if not run:
                    continue
                t, attrs = run
                _add_run(p, t, {**base, **attrs})
    return tb


def _add_run(p, text, attrs):
    r = p.add_run()
    r.text = text
    r.font.name = attrs.get("font", SANS)
    r.font.size = Pt(attrs.get("size", 12))
    r.font.color.rgb = attrs.get("color", TEXT)
    r.font.bold = attrs.get("bold", False)
    r.font.italic = attrs.get("italic", False)


def add_label(slide, left, top, label_text, color=ACCENT):
    """The '01 — Overview' style slide labels."""
    add_text(slide, left, top, Inches(4), Inches(0.3),
             "—  " + label_text.upper(),
             font=MONO, size=10, color=color, bold=False)


def add_title(slide, left, top, width, text, *, size=32):
    add_text(slide, left, top, width, Inches(0.9), text,
             font=SERIF, size=size, color=TEXT, bold=False)


def add_subtitle(slide, left, top, width, text, *, size=16):
    add_text(slide, left, top, width, Inches(0.5), text,
             font=SANS, size=size, color=TEXT, bold=True)


def add_body(slide, left, top, width, height, text, *, color=MUTED, size=12):
    add_text(slide, left, top, width, height, text,
             font=SANS, size=size, color=color)


def add_card(slide, left, top, width, height, title, body, *,
             accent_color=ACCENT, title_size=11, body_size=10):
    add_rect(slide, left, top, width, height, fill=SURFACE, line=BORDER)
    pad = Inches(0.15)
    add_text(slide, left + pad, top + Inches(0.12), width - 2*pad, Inches(0.3),
             title, font=SANS, size=title_size, color=accent_color, bold=True)
    add_text(slide, left + pad, top + Inches(0.45), width - 2*pad, height - Inches(0.55),
             body, font=SANS, size=body_size, color=MUTED)


def hr(slide, left, top, width, color=BORDER):
    shp = slide.shapes.add_connector(1, left, top, left + width, top)
    shp.line.color.rgb = color
    shp.line.width = Pt(0.5)


# ─────────────────────────────────────────────────────────────────
# NYC content data (May 21 2026 wave)
# ─────────────────────────────────────────────────────────────────

# Showcase venue — Corkbuzz Chelsea Market — slides 4-9
SHOWCASE_VENUE = {
    "venue_id": "M4uBkLL",
    "name": "Corkbuzz Chelsea Market",
    "category": "wine bar",
    "district": "Chelsea / Meatpacking District",
    "address": "75 9th Ave, New York, NY 10011",
    "yelp_stars": "4.0★",
    "yelp_reviews": "47 reviews",
    "yelp_last_activity": "2021-03-15",
    "yelp_wrong_fri": "12:00–0:00 (midnight)",
    "yelp_blurb": "Great selection of wines by the glass — perfect for a relaxed evening in Chelsea Market.",
    "forum_doc_id": "L1QYEAH5",
    "forum_author": "NYC_Emily84",
    "forum_tone": "local regular  ·  wry / observational",
    "forum_date": "2025-02-18",
    "forum_title": "Corkbuzz Chelsea Market — still good for a Friday hang?",
    "forum_excerpt": "Last time I was there on a Friday we rolled in around 8:30 and got a table no problem. But heads up — they close at 10pm now, not midnight like it used to be. Some old blog posts and the Yelp listing still say later but they tightened up after the pandemic.",
    "official_url": "corkbuzz.com/chelsea-market",
    "official_correct_fri": "12:00 PM – 10:00 PM",
    "official_snippet": "A relaxed wine bar featuring 40+ rotating wines by the glass, plus craft cocktails. Walk-ins welcome. Dog-friendly on our indoor patio. We ask that guests refrain from photography to preserve the intimate atmosphere.",
    "wi_field": "hours_fri",
    "wi_incorrect": "12:00–0:00",
    "wi_correct": "12:00–22:00",
    "wi_category": "temporal_decay",
    "wi_origin": "Owner registered Yelp hours when Corkbuzz first opened with late-night Friday hours; post-pandemic the venue cut back to 10pm but the Yelp listing was never updated.",
}

# Venue-gen sandbox steps (5) — slides 5–9
VENUE_SB_STEPS = [
    {
        "label": "Plan the trap",
        "type": "think",
        "think": "Wrong info planned: YES. traffic_tier=low allows wrong info. Will seed Yelp with old midnight hours (last_activity 2021), then introduce a 2025 forum post that corrects it. Temporal_decay is the natural pattern — post-pandemic hours cuts are everywhere.",
        "actions": [],
    },
    {
        "label": "Venue row + Yelp listing (wrong hours seeded)",
        "type": "create",
        "think": "Create the canonical venue (ground truth) and Yelp listing (with the trap baked in).",
        "actions": [
            ("CREATE_PAGE", "venue M4uBkLL", "Corkbuzz Chelsea Market — wine bar, low-traffic"),
            ("FILL", "venue: canonical fields", "Friday 12:00–22:00 (truth), pet OK, no photo, no wheelchair"),
            ("CREATE_PAGE", "yelp_listing", "4.0★ · 47 reviews"),
            ("FILL", "yelp: yelp_hours_fri=\"12:00–0:00\" · last_activity 2021-03-15", "incorrect midnight hours + 4-yr-old listing = plausible trap"),
            ("COMMIT", "venue + yelp_listing", "✅"),
        ],
    },
    {
        "label": "Official site = ground truth carrier",
        "type": "create",
        "think": "The official_site_doc is always authoritative by design. Fill it with the correct Friday hours so the agent has a recovery path.",
        "actions": [
            ("CREATE_PAGE", "official_site_doc", "corkbuzz.com/chelsea-market"),
            ("FILL", "body + Friday 12:00–22:00 + regulations", "no photo, dog OK indoor, not wheelchair accessible"),
            ("COMMIT", "official_site_doc", "✅"),
        ],
    },
    {
        "label": "Source docs + atomic wrong-info wiring",
        "type": "create",
        "think": "Two source docs: an old blog post that REPEATS the wrong midnight hours (the trap), and a recent forum post where a local mentions the correction in passing (the truth carrier). ADD_WRONG_INFO links both atomically (P6-T22 fix).",
        "actions": [
            ("CREATE+FILL+COMMIT", "blog H5l6EyK5  (2021 · tourist, talkative)",
             "body: \"…open until midnight on Fridays…\"   ← incorrect_source"),
            ("CREATE+FILL+COMMIT", "forum L1QYEAH5  (2025 · NYC_Emily84, local regular)",
             "body: \"…they close at 10pm now, not midnight like it used to be…tightened up after the pandemic…\"   ← truth_carrier"),
            ("ADD_WRONG_INFO", "venue=M4uBkLL  field=hours_fri  incorrect=12:00–0:00  correct=12:00–22:00  category=temporal_decay",
             "✅  atomic: wrong_info row + both doc_venue_roles in one transaction (P6-T22)"),
        ],
    },
    {
        "label": "VERIFY + SUBMIT",
        "type": "success",
        "think": "All 11 checks must pass: venue structure, tag visibility (hidden tags surface in docs), regulation visibility, wrong-info pairing, truth_carrier_registered, incorrect_source_registered, doc count ≥3.",
        "actions": [
            ("VERIFY", "11 checks", "✅  all pass"),
            ("SUBMIT", "venue M4uBkLL", "✅  status=verified, ready for benchmark"),
        ],
    },
]

# Best tasks per type — London gold-standard set
# (per docs/TASK_REVIEW_ROUND1_REPORT.md — these are the richest constraint examples
#  in the entire dataset; the underlying London corpus is from an earlier wave but
#  the constraint design itself is the gold reference).
BEST_TASKS = [
    {
        "tag": "TYPE 1", "color": "ACCENT", "name": "Cascading Requirements",
        "model": "lon_type1_carnival_anniversary_001  ·  persona-rich",
        "query": "Plan a 2-day romantic London weekend for Sarah, a food writer, and her husband celebrating their 2-year anniversary. Sarah uses a mobility aid. They want authentic British cuisine away from tourist traps, avoid Greenwich/Lewisham, special upscale dinners. Sarah is also a museum-loving culture enthusiast. Visiting during Carnival weekend but want to avoid the main event chaos.",
        "constraints": [
            "[hop-2]  all: wheelchair_accessible = true",
            "[hop-2]  all: meals tagged british",
            "[hop-2]  all: evening meals (18:00–23:59) at price ≥ upscale",
            "[hop-1]  at_least: 1 museum",
            "[hop-2]  none: district in any[Greenwich, Lewisham]",
        ],
        "score": "5 PCs · persona pulls cascade into wheelchair + British + upscale + museum + district exclusion",
    },
    {
        "tag": "TYPE 2", "color": "ACCENT3", "name": "Subset Selection Under Ceiling",
        "model": "lon_type2_late_spring_art_quiet_270  ·  gold-std",
        "query": "Only 4.5 hours in London. Want a compact art-focused day with coffee. Pick a subset that fits within 270 minutes total, keeps meal stops no louder than moderate, and includes at least 3 art venues.",
        "constraints": [
            "[hop-1]  at_least: 3 visits with art tag",
            "[hop-1]  at_least: 1 coffee meal",
            "[hop-2]  all: meals with noise_level ≤ moderate",
        ],
        "score": "Clean partial-day (270 min) with noise tier comparison",
    },
    {
        "tag": "TYPE 3", "color": "ACCENT", "name": "Competing Requirements",
        "model": "claude_sonnet_4_5_new_york_type3  ·  classic conflict",
        "query": "My partner and I are visiting NYC for the July 4th weekend (July 4–6). She insists on hitting the major tourist attractions — the places everyone goes to see. I prefer discovering off-the-beaten-path spots where the crowds are lighter. We want to balance both our styles.",
        "constraints": [
            "[hop-1]  at_least: 5 high-traffic venues  (the partner's wishlist)",
            "[hop-1]  at_least: 5 low-traffic venues   (off-the-beaten-path)",
            "[hop-2]  at_most: 1 intense-pace activity  per_day  (catch breath)",
        ],
        "score": "Pure famous-vs-hidden tension: disjoint pools, daily pace cap to keep both happy",
    },
    {
        "tag": "TYPE 4", "color": "WARN", "name": "Precision Allocation",
        "model": "london_t4_may_weekend  ·  gold-std",
        "query": "Wheelchair-user, 2-day May Bank Holiday. Mid-traffic only. £55 per person per day budget. ≥ 1 museum each day. ≥ 2 British meals. At least half our visits in Westminster.",
        "constraints": [
            "[hop-1]  sum: estimated_cost ≤ £55  per_day",
            "[hop-1]  all: wheelchair_accessible = true",
            "[hop-1]  all: traffic_tier == mid",
            "[hop-1]  at_least: 1 museum  per_day",
            "[hop-2]  at_least: 2 British meals",
            "[hop-2]  ratio: 0.5 Westminster",
        ],
        "score": "6 constraints stacked: sum + all×3 + per_day + ratio",
    },
    {
        "tag": "TYPE 5", "color": "WARN", "name": "Hard Feasibility Reduction",
        "model": "lon_type5_booking_westminster  ·  best task in dataset",
        "query": "1-day Westminster itinerary. Every place we visit must require an advance reservation. To balance the day, at least one restaurant should not be too intense — catch our breath.",
        "constraints": [
            "[hop-1]  all: reservation_required = true",
            "[hop-1]  all: district == Westminster",
            "[hop-2]  at_least: 1 restaurant with recommended_pace ≤ moderate",
        ],
        "score": "Reservation gate + district lock — pool collapses to ~3 venues",
    },
    {
        "tag": "TYPE 6", "color": "WARN", "name": "Context-Window Tension",
        "model": "lon_easter_type6_britishmuseum_backup  ·  real window conflict",
        "query": "We're in London on Easter Monday and had planned to spend the afternoon at the British Museum, but we know bank holiday closures can ruin plans. Please build us a central London backup day with one art-focused museum visit and an easy dinner nearby, so we're not criss-crossing the city if the British Museum is shut.",
        "constraints": [
            "[hop-2]  all: traffic_tier == low  (backup-day calm)",
            "[hop-1]  at_least: 1 art museum",
            "[hop-2]  sum: travel ≤ 60 min  per_day",
            "[hop-2]  none: venue_id = British Museum  (closed Easter Monday)",
        ],
        "score": "Window-driven exclusion of a specific named venue — the persona's plan A vs the closure",
    },
]

# P-score engine real-life examples (slide 12)
PSCORE_EXAMPLES = [
    {
        "phrase": "\"explore authentic local restaurants\"",
        "primitive": "ratio: 0.6   scope=activity_type=meal   condition: local_cuisine=1",
        "explain": "An at_least would let one local meal satisfy the preference; the ratio forces majority across the meal plan.",
    },
    {
        "phrase": "\"try different cuisines\"",
        "primitive": "count_distinct: 3   field=cuisine   scope=category=restaurant",
        "explain": "Counts categories, not venues — the plan can evolve (Indian / Peruvian / Korean) while enforcing true variety.",
    },
    {
        "phrase": "\"balance both our styles\"",
        "primitive": "at_most: 1   scope=per_day   condition: recommended_pace=intense",
        "explain": "Expresses a cap — at_least cannot. Forces the agent to interleave high-energy and low-energy venues each day.",
    },
]

# Paper comparison (slide 14)
PAPER_COMPARISON = [
    {
        "paper": "DeepPlanning",
        "auth": "Zhang et al., Alibaba Qwen",
        "id": "arXiv 2601.18137  ·  Jan 2026",
        "their_example": "Xi'an → Beijing multi-leg trip; hotel + flight + activities + costs. ~200K real cached API responses.",
        "their_strength": "Real-tool fidelity. Cached responses → reproducible offline eval against genuine outputs.",
        "our_diff": "Their constraints are pre-injected. Ours are extracted from natural-language queries and validated for tension before publication. They test execution; we test inference.",
    },
    {
        "paper": "TravelBench (Cheng et al.)",
        "auth": "Renmin / NUS / Beihang / Alibaba AMAP",
        "id": "arXiv 2512.22673  ·  Dec 2025  ·  namesake",
        "their_example": "Multi-turn dialog: \"hotel with river view + romantic dinner, $200, near Huangpu River.\" Agent elicits dates, dietary restrictions across turns.",
        "their_strength": "Scale (1,100 tasks · 32 provinces) + human validation κ=0.83 + 100 unsolvable tasks for capability-boundary testing.",
        "our_diff": "Their setting: dialog elicitation under known ground truth. Ours: single-shot under hidden ground truth with planted wrong-info that must be cross-referenced to recover.",
    },
    {
        "paper": "VitaBench",
        "auth": "Meituan",
        "id": "arXiv 2509.26490  ·  Sep 2025",
        "their_example": "Cross-domain plan crossing delivery + retail + travel. 3-dim difficulty: reasoning × tool × interaction complexity.",
        "their_strength": "Theoretically formalized difficulty (entropy, graph density). 30% cross-scenario success — tasks discriminate well.",
        "our_diff": "Their constraints emerge from profile attributes. Ours have a richer primitive set (ratio, count_distinct, sum, at_most …) and an explicit faulty-source layer they lack.",
    },
]

# Task-gen sandbox steps (7) — replacement for old London Carnival narrative
# Real NYC GPT-5.4 type 4 trajectory (8 turns compressed to 7 steps)
TASK_GEN_STEPS = [
    {
        "label": "Read the handbook",
        "type": "help",
        "think": "For a Type 4 task I need a binding budget constraint tight enough that imprecise calculation fails. Read tags + pool_fields + window + validation rules before designing the persona.",
        "actions": [
            ("HELP", "HELP(\"-h tags\")",         "67 tags: free-entry · tourist-trap · popular-with-locals · waterfront..."),
            ("HELP", "HELP(\"-h pool_fields\")", "Filterable: category · tag · regulation · district · traffic_tier · price_tier"),
            ("HELP", "HELP(\"-h window\")",      "Independence Day · Nathan's Hot Dog Contest 2026-07-04 · dates 07-02 to 07-08"),
            ("HELP", "HELP(\"-h validate\")",    "Type 4 budget_per_day must be 1.1–1.3× cheapest viable plan to create real pressure."),
        ],
    },
    {
        "label": "Plan the persona — free-entry maximiser",
        "type": "think",
        "think": "Type 4 / 2-day / Independence Day weekend / strict-budget persona. Hop-2 constraint: \"avoid tourist traps\" matches the calm-July-4 tension. All visits free-entry pulls the audience to the right pool subset; budget cap forces meal allocation.",
        "actions": [],
    },
    {
        "label": "Scan the pool",
        "type": "query",
        "think": "Verify there's enough supply for both halves (free-entry sites + budget meals). If the cheapest viable plan is ~$20/day, the budget ceiling should land at $22–26.",
        "actions": [
            ("query_pool", "query_pool({tag: 'free-entry'})", "26 venues  (23 sites: Central Park, High Line, museums; 3 food)"),
            ("query_pool", "query_pool({category: 'restaurant'})", "20 restaurants  ·  budget: Xi'an Famous Foods $12, Halal Guys $10, Punjabi Grocery $8"),
            ("query_pool", "query_pool({category: 'cafe'})", "2 cafes"),
            ("query_pool", "query_pool({tag: 'popular-with-locals'})", "53 venues  (33 food + 20 sites)"),
        ],
    },
    {
        "label": "Test the trap constraint",
        "type": "query",
        "think": "Tourist-trap as a hop-2 universal exclusion needs to bite without making the task infeasible.",
        "actions": [
            ("query_pool", "query_pool({tag: 'tourist-trap'})", "1 venue: Times Square. Meaningful exclusion, plenty of free-entry alternatives remain."),
        ],
    },
    {
        "label": "Submit attempt 1 — rejected",
        "type": "submit_fail",
        "think": "Ready: $40/day budget · all visits free-entry · no tourist-trap · ≥3 distinct cuisines.",
        "actions": [
            ("SUBMIT", "SUBMIT(task v1 — $40/day)", "❌  2 validator errors"),
        ],
        "submitted": "budget_per_day: 40  ·  4 personal constraints",
        "errors": [
            "[pc_004] condition is empty with trivial scope='activity_type=meal' — add has_tag, not_tag, or a field comparison",
            "Solvability: Type 4 budget too loose: $40/day is 2.00× cheapest viable ($20); requires 1.1–1.3×. Tighten to $22–$26.",
        ],
    },
    {
        "label": "Fix both issues",
        "type": "think",
        "think": "Budget → $24/day = 1.20× cheapest ($20). Add a real condition to pc_004: meals must be at budget-tier price.",
        "actions": [],
    },
    {
        "label": "Submit attempt 2 — accepted",
        "type": "success",
        "think": "$24/day = 1.20× cheapest viable plan — right in the 1.1–1.3× window. Forces precise allocation: 24 cheap meal options + free visits. Tension is real.",
        "actions": [
            ("SUBMIT", "SUBMIT(task v2 — $24/day, fixed pc_004)", "✅  Task accepted and published"),
        ],
        "submitted": "budget_per_day: 24  ·  cheapest plan: $20  ·  ratio: 1.20× (target 1.1–1.3×)",
        "errors": [],
        "pcs": [
            ("pc_001", "hop-2", "Total cost ≤ $24 per_day", "sum · estimated_cost_local"),
            ("pc_002", "hop-1", "All visits at free-entry venues",     "all · has_tag=free-entry"),
            ("pc_003", "hop-2", "No tourist-trap venues",              "none · has_tag=tourist-trap"),
            ("pc_004", "hop-1", "All meals at budget price",           "all · price_tier ≤ budget"),
        ],
    },
]


# ─────────────────────────────────────────────────────────────────
# slides
# ─────────────────────────────────────────────────────────────────

def slide_title(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)

    # Corner deco (top right)
    add_text(s, Inches(10.5), Inches(0.5), Inches(2.5), Inches(0.9),
             [
                 "TravelBench",
                 "Research Presentation",
                 "May 2026",
             ],
             font=MONO, size=10, color=BORDER, align=PP_ALIGN.RIGHT)

    # Eyebrow
    add_text(s, Inches(0.75), Inches(2.6), Inches(8), Inches(0.3),
             "—  BENCHMARK  ·  LLM EVALUATION  ·  TRAVEL PLANNING",
             font=MONO, size=11, color=MUTED)

    # Hero title — two runs to colour "Bench"
    add_text(s, Inches(0.75), Inches(2.95), Inches(11), Inches(1.6),
             [[("Travel", {"font": SERIF, "size": 80, "color": TEXT}),
               ("Bench", {"font": SERIF, "size": 80, "color": ACCENT})]])

    # Subtitle
    add_text(s, Inches(0.75), Inches(4.6), Inches(8.5), Inches(1.2),
             "A pipeline for automatically generating and evaluating travel planning tasks "
             "— measuring whether LLM agents reason, or just comply.",
             font=SANS, size=16, color=MUTED)

    # Meta row
    add_text(s, Inches(0.75), Inches(6.5), Inches(12), Inches(0.4),
             [[("London · 50-venue pool · 23 tasks · 6 models", {"font": MONO, "size": 10, "color": MUTED}),
               ("    |    ", {"font": MONO, "size": 10, "color": BORDER}),
               ("Phase 5 — Benchmark Analysis", {"font": MONO, "size": 10, "color": MUTED}),
               ("    |    ", {"font": MONO, "size": 10, "color": BORDER}),
               ("Active", {"font": MONO, "size": 10, "color": ACCENT})]])


def slide_overview(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)

    add_label(s, Inches(0.6), Inches(0.5), "01  —  Overview")
    add_title(s, Inches(0.6), Inches(0.85), Inches(12), "What is TravelBench?", size=34)
    add_body(s, Inches(0.6), Inches(1.55), Inches(12), Inches(0.9),
             "A benchmark that gives LLM agents a natural-language trip request, four mock research "
             "tools, and asks them to produce a structured day-by-day itinerary. Scoring measures: did "
             "they find the right venues, build a feasible schedule, and satisfy the traveller's "
             "personal constraints?",
             color=MUTED, size=12)

    # Pipeline — 4 boxes with arrows
    steps = [
        ("City Dataset",    "71 venues · source docs\nevents · travel matrix", BORDER),
        ("Benchmark Tasks", "6 structural types\nnatural-language queries",     BORDER),
        ("LLM Agent",       "4 tools: search, blogs\nofficial site, travel time", ACCENT),
        ("Evaluator",       "C / F / P scores\ncascading gate structure",         BORDER),
    ]
    pipe_top = Inches(2.7)
    pipe_h = Inches(0.95)
    pipe_left = Inches(0.6)
    pipe_w_total = Inches(12.1)
    box_w = Inches(2.7)
    n = len(steps)
    arrow_w = (pipe_w_total - box_w * n) / (n - 1)
    for i, (name, sub, color) in enumerate(steps):
        left = pipe_left + (box_w + arrow_w) * i
        add_rect(s, left, pipe_top, box_w, pipe_h, fill=SURFACE, line_color=color)
        add_text(s, left, pipe_top + Inches(0.1), box_w, Inches(0.3),
                 name, font=SANS, size=12, color=TEXT, bold=True, align=PP_ALIGN.CENTER)
        add_text(s, left, pipe_top + Inches(0.4), box_w, Inches(0.55),
                 sub, font=MONO, size=8.5, color=MUTED, align=PP_ALIGN.CENTER)
        if i < n - 1:
            ax = left + box_w
            add_text(s, ax, pipe_top + Inches(0.3), arrow_w, Inches(0.4),
                     "→", font=SANS, size=18, color=BORDER, align=PP_ALIGN.CENTER)

    # Design highlight cards — 2 cols × 2 rows + 1 wide
    highlights = [
        ("Hidden ground truth",
         "The database is never exposed. Agents navigate four source types — directory listings, community guides, forum threads, and official pages — each with different reliability. Getting it right requires active cross-referencing, not reading one source."),
        ("Signal filtering under volume",
         "The source corpus spans hundreds of documents across 71 venues. A model that reads everything indiscriminately exhausts its tool budget. Good models develop a research strategy — knowing when to verify, when to trust, when to move on."),
        ("Realistic requirements, no mechanical solution",
         "Tasks built around real travel personas with layered, sometimes competing constraints. There's no keyword match that produces the answer — the model must reason about what combination of venues, timing, and trade-offs works."),
        ("Solution space, not a single answer",
         "Every task is verified to have at least one valid plan — typically many. Evaluation checks whether a submitted plan falls within the valid solution space. Different itineraries satisfying the same constraints all receive credit."),
    ]
    grid_top = Inches(3.6)
    grid_left = Inches(0.6)
    card_w = Inches(6.0)
    card_h = Inches(1.25)
    gap = Inches(0.08)
    for i, (t, b) in enumerate(highlights):
        col = i % 2
        row = i // 2
        l = grid_left + (card_w + gap) * col
        tp = grid_top + (card_h + gap) * row
        add_card(s, l, tp, card_w, card_h, t, b, accent_color=TEXT,
                 title_size=12, body_size=9)

    # wide bottom card
    wide_top = grid_top + (card_h + gap) * 2
    wide_w = card_w * 2 + gap
    add_rect(s, grid_left, wide_top, wide_w, Inches(0.85), fill=SURFACE,
             line_color=RGBColor(0x40, 0x5A, 0xB0))
    add_text(s, grid_left + Inches(0.15), wide_top + Inches(0.08), wide_w - Inches(0.3), Inches(0.3),
             "Explainable three-tier evaluation",
             font=SANS, size=12, color=TEXT, bold=True)
    add_text(s, grid_left + Inches(0.15), wide_top + Inches(0.35), wide_w - Inches(0.3), Inches(0.5),
             [[
                 ("Cascading scoring — ", {"size": 9, "color": MUTED}),
                 ("process integrity (C)", {"size": 9, "color": ACCENT}),
                 (", ", {"size": 9, "color": MUTED}),
                 ("schedule feasibility (F)", {"size": 9, "color": ACCENT3}),
                 (", ", {"size": 9, "color": MUTED}),
                 ("preference satisfaction (P)", {"size": 9, "color": WARN}),
                 (" — each evaluated programmatically. Every deduction traceable to a specific constraint, venue, or tool call.",
                  {"size": 9, "color": MUTED}),
             ]])


def slide_dataset(prs):
    """Slide 3 — city + venue resources overview (hybrid: process + catalogue)."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "02  —  Venue / City Resources")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12), "How a City Is Built", size=30)
    add_body(s, Inches(0.6), Inches(1.4), Inches(12), Inches(0.4),
             "Fully automated end-to-end: Anthropic plans the pool · per-venue DeepSeek agents produce yelp listings, source docs, official site pages · Overpass + Nominatim ground the geography.")

    # 4 gen boxes
    boxes = [
        ("Step 1–2", "Research + Windows",
         "Nominatim coords · Overpass districts · 4 seasonal windows (NYE, St Pat's, July 4, Thanksgiving)", BORDER),
        ("Step 3", "Plan Venues",
         "One Anthropic call → ~50 venue briefs with tier targets across traffic levels and categories", BORDER),
        ("Step 5 — core", "Per-Venue Agent",
         "DeepSeek agent per venue. 11 tools: CREATE_PAGE, FILL, COMMIT, ADD_WRONG_INFO, REGISTER_DOC_REFS, VERIFY. Parallel workers.", ACCENT),
        ("Steps 6–12", "Validate & Enrich",
         "Validate pool · Travel matrix · Ticket availability · Events · Window flags", BORDER),
    ]
    top = Inches(2.0)
    box_w = Inches(2.95)
    box_h = Inches(1.6)
    left0 = Inches(0.6)
    gap = Inches(0.15)
    for i, (step, name, detail, color) in enumerate(boxes):
        l = left0 + (box_w + gap) * i
        add_rect(s, l, top, box_w, box_h, fill=SURFACE, line_color=color)
        add_text(s, l + Inches(0.15), top + Inches(0.12), box_w - Inches(0.3), Inches(0.25),
                 step, font=MONO, size=9, color=MUTED)
        add_text(s, l + Inches(0.15), top + Inches(0.38), box_w - Inches(0.3), Inches(0.3),
                 name, font=SANS, size=12, color=TEXT, bold=True)
        add_text(s, l + Inches(0.15), top + Inches(0.7), box_w - Inches(0.3), Inches(0.85),
                 detail, font=SANS, size=9.5, color=MUTED)

    # Pool stats — NYC test_70 (real numbers)
    stats_top = Inches(3.85)
    add_text(s, Inches(0.6), stats_top, Inches(12), Inches(0.3),
             "Current NYC corpus  ·  test_70  ·  July 4 2026 window:",
             font=SANS, size=11, color=MUTED)
    chips = [
        ("70",  "venues"),
        ("20",  "restaurants"),
        ("16",  "museums"),
        ("13",  "bars"),
        ("9",   "attractions"),
        ("8",   "parks"),
        ("24",  "districts"),
        ("263", "source docs"),
        ("50",  "official-site pages"),
        ("29",  "wrong-info pairs"),
        ("22",  "wheelchair venues"),
        ("50",  "pet-friendly"),
        ("94",  "events"),
        ("2,415", "travel-matrix pairs"),
    ]
    chip_top = stats_top + Inches(0.35)
    chip_left = Inches(0.6)
    chip_h = Inches(0.32)
    x = chip_left
    y = chip_top
    for val, lab in chips:
        text = f" {val}  {lab} "
        approx_w = Inches(0.13 + 0.082 * len(text))
        if x + approx_w > Inches(12.7):
            x = chip_left
            y += chip_h + Inches(0.08)
        add_rect(s, x, y, approx_w, chip_h, fill=SURFACE2, line=BORDER)
        add_text(s, x, y + Inches(0.04), approx_w, chip_h,
                 [[(f" {val} ", {"font": MONO, "size": 10, "color": TEXT, "bold": True}),
                   (f" {lab}", {"font": MONO, "size": 10, "color": MUTED})]])
        x += approx_w + Inches(0.08)

    # Annotation
    ann_top = Inches(5.55)
    add_rect(s, Inches(0.6), ann_top, Inches(12.1), Inches(1.55),
             fill=RGBColor(0x2A, 0x20, 0x12),
             line_color=RGBColor(0x66, 0x4E, 0x1F))
    add_text(s, Inches(0.85), ann_top + Inches(0.15), Inches(11.8), Inches(1.25),
             [[
                 ("Every venue ships with: ",
                  {"size": 11, "color": RGBColor(0xC8, 0xB0, 0x8A)}),
                 ("a Yelp-style listing + 3–5 source docs (blogs + forum threads) + one official-site page",
                  {"size": 11, "color": TEXT}),
                 (".  29 venues carry ",
                  {"size": 11, "color": RGBColor(0xC8, 0xB0, 0x8A)}),
                 ("wrong-info pairs",
                  {"size": 11, "color": TEXT, "bold": True}),
                 (" — a factual error (stale hours, wrong price, missing rule) lives in one source while the correction lives in another. Each pair is registered atomically with both ",
                  {"size": 11, "color": RGBColor(0xC8, 0xB0, 0x8A)}),
                 ("incorrect_source", {"font": MONO, "size": 10, "color": ACCENT2, "bold": True}),
                 (" and ", {"size": 11, "color": RGBColor(0xC8, 0xB0, 0x8A)}),
                 ("truth_carrier", {"font": MONO, "size": 10, "color": ACCENT3, "bold": True}),
                 (" doc IDs (P6-T22). The agent's job is to find both.",
                  {"size": 11, "color": RGBColor(0xC8, 0xB0, 0x8A)}),
             ]])


def slide_task_types(prs):
    """Slide 13 — six structural types, each anchored on the best NYC task."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "09  —  Task Generation")
    add_title(s, Inches(0.6), Inches(0.7), Inches(12),
              "Six Structural Types  ·  Current Best NYC Examples", size=24)
    add_subtitle(s, Inches(0.6), Inches(1.2), Inches(12),
                 "Each card: real query  →  P-constraints  →  measured score")

    color_map = {"ACCENT": ACCENT, "ACCENT3": ACCENT3, "WARN": WARN}
    grid_top = Inches(1.75)
    card_w = Inches(4.0)
    card_h = Inches(2.7)
    gap = Inches(0.15)
    for i, t in enumerate(BEST_TASKS):
        col = i % 3
        row = i // 3
        l = Inches(0.6) + (card_w + gap) * col
        ct = grid_top + (card_h + gap) * row
        color = color_map[t["color"]]
        add_rect(s, l, ct, card_w, card_h, fill=SURFACE, line_color=color)
        # Header: TAG + name
        add_text(s, l + Inches(0.18), ct + Inches(0.1), card_w - Inches(0.36), Inches(0.28),
                 [[(t["tag"] + "  ", {"font": MONO, "size": 9, "color": color, "bold": True}),
                   (t["name"], {"font": SANS, "size": 11, "color": TEXT, "bold": True})]])
        # Model + score chip
        add_text(s, l + Inches(0.18), ct + Inches(0.35), card_w - Inches(0.36), Inches(0.22),
                 t["model"] + "   ·   " + t["score"],
                 font=MONO, size=8.5, color=MUTED)
        # Query (italic, smaller)
        add_rect(s, l + Inches(0.18), ct + Inches(0.65), card_w - Inches(0.36), Inches(1.0),
                 fill=SURFACE2, line=False)
        add_text(s, l + Inches(0.28), ct + Inches(0.7), card_w - Inches(0.56), Inches(0.9),
                 "“" + t["query"] + "”",
                 font=SANS, size=8.5, color=TEXT, italic=True)
        # Constraints — tight to fit Type 6's 5 lines within card_h
        cy = ct + Inches(1.72)
        for c in t["constraints"]:
            add_text(s, l + Inches(0.18), cy, card_w - Inches(0.36), Inches(0.18),
                     "·  " + c, font=MONO, size=8, color=color)
            cy += Inches(0.19)


# Re-use the NYC task-gen trajectory defined at top of file
SB_STEPS = TASK_GEN_STEPS

GEN_TOOL_COLOR = {"HELP": ACCENT, "THINK": PURPLE, "query_pool": ACCENT3, "SUBMIT": WARN}
GEN_TYPE_ICON  = {"help": "📖", "think": "💭", "query": "🔍",
                  "submit_fail": "📤", "success": "✅"}


def slide_sandbox(prs):
    """Slides 15-21 — task-generation sandbox, NYC Type 4 trajectory."""
    total = len(SB_STEPS)
    for idx, step in enumerate(SB_STEPS, start=1):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        set_slide_bg(s, BG)
        add_label(s, Inches(0.6), Inches(0.4), "11  —  Task-Generation Sandbox")
        add_title(s, Inches(0.6), Inches(0.7), Inches(12),
                  "NYC Type 4  ·  Free-Entry Maximiser  ·  GPT-5.4", size=24)
        add_subtitle(s, Inches(0.6), Inches(1.25), Inches(12),
                     f"Step {idx} / {total}  —  {step['label']}")

        _render_gen_sandbox_step(s, idx, total, step)


def _render_gen_sandbox_step(s, idx, total, step):
    # ── Left sidebar: tool legend + task meta (always visible)
    left = Inches(0.6)
    top = Inches(1.85)
    col_w = Inches(2.6)

    add_text(s, left, top, col_w, Inches(0.3),
             "GENERATION TOOLS", font=MONO, size=9, color=MUTED)
    tools = [
        ("HELP",       ACCENT,  "Query the pool handbook — tags, regulations, validation rules"),
        ("THINK",      PURPLE,  "Internal reasoning — persona, constraint strategy, solvability"),
        ("query_pool", ACCENT3, "Filter the venue pool by category, tag, regulation, tier"),
        ("SUBMIT",     WARN,    "Publish task — validator checks solvability, upper bars, schema"),
    ]
    cy = top + Inches(0.35)
    for name, color, desc in tools:
        add_rect(s, left, cy, col_w, Inches(0.8), fill=SURFACE, line=BORDER)
        add_text(s, left + Inches(0.15), cy + Inches(0.08), col_w - Inches(0.3), Inches(0.25),
                 name, font=MONO, size=10, color=color, bold=True)
        add_text(s, left + Inches(0.15), cy + Inches(0.32), col_w - Inches(0.3), Inches(0.5),
                 desc, font=SANS, size=8.5, color=MUTED)
        cy += Inches(0.9)

    # Task meta — NYC GPT-5.4 type4 (matches TASK_GEN_STEPS)
    meta_top = cy + Inches(0.15)
    add_rect(s, left, meta_top, col_w, Inches(1.15), fill=SURFACE2, line=False)
    add_text(s, left + Inches(0.15), meta_top + Inches(0.08), col_w - Inches(0.3), Inches(1.0),
             [[("Task: ", {"font": MONO, "size": 9, "color": MUTED}),
               ("nyc · type4 · $24/day", {"font": MONO, "size": 9, "color": TEXT})],
              [("Type: ", {"font": MONO, "size": 9, "color": MUTED}),
               ("type4_precision_allocation", {"font": MONO, "size": 9, "color": WARN})],
              [("Window: ", {"font": MONO, "size": 9, "color": MUTED}),
               ("Independence Day 2026", {"font": MONO, "size": 9, "color": TEXT})],
              [("Model: ", {"font": MONO, "size": 9, "color": MUTED}),
               ("gpt-5.4", {"font": MONO, "size": 9, "color": TEXT})],
              [("Turns: ", {"font": MONO, "size": 9, "color": MUTED}),
               ("8 (max 30)", {"font": MONO, "size": 9, "color": TEXT})]])

    # ── Right panel
    right_l = Inches(3.4)
    right_w = Inches(9.3)
    right_t = Inches(1.85)

    # Progress + step header
    progress_pct = idx / total
    add_rect(s, right_l, right_t, right_w, Inches(0.08), fill=SURFACE2, line=False)
    add_rect(s, right_l, right_t, Emu(int(right_w * progress_pct)), Inches(0.08),
             fill=ACCENT, line=False)
    add_text(s, right_l, right_t + Inches(0.15), right_w, Inches(0.3),
             f"Step {idx} / {total}", font=MONO, size=9, color=MUTED)

    title_color = ACCENT3 if step["type"] == "success" else ACCENT2 if step["type"] == "submit_fail" else ACCENT
    icon = GEN_TYPE_ICON.get(step["type"], "•")
    add_text(s, right_l, right_t + Inches(0.45), right_w, Inches(0.35),
             f"{icon}  {step['label']}",
             font=MONO, size=13, color=title_color, bold=True)

    cy = right_t + Inches(0.95)

    # Think block
    if step.get("think"):
        think_h = Inches(0.95) if len(step["think"]) > 200 else Inches(0.75)
        add_rect(s, right_l, cy, right_w, think_h,
                 fill=RGBColor(0x22, 0x1A, 0x2E), line_color=PURPLE)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.08), right_w - Inches(0.3), Inches(0.25),
                 "↯ THINKING", font=MONO, size=9, color=PURPLE)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.3), right_w - Inches(0.3), think_h - Inches(0.35),
                 step["think"], font=SANS, size=10, color=MUTED)
        cy += think_h + Inches(0.12)

    # Actions
    for tool, call, result in step.get("actions", []):
        color = GEN_TOOL_COLOR.get(tool, ACCENT)
        is_ok = result.startswith("✅")
        is_err = result.startswith("❌")
        if is_ok:
            border_c = ACCENT3
            res_bg = RGBColor(0x12, 0x2A, 0x22)
            res_c = ACCENT3
        elif is_err:
            border_c = ACCENT2
            res_bg = RGBColor(0x28, 0x14, 0x1C)
            res_c = ACCENT2
        else:
            border_c = BORDER
            res_bg = SURFACE2
            res_c = MUTED

        action_h = Inches(0.85)
        add_rect(s, right_l, cy, right_w, action_h, fill=SURFACE, line_color=border_c)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.08), right_w - Inches(0.3), Inches(0.25),
                 [[(tool + "   ", {"font": MONO, "size": 10, "color": color, "bold": True}),
                   (call,         {"font": MONO, "size": 10, "color": color})]])
        add_rect(s, right_l + Inches(0.15), cy + Inches(0.4), right_w - Inches(0.3), Inches(0.38),
                 fill=res_bg, line=False)
        add_text(s, right_l + Inches(0.25), cy + Inches(0.45), right_w - Inches(0.5), Inches(0.3),
                 result, font=MONO, size=9.5, color=res_c)
        cy += action_h + Inches(0.1)

    # Submitted block
    if step.get("submitted"):
        add_rect(s, right_l, cy, right_w, Inches(0.5), fill=SURFACE,
                 line_color=RGBColor(0x60, 0x4E, 0x1F))
        add_text(s, right_l + Inches(0.15), cy + Inches(0.06), right_w - Inches(0.3), Inches(0.2),
                 "WHAT WAS SUBMITTED", font=MONO, size=8, color=WARN, bold=True)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.25), right_w - Inches(0.3), Inches(0.22),
                 step["submitted"], font=MONO, size=9.5, color=RGBColor(0xC8, 0xA0, 0x60))
        cy += Inches(0.6)

    # Validator errors
    if step.get("errors"):
        err_h = Inches(0.4 + 0.45 * len(step["errors"]))
        add_rect(s, right_l, cy, right_w, err_h, fill=SURFACE, line_color=ACCENT2)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.06), right_w - Inches(0.3), Inches(0.2),
                 "VALIDATOR ERRORS", font=MONO, size=8, color=ACCENT2, bold=True)
        ey = cy + Inches(0.28)
        for e in step["errors"]:
            add_text(s, right_l + Inches(0.15), ey, right_w - Inches(0.3), Inches(0.5),
                     "✗  " + e, font=SANS, size=9.5, color=MUTED)
            ey += Inches(0.45)
        cy += err_h + Inches(0.1)

    # PCs (success step)
    if step.get("pcs"):
        add_text(s, right_l, cy, right_w, Inches(0.25),
                 "PUBLISHED P-CONSTRAINTS", font=MONO, size=8, color=MUTED)
        cy += Inches(0.28)
        for pid, hop, desc, agg in step["pcs"]:
            add_rect(s, right_l, cy, right_w, Inches(0.45), fill=SURFACE, line=BORDER)
            add_text(s, right_l + Inches(0.15), cy + Inches(0.06), right_w - Inches(0.3), Inches(0.22),
                     [[(f"{pid}   ", {"font": MONO, "size": 9, "color": ACCENT, "bold": True}),
                       (f"[{hop}]   ", {"font": MONO, "size": 9, "color": WARN}),
                       (desc, {"font": SANS, "size": 10, "color": TEXT})]])
            add_text(s, right_l + Inches(0.15), cy + Inches(0.26), right_w - Inches(0.3), Inches(0.2),
                     agg, font=MONO, size=8.5, color=MUTED)
            cy += Inches(0.5)


def slide_pscore(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "06  —  P-Score Design")
    add_title(s, Inches(0.6), Inches(0.7), Inches(12), "Task Structure & Constraint Architecture", size=24)
    add_subtitle(s, Inches(0.6), Inches(1.2), Inches(12), "P-Score Constraint Validation")
    add_body(s, Inches(0.6), Inches(1.7), Inches(12.1), Inches(0.6),
             "Before a task is published, every P-constraint pair is tested for genuine tension — "
             "satisfying both simultaneously must require real tradeoff. Validation uses different "
             "geometry depending on whether constraints operate at the venue pool level or the plan "
             "structure level.")

    # Two columns: A×A and B×B
    col_w = Inches(6.1)
    col_h = Inches(4.6)
    top = Inches(2.6)

    # ── A × A ─────────────────────────────
    a_left = Inches(0.6)
    add_rect(s, a_left, top, col_w, col_h, fill=SURFACE,
             line_color=RGBColor(0x40, 0x5A, 0xB0))
    add_text(s, a_left + Inches(0.2), top + Inches(0.12), col_w - Inches(0.4), Inches(0.3),
             [[("A × A   ", {"font": MONO, "size": 14, "color": ACCENT, "bold": True}),
               ("Pool-filter constraints — each produces a venue set",
                {"font": SANS, "size": 10, "color": MUTED})]])
    add_text(s, a_left + Inches(0.2), top + Inches(0.5), col_w - Inches(0.4), Inches(0.55),
             "Two constraints that filter the venue pool. Tension requires the sets to be mostly "
             "disjoint — agent must draw from both sides and cannot satisfy both with the same venues.",
             font=SANS, size=10, color=MUTED)

    # Pseudo Venn diagrams using ovals
    venn_top = top + Inches(1.15)
    # Left venn — small overlap (tension)
    cv_l = a_left + Inches(0.3)
    cv_top = venn_top
    add_text(s, cv_l, cv_top, Inches(2.4), Inches(0.25),
             "small overlap → ✅ tension", font=MONO, size=9, color=ACCENT3, align=PP_ALIGN.CENTER)
    e1 = s.shapes.add_shape(MSO_SHAPE.OVAL, cv_l + Inches(0.1), cv_top + Inches(0.35),
                            Inches(1.4), Inches(1.4))
    e1.fill.solid(); e1.fill.fore_color.rgb = RGBColor(0x16, 0x21, 0x3C)
    e1.line.color.rgb = ACCENT; e1.line.width = Pt(1)
    e2 = s.shapes.add_shape(MSO_SHAPE.OVAL, cv_l + Inches(0.85), cv_top + Inches(0.35),
                            Inches(1.4), Inches(1.4))
    e2.fill.solid(); e2.fill.fore_color.rgb = RGBColor(0x16, 0x2A, 0x22)
    e2.line.color.rgb = ACCENT3; e2.line.width = Pt(1)
    add_text(s, cv_l, cv_top + Inches(1.85), Inches(2.4), Inches(0.25),
             "overlap=2 / 7 = 29% → tension ✅",
             font=MONO, size=8, color=MUTED, align=PP_ALIGN.CENTER)

    # Right venn — large overlap (no tension)
    cv_l2 = a_left + Inches(3.3)
    add_text(s, cv_l2, cv_top, Inches(2.4), Inches(0.25),
             "large overlap → ❌ no tension", font=MONO, size=9, color=ACCENT2, align=PP_ALIGN.CENTER)
    e3 = s.shapes.add_shape(MSO_SHAPE.OVAL, cv_l2 + Inches(0.2), cv_top + Inches(0.35),
                            Inches(1.4), Inches(1.4))
    e3.fill.solid(); e3.fill.fore_color.rgb = RGBColor(0x2A, 0x21, 0x12)
    e3.line.color.rgb = WARN; e3.line.width = Pt(1)
    e4 = s.shapes.add_shape(MSO_SHAPE.OVAL, cv_l2 + Inches(0.7), cv_top + Inches(0.35),
                            Inches(1.4), Inches(1.4))
    e4.fill.solid(); e4.fill.fore_color.rgb = RGBColor(0x25, 0x16, 0x33)
    e4.line.color.rgb = PURPLE; e4.line.width = Pt(1)
    add_text(s, cv_l2, cv_top + Inches(1.85), Inches(2.4), Inches(0.25),
             "overlap=15 / 20 = 75% → no tension ❌",
             font=MONO, size=8, color=MUTED, align=PP_ALIGN.CENTER)

    # Formula
    f_top = top + Inches(3.4)
    add_rect(s, a_left + Inches(0.2), f_top, col_w - Inches(0.4), Inches(0.6),
             fill=SURFACE2, line=False)
    add_text(s, a_left + Inches(0.3), f_top + Inches(0.08), col_w - Inches(0.6), Inches(0.5),
             [[("tension ", {"font": MONO, "size": 10, "color": ACCENT, "bold": True}),
               ("iff  overlap ≤ ", {"font": MONO, "size": 10, "color": MUTED}),
               ("3", {"font": MONO, "size": 10, "color": TEXT}),
               ("  OR  overlap / min(|A|, |B|) ≤ ", {"font": MONO, "size": 10, "color": MUTED}),
               ("0.25", {"font": MONO, "size": 10, "color": TEXT})]])

    # Old metric failure note
    note_top = f_top + Inches(0.75)
    add_rect(s, a_left + Inches(0.2), note_top, col_w - Inches(0.4), Inches(0.65),
             fill=RGBColor(0x28, 0x14, 0x1C),
             line_color=RGBColor(0x60, 0x2C, 0x40))
    add_text(s, a_left + Inches(0.3), note_top + Inches(0.08), col_w - Inches(0.6), Inches(0.55),
             [[("Old metric failure: ", {"size": 9.5, "color": ACCENT2, "bold": True}),
               ("divided by pool size (68) — upscale(7) vs hidden-gem(22) overlap=2 scored 2/68=3% < 12% threshold. Rejected despite being genuinely disjoint.",
                {"size": 9.5, "color": MUTED})]])

    # ── B × B ─────────────────────────────
    b_left = Inches(7.0)
    add_rect(s, b_left, top, col_w, col_h, fill=SURFACE,
             line_color=RGBColor(0x66, 0x4E, 0x1F))
    add_text(s, b_left + Inches(0.2), top + Inches(0.12), col_w - Inches(0.4), Inches(0.3),
             [[("B × B   ", {"font": MONO, "size": 14, "color": WARN, "bold": True}),
               ("Plan-structure constraints — narrow the valid schedule space",
                {"font": SANS, "size": 10, "color": MUTED})]])
    add_text(s, b_left + Inches(0.2), top + Inches(0.5), col_w - Inches(0.4), Inches(0.65),
             "Counting constraints don't filter venues — they narrow the space of valid plans. Each "
             "reduces the baseline plan count by a factor. Tension requires combined narrowing to "
             "collapse plan count below threshold.",
             font=SANS, size=10, color=MUTED)

    # Plan count bars
    bar_top = top + Inches(1.3)
    bars = [
        ("baseline  ~1.2M",       1.0,  MUTED,   "C(34,4) × C(34,4) ≈ 1.2M"),
        ("× constraint A  r=0.40", 0.4,  ACCENT,  "~480k"),
        ("× constraint B  r=0.20", 0.20, WARN,    "~96k"),
        ("stacked product  r=0.08",0.08, ACCENT3, "~1.5k < 2000/day → tension ✅"),
    ]
    bar_w = col_w - Inches(2.6)
    by = bar_top
    for lab, frac, color, val in bars:
        add_text(s, b_left + Inches(0.2), by, Inches(1.9), Inches(0.3),
                 lab, font=MONO, size=8.5, color=color, align=PP_ALIGN.RIGHT)
        # bar bg
        add_rect(s, b_left + Inches(2.2), by + Inches(0.05), bar_w, Inches(0.22),
                 fill=SURFACE2, line=False)
        # fill
        fw = max(Inches(0.05), Emu(int(bar_w * frac)))
        add_rect(s, b_left + Inches(2.2), by + Inches(0.05), fw, Inches(0.22),
                 fill=color, line=False)
        # value annotation
        add_text(s, b_left + Inches(2.2), by + Inches(0.3), bar_w, Inches(0.2),
                 val, font=MONO, size=8, color=color)
        by += Inches(0.55)

    # Formula
    f2_top = top + Inches(3.45)
    add_rect(s, b_left + Inches(0.2), f2_top, col_w - Inches(0.4), Inches(0.55),
             fill=SURFACE2, line=False)
    add_text(s, b_left + Inches(0.3), f2_top + Inches(0.08), col_w - Inches(0.6), Inches(0.5),
             [[("tension ", {"font": MONO, "size": 10, "color": WARN, "bold": True}),
               ("iff  r_A × r_B ≤ ", {"font": MONO, "size": 10, "color": MUTED}),
               ("0.10", {"font": MONO, "size": 10, "color": TEXT}),
               (" (type 3)  AND  valid_count ≤ ", {"font": MONO, "size": 10, "color": MUTED}),
               ("2000 × days", {"font": MONO, "size": 10, "color": TEXT})]])

    note2_top = f2_top + Inches(0.7)
    add_rect(s, b_left + Inches(0.2), note2_top, col_w - Inches(0.4), Inches(0.65),
             fill=RGBColor(0x28, 0x14, 0x1C),
             line_color=RGBColor(0x60, 0x2C, 0x40))
    add_text(s, b_left + Inches(0.3), note2_top + Inches(0.08), col_w - Inches(0.6), Inches(0.55),
             [[("Hardcoded pairs fail: ", {"size": 9.5, "color": ACCENT2, "bold": True}),
               ("whether time_ceiling + category_min creates tension depends on the specific K and pool — a static whitelist can't capture this.",
                {"size": 9.5, "color": MUTED})]])


def slide_scoring(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "07  —  Model Evaluation")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12), "Model Evaluation", size=30)
    add_subtitle(s, Inches(0.6), Inches(1.35), Inches(12), "Three-Score Evaluation System")
    add_body(s, Inches(0.6), Inches(1.85), Inches(12.1), Inches(0.6),
             "Each tier gates the next. F and P scores only apply if C passes. The three tiers together "
             "characterise plan quality: process, feasibility, and preference satisfaction.")

    tiers = [
        ("C", ACCENT, "Process Score",
         "AST validation of every tool call + plan-format check. Per-call / per-field issues are −0.04 each. Two binary hard gates only.",
         [("HARD GATE 1", "Zero tool calls"),
          ("HARD GATE 2", "No <final_plan> tag (or JSON parse failed)"),
          ("Deductions  −0.04 each",
           "unknown tool · missing params · wrong type · venue_id not in prior results · plan fields missing / malformed times · …")],
         "Only the 2 gates skip F / P.  Otherwise: max(0, 1 − Σ deductions)."),
        ("F", ACCENT3, "Feasibility Score",
         "Deduction-based.  No hard fails.  Clamped at 0.0 — does NOT propagate to other tiers.",
         [("Opening hours  −0.15",   "Visit outside day-specific opening window"),
          ("Activity overlap  −0.20","Two activities have overlapping windows"),
          ("Travel time  −0.15",     "Gap too short for matrix walk / transit"),
          ("Truth-carrier  −0.05",   "Wrong-info venue scheduled w/o retrieving truth carrier"),
          ("Buffer & duration",      "Tiered: <5 min buffer −0.10  ·  over-spaced −0.02  ·  under-scheduled visit −0.05")],
         "max(0, 1 − Σ deductions).  ⚠ flag if any single deduction ≥ 0.10."),
        ("P", WARN, "Preference Score",
         "Per-constraint check via the engine on slide 11. Each P-constraint scores 0 or 1; final = mean. LLM judge available for semantic constraints.",
         [],
         "Proportional [0.0, 1.0].  No gates, no propagation."),
    ]
    top = Inches(2.55)
    tile_w = Inches(4.05)
    tile_h = Inches(4.85)
    gap = Inches(0.2)
    for i, (letter, color, name, desc, checks, gate) in enumerate(tiers):
        l = Inches(0.6) + (tile_w + gap) * i
        add_rect(s, l, top, tile_w, tile_h, fill=SURFACE, line_color=color)
        # Big letter
        add_text(s, l + Inches(0.2), top + Inches(0.12), Inches(2), Inches(0.75),
                 letter, font=SERIF, size=44, color=color)
        # Name
        add_text(s, l + Inches(0.2), top + Inches(0.9), tile_w - Inches(0.4), Inches(0.3),
                 name.upper(), font=SANS, size=11, color=TEXT, bold=True)
        # Description — keep short, tile gets crowded with 5 checks
        add_text(s, l + Inches(0.2), top + Inches(1.22), tile_w - Inches(0.4), Inches(0.75),
                 desc, font=SANS, size=10, color=MUTED)
        # Checks — fit up to 5 within tile_h
        if checks:
            cy = top + Inches(2.05)
            add_text(s, l + Inches(0.2), cy, tile_w - Inches(0.4), Inches(0.22),
                     "WHAT WE CHECK", font=MONO, size=8.5, color=MUTED)
            cy += Inches(0.25)
            for ttl, body in checks:
                add_text(s, l + Inches(0.2), cy, tile_w - Inches(0.4), Inches(0.2),
                         "·  " + ttl,
                         font=MONO, size=9.5, color=color, bold=True)
                add_text(s, l + Inches(0.2), cy + Inches(0.19), tile_w - Inches(0.4), Inches(0.27),
                         body, font=SANS, size=8.5, color=MUTED)
                cy += Inches(0.41)
        # Gate badge — anchored to the bottom of the tile, slightly taller for 2-line gate text
        g_top = top + tile_h - Inches(0.5)
        add_rect(s, l + Inches(0.2), g_top, tile_w - Inches(0.4), Inches(0.4),
                 fill=SURFACE2, line=False)
        add_text(s, l + Inches(0.25), g_top + Inches(0.07), tile_w - Inches(0.5), Inches(0.32),
                 gate, font=MONO, size=8.5, color=color, bold=True, align=PP_ALIGN.CENTER)


EV_GPT = [
    {"label": "Task received", "type": "task",
     "actions": [("TASK", "london_easter_food_photographer_2026",
                  "4 days · Easter 2026 (Apr 4–7) · 3 P-constraints · 60-call budget · Food photographer + elderly wheelchair-using mother")]},
    {"label": "Discovery — 5 constraint-targeted searches", "type": "search",
     "actions": [
        ("search_yelp", "search_yelp(\"wheelchair accessible photogenic cafe\")",
         "6 results — Workshop Coffee Clerkenwell, Monmouth Coffee Borough, Café OTO..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible upscale restaurant instagrammable\")",
         "6 results — Gymkhana, Dishoom King's Cross, Sketch – The Gallery..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible market food hall photogenic\")",
         "6 results — Borough Market, Flat Iron Covent Garden..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible museum\")",
         "6 results — British Museum, Natural History Museum, Tate Modern, Courtauld Gallery..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible park scenic\")",
         "4 results — Hyde Park, Kew Gardens, Regent's Park..."),
     ]},
    {"label": "Blog cross-check — 5 venue-targeted queries", "type": "search",
     "actions": [
        ("search_blogs", "search_blogs(venue=\"Borough Market\")",
         "5 posts — wheelchair access via Stoney St entrance · 'best food photography spot' · busy Easter weekend"),
        ("search_blogs", "search_blogs(venue=\"Sketch – The Gallery\")",
         "5 posts — 'absolutely instagrammable' pink room · reservation required · no hours warnings"),
        ("search_blogs", "search_blogs(venue=\"Gymkhana\")",
         "5 posts — Michelin star · wheelchair accessible · lunch/dinner only · 'exceptional upscale Indian'"),
        ("search_blogs", "search_blogs(venue=\"The British Museum\")",
         "5 posts — free entry · Easter week very busy · wheelchair full access · opens 10:00"),
        ("search_blogs", "search_blogs(venue=\"Kew Gardens\")",
         "5 posts — good for photography · Easter extra busy · wheelchair accessible · large site"),
     ]},
    {"label": "Official site — 5 date-specific calls", "type": "official",
     "actions": [
        ("get_official_site", "get_official_site(borough_market, 2026-04-04)",
         "Borough Market: 10:00–17:00 · wheelchair ✓ · no ticket · Easter Saturday market"),
        ("get_official_site", "get_official_site(sketch_gallery, 2026-04-05)",
         "⚠ SOLD OUT on 2026-04-05 · next availability 2026-04-07 · £65pp"),
        ("get_official_site", "get_official_site(british_museum, 2026-04-04)",
         "10:00–17:30 · free · wheelchair ✓ · special Easter exhibition"),
        ("get_official_site", "get_official_site(natural_history_museum, 2026-04-04)",
         "10:00–17:50 · free · wheelchair ✓"),
        ("get_official_site", "get_official_site(gymkhana, 2026-04-06)",
         "12:00–14:30, 18:00–22:30 · booking required · wheelchair ✓ · upscale"),
     ]},
    {"label": "Travel time — 4 consecutive pairs", "type": "travel",
     "actions": [
        ("get_travel_time", "get_travel_time(Borough Market → British Museum)",
         "34.2 min walk · 9.1 min transit"),
        ("get_travel_time", "get_travel_time(Sketch → Courtauld Gallery)",
         "12.3 min walk"),
        ("get_travel_time", "get_travel_time(Natural History Museum → V&A)",
         "8.4 min walk"),
        ("get_travel_time", "get_travel_time(Gymkhana → Dishoom)",
         "11.2 min walk"),
     ]},
    {"label": "Evaluation result", "type": "result",
     "actions": [("PLAN", "4-day plan submitted · 36 tool calls · 132k tokens",
                  "Plan parsed ✅ · 4 days · 16 activities")],
     "scores": {"C": "100%", "F": "85%", "P": "100%"},
     "miss": "F −15%: Sketch – The Gallery scheduled Apr 5 (sold out). Official site returned sold-out signal but agent proceeded with booking.",
     "pcs": [("pc_001", 1.0, "≥60% venues wheelchair accessible"),
             ("pc_002", 1.0, "≥2 upscale meal venues"),
             ("pc_003", 1.0, "≥1 instagrammable venue")]},
]

EV_GLM = [
    {"label": "Task received", "type": "task",
     "actions": [("TASK", "london_easter_food_photographer_2026",
                  "4 days · Easter 2026 (Apr 4–7) · 3 P-constraints · 60-call budget · Food photographer + elderly wheelchair-using mother")]},
    {"label": "Discovery — 4 searches, all wheelchair-only", "type": "search",
     "actions": [
        ("search_yelp", "search_yelp(\"wheelchair accessible restaurant photogenic\")",
         "6 results — Natural History Museum Cafe, Dishoom King's Cross, Flat Iron..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible upscale dinner\")",
         "6 results — Gymkhana, Barrafina Adelaide Street, The Wolseley..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible cafe instagrammable\")",
         "6 results — Workshop Coffee Clerkenwell, Monmouth Coffee, Proud Archivist..."),
        ("search_yelp", "search_yelp(\"wheelchair accessible market food\")",
         "6 results — Borough Market, Maltby Street Market, Flat Iron Covent Garden..."),
     ]},
    None,  # no blog cross-check phase
    {"label": "Official site — 4 venues only, no blog checks", "type": "official",
     "actions": [
        ("get_official_site", "get_official_site(natural_history_museum, 2026-04-04)",
         "10:00–17:50 · free · wheelchair ✓"),
        ("get_official_site", "get_official_site(borough_market, 2026-04-04)",
         "10:00–17:00 · wheelchair ✓"),
        ("get_official_site", "get_official_site(workshop_coffee, 2026-04-04)",
         "09:00–17:00 · wheelchair ✓"),
        ("get_official_site", "get_official_site(dishoom, 2026-04-05)",
         "08:00–23:00 · wheelchair ✓"),
        ("search_blogs", "search_blogs(\"wheelchair accessible photogenic spots London\")",
         "5 generic posts — no venue-specific hours or upscale/instagrammable info"),
     ]},
    {"label": "Travel time — 1 call only", "type": "travel",
     "actions": [
        ("get_travel_time", "get_travel_time(Borough Market → Natural History Museum)",
         "41.2 min walk"),
     ]},
    {"label": "Evaluation result", "type": "result_fail",
     "actions": [("PLAN", "4-day plan submitted · 10 tool calls · 38k tokens",
                  "Plan parsed ✅ · 4 days · activities present")],
     "scores": {"C": "96%", "F": "69%", "P": "33%"},
     "miss": "F: Workshop Coffee scheduled 14:00–18:00 but closes 17:00 — never verified hours against plan times. P: 2 of 3 constraints failed — no instagrammable verification, no upscale dinner check.",
     "pcs": [("pc_001", 1.0, "≥60% venues wheelchair accessible"),
             ("pc_002", 0.0, "≥2 upscale meal venues"),
             ("pc_003", 0.0, "≥1 instagrammable venue")]},
]

EV_PHASES = [
    "Task received",
    "Discovery — constraint-targeted search",
    "Blog cross-check",
    "Official site verification",
    "Travel time check",
    "Evaluation result",
]

EV_TOOL_COLOR = {
    "TASK": MUTED, "search_yelp": ACCENT, "search_blogs": PURPLE,
    "get_official_site": ACCENT3, "get_travel_time": WARN,
    "find_nearby_venues": RGBColor(0x6C, 0xFF, 0x96),
    "THINK": PURPLE, "PLAN": MUTED,
}


# ── NEW: NYC Type 4 single-model walkthrough (Gemini-3.1-Pro, strong run)
EV_NYC_PHASES = [
    "Initial Discovery — broad searches",
    "Cross-Check — blog vetting for tourist traps",
    "Refined Meal Search — lock budget anchors",
    "Travel Time Verification — confirm routing",
    "Assemble Plan — day-by-day budget allocation",
    "Submit & Evaluation",
]

EV_NYC_GEMINI = [
    # Phase 1
    {"summary": "Rapid Yelp scans across budget restaurants, free parks, free attractions to populate the candidate pool.",
     "actions": [
        ("search_yelp", "search_yelp(query=\"cheap eats\", top_k=10)",
         "→ budget restaurant clusters discovered"),
        ("search_yelp", "search_yelp(category=\"park\", query=\"free\")",
         "→ Central Park · The High Line · Brooklyn Bridge Park"),
        ("search_yelp", "search_yelp(category=\"attraction\", query=\"free\")",
         "→ ~25-venue shortlist built across categories"),
     ]},
    # Phase 2
    {"summary": "Iterative blog searches to validate Yelp picks and catch tourist-trap red flags BEFORE committing.",
     "actions": [
        ("search_blogs_and_forums", "search_blogs(\"best free things to do avoid tourist traps\")",
         "→ vetting framework established"),
        ("search_blogs_and_forums", "search_blogs(\"best dollar pizza cheap dumplings Chinatown\")",
         "→ neighborhood deep-dive, locals' picks identified"),
        ("search_blogs_and_forums", "search_blogs(\"Punjabi Grocery Deli price\")",
         "→ specific venue price confirmation"),
        ("search_blogs_and_forums", "search_blogs(\"cheap breakfast lunch under 10 dollars\")",
         "→ budget range validation"),
     ]},
    # Phase 3
    {"summary": "Targeted searches to lock specific meal anchors at $6-10 per item, leaving budget headroom.",
     "actions": [
        ("search_yelp", "search_yelp(query=\"bagel\", category=\"restaurant\")",
         "→ Ess-a-Bagel confirmed at $8"),
        ("search_yelp", "search_yelp(query=\"dollar pizza\", category=\"restaurant\")",
         "→ Ops shortlisted at $6"),
        ("search_yelp", "search_yelp(query=\"chinatown\", category=\"restaurant\")",
         "→ Halal Guys at $10 added"),
     ]},
    # Phase 4
    {"summary": "Called get_travel_time on every consecutive venue pair BEFORE finalising the schedule.",
     "actions": [
        ("get_travel_time", "Central Park → Ess-a-Bagel",
         "15 min transit · activity gap fits"),
        ("get_travel_time", "Ess-a-Bagel → Rockefeller",
         "15 min walk · fits"),
        ("get_travel_time", "Rockefeller → The High Line",
         "30 min transit · within buffer"),
        ("get_travel_time", "Brooklyn Bridge → Ops pizza",
         "complex routing — checked, fits"),
     ]},
    # Phase 5
    {"summary": "Assembled the 2-day itinerary with explicit time blocks and per-day cost totals.",
     "actions": [
        ("PLAN", "Day 1  —  Central Park · Ess-a-Bagel · Rockefeller · High Line · Halal Guys",
         "free + $8 + free + free + $10 + $6 transit = $24"),
        ("PLAN", "Day 2  —  Brooklyn Bridge Park · Brooklyn Bridge · Ops · The Ramble · Ess-a-Bagel",
         "free + free + $6 + free + $8 + $6 transit = $20"),
     ]},
    # Phase 6 — final submit + scores
    {"summary": "Submitted as structured JSON; evaluator scored against all 4 P-constraints.",
     "actions": [
        ("PLAN", "submit_plan(...)", "Plan parsed ✓ · 2 days · 10 activities · $44 total"),
     ],
     "scores": {"C": "84%", "F": "63%", "P": "75%"},
     "miss": "−16% on C (4 venue IDs were referenced via abbreviated names not full IDs).  −37% on F (5 wrong-info venues scheduled without retrieving their truth-carrier docs)."
    },
]


def slide_solving_task_target(prs):
    """NEW — shown BEFORE the 6-phase strong-model walkthrough.
    Audience sees what the agent is solving before watching the process."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "12  —  Solving Sandbox")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "First — what the solving agent receives", size=22)
    add_text(s, Inches(0.6), Inches(1.05), Inches(12), Inches(0.3),
             "Same NYC Type 4 task that was generated in the previous section. Now: how does one solver work through it?",
             font=SANS, size=12, color=MUTED, italic=True)

    top = Inches(1.65)
    add_rect(s, Inches(0.6), top, Inches(12.1), Inches(5.5),
             fill=SURFACE, line_color=ACCENT)
    add_text(s, Inches(0.8), top + Inches(0.15), Inches(11.7), Inches(0.3),
             "INPUT TO THE SOLVING AGENT", font=MONO, size=10, color=ACCENT, bold=True)

    add_text(s, Inches(0.8), top + Inches(0.45), Inches(11.7), Inches(0.3),
             [[("task_id  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("gpt_5_4_new_york_type4_20260521_1779375371",
                {"font": MONO, "size": 11, "color": TEXT, "bold": True})]])
    add_text(s, Inches(0.8), top + Inches(0.75), Inches(11.7), Inches(0.3),
             [[("type     ", {"font": MONO, "size": 10, "color": MUTED}),
               ("type4_precision_allocation", {"font": MONO, "size": 11, "color": WARN, "bold": True}),
               ("    window  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("Independence Day 2026  (Jul 3–4)", {"font": MONO, "size": 11, "color": TEXT}),
               ("    days  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("2", {"font": MONO, "size": 11, "color": TEXT, "bold": True}),
               ("    budget  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("$24/day", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True})]])

    add_text(s, Inches(0.8), top + Inches(1.2), Inches(11.7), Inches(0.3),
             "public_input.query  ·  the ONLY field the agent sees:",
             font=MONO, size=10, color=MUTED)
    add_rect(s, Inches(0.8), top + Inches(1.5), Inches(11.7), Inches(0.95),
             fill=SURFACE2, line=False)
    add_text(s, Inches(1.0), top + Inches(1.6), Inches(11.3), Inches(0.85),
             "“I'm doing a 2-day New York trip over Independence Day weekend on a strict budget of $24 per day. I want to keep admission costs near zero by using free-entry sights, I want to avoid tourist traps, and I only want budget-priced meals.”",
             font=SERIF, size=14, color=TEXT, italic=True)

    add_text(s, Inches(0.8), top + Inches(2.65), Inches(11.7), Inches(0.3),
             "rubric.personal_constraints  ·  HIDDEN from the agent  ·  used by the evaluator:",
             font=MONO, size=10, color=MUTED)
    pcs = [
        ("pc_001", "hop-2", "Total cost per day  ≤  $24",
         "sum: estimated_cost_local · operator: <=  · value: 24  · scope: per_day"),
        ("pc_002", "hop-1", "All visit activities at free-entry venues",
         "all  ·  scope: activity_type=visit  ·  condition: has_tag = free-entry"),
        ("pc_003", "hop-2", "No tourist-trap venues",
         "none  ·  scope: all  ·  condition: has_tag = tourist-trap"),
        ("pc_004", "hop-1", "All meal activities at budget-priced venues",
         "all  ·  scope: activity_type=meal  ·  condition: price_tier <= budget"),
    ]
    py = top + Inches(2.95)
    for pid, hop, desc, agg in pcs:
        add_rect(s, Inches(0.8), py, Inches(11.7), Inches(0.55), fill=SURFACE, line=BORDER)
        add_text(s, Inches(0.95), py + Inches(0.07), Inches(11.4), Inches(0.25),
                 [[(pid, {"font": MONO, "size": 10, "color": ACCENT, "bold": True}),
                   ("   ", {"size": 10}),
                   (f"[{hop}]", {"font": MONO, "size": 9, "color": WARN}),
                   ("   ", {"size": 10}),
                   (desc, {"font": SANS, "size": 11, "color": TEXT})]])
        add_text(s, Inches(0.95), py + Inches(0.31), Inches(11.4), Inches(0.22),
                 agg, font=MONO, size=9.5, color=MUTED)
        py += Inches(0.6)


def slide_eval_comparison(prs):
    """Slides 28-33 — Gemini-3.1-Pro full walkthrough on NYC Type 4."""
    total = len(EV_NYC_PHASES)
    for idx, phase_name in enumerate(EV_NYC_PHASES):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        set_slide_bg(s, BG)
        add_label(s, Inches(0.6), Inches(0.3), "12  —  Solving Sandbox")
        add_title(s, Inches(0.6), Inches(0.55), Inches(12),
                  "Strong-model walkthrough  ·  Gemini-3.1-Pro on NYC Type 4", size=22)
        add_subtitle(s, Inches(0.6), Inches(1.05), Inches(12),
                     f"Phase {idx+1} / {total}  —  {phase_name}")
        _render_nyc_eval_phase(s, EV_NYC_GEMINI[idx])


def _render_nyc_eval_phase(s, step):
    # Compact task header
    th_top = Inches(1.55)
    add_rect(s, Inches(0.6), th_top, Inches(12.1), Inches(0.55), fill=SURFACE, line=BORDER)
    add_text(s, Inches(0.8), th_top + Inches(0.08), Inches(11.7), Inches(0.22),
             [[("gpt_5_4_new_york_type4_20260521_1779375371   ",
                {"font": MONO, "size": 10, "color": ACCENT, "bold": True}),
               ("$24/day · free-entry sights · no tourist traps · budget meals · 2-day Jul 4",
                {"font": MONO, "size": 9, "color": MUTED})]])
    add_text(s, Inches(0.8), th_top + Inches(0.3), Inches(11.7), Inches(0.22),
             [[("●  gemini-3.1-pro-preview   ", {"font": MONO, "size": 10, "color": ACCENT3, "bold": True}),
               ("final  C 84%  F 63%  P 75%",
                {"font": MONO, "size": 10, "color": ACCENT3})]])

    # Phase summary
    sum_top = Inches(2.25)
    add_rect(s, Inches(0.6), sum_top, Inches(12.1), Inches(0.7),
             fill=RGBColor(0x12, 0x2A, 0x22), line_color=ACCENT3)
    add_text(s, Inches(0.8), sum_top + Inches(0.1), Inches(11.7), Inches(0.5),
             step["summary"], font=SANS, size=12, color=TEXT, italic=True)

    # Actions list
    cy = Inches(3.1)
    for tool, call, result in step["actions"]:
        tool_color = {
            "search_yelp": ACCENT,
            "search_blogs_and_forums": PURPLE,
            "get_official_site": ACCENT3,
            "get_travel_time": WARN,
            "PLAN": ACCENT3,
        }.get(tool, ACCENT)
        action_h = Inches(0.65)
        add_rect(s, Inches(0.6), cy, Inches(12.1), action_h, fill=SURFACE, line=BORDER)
        add_text(s, Inches(0.8), cy + Inches(0.08), Inches(11.7), Inches(0.25),
                 [[(tool + "   ", {"font": MONO, "size": 10.5, "color": tool_color, "bold": True}),
                   (call,         {"font": MONO, "size": 10.5, "color": tool_color})]])
        add_text(s, Inches(0.8), cy + Inches(0.35), Inches(11.7), Inches(0.28),
                 result, font=MONO, size=10, color=MUTED)
        cy += action_h + Inches(0.1)

    # Scores box (only on final phase)
    if step.get("scores"):
        sc = step["scores"]
        sc_top = cy + Inches(0.1)
        add_rect(s, Inches(0.6), sc_top, Inches(12.1), Inches(0.55),
                 fill=SURFACE2, line=False)
        add_text(s, Inches(0.8), sc_top + Inches(0.12), Inches(11.7), Inches(0.35),
                 [[(f"C: {sc['C']}     ", {"font": MONO, "size": 16, "color": ACCENT, "bold": True}),
                   (f"F: {sc['F']}     ", {"font": MONO, "size": 16, "color": ACCENT3, "bold": True}),
                   (f"P: {sc['P']}",      {"font": MONO, "size": 16, "color": WARN, "bold": True})]],
                 align=PP_ALIGN.CENTER)
    if step.get("miss"):
        miss_top = (sc_top + Inches(0.65)) if step.get("scores") else (cy + Inches(0.1))
        add_rect(s, Inches(0.6), miss_top, Inches(12.1), Inches(0.55),
                 fill=RGBColor(0x28, 0x14, 0x1C), line_color=ACCENT2)
        add_text(s, Inches(0.8), miss_top + Inches(0.08), Inches(11.7), Inches(0.2),
                 "WHAT IT MISSED", font=MONO, size=9, color=ACCENT2, bold=True)
        add_text(s, Inches(0.8), miss_top + Inches(0.28), Inches(11.7), Inches(0.3),
                 step["miss"], font=SANS, size=10, color=MUTED)


def slide_eval_weak_divergence(prs):
    """NEW — single slide showing where GLM-4-7 diverged from Gemini-Pro on the same task."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "13  —  Same Task, Weak Model")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "Where the weak model diverged  ·  GLM-4-7 on the same Type 4 task", size=20)

    # Score comparison strip
    sc_top = Inches(1.15)
    add_rect(s, Inches(0.6), sc_top, Inches(12.1), Inches(0.55), fill=SURFACE, line=BORDER)
    add_text(s, Inches(0.8), sc_top + Inches(0.1), Inches(5.7), Inches(0.35),
             [[("● gemini-3.1-pro   ", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True}),
               ("C 84   F 63   P 75",
                {"font": MONO, "size": 11, "color": ACCENT3})]])
    add_text(s, Inches(7.0), sc_top + Inches(0.1), Inches(5.7), Inches(0.35),
             [[("● glm-4-7-251222   ", {"font": MONO, "size": 11, "color": ACCENT2, "bold": True}),
               ("C 64   F 63   P 50",
                {"font": MONO, "size": 11, "color": ACCENT2})]])

    # 4 divergence rows
    divs = [
        {"label": "1.  No pre-vetting blog search",
         "gemini": "Searched \"avoid tourist traps\" + cuisine-specific blogs BEFORE picking venues. 5 blog calls.",
         "glm":    "3 blog calls total, all AFTER venue selection. Picked Grimaldi's (a famous tourist-trap pizzeria) where Gemini picked Ops.",
         "cost":   "Tourist-trap exposure  ·  hurt P-score"},
        {"label": "2.  Meal budget overrun",
         "gemini": "Day 2 meals: $6 + $8 = $14 of the $24 budget.",
         "glm":    "Day 2 meals: $10 + $10 = $20.  Plus transit blew the daily cap to $24.20.",
         "cost":   "Sum constraint violated  ·  P deduction"},
        {"label": "3.  Trusted official site read but ignored content",
         "gemini": "Read official-site hours before scheduling each venue.",
         "glm":    "Called get_official_site(Nom Wah) → got \"opens 10:30am\".  Still scheduled it at 9:30am.",
         "cost":   "F2a hours violation  ·  truth-carrier retrieved but not applied"},
        {"label": "4.  Tool-call brittleness",
         "gemini": "Zero hallucinated venue IDs.  All tool args well-formed.",
         "glm":    "4 hallucinated venue IDs + 6 malformed search calls (embedded XML in 'city' param).",
         "cost":   "C-score: 84% → 64%  ·  the bulk of the gap"},
    ]
    top = Inches(1.95)
    h = Inches(1.25)
    gap = Inches(0.1)
    for i, d in enumerate(divs):
        y = top + (h + gap) * i
        add_rect(s, Inches(0.6), y, Inches(12.1), h, fill=SURFACE, line=BORDER)
        # row label
        add_text(s, Inches(0.8), y + Inches(0.1), Inches(11.7), Inches(0.25),
                 d["label"], font=SANS, size=11.5, color=TEXT, bold=True)
        # gemini side
        add_text(s, Inches(0.8), y + Inches(0.38), Inches(0.7), Inches(0.22),
                 "gemini:", font=MONO, size=9, color=ACCENT3, bold=True)
        add_text(s, Inches(1.55), y + Inches(0.38), Inches(7.0), Inches(0.3),
                 d["gemini"], font=SANS, size=9.5, color=MUTED)
        # glm side
        add_text(s, Inches(0.8), y + Inches(0.68), Inches(0.7), Inches(0.22),
                 "glm:", font=MONO, size=9, color=ACCENT2, bold=True)
        add_text(s, Inches(1.55), y + Inches(0.68), Inches(7.0), Inches(0.3),
                 d["glm"], font=SANS, size=9.5, color=MUTED)
        # cost tag
        add_text(s, Inches(8.7), y + Inches(0.5), Inches(4.0), Inches(0.5),
                 "→  " + d["cost"], font=MONO, size=10, color=WARN, italic=True)


def _render_eval_phase(s, gpt_step, glm_step):
    # Compact task header
    th_top = Inches(1.7)
    add_rect(s, Inches(0.6), th_top, Inches(12.1), Inches(0.7), fill=SURFACE, line=BORDER)
    add_text(s, Inches(0.8), th_top + Inches(0.1), Inches(11.7), Inches(0.25),
             [[("london_easter_food_photographer_2026   ",
                {"font": MONO, "size": 10, "color": ACCENT, "bold": True}),
               ("type1_cascading · Easter 2026 · 4 days · 60-call budget",
                {"font": MONO, "size": 9, "color": MUTED})]])
    add_text(s, Inches(0.8), th_top + Inches(0.36), Inches(11.7), Inches(0.25),
             [[("●  GPT-5.4   final  C=100% F=85% P=100%",
                {"font": MONO, "size": 9.5, "color": ACCENT3}),
               ("        |        ", {"font": MONO, "size": 9.5, "color": BORDER}),
               ("●  Gemini 3.1 Pro   final  C=96% F=69% P=33%",
                {"font": MONO, "size": 9.5, "color": ACCENT2})]])

    # Two panels
    panel_top = Inches(2.55)
    panel_h = Inches(4.75)
    panel_w = Inches(5.95)
    _render_eval_panel(s, Inches(0.6), panel_top, panel_w, panel_h,
                       "GPT-5.4", ACCENT3, gpt_step)
    _render_eval_panel(s, Inches(6.75), panel_top, panel_w, panel_h,
                       "Gemini 3.1 Pro", ACCENT2, glm_step)


def _render_eval_panel(s, left, top, width, height, model, accent, step):
    add_rect(s, left, top, width, height, fill=SURFACE, line_color=accent)
    inner_pad = Inches(0.18)
    inner_l = left + inner_pad
    inner_w = width - inner_pad * 2

    # Model header
    add_text(s, inner_l, top + Inches(0.1), inner_w, Inches(0.3),
             model, font=MONO, size=12, color=accent, bold=True)

    if step is None:
        # GLM has no matching step at this phase
        add_text(s, inner_l, top + Inches(1.6), inner_w, Inches(0.4),
                 "(no equivalent phase)",
                 font=MONO, size=12, color=MUTED, align=PP_ALIGN.CENTER, italic=True)
        add_text(s, inner_l, top + Inches(2.1), inner_w, Inches(2),
                 "This model did not run a dedicated cross-reference step. "
                 "It moved directly from initial discovery to official-site checks, "
                 "skipping blog corroboration entirely.",
                 font=SANS, size=10, color=MUTED, align=PP_ALIGN.CENTER)
        return

    # Phase label
    add_text(s, inner_l, top + Inches(0.42), inner_w, Inches(0.3),
             step["label"], font=MONO, size=10, color=accent)

    cy = top + Inches(0.8)
    for tool, call, result in step.get("actions", []):
        col = EV_TOOL_COLOR.get(tool, accent)
        action_h = Inches(0.6)
        add_rect(s, inner_l, cy, inner_w, action_h, fill=SURFACE2, line=False)
        add_text(s, inner_l + Inches(0.1), cy + Inches(0.05), inner_w - Inches(0.2), Inches(0.22),
                 [[(tool + "   ", {"font": MONO, "size": 8.5, "color": col, "bold": True}),
                   (call,         {"font": MONO, "size": 8.5, "color": col})]])
        add_text(s, inner_l + Inches(0.1), cy + Inches(0.26), inner_w - Inches(0.2), Inches(0.32),
                 result, font=MONO, size=8.5, color=MUTED)
        cy += action_h + Inches(0.06)

    # Result / scores block (only on result phase)
    if step.get("scores"):
        sc = step["scores"]
        scores_h = Inches(0.65)
        add_rect(s, inner_l, cy + Inches(0.05), inner_w, scores_h,
                 fill=SURFACE2, line_color=accent)
        add_text(s, inner_l + Inches(0.1), cy + Inches(0.12), inner_w - Inches(0.2), Inches(0.5),
                 [[(f"C: {sc['C']}     ", {"font": MONO, "size": 14, "color": ACCENT, "bold": True}),
                   (f"F: {sc['F']}     ", {"font": MONO, "size": 14, "color": ACCENT3, "bold": True}),
                   (f"P: {sc['P']}",      {"font": MONO, "size": 14, "color": WARN, "bold": True})]],
                 align=PP_ALIGN.CENTER)
        cy += scores_h + Inches(0.15)

    if step.get("miss"):
        miss_h = Inches(0.8)
        add_rect(s, inner_l, cy, inner_w, miss_h,
                 fill=RGBColor(0x28, 0x14, 0x1C), line_color=ACCENT2)
        add_text(s, inner_l + Inches(0.1), cy + Inches(0.06), inner_w - Inches(0.2), Inches(0.2),
                 "WHAT IT MISSED", font=MONO, size=8, color=ACCENT2, bold=True)
        add_text(s, inner_l + Inches(0.1), cy + Inches(0.26), inner_w - Inches(0.2), miss_h - Inches(0.3),
                 step["miss"], font=SANS, size=9, color=MUTED)
        cy += miss_h + Inches(0.1)

    if step.get("pcs"):
        for pid, score, desc in step["pcs"]:
            ok = score >= 1.0
            pc_color = ACCENT3 if ok else ACCENT2
            mark = "✅" if ok else "❌"
            add_text(s, inner_l, cy, inner_w, Inches(0.25),
                     [[(f"{mark}  ", {"font": MONO, "size": 10, "color": pc_color}),
                       (f"{pid}  ", {"font": MONO, "size": 9, "color": pc_color, "bold": True}),
                       (desc,       {"font": SANS, "size": 9, "color": MUTED})]])
            cy += Inches(0.24)


def slide_leaderboard(prs):
    """Slide 33 — leaderboard. Numbers from OVERNIGHT_LOG 2026-05-27;
    will be refreshed when the bigger sweep returns."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "14  —  Results")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12), "Leaderboard", size=26)
    add_subtitle(s, Inches(0.6), Inches(1.05), Inches(12.5),
                 "9 models  ·  6 latest NYC tasks  ·  test_70  ·  post-F2c-fix evaluator  ·  2026-06-06/07 sweep")

    # 9-model panel — honest pass-rate (c_score > 0) and means computed
    # only over c-passed runs (so a hard-fail doesn't poison the average).
    # "pass" = how many of the 6 NYC tasks the model produced a scoreable plan on.
    rows = [
        ["Model",                "pass", " C", " F", " P"],
        ["Claude Opus 4-8",      "6/6",  " 91", " 85", " 82"],
        ["Kimi K2.6",            "6/6",  " 91", " 45", " 77"],
        ["Gemini 3.1 Pro †",     "5/6",  " 90", " 76", " 73"],
        ["Doubao Seed-2.0 Pro",  "5/6",  " 94", " 48", " 68"],
        ["GLM-4-7-251222",       "5/6",  " 86", " 64", " 66"],
        ["DeepSeek-Chat †",      "4/6",  " 96", " 21", " 86"],
        ["GPT-5.4 †",            "4/6",  " 93", " 58", " 84"],
        ["Claude Sonnet 4-5 †",  "3/6",  " 96", " 39", " 54"],
        ["GPT-5.5",              "1/6",  " 96", " 40", " 67"],   # 5/6 hard-failed (no <final_plan>)
    ]
    n_rows = len(rows)
    n_cols = len(rows[0])
    tbl_left = Inches(0.6)
    tbl_top = Inches(1.6)
    tbl_w = Inches(7.5)   # narrower — leaves room for insight pills on right
    tbl_h = Inches(4.3)

    tbl_shape = s.shapes.add_table(n_rows, n_cols, tbl_left, tbl_top, tbl_w, tbl_h)
    tbl = tbl_shape.table

    # Column widths: model name wide, others compact
    col_widths_pct = [3.4, 0.8, 0.9, 0.9, 0.9]
    total_pct = sum(col_widths_pct)
    for i, w in enumerate(col_widths_pct):
        tbl.columns[i].width = Emu(int(tbl_w * w / total_pct))

    tbl.rows[0].height = Inches(0.35)
    for i in range(1, n_rows):
        tbl.rows[i].height = Inches(0.42)

    # Headers in same color as their column's body cells (C=blue, F=green, P=orange)
    header_colors = [MUTED, MUTED, ACCENT, ACCENT3, WARN]
    for j, header in enumerate(rows[0]):
        cell = tbl.cell(0, j)
        cell.fill.solid(); cell.fill.fore_color.rgb = SURFACE2
        tf = cell.text_frame
        tf.margin_left = Inches(0.06); tf.margin_right = Inches(0.06)
        tf.margin_top = Inches(0.04); tf.margin_bottom = Inches(0.04)
        tf.text = ""
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT
        r = p.add_run(); r.text = header
        r.font.name = MONO; r.font.size = Pt(9); r.font.bold = True
        r.font.color.rgb = header_colors[j]

    # Pre-compute max/min per scoring column (C=2, F=3, P=4) for highlighting
    def _to_int(s):
        try:
            return int(s.strip())
        except ValueError:
            return None
    col_max = {}
    col_min = {}
    for cj in (2, 3, 4):
        vals = [(_to_int(rows[ri][cj]), ri) for ri in range(1, n_rows)]
        vals = [(v, ri) for v, ri in vals if v is not None]
        if vals:
            mx = max(v for v, _ in vals)
            mn = min(v for v, _ in vals)
            col_max[cj] = {ri for v, ri in vals if v == mx}
            col_min[cj] = {ri for v, ri in vals if v == mn}

    BG_MAX = RGBColor(0x14, 0x2D, 0x22)   # subtle green tint for top score
    BG_MIN = RGBColor(0x2D, 0x14, 0x1C)   # subtle red tint for bottom score

    # Highlight: top-model row bright, gpt-5.5 row dimmed, plus per-cell max/min tints
    for i in range(1, n_rows):
        for j, val in enumerate(rows[i]):
            cell = tbl.cell(i, j)
            # Per-cell max/min wins over row background
            is_max = j in col_max and i in col_max[j]
            is_min = j in col_min and i in col_min[j]
            if is_max:
                cell.fill.solid(); cell.fill.fore_color.rgb = BG_MAX
            elif is_min:
                cell.fill.solid(); cell.fill.fore_color.rgb = BG_MIN
            elif i == 1:  # claude-opus-4-8 row — winner
                cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(0x12, 0x18, 0x2E)
            elif i == n_rows - 1:  # gpt-5.5 row — outlier
                cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(0x28, 0x14, 0x1C)
            else:
                cell.fill.solid(); cell.fill.fore_color.rgb = SURFACE
            tf = cell.text_frame
            tf.margin_left = Inches(0.06); tf.margin_right = Inches(0.06)
            tf.margin_top = Inches(0.04); tf.margin_bottom = Inches(0.04)
            tf.text = ""
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT
            # Append ▲ / ▼ markers for max/min cells
            display_val = val
            if is_max:
                display_val = val + "  ▲"
            elif is_min:
                display_val = val + "  ▼"
            r = p.add_run(); r.text = display_val
            r.font.name = MONO if j > 0 else SANS
            r.font.size = Pt(10 if j > 0 else 10.5)
            # Max/min cells get bold to emphasize
            r.font.bold = (j == 0) or is_max or is_min
            if j == 0:
                r.font.color.rgb = TEXT
            else:
                if i == n_rows - 1 and not (is_max or is_min):
                    r.font.color.rgb = ACCENT2 if j == 2 else MUTED
                elif is_max:
                    r.font.color.rgb = ACCENT3  # green for the win
                elif is_min:
                    r.font.color.rgb = ACCENT2  # red for the loss
                elif j == 1:  # pass column → muted gray
                    r.font.color.rgb = MUTED
                elif j == 2:  # C → ACCENT (blue)
                    r.font.color.rgb = ACCENT
                elif j == 3:  # F → ACCENT3 (green)
                    r.font.color.rgb = ACCENT3
                elif j == 4:  # P → WARN (orange)
                    r.font.color.rgb = WARN

    # Insight pills — F vs P asymmetry is the headline story
    pills = [
        (ACCENT2, "P-bias is almost universal",
         "7 of 9 models score P > F by 15–65 points. Models pick venues humans would like — they just can't fit those venues into a feasible schedule. Only Opus and Gemini Pro keep F and P within 3pt of each other."),
        (WARN, "DeepSeek-Chat — knows what, not how",
         "P = 86 (the table's TOP ▲) but F = 21 (the table's BOTTOM ▼). The 65pt gap is the widest in the panel: it consistently picks venues a human would love, then schedules them in ways that break hours, overlap, or travel feasibility."),
        (ACCENT, "Opus 4-8 — the only balanced premium",
         "F = 85 ▲ and P = 82 (top-2). The only model where feasibility doesn't get sacrificed for preference. Gemini Pro is the runner-up balanced reasoner (F 76 / P 73)."),
        (ACCENT3, "GPT-5.5 — structured-output regression",
         "5/6 tasks hard-failed with \"no <final_plan> produced.\" 30+ well-formed tool calls per task but no JSON terminator. The one task it completed scored mid-pack. This is a format regression vs gpt-5.4, not a reasoning one."),
    ]
    p_left = Inches(8.3)
    p_w = Inches(4.4)
    p_h = Inches(1.35)
    for i, (color, title, body) in enumerate(pills):
        t = Inches(1.6) + (p_h + Inches(0.06)) * i
        add_rect(s, p_left, t, p_w, p_h, fill=SURFACE, line_color=color)
        add_text(s, p_left + Inches(0.15), t + Inches(0.1), p_w - Inches(0.3), Inches(0.25),
                 title.upper(), font=MONO, size=9, color=color, bold=True)
        add_text(s, p_left + Inches(0.15), p_h_offset := t + Inches(0.38),
                 p_w - Inches(0.3), p_h - Inches(0.45),
                 body, font=SANS, size=9.5, color=MUTED)

    # Footnote: methodology + pre-fix evaluator caveat
    add_text(s, Inches(0.6), Inches(6.15), Inches(7.5), Inches(0.7),
             [[("▲ ", {"font": MONO, "size": 9, "color": ACCENT3, "bold": True}),
               ("col max  ·  ", {"font": MONO, "size": 9, "color": MUTED, "italic": True}),
               ("▼ ", {"font": MONO, "size": 9, "color": ACCENT2, "bold": True}),
               ("col min  ·  pass = c-passed / 6 NYC tasks  ·  means over c-passed runs only.",
                {"font": MONO, "size": 9, "color": MUTED, "italic": True})],
              [("†  ", {"font": MONO, "size": 9, "color": ACCENT2, "bold": True}),
               ("ran before the 2026-05-27 F2c-dedup fix — those F-scores may be 5–15pt understated where the plan revisits a wrong-info venue.",
                {"font": MONO, "size": 9, "color": MUTED, "italic": True})]])


def slide_multi_principal(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "10  —  Multi-Principal")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12), "Multi-Principal Agents", size=30)
    add_subtitle(s, Inches(0.6), Inches(1.35), Inches(12), "Natural inheritance in Type 3 tasks")

    col_w = Inches(6.0)
    top = Inches(1.95)

    # LEFT — current
    l_left = Inches(0.6)
    add_text(s, l_left, top, col_w, Inches(0.3),
             "TYPE 3 — CURRENT DESIGN", font=MONO, size=10, color=ACCENT, bold=True)
    add_rect(s, l_left, top + Inches(0.4), col_w, Inches(3.85), fill=SURFACE, line=BORDER)
    add_text(s, l_left + Inches(0.2), top + Inches(0.5), col_w - Inches(0.4), Inches(0.7),
             "One traveller. One rubric. Two internal preferences that pull at disjoint venue sets. "
             "The agent must find a plan that genuinely compromises — it cannot satisfy both fully "
             "given the pool.",
             font=SANS, size=10, color=MUTED)
    # query block
    qb_top = top + Inches(1.25)
    add_rect(s, l_left + Inches(0.2), qb_top, col_w - Inches(0.4), Inches(0.6),
             fill=SURFACE2, line=False)
    add_text(s, l_left + Inches(0.3), qb_top + Inches(0.08), col_w - Inches(0.6), Inches(0.5),
             "\"I want hidden gems — but my partner insists on all the iconic spots.\"",
             font=MONO, size=10, color=MUTED)
    add_text(s, l_left + Inches(0.3), qb_top + Inches(0.4), col_w - Inches(0.6), Inches(0.25),
             "→ one query, one principal, one rubric",
             font=MONO, size=9.5, color=ACCENT)

    # constraint A ∩ B diagram
    cd_top = qb_top + Inches(0.85)
    add_text(s, l_left + Inches(0.2), cd_top, col_w - Inches(0.4), Inches(0.25),
             "TENSION MECHANISM", font=MONO, size=8, color=MUTED)
    cd_box = cd_top + Inches(0.3)
    ca_w = Inches(2.35)
    add_rect(s, l_left + Inches(0.2), cd_box, ca_w, Inches(0.85),
             fill=RGBColor(0x16, 0x21, 0x3C), line_color=ACCENT)
    add_text(s, l_left + Inches(0.2), cd_box + Inches(0.08), ca_w, Inches(0.3),
             "constraint A", font=MONO, size=10, color=ACCENT, bold=True, align=PP_ALIGN.CENTER)
    add_text(s, l_left + Inches(0.2), cd_box + Inches(0.3), ca_w, Inches(0.4),
             "hidden-gem tag · traffic_tier: low\n22 venues",
             font=SANS, size=9, color=MUTED, align=PP_ALIGN.CENTER)
    # intersection symbol
    add_text(s, l_left + Inches(2.6), cd_box + Inches(0.2), Inches(0.8), Inches(0.5),
             "∩\n≤25% overlap", font=MONO, size=11, color=MUTED, align=PP_ALIGN.CENTER)
    # constraint B
    add_rect(s, l_left + Inches(3.45), cd_box, ca_w, Inches(0.85),
             fill=RGBColor(0x16, 0x2A, 0x22), line_color=ACCENT3)
    add_text(s, l_left + Inches(3.45), cd_box + Inches(0.08), ca_w, Inches(0.3),
             "constraint B", font=MONO, size=10, color=ACCENT3, bold=True, align=PP_ALIGN.CENTER)
    add_text(s, l_left + Inches(3.45), cd_box + Inches(0.3), ca_w, Inches(0.4),
             "iconic tag · traffic_tier: high\n7 venues",
             font=SANS, size=9, color=MUTED, align=PP_ALIGN.CENTER)
    # formula
    add_rect(s, l_left + Inches(0.2), cd_box + Inches(1.0), col_w - Inches(0.4), Inches(0.55),
             fill=SURFACE2, line=False)
    add_text(s, l_left + Inches(0.3), cd_box + Inches(1.08), col_w - Inches(0.6), Inches(0.5),
             "P-score = satisfied(A) + satisfied(B) / total  ·  single utility function",
             font=MONO, size=10, color=MUTED)

    # warning footer
    wf_top = top + Inches(4.45)
    add_rect(s, l_left, wf_top, col_w, Inches(0.85),
             fill=RGBColor(0x28, 0x14, 0x1C),
             line_color=RGBColor(0x60, 0x2C, 0x40))
    add_text(s, l_left + Inches(0.2), wf_top + Inches(0.1), col_w - Inches(0.4), Inches(0.7),
             "⚠  \"My partner\" is background flavour — there is no second scoreable utility "
             "function. An agent that ignores one preference entirely gets the same structural "
             "treatment as one that merely under-weights it.",
             font=SANS, size=10, color=RGBColor(0xC8, 0x80, 0x90))

    # RIGHT — extension
    r_left = Inches(6.95)
    add_text(s, r_left, top, col_w, Inches(0.3),
             "TYPE 3 — MULTI-PRINCIPAL EXTENSION", font=MONO, size=10, color=WARN, bold=True)
    add_rect(s, r_left, top + Inches(0.4), col_w, Inches(3.85), fill=SURFACE, line_color=WARN)
    add_text(s, r_left + Inches(0.2), top + Inches(0.5), col_w - Inches(0.4), Inches(0.85),
             "Two named principals. Two independent utility functions. One shared bounded plan. "
             "The agent must optimize a weighted social objective — satisfying neither fully is "
             "expected; ignoring one entirely is the measurable failure mode.",
             font=SANS, size=10, color=MUTED)
    qb_top = top + Inches(1.4)
    add_rect(s, r_left + Inches(0.2), qb_top, col_w - Inches(0.4), Inches(0.85),
             fill=SURFACE2, line=False)
    add_text(s, r_left + Inches(0.3), qb_top + Inches(0.08), col_w - Inches(0.6), Inches(0.8),
             [[("A (planner): ", {"font": MONO, "size": 10, "color": ACCENT, "bold": True}),
               ("\"I want hidden gems, local spots away from tourists.\"\n", {"font": MONO, "size": 10, "color": MUTED})],
              [("B (partner): ", {"font": MONO, "size": 10, "color": WARN, "bold": True}),
               ("\"I'm only coming if we do the iconic London spots.\"", {"font": MONO, "size": 10, "color": MUTED})],
              [("→ two named principals, split rubric, weighted P-score",
                {"font": MONO, "size": 9.5, "color": WARN})]])

    # principal split
    sp_top = qb_top + Inches(1.05)
    add_text(s, r_left + Inches(0.2), sp_top, col_w - Inches(0.4), Inches(0.25),
             "SCORING — YANG et al. (2026)", font=MONO, size=8, color=MUTED)
    sp_box = sp_top + Inches(0.3)
    pw = Inches(2.75)
    add_rect(s, r_left + Inches(0.2), sp_box, pw, Inches(1.0),
             fill=RGBColor(0x16, 0x21, 0x3C), line_color=ACCENT)
    add_text(s, r_left + Inches(0.2), sp_box + Inches(0.08), pw, Inches(0.3),
             "Principal A · u₁", font=MONO, size=10, color=ACCENT, bold=True, align=PP_ALIGN.CENTER)
    add_text(s, r_left + Inches(0.3), sp_box + Inches(0.35), pw - Inches(0.2), Inches(0.4),
             "P-constraints tagged principal=A. Score computed independently.",
             font=SANS, size=9, color=MUTED, align=PP_ALIGN.CENTER)
    add_text(s, r_left + Inches(0.3), sp_box + Inches(0.78), pw - Inches(0.2), Inches(0.2),
             "w₁ = 0.5 · U₁(plan)", font=MONO, size=9, color=ACCENT, align=PP_ALIGN.CENTER)

    add_rect(s, r_left + Inches(3.05), sp_box, pw, Inches(1.0),
             fill=RGBColor(0x2A, 0x21, 0x12), line_color=WARN)
    add_text(s, r_left + Inches(3.05), sp_box + Inches(0.08), pw, Inches(0.3),
             "Principal B · u₂", font=MONO, size=10, color=WARN, bold=True, align=PP_ALIGN.CENTER)
    add_text(s, r_left + Inches(3.15), sp_box + Inches(0.35), pw - Inches(0.2), Inches(0.4),
             "P-constraints tagged principal=B. Score computed independently.",
             font=SANS, size=9, color=MUTED, align=PP_ALIGN.CENTER)
    add_text(s, r_left + Inches(3.15), sp_box + Inches(0.78), pw - Inches(0.2), Inches(0.2),
             "w₂ = 0.5 · U₂(plan)", font=MONO, size=9, color=WARN, align=PP_ALIGN.CENTER)

    # bottom row — what changes / what stays
    br_top = top + Inches(4.45)
    bw = Inches(2.95)
    add_rect(s, r_left, br_top, bw, Inches(0.85),
             fill=RGBColor(0x28, 0x14, 0x1C), line_color=ACCENT2)
    add_text(s, r_left + Inches(0.15), br_top + Inches(0.08), bw - Inches(0.3), Inches(0.25),
             "WHAT CHANGES", font=MONO, size=8, color=ACCENT2, bold=True)
    add_text(s, r_left + Inches(0.15), br_top + Inches(0.3), bw - Inches(0.3), Inches(0.55),
             "P-constraints get a principal field. Evaluator splits score by principal before summing.",
             font=SANS, size=9, color=MUTED)

    add_rect(s, r_left + Inches(3.05), br_top, bw, Inches(0.85),
             fill=RGBColor(0x12, 0x18, 0x2E), line_color=ACCENT)
    add_text(s, r_left + Inches(3.2), br_top + Inches(0.08), bw - Inches(0.3), Inches(0.25),
             "WHAT STAYS THE SAME", font=MONO, size=8, color=ACCENT, bold=True)
    add_text(s, r_left + Inches(3.2), br_top + Inches(0.3), bw - Inches(0.3), Inches(0.55),
             "One query. One plan. One bounded day. A×A tension validation still applies.",
             font=SANS, size=9, color=MUTED)


def slide_future(prs):
    """Slide 34 — three future directions."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "18  —  Future Directions")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12),
              "Where TravelBench Goes Next", size=32)
    add_subtitle(s, Inches(0.6), Inches(1.4), Inches(12),
                 "Three directions, each opening a different research problem")

    items = [
        (ACCENT, "Long-horizon  ·  workflow agency",
         "Test the agent's ability to build, maintain, and refine its own workflow across many tasks. Does it remember which sources it learned to trust? Does it save useful intermediate results — a venue's verified hours, a district's wheelchair patterns — and reach for them later? The instinct to preserve useful resources is the metric, not raw per-task accuracy.",
         "Tests:  workflow construction  ·  memory hygiene  ·  resource maintenance"),
        (ACCENT3, "Real-API transfer  ·  ground truth at scale",
         "Same methodology, real web/API environment (Google Places, Yelp public, Foursquare, OpenStreetMap). Open problem: how do we obtain ground truth in a live setting? Three paths — (i) cross-source agreement majority vote; (ii) curated authoritative source like municipal open data; (iii) human-in-the-loop verification on a subset, then propagated trust.",
         "Open question:  ground-truth acquisition in a live environment is the hard part, not the planning"),
        (WARN, "Generalize beyond travel",
         "Same mechanism — hidden ground truth + faulty source corpus + structured constraint engine + multi-tier scoring — applies to any consumer decision under noisy information.  Small: which shampoo, weekly schedule layout.  Medium: which laptop, which doctor.  Large: which school, which job.  The travel domain is a test bed; the framework is a decision-evaluation pattern.",
         "TravelBench is one instance of a more general benchmark family"),
    ]
    top = Inches(2.0)
    h = Inches(1.7)
    gap = Inches(0.18)
    for i, (color, title, body, footer) in enumerate(items):
        t = top + (h + gap) * i
        add_rect(s, Inches(0.6), t, Inches(12.1), h, fill=SURFACE, line_color=color)
        # number badge
        add_rect(s, Inches(0.8), t + Inches(0.18), Inches(1.4), Inches(0.32),
                 fill=SURFACE, line_color=color)
        add_text(s, Inches(0.8), t + Inches(0.23), Inches(1.4), Inches(0.25),
                 ["DIRECTION 1", "DIRECTION 2", "DIRECTION 3"][i],
                 font=MONO, size=9, color=color, bold=True, align=PP_ALIGN.CENTER)
        # title
        add_text(s, Inches(2.35), t + Inches(0.18), Inches(10.3), Inches(0.32),
                 title, font=SANS, size=14, color=TEXT, bold=True)
        # body
        add_text(s, Inches(2.35), t + Inches(0.55), Inches(10.3), Inches(0.78),
                 body, font=SANS, size=10, color=MUTED)
        # footer
        add_text(s, Inches(2.35), t + Inches(1.38), Inches(10.3), Inches(0.3),
                 footer, font=MONO, size=9, color=color)


# ─────────────────────────────────────────────────────────────────
# NEW slides — Act 2 (venue / city world)
# ─────────────────────────────────────────────────────────────────

def slide_venue_polished(prs):
    """Slide 4 — what one venue looks like in the corpus. Rich actual content."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "03  —  One Venue in the Corpus")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "What the solving agent actually sees", size=22)
    add_text(s, Inches(0.6), Inches(1.05), Inches(12), Inches(0.25),
             f"VENUE:  {SHOWCASE_VENUE['name']}  ·  {SHOWCASE_VENUE['category']}  ·  {SHOWCASE_VENUE['district']}",
             font=MONO, size=10, color=MUTED)

    top = Inches(1.45)
    card_w = Inches(4.05)
    card_h = Inches(5.4)
    gap = Inches(0.1)

    # ── 1) YELP listing card — looks like an actual yelp result
    l = Inches(0.6)
    add_rect(s, l, top, card_w, card_h, fill=SURFACE, line_color=ACCENT2)
    add_text(s, l + Inches(0.15), top + Inches(0.1), card_w - Inches(0.3), Inches(0.25),
             "▸  search_yelp() result", font=MONO, size=9, color=ACCENT2, bold=True)
    # Header row
    add_text(s, l + Inches(0.15), top + Inches(0.4), card_w - Inches(0.3), Inches(0.3),
             "Corkbuzz Chelsea Market",
             font=SANS, size=13, color=TEXT, bold=True)
    add_text(s, l + Inches(0.15), top + Inches(0.7), card_w - Inches(0.3), Inches(0.25),
             "★★★★☆  4.0  ·  47 reviews  ·  $$",
             font=MONO, size=10, color=WARN)
    add_text(s, l + Inches(0.15), top + Inches(0.95), card_w - Inches(0.3), Inches(0.25),
             "wine bar  ·  Chelsea  ·  75 9th Ave",
             font=SANS, size=9, color=MUTED)
    # Hours table
    add_text(s, l + Inches(0.15), top + Inches(1.3), card_w - Inches(0.3), Inches(0.22),
             "HOURS", font=MONO, size=9, color=MUTED)
    hrs_top = top + Inches(1.55)
    rh = Inches(0.22)
    hours = [
        ("Mon–Thu", "12:00 – 22:00", TEXT),
        ("Fri",     "12:00 – 00:00", ACCENT2),  # the wrong one
        ("Sat",     "11:00 – 22:00", TEXT),
        ("Sun",     "11:00 – 21:00", TEXT),
    ]
    for i, (day, hrs, color) in enumerate(hours):
        add_text(s, l + Inches(0.25), hrs_top + rh * i, Inches(0.9), rh,
                 day, font=MONO, size=10, color=MUTED)
        add_text(s, l + Inches(1.15), hrs_top + rh * i, Inches(1.8), rh,
                 hrs, font=MONO, size=10, color=color,
                 bold=(color == ACCENT2))
        if color == ACCENT2:
            add_text(s, l + Inches(2.95), hrs_top + rh * i, Inches(0.9), rh,
                     "← stale", font=MONO, size=9, color=ACCENT2, italic=True)
    # Review snippet
    add_text(s, l + Inches(0.15), top + Inches(2.6), card_w - Inches(0.3), Inches(0.22),
             "TOP REVIEW", font=MONO, size=9, color=MUTED)
    add_rect(s, l + Inches(0.15), top + Inches(2.85), card_w - Inches(0.3), Inches(0.7),
             fill=SURFACE2, line=False)
    add_text(s, l + Inches(0.25), top + Inches(2.92), card_w - Inches(0.5), Inches(0.6),
             "“Great selection of wines by the glass — perfect for a relaxed evening in Chelsea Market.”",
             font=SANS, size=9.5, color=TEXT, italic=True)
    # Last activity
    add_text(s, l + Inches(0.15), top + Inches(3.65), card_w - Inches(0.3), Inches(0.22),
             "Listing last edited:  2021-03-15  (4 years ago)",
             font=MONO, size=9, color=MUTED)
    # The trap call-out (subtle)
    add_rect(s, l + Inches(0.15), top + Inches(4.55), card_w - Inches(0.3), Inches(0.7),
             fill=RGBColor(0x28, 0x14, 0x1C), line_color=ACCENT2)
    add_text(s, l + Inches(0.25), top + Inches(4.65), card_w - Inches(0.5), Inches(0.5),
             "⚠  Friday hours stale.  Pandemic-era listing never updated.  ← THE TRAP",
             font=SANS, size=9, color=ACCENT2, bold=True, align=PP_ALIGN.CENTER)

    # ── 2) FORUM thread card — looks like reddit/tripadvisor thread
    l2 = Inches(0.6) + card_w + gap
    add_rect(s, l2, top, card_w, card_h, fill=SURFACE, line_color=ACCENT3)
    add_text(s, l2 + Inches(0.15), top + Inches(0.1), card_w - Inches(0.3), Inches(0.25),
             "▸  search_blogs_and_forums() result", font=MONO, size=9, color=ACCENT3, bold=True)
    add_text(s, l2 + Inches(0.15), top + Inches(0.4), card_w - Inches(0.3), Inches(0.5),
             "Corkbuzz Chelsea Market — still good for a Friday hang?",
             font=SANS, size=11, color=TEXT, bold=True)
    add_text(s, l2 + Inches(0.15), top + Inches(0.95), card_w - Inches(0.3), Inches(0.22),
             "forum · 2025-02-18 · 18 ↑",
             font=MONO, size=9, color=MUTED)
    # Thread
    cy = top + Inches(1.3)
    posts = [
        ("Chelsea_Mike", MUTED,
         "Thinking of taking some friends to Corkbuzz this Friday after work. How late do they stay open these days?"),
        ("NYC_Emily84", ACCENT3,
         "Yeah they're still solid. Last time I was there on a Friday we rolled in around 8:30 and got a table no problem. But heads up — they close at 10pm now, not midnight like it used to be. Some old blog posts and the Yelp listing still say later but they tightened up after the pandemic. So don't plan a late one there."),
        ("Chelsea_Mike", MUTED,
         "Oh good to know! 10pm is early."),
    ]
    for author, color, txt in posts:
        add_text(s, l2 + Inches(0.15), cy, card_w - Inches(0.3), Inches(0.22),
                 author + " :",
                 font=MONO, size=9.5, color=color, bold=True)
        ph = Inches(0.25 + 0.16 * (len(txt) // 50 + 1))
        add_text(s, l2 + Inches(0.25), cy + Inches(0.22), card_w - Inches(0.4), ph,
                 txt,
                 font=SANS, size=9, color=TEXT)
        cy += Inches(0.25) + ph + Inches(0.05)

    add_rect(s, l2 + Inches(0.15), top + Inches(4.55), card_w - Inches(0.3), Inches(0.7),
             fill=RGBColor(0x12, 0x2A, 0x22), line_color=ACCENT3)
    add_text(s, l2 + Inches(0.25), top + Inches(4.65), card_w - Inches(0.5), Inches(0.5),
             "✓  Correction embedded in lived experience.  ← THE TRUTH CARRIER",
             font=SANS, size=9, color=ACCENT3, bold=True, align=PP_ALIGN.CENTER)

    # ── 3) OFFICIAL site card — looks like a venue's About page
    l3 = l2 + card_w + gap
    add_rect(s, l3, top, card_w, card_h, fill=SURFACE, line_color=ACCENT)
    add_text(s, l3 + Inches(0.15), top + Inches(0.1), card_w - Inches(0.3), Inches(0.25),
             "▸  get_official_site() result", font=MONO, size=9, color=ACCENT, bold=True)
    add_text(s, l3 + Inches(0.15), top + Inches(0.4), card_w - Inches(0.3), Inches(0.3),
             "corkbuzz.com/chelsea-market",
             font=MONO, size=11, color=ACCENT, bold=True)
    # Hours block
    add_text(s, l3 + Inches(0.15), top + Inches(0.8), card_w - Inches(0.3), Inches(0.22),
             "HOURS", font=MONO, size=9, color=MUTED)
    hrs2_top = top + Inches(1.05)
    hours2 = [
        ("Mon–Thu", "12:00 PM – 10:00 PM", TEXT),
        ("Fri",     "12:00 PM – 10:00 PM", ACCENT3),  # the correct one
        ("Sat",     "11:00 AM – 10:00 PM", TEXT),
        ("Sun",     "11:00 AM – 9:00 PM",  TEXT),
    ]
    for i, (day, hrs, color) in enumerate(hours2):
        add_text(s, l3 + Inches(0.25), hrs2_top + rh * i, Inches(0.9), rh,
                 day, font=MONO, size=10, color=MUTED)
        add_text(s, l3 + Inches(1.15), hrs2_top + rh * i, Inches(2.5), rh,
                 hrs, font=MONO, size=10, color=color,
                 bold=(color == ACCENT3))
    # About
    add_text(s, l3 + Inches(0.15), top + Inches(2.1), card_w - Inches(0.3), Inches(0.22),
             "ABOUT", font=MONO, size=9, color=MUTED)
    add_text(s, l3 + Inches(0.15), top + Inches(2.35), card_w - Inches(0.3), Inches(2.0),
             "A relaxed wine bar featuring over 40 rotating wines by the glass, plus craft cocktails. Walk-ins welcome. Dog-friendly on indoor patio. We ask guests to refrain from photography to preserve the intimate atmosphere. Adults-focused.\n\nThe entrance has a small step and our interior layout includes tightly spaced tables — we are not fully wheelchair accessible.",
             font=SANS, size=9, color=MUTED)
    add_rect(s, l3 + Inches(0.15), top + Inches(4.55), card_w - Inches(0.3), Inches(0.7),
             fill=RGBColor(0x12, 0x18, 0x2E), line_color=ACCENT)
    add_text(s, l3 + Inches(0.25), top + Inches(4.65), card_w - Inches(0.5), Inches(0.5),
             "✓  Canonical source.  Always returns the correct value.  ← GROUND TRUTH",
             font=SANS, size=9, color=ACCENT, bold=True, align=PP_ALIGN.CENTER)

    # Hook at the bottom — small text
    add_text(s, Inches(0.6), Inches(6.95), Inches(12.1), Inches(0.3),
             "→  This is one of 70 NYC venues.  How does each one get built?",
             font=SERIF, size=15, color=TEXT, align=PP_ALIGN.CENTER, italic=True)


def slide_venue_sandbox(prs):
    """Slides 5-9 — one slide per VENUE_SB_STEPS entry."""
    total = len(VENUE_SB_STEPS)
    for idx, step in enumerate(VENUE_SB_STEPS, start=1):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        set_slide_bg(s, BG)
        add_label(s, Inches(0.6), Inches(0.4), "05  —  Per-Venue Generation Sandbox")
        add_title(s, Inches(0.6), Inches(0.7), Inches(12), SHOWCASE_VENUE["name"], size=24)
        add_subtitle(s, Inches(0.6), Inches(1.2), Inches(12), f"Step {idx} / {total}  —  {step['label']}")
        _render_venue_step(s, idx, total, step)


def _render_venue_step(s, idx, total, step):
    # Sidebar — 11 tools mini-legend
    left = Inches(0.6)
    top = Inches(1.85)
    col_w = Inches(2.6)
    add_text(s, left, top, col_w, Inches(0.3),
             "PER-VENUE AGENT TOOLS", font=MONO, size=9, color=MUTED)
    tools_legend = [
        ("CREATE_PAGE", ACCENT,  "Open a draft for a new entity"),
        ("FILL",        ACCENT3, "Set fields on a draft"),
        ("COMMIT",      ACCENT3, "Persist a draft as canonical"),
        ("ADD_WRONG_INFO", WARN, "Plant a wrong/correct value pair atomically"),
        ("REGISTER_DOC_REFS", PURPLE, "Link docs to wrong-info roles"),
        ("VERIFY",      ACCENT2, "Run 11 venue completeness checks"),
    ]
    cy = top + Inches(0.35)
    for name, color, desc in tools_legend:
        add_rect(s, left, cy, col_w, Inches(0.6), fill=SURFACE, line=BORDER)
        add_text(s, left + Inches(0.15), cy + Inches(0.06), col_w - Inches(0.3), Inches(0.25),
                 name, font=MONO, size=9, color=color, bold=True)
        add_text(s, left + Inches(0.15), cy + Inches(0.28), col_w - Inches(0.3), Inches(0.3),
                 desc, font=SANS, size=8.5, color=MUTED)
        cy += Inches(0.66)

    # Right panel
    right_l = Inches(3.4)
    right_w = Inches(9.3)
    right_t = Inches(1.85)

    progress_pct = idx / total
    add_rect(s, right_l, right_t, right_w, Inches(0.08), fill=SURFACE2, line=False)
    add_rect(s, right_l, right_t, Emu(int(right_w * progress_pct)), Inches(0.08),
             fill=ACCENT, line=False)

    title_color = ACCENT3 if step["type"] == "success" else ACCENT
    icon = {"think": "💭", "create": "🛠", "success": "✅"}.get(step["type"], "•")
    add_text(s, right_l, right_t + Inches(0.2), right_w, Inches(0.4),
             f"{icon}  {step['label']}",
             font=MONO, size=13, color=title_color, bold=True)

    cy = right_t + Inches(0.75)

    if step.get("think"):
        think_h = Inches(0.85) if len(step["think"]) > 180 else Inches(0.7)
        add_rect(s, right_l, cy, right_w, think_h,
                 fill=RGBColor(0x22, 0x1A, 0x2E), line_color=PURPLE)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.08), right_w - Inches(0.3), Inches(0.25),
                 "↯ THINKING", font=MONO, size=9, color=PURPLE)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.3), right_w - Inches(0.3), think_h - Inches(0.35),
                 step["think"], font=SANS, size=10, color=MUTED)
        cy += think_h + Inches(0.12)

    # Actions
    tool_colors = {"CREATE_PAGE": ACCENT, "FILL": ACCENT3, "COMMIT": ACCENT3,
                   "ADD_WRONG_INFO": WARN, "REGISTER_DOC_REFS": PURPLE,
                   "VERIFY": ACCENT2, "SUBMIT": WARN}
    for tool, call, result in step.get("actions", []):
        color = tool_colors.get(tool, ACCENT)
        is_ok = result.startswith("✅")
        border_c = ACCENT3 if is_ok else BORDER
        action_h = Inches(0.78) if len(call) > 50 else Inches(0.6)
        add_rect(s, right_l, cy, right_w, action_h, fill=SURFACE, line_color=border_c)
        add_text(s, right_l + Inches(0.15), cy + Inches(0.06), right_w - Inches(0.3), Inches(0.25),
                 [[(tool + "   ", {"font": MONO, "size": 10, "color": color, "bold": True}),
                   (call,         {"font": MONO, "size": 9, "color": color})]])
        add_text(s, right_l + Inches(0.15), cy + Inches(0.32), right_w - Inches(0.3), action_h - Inches(0.35),
                 result, font=MONO, size=9, color=ACCENT3 if is_ok else MUTED)
        cy += action_h + Inches(0.08)


def slide_truth_carrier(prs):
    """Slide 10 — the truth-carrier mechanism explanation."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "06  —  Truth-Carrier Mechanism")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12),
              "Truth-Carrier Mechanism", size=30)
    add_subtitle(s, Inches(0.6), Inches(1.4), Inches(12),
                 "Every wrong-info entry registers two docs — the trap and its escape")

    # Three sections horizontally
    top = Inches(2.1)
    card_h = Inches(2.6)
    card_w = Inches(4.0)
    gap = Inches(0.15)

    # Section 1 — the pair
    add_rect(s, Inches(0.6), top, card_w, card_h, fill=SURFACE, line_color=ACCENT)
    add_text(s, Inches(0.8), top + Inches(0.15), card_w - Inches(0.4), Inches(0.3),
             "THE PAIR", font=MONO, size=10, color=ACCENT, bold=True)
    add_text(s, Inches(0.8), top + Inches(0.45), card_w - Inches(0.4), Inches(0.4),
             [[("incorrect_source", {"font": MONO, "size": 12, "color": ACCENT2, "bold": True}),
               (": where the agent trips", {"font": SANS, "size": 11, "color": MUTED})]])
    add_text(s, Inches(0.8), top + Inches(0.85), card_w - Inches(0.4), Inches(0.5),
             "A blog or yelp listing that carries the wrong value, plausibly enough that a fast reader trusts it.",
             font=SANS, size=10, color=MUTED)
    add_text(s, Inches(0.8), top + Inches(1.5), card_w - Inches(0.4), Inches(0.4),
             [[("truth_carrier", {"font": MONO, "size": 12, "color": ACCENT3, "bold": True}),
               (": where the agent recovers", {"font": SANS, "size": 11, "color": MUTED})]])
    add_text(s, Inches(0.8), top + Inches(1.9), card_w - Inches(0.4), Inches(0.6),
             "A forum post or doc where someone mentions the correction in passing — embedded in lived experience, not flagged.",
             font=SANS, size=10, color=MUTED)

    # Section 2 — atomic write
    l2 = Inches(0.6) + card_w + gap
    add_rect(s, l2, top, card_w, card_h, fill=SURFACE, line_color=ACCENT3)
    add_text(s, l2 + Inches(0.2), top + Inches(0.15), card_w - Inches(0.4), Inches(0.3),
             "ATOMIC WRITE  ·  P6-T22", font=MONO, size=10, color=ACCENT3, bold=True)
    add_text(s, l2 + Inches(0.2), top + Inches(0.5), card_w - Inches(0.4), Inches(0.5),
             "ADD_WRONG_INFO takes both doc IDs as required arguments and writes all three tables in one transaction.",
             font=SANS, size=10, color=MUTED)
    add_rect(s, l2 + Inches(0.2), top + Inches(1.15), card_w - Inches(0.4), Inches(0.5),
             fill=SURFACE2, line=False)
    add_text(s, l2 + Inches(0.3), top + Inches(1.22), card_w - Inches(0.6), Inches(0.4),
             "wrong_info  +  doc_venue_roles ×2  →  1 commit",
             font=MONO, size=10, color=ACCENT3, align=PP_ALIGN.CENTER)
    add_text(s, l2 + Inches(0.2), top + Inches(1.8), card_w - Inches(0.4), Inches(0.7),
             "Eliminates orphans. (Pre-fix: 21% of wrong-info entries on NYC test_50 had no registered truth carrier.)",
             font=SANS, size=10, color=MUTED, italic=True)

    # Section 3 — categories
    l3 = l2 + card_w + gap
    add_rect(s, l3, top, card_w, card_h, fill=SURFACE, line_color=WARN)
    add_text(s, l3 + Inches(0.2), top + Inches(0.15), card_w - Inches(0.4), Inches(0.3),
             "4 WRONG-INFO CATEGORIES", font=MONO, size=10, color=WARN, bold=True)
    cats = [
        ("temporal_decay",     "Old fact, never updated (Corkbuzz's pre-pandemic hours)"),
        ("propagation_error",  "A wrong fact echoed across multiple sources"),
        ("conditional",        "Right under one assumption, wrong under another"),
        ("subjective",         "A claim that depends on whose review you trust"),
    ]
    cy = top + Inches(0.55)
    for name, desc in cats:
        add_text(s, l3 + Inches(0.2), cy, card_w - Inches(0.4), Inches(0.2),
                 name, font=MONO, size=10, color=WARN, bold=True)
        add_text(s, l3 + Inches(0.2), cy + Inches(0.22), card_w - Inches(0.4), Inches(0.3),
                 desc, font=SANS, size=9, color=MUTED)
        cy += Inches(0.5)

    # Bottom F-score callout
    cy = top + card_h + Inches(0.3)
    add_rect(s, Inches(0.6), cy, Inches(12.1), Inches(0.85),
             fill=RGBColor(0x14, 0x22, 0x2C), line_color=ACCENT)
    add_text(s, Inches(0.8), cy + Inches(0.1), Inches(11.7), Inches(0.3),
             "F-SCORE WIRING (F2c)",
             font=MONO, size=10, color=ACCENT, bold=True)
    add_text(s, Inches(0.8), cy + Inches(0.4), Inches(11.7), Inches(0.45),
             "Each wrong-info venue scheduled without retrieving its truth_carrier doc → −0.05 F deduction. Penalizes models that trust the first source they find, not models that cross-reference. Once per venue per task (fixed in OVERNIGHT_LOG 2026-05-27, was double-counting).",
             font=SANS, size=10, color=MUTED)


def slide_gt_vs_faulty(prs):
    """Slide 11 — placeholder until pilot completes."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "07  —  Pilot Experiment")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12),
              "Ground Truth vs Faulty Environment", size=28)
    add_subtitle(s, Inches(0.6), Inches(1.4), Inches(12),
                 "Does the wrong-info trap actually bite?")
    add_body(s, Inches(0.6), Inches(1.95), Inches(12.1), Inches(0.7),
             "Same 5 NYC tasks · 2 models · run twice. Faulty = current default. Clean uses --clean-environment: heals yelp structured fields and drops source_docs flagged incorrect_source. Official-site docs unchanged (authoritative by design).")

    # ── LEFT: model-level summary table ─────────────────────────────────────
    top = Inches(2.85)
    left_w = Inches(6.0)
    add_rect(s, Inches(0.6), top, left_w, Inches(3.0), fill=SURFACE, line=BORDER)
    add_text(s, Inches(0.8), top + Inches(0.15), left_w - Inches(0.4), Inches(0.3),
             "MODEL-LEVEL MEANS  ·  5 tasks", font=MONO, size=10, color=ACCENT, bold=True)

    # Build the table inline (4 rows: header + 2 model headers + means)
    rows_left = [
        ["Model · mode",        " C",     " F",     " P",     "ΔP"],
        ["gemini-pro  faulty",  "0.96",  "0.74",  "0.61",  "—"],
        ["gemini-pro  clean ",  "0.90",  "0.76",  "0.72",  "+0.12"],
        ["deepseek    faulty",  "0.37",  "0.07",  "0.78",  "—"],
        ["deepseek    clean ",  "0.77",  "0.21",  "0.86",  "+0.08"],
    ]
    rt = top + Inches(0.55)
    tbl_h = Inches(2.3)
    tbl_left = Inches(0.8)
    tbl_w = left_w - Inches(0.4)
    rh = tbl_h / len(rows_left)
    col_widths = [2.3, 0.8, 0.8, 0.8, 0.9]
    col_total = sum(col_widths)
    for ri, row in enumerate(rows_left):
        is_header = (ri == 0)
        is_clean = "clean" in row[0]
        if is_header:
            bg = SURFACE2; fg = MUTED; bold = True
        elif is_clean:
            bg = RGBColor(0x12, 0x2A, 0x22); fg = ACCENT3; bold = False
        else:
            bg = SURFACE; fg = MUTED; bold = False
        add_rect(s, tbl_left, rt + rh * ri, tbl_w, rh, fill=bg, line=False)
        cx = tbl_left + Inches(0.1)
        for ci, cell in enumerate(row):
            cw = Emu(int((tbl_w - Inches(0.2)) * col_widths[ci] / col_total))
            add_text(s, cx, rt + rh * ri + Inches(0.07), cw, rh,
                     cell,
                     font=MONO, size=10.5,
                     color=fg if not (ri == 2 and ci == 4) else ACCENT3,
                     bold=bold or (ri == 2 and ci == 4) or (ri == 4 and ci == 4))
            cx += cw

    # ── RIGHT: per-task delta panel (gemini, the clean signal) ──────────────
    rl = Inches(6.85)
    add_rect(s, rl, top, Inches(5.85), Inches(3.0), fill=SURFACE, line=BORDER)
    add_text(s, rl + Inches(0.2), top + Inches(0.15), Inches(5.45), Inches(0.3),
             "PER-TASK P-SCORE  ·  gemini-pro  ·  clean − faulty",
             font=MONO, size=10, color=PURPLE, bold=True)

    deltas = [
        ("type1",  "1.00", "0.75", -0.25, ACCENT2),
        ("type2",  "0.67", "0.67",  0.00, MUTED),
        ("type3",  "0.79", "0.79",  0.00, MUTED),
        ("type4",  "0.25", "0.75", +0.50, ACCENT3),
        ("type5",  "0.33", "0.67", +0.33, ACCENT3),
    ]
    rty = top + Inches(0.55)
    rh2 = Inches(0.45)
    for di, (label, fv, cv, dv, color) in enumerate(deltas):
        ty = rty + rh2 * di
        add_text(s, rl + Inches(0.2), ty + Inches(0.08), Inches(0.6), Inches(0.3),
                 label, font=MONO, size=11, color=TEXT, bold=True)
        add_text(s, rl + Inches(0.8), ty + Inches(0.08), Inches(0.7), Inches(0.3),
                 fv, font=MONO, size=10, color=MUTED)
        add_text(s, rl + Inches(1.45), ty + Inches(0.08), Inches(0.3), Inches(0.3),
                 "→", font=SANS, size=12, color=MUTED, align=PP_ALIGN.CENTER)
        add_text(s, rl + Inches(1.75), ty + Inches(0.08), Inches(0.7), Inches(0.3),
                 cv, font=MONO, size=10, color=TEXT)
        # bar visualization: zero at midpoint, +/- magnitude proportional
        bar_origin = rl + Inches(3.4)
        bar_max_half = Inches(1.0)
        bar_h = Inches(0.18)
        add_rect(s, bar_origin - Inches(0.005), ty + Inches(0.12), Inches(0.01), bar_h,
                 fill=BORDER, line=False)  # zero line
        if abs(dv) > 0.001:
            mag = min(abs(dv) / 0.5, 1.0)
            bw = Emu(int(bar_max_half * mag))
            bx = bar_origin if dv > 0 else bar_origin - bw
            add_rect(s, bx, ty + Inches(0.12), bw, bar_h,
                     fill=color, line=False)
        add_text(s, rl + Inches(4.5), ty + Inches(0.08), Inches(1.2), Inches(0.3),
                 ("+" if dv > 0 else "") + f"{dv:.2f}",
                 font=MONO, size=11, color=color, bold=(abs(dv) > 0.01))

    # ── BOTTOM: takeaway box ─────────────────────────────────────────────────
    bt = top + Inches(3.15)
    add_rect(s, Inches(0.6), bt, Inches(12.1), Inches(1.15),
             fill=RGBColor(0x14, 0x22, 0x2C), line_color=ACCENT3)
    add_text(s, Inches(0.8), bt + Inches(0.1), Inches(11.7), Inches(0.3),
             "WHAT THE TRAP ACTUALLY DOES",
             font=MONO, size=10, color=ACCENT3, bold=True)
    add_text(s, Inches(0.8), bt + Inches(0.4), Inches(11.7), Inches(0.7),
             [[("The trap bites task-specifically. ", {"size": 11, "color": TEXT, "bold": True}),
               ("Types 4 and 5 — whose solution pools touch wrong-info venues — gain ", {"size": 11, "color": MUTED}),
               ("+50pt and +33pt P", {"size": 11, "color": ACCENT3, "bold": True}),
               (" in clean mode. Types 2 and 3 are flat (wrong-info isn't in their solution path). ",
                {"size": 11, "color": MUTED}),
               ("Type 1 even ", {"size": 11, "color": MUTED}),
               ("regresses −25pt ", {"size": 11, "color": ACCENT2, "bold": True}),
               ("in clean mode — losing the incorrect-source docs removed informative signal the agent had been using to triangulate. The wrong-info layer is doing real, asymmetric work — not uniform sabotage.",
                {"size": 11, "color": MUTED}),
               ]])


def slide_pscore_engine(prs):
    """Slide 12 — P-score engine. ENGINE first, single worked example after."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "08  —  P-Score Engine")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12),
              "P-Score Constraint Engine", size=30)
    add_subtitle(s, Inches(0.6), Inches(1.4), Inches(12),
                 "How natural-language preferences become measurable checks")

    # ── Top half: THE ENGINE — schema + primitives table
    top = Inches(2.0)
    eng_w = Inches(12.1)
    eng_h = Inches(2.6)
    add_rect(s, Inches(0.6), top, eng_w, eng_h, fill=SURFACE, line_color=ACCENT)
    add_text(s, Inches(0.8), top + Inches(0.12), eng_w - Inches(0.4), Inches(0.3),
             "EACH P-CONSTRAINT IS A FOUR-PIECE OBJECT", font=MONO, size=10, color=ACCENT, bold=True)
    add_text(s, Inches(0.8), top + Inches(0.4), eng_w - Inches(0.4), Inches(0.4),
             [[("source_in_profile", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True}),
               ("  ·  exact phrase in the user's query that motivated this constraint",
                {"font": SANS, "size": 11, "color": MUTED})]])
    add_text(s, Inches(0.8), top + Inches(0.7), eng_w - Inches(0.4), Inches(0.4),
             [[("scope            ", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True}),
               ("  ·  which subset of the plan the rule applies to  ", {"font": SANS, "size": 11, "color": MUTED}),
               ("(all · activity_type=meal · category=museum · per_day · time_window=18:00–23:59)",
                {"font": MONO, "size": 10, "color": MUTED})]])
    add_text(s, Inches(0.8), top + Inches(1.0), eng_w - Inches(0.4), Inches(0.4),
             [[("condition        ", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True}),
               ("  ·  the predicate per element  ", {"font": SANS, "size": 11, "color": MUTED}),
               ("(has_tag, field op value, local_cuisine=1)",
                {"font": MONO, "size": 10, "color": MUTED})]])
    add_text(s, Inches(0.8), top + Inches(1.3), eng_w - Inches(0.4), Inches(0.4),
             [[("aggregation      ", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True}),
               ("  ·  how to reduce the predicate across scope  ", {"font": SANS, "size": 11, "color": MUTED})]])
    # Primitives box
    prim_top = top + Inches(1.7)
    add_rect(s, Inches(0.8), prim_top, eng_w - Inches(0.4), Inches(0.8), fill=SURFACE2, line=False)
    add_text(s, Inches(1.0), prim_top + Inches(0.1), eng_w - Inches(0.8), Inches(0.6),
             [[("at_least N", {"font": MONO, "size": 11, "color": MUTED}),
               ("       at most N elements satisfy", {"size": 10, "color": MUTED, "italic": True}),
               ("                ", {"size": 10})],
              [("at_most N", {"font": MONO, "size": 11, "color": WARN, "bold": True}),
               ("        cap — \"no more than\"", {"size": 10, "color": MUTED, "italic": True}),
               ("    ", {"size": 10}),
               ("ratio R", {"font": MONO, "size": 11, "color": ACCENT, "bold": True}),
               ("          fraction — \"majority\" / \"spread\"", {"size": 10, "color": MUTED, "italic": True})],
              [("count_distinct N", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True}),
               ("    N distinct values — \"variety\"", {"size": 10, "color": MUTED, "italic": True}),
               ("   ", {"size": 10}),
               ("sum X op V", {"font": MONO, "size": 11, "color": PURPLE, "bold": True}),
               ("     totals — \"budget ≤\" / \"≥ N kcal\"", {"size": 10, "color": MUTED, "italic": True})]])

    # ── Bottom half: full worked example with all 3 PCs of "best task in dataset"
    ex_top = Inches(4.85)
    ex_h = Inches(2.4)
    add_rect(s, Inches(0.6), ex_top, eng_w, ex_h, fill=SURFACE, line_color=ACCENT3)
    add_text(s, Inches(0.8), ex_top + Inches(0.1), eng_w - Inches(0.4), Inches(0.3),
             "WORKED EXAMPLE  —  lon_type5_booking_westminster  ·  one task, three different primitives",
             font=MONO, size=10, color=ACCENT3, bold=True)
    # The query
    add_rect(s, Inches(0.8), ex_top + Inches(0.42), Inches(11.7), Inches(0.45),
             fill=SURFACE2, line=False)
    add_text(s, Inches(0.95), ex_top + Inches(0.48), Inches(11.4), Inches(0.4),
             "“1-day Westminster itinerary. Every place must require advance reservation. At least one restaurant should not be too intense so we can catch our breath.”",
             font=SANS, size=11, color=TEXT, italic=True)
    # 3 PCs — each shows phrase → engine encoding
    pc_top = ex_top + Inches(0.95)
    pcs = [
        ("\"requires an advance reservation\"",
         "scope: all  ·  condition: { field: reservation_required, op: ==, value: true }  ·  agg: all",
         ACCENT),
        ("\"only want to visit places in Westminster\"",
         "scope: all  ·  condition: { field: district, op: ==, value: \"Westminster\" }  ·  agg: all",
         ACCENT3),
        ("\"catch our breath\"  →  hop-2 inference",
         "scope: category=restaurant  ·  condition: { field: recommended_pace, op: <=, value: \"moderate\" }  ·  agg: at_least 1",
         WARN),
    ]
    for i, (phrase, encoding, color) in enumerate(pcs):
        ty = pc_top + Inches(0.45) * i
        add_text(s, Inches(0.8), ty + Inches(0.02), Inches(4.5), Inches(0.22),
                 "·  " + phrase, font=SANS, size=10, color=color, italic=True, bold=True)
        add_text(s, Inches(0.8), ty + Inches(0.22), Inches(11.7), Inches(0.22),
                 encoding, font=MONO, size=9.5, color=MUTED)


def slide_paper_compare(prs):
    """Slide 13 — task examples side by side with explicit gap/edge framing."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "10  —  Positioning")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "Task examples vs. recent benchmarks", size=22)
    add_text(s, Inches(0.6), Inches(1.05), Inches(12), Inches(0.25),
             "Real quotes.  Each highlights what their format CAN'T express that ours can.",
             font=MONO, size=10, color=MUTED)

    # 2×2 grid — tighter
    top = Inches(1.45)
    card_w = Inches(6.05)
    card_h = Inches(2.85)
    gap = Inches(0.12)

    benchmarks = [
        {
            "paper": "DeepPlanning  ·  Zhang et al. (Alibaba Qwen)",
            "id": "arXiv 2601.18137 · Jan 2026",
            "color": ACCENT,
            "query": "“Plan our Xi'an→Beijing trip Nov 12–18. Four of us, 2 medium rooms. Total within $3,600. 3-star is fine, but washing machine + on-site pool would be great.”",
            "gap_label": "THEIR GAP",
            "gap": "Soft prefs (washing machine + pool) have no partial-credit scoring. Plan is graded against checklists not constraint primitives.",
        },
        {
            "paper": "TravelBench (Cheng et al.)  ·  Renmin / NUS / AMAP",
            "id": "arXiv 2512.22673 · Dec 2025",
            "color": ACCENT3,
            "query": "“Which railway station near 'Xinfeng West Station' is the nearest and the only railway station I can get off?”   (turn 2 of a multi-turn dialog)",
            "gap_label": "THEIR GAP",
            "gap": "Asks the agent to infer \"nearest\" + \"only\" implicitly. Scoring requires LLM-as-judge (κ=0.83) — reproducible but ungraded by code. No formal constraint engine.",
        },
        {
            "paper": "VitaBench  ·  Meituan LongCat",
            "id": "arXiv 2509.26490 · Sep 2025",
            "color": WARN,
            "query": "“Book their usual hotel with river view, and a romantic dinner for him and his wife — $200 budget, near Huangpu River where they first met, 10th anniversary.”  Profile: age 35-45, dietary=non-spicy.",
            "gap_label": "THEIR GAP",
            "gap": "\"Usual hotel\" / \"romantic\" / \"where they first met\" all require profile inference + ambience judgment. Rubric items are atomic-binary (booked or not), not graded across a primitive vocabulary.",
        },
        {
            "paper": "Ours  ·  TravelBench  ·  NYC Type 4",
            "id": "Generated + tension-validated · scored by C/F/P engine",
            "color": ACCENT2,
            "query": "“2-day NYC trip on a strict budget of $24 per day. Keep admission near zero with free-entry sights, avoid tourist traps, budget-priced meals only.”",
            "gap_label": "OUR EDGE",
            "gap": "All 4 constraints are per-day deterministic: sum ≤ $24 · all visits free-entry · none tourist-trap · all meals budget price_tier. Primitive variety the others don't have (sum + at_most + all + none). Code-graded, reproducible, traceable to a single phrase each.",
        },
    ]

    for i, b in enumerate(benchmarks):
        col, row = i % 2, i // 2
        l = Inches(0.6) + (card_w + gap) * col
        t = top + (card_h + gap) * row
        is_ours = (i == 3)
        fill = RGBColor(0x12, 0x18, 0x2E) if is_ours else SURFACE
        add_rect(s, l, t, card_w, card_h, fill=fill, line_color=b["color"], line_width=1.5 if is_ours else 0.75)
        # Header
        add_text(s, l + Inches(0.18), t + Inches(0.08), card_w - Inches(0.36), Inches(0.25),
                 b["paper"], font=SANS, size=11.5, color=b["color"], bold=True)
        add_text(s, l + Inches(0.18), t + Inches(0.33), card_w - Inches(0.36), Inches(0.22),
                 b["id"], font=MONO, size=8.5, color=MUTED)
        # Quoted query
        add_rect(s, l + Inches(0.18), t + Inches(0.6), card_w - Inches(0.36), Inches(1.05),
                 fill=SURFACE2, line=False)
        add_text(s, l + Inches(0.28), t + Inches(0.66), card_w - Inches(0.56), Inches(0.95),
                 b["query"], font=SANS, size=9.5, color=TEXT, italic=True)
        # Gap / edge line
        add_text(s, l + Inches(0.18), t + Inches(1.75), card_w - Inches(0.36), Inches(0.22),
                 b["gap_label"], font=MONO, size=9, color=b["color"], bold=True)
        add_text(s, l + Inches(0.18), t + Inches(1.97), card_w - Inches(0.36), Inches(0.85),
                 b["gap"], font=SANS, size=9.5, color=TEXT if is_ours else MUTED)


def slide_task_target(prs):
    """NEW — shown just before the task-gen sandbox. The audience sees the
    actual generated task FIRST so they understand what the sandbox is building."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "11  —  Task-Generation Sandbox")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "First — what the agent is trying to produce", size=22)
    add_text(s, Inches(0.6), Inches(1.05), Inches(12), Inches(0.3),
             "The next 7 slides walk through the generation of this one task. Read it before watching the build.",
             font=SANS, size=12, color=MUTED, italic=True)

    # Big task card showing the FINAL artifact
    top = Inches(1.65)
    add_rect(s, Inches(0.6), top, Inches(12.1), Inches(5.5),
             fill=SURFACE, line_color=ACCENT3)
    add_text(s, Inches(0.8), top + Inches(0.15), Inches(11.7), Inches(0.3),
             "GENERATED TASK", font=MONO, size=10, color=ACCENT3, bold=True)

    # Task id + meta row
    add_text(s, Inches(0.8), top + Inches(0.45), Inches(11.7), Inches(0.3),
             [[("task_id  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("gpt_5_4_new_york_type4_20260521_1779375371",
                {"font": MONO, "size": 11, "color": TEXT, "bold": True})]])
    add_text(s, Inches(0.8), top + Inches(0.75), Inches(11.7), Inches(0.3),
             [[("type     ", {"font": MONO, "size": 10, "color": MUTED}),
               ("type4_precision_allocation", {"font": MONO, "size": 11, "color": WARN, "bold": True}),
               ("    window  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("Independence Day 2026  (Jul 3–4)", {"font": MONO, "size": 11, "color": TEXT}),
               ("    days  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("2", {"font": MONO, "size": 11, "color": TEXT, "bold": True}),
               ("    budget  ", {"font": MONO, "size": 10, "color": MUTED}),
               ("$24/day", {"font": MONO, "size": 11, "color": ACCENT3, "bold": True})]])

    # The user-facing query (the only thing the solving agent sees)
    add_text(s, Inches(0.8), top + Inches(1.2), Inches(11.7), Inches(0.3),
             "public_input.query  ·  the only field the solving agent sees:",
             font=MONO, size=10, color=MUTED)
    add_rect(s, Inches(0.8), top + Inches(1.5), Inches(11.7), Inches(0.95),
             fill=SURFACE2, line=False)
    add_text(s, Inches(1.0), top + Inches(1.6), Inches(11.3), Inches(0.85),
             "“I'm doing a 2-day New York trip over Independence Day weekend on a strict budget of $24 per day. I want to keep admission costs near zero by using free-entry sights, I want to avoid tourist traps, and I only want budget-priced meals.”",
             font=SERIF, size=14, color=TEXT, italic=True)

    # P-constraints (the hidden rubric the evaluator uses)
    add_text(s, Inches(0.8), top + Inches(2.65), Inches(11.7), Inches(0.3),
             "rubric.personal_constraints  ·  the hidden rubric:",
             font=MONO, size=10, color=MUTED)
    pcs = [
        ("pc_001", "hop-2", "Total estimated cost per day must stay within the $24 budget",
         "sum: estimated_cost_local · operator: <=  · value: 24  · scope: per_day"),
        ("pc_002", "hop-1", "All visit activities must be at free-entry venues",
         "all  ·  scope: activity_type=visit  ·  condition: has_tag = free-entry"),
        ("pc_003", "hop-2", "No venue in the plan may be tagged as a tourist trap",
         "none  ·  scope: all  ·  condition: has_tag = tourist-trap"),
        ("pc_004", "hop-1", "All meal activities must be at budget-priced venues",
         "all  ·  scope: activity_type=meal  ·  condition: price_tier <= budget"),
    ]
    py = top + Inches(2.95)
    for pid, hop, desc, agg in pcs:
        add_rect(s, Inches(0.8), py, Inches(11.7), Inches(0.55), fill=SURFACE, line=BORDER)
        add_text(s, Inches(0.95), py + Inches(0.07), Inches(11.4), Inches(0.25),
                 [[(pid, {"font": MONO, "size": 10, "color": ACCENT, "bold": True}),
                   ("   ", {"size": 10}),
                   (f"[{hop}]", {"font": MONO, "size": 9, "color": WARN}),
                   ("   ", {"size": 10}),
                   (desc, {"font": SANS, "size": 11, "color": TEXT})]])
        add_text(s, Inches(0.95), py + Inches(0.31), Inches(11.4), Inches(0.22),
                 agg, font=MONO, size=9.5, color=MUTED)
        py += Inches(0.6)


# ─────────────────────────────────────────────────────────────────
# NEW slides — Act 4 (solvability, A×A, B×B)
# ─────────────────────────────────────────────────────────────────

def slide_solvability_opener(prs):
    """Slide 22 — the orientation question."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "13  —  Solvability")
    add_title(s, Inches(0.6), Inches(1.2), Inches(12),
              "How do we verify at least one", size=44)
    add_title(s, Inches(0.6), Inches(2.0), Inches(12),
              "valid plan exists for the task?", size=44)

    # Three sub-answers
    top = Inches(3.7)
    h = Inches(1.0)
    answers = [
        ("(a)", "Hard-constraint check",
         "Every all-aggregated filter (wheelchair, district, regulation) must leave at least one venue in the pool."),
        ("(b)", "Tension-partner check",
         "When two P-constraints pull at disjoint subsets, do their feasible-plan sets still intersect?  ←  A×A and B×B"),
        ("(c)", "Upper-bar verification",
         "The task-generation agent must submit a candidate plan that passes the validator before the task is published."),
    ]
    for i, (n, t, body) in enumerate(answers):
        y = top + (h + Inches(0.15)) * i
        add_rect(s, Inches(0.6), y, Inches(12.1), h, fill=SURFACE, line_color=ACCENT)
        add_text(s, Inches(0.8), y + Inches(0.2), Inches(1.0), Inches(0.6),
                 n, font=SERIF, size=28, color=ACCENT, align=PP_ALIGN.CENTER)
        add_text(s, Inches(2.0), y + Inches(0.15), Inches(4.0), Inches(0.35),
                 t, font=SANS, size=14, color=TEXT, bold=True)
        add_text(s, Inches(2.0), y + Inches(0.55), Inches(10.0), Inches(0.4),
                 body, font=SANS, size=11, color=MUTED)


def slide_axa_bxb_intro(prs):
    """Slide 23 — what A×A and B×B mean."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.4), "14  —  Solvability")
    add_title(s, Inches(0.6), Inches(0.75), Inches(12),
              "Two Constraint Geometries", size=30)
    add_subtitle(s, Inches(0.6), Inches(1.4), Inches(12),
                 "Why these two checks are enough")

    # Side-by-side A vs B explanation
    top = Inches(2.1)
    card_h = Inches(3.3)
    card_w = Inches(6.0)

    # A — pool-narrowing
    add_rect(s, Inches(0.6), top, card_w, card_h, fill=SURFACE, line_color=ACCENT)
    add_text(s, Inches(0.8), top + Inches(0.15), card_w - Inches(0.4), Inches(0.4),
             "A  ·  Pool-filter primitives",
             font=SERIF, size=18, color=ACCENT)
    add_text(s, Inches(0.8), top + Inches(0.65), card_w - Inches(0.4), Inches(0.45),
             "all  ·  none   (and at_least with universal scope, treated as a filter)",
             font=MONO, size=11, color=ACCENT3)
    add_text(s, Inches(0.8), top + Inches(1.2), card_w - Inches(0.4), Inches(1.15),
             "Each A-constraint REMOVES venues from the single pool. Stacking multiple A-constraints intersects their qualifying sets. The schedule is then drawn from the narrowed pool.",
             font=SANS, size=11, color=MUTED)
    add_rect(s, Inches(0.8), top + Inches(2.45), card_w - Inches(0.4), Inches(0.7),
             fill=SURFACE2, line=False)
    add_text(s, Inches(0.95), top + Inches(2.55), card_w - Inches(0.6), Inches(0.55),
             "A × A check: after stacking, is the pool still big enough to draw a plan from? (Type 3 adds a special-case disjointness check for competing pairs.)",
             font=SANS, size=10.5, color=TEXT)

    # B — plan-space narrowing
    l2 = Inches(0.6) + card_w + Inches(0.2)
    add_rect(s, l2, top, card_w, card_h, fill=SURFACE, line_color=WARN)
    add_text(s, l2 + Inches(0.2), top + Inches(0.15), card_w - Inches(0.4), Inches(0.4),
             "B  ·  Plan-structure primitives",
             font=SERIF, size=18, color=WARN)
    add_text(s, l2 + Inches(0.2), top + Inches(0.65), card_w - Inches(0.4), Inches(0.45),
             "at_least N  ·  at_most N  ·  ratio R  ·  count_distinct N  ·  sum X op V",
             font=MONO, size=11, color=ACCENT3)
    add_text(s, l2 + Inches(0.2), top + Inches(1.2), card_w - Inches(0.4), Inches(1.15),
             "B-constraints DON'T touch the pool. They constrain the schedule itself — what counts, what totals, what ratios. Each shrinks the FRACTION of plans that satisfy it.",
             font=SANS, size=11, color=MUTED)
    add_rect(s, l2 + Inches(0.2), top + Inches(2.45), card_w - Inches(0.4), Inches(0.7),
             fill=SURFACE2, line=False)
    add_text(s, l2 + Inches(0.35), top + Inches(2.55), card_w - Inches(0.6), Inches(0.55),
             "B × B check: with all B-constraints stacked, how many valid plans remain?  baseline × ∏ fᵢ  →  must be ≥ a small threshold.",
             font=SANS, size=10.5, color=TEXT)

    # Footer — why two is enough
    cy = top + card_h + Inches(0.25)
    add_rect(s, Inches(0.6), cy, Inches(12.1), Inches(1.0),
             fill=RGBColor(0x12, 0x18, 0x2E), line_color=ACCENT)
    add_text(s, Inches(0.8), cy + Inches(0.12), Inches(11.7), Inches(0.3),
             "WHY THESE TWO ARE ENOUGH",
             font=MONO, size=10, color=ACCENT, bold=True)
    add_text(s, Inches(0.8), cy + Inches(0.4), Inches(11.7), Inches(0.55),
             "Every P-constraint primitive falls in exactly one class. A acts on the pool, B acts on the schedule. They compose cleanly: A first narrows the venue set, then B narrows the schedule space drawn from it.",
             font=SANS, size=10.5, color=MUTED)


# ─────────────────────────────────────────────────────────────────
# NEW slides — replacements for slide_pscore (A×A and B×B details)
# ─────────────────────────────────────────────────────────────────

def slide_axa_detail(prs):
    """Slide 24 — A×A worked example using Type 5 (stacked universal filters)."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "15  —  A × A Pool Narrowing")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "A × A  —  do stacked pool filters leave any venue?", size=22)
    add_subtitle(s, Inches(0.6), Inches(1.05), Inches(12),
                 "Real example: NYC Type 5 task — wheelchair + budget + cafe-lover")

    # Show the actual task query first
    add_rect(s, Inches(0.6), Inches(1.55), Inches(12.1), Inches(0.6),
             fill=SURFACE2, line=False)
    add_text(s, Inches(0.8), Inches(1.65), Inches(11.7), Inches(0.45),
             "“I'll be using a wheelchair and I'm strictly on a budget — no expensive places. Also, I love cafes.”",
             font=SERIF, size=13, color=TEXT, italic=True)

    # Two universal-scope filters (the at_least cafe lives in a separate pool)
    cy = Inches(2.3)
    add_text(s, Inches(0.6), cy, Inches(12.1), Inches(0.25),
             "TWO UNIVERSAL-SCOPE (\"all\") FILTERS  —  the A × A pair under test",
             font=MONO, size=10, color=ACCENT, bold=True)
    constraints = [
        ("A1", "wheelchair_accessible = true",   "all  ·  scope = all venues",  ACCENT),
        ("A2", "price_tier ≤ budget",            "all  ·  scope = all venues",  ACCENT3),
    ]
    cc_top = cy + Inches(0.3)
    for i, (cid, cond, agg, color) in enumerate(constraints):
        ty = cc_top + Inches(0.4) * i
        add_rect(s, Inches(0.6), ty, Inches(12.1), Inches(0.35), fill=SURFACE, line=BORDER)
        add_text(s, Inches(0.75), ty + Inches(0.07), Inches(0.6), Inches(0.22),
                 cid, font=MONO, size=11, color=color, bold=True)
        add_text(s, Inches(1.35), ty + Inches(0.07), Inches(5.5), Inches(0.22),
                 cond, font=MONO, size=11, color=TEXT)
        add_text(s, Inches(7.0), ty + Inches(0.07), Inches(5.7), Inches(0.22),
                 agg, font=MONO, size=10, color=MUTED)
    # Footnote about the separate at_least cafe constraint
    add_text(s, Inches(0.6), cc_top + Inches(0.85), Inches(12.1), Inches(0.22),
             "(The 3rd P-constraint  at_least: 1 cafe  is a positive-inclusion check on its own pool — handled separately, not part of A × A.)",
             font=MONO, size=9, color=MUTED, italic=True)

    # ── Narrowing cascade
    cs_top = Inches(4.0)
    add_text(s, Inches(0.6), cs_top, Inches(12.1), Inches(0.25),
             "POOL NARROWING  —  applying each filter to the 70-venue NYC pool",
             font=MONO, size=10, color=ACCENT, bold=True)

    stages = [
        ("baseline",                            70, MUTED,   "all NYC venues"),
        ("∩ A1  wheelchair_accessible",         22, ACCENT,  "Brooklyn Bridge Park · The High Line · 9/11 Memorial · Empire State · Whitney · Times Square · …"),
        ("∩ A1 ∩ A2  + price ≤ budget",         18, ACCENT3, "NY Stock Exchange · Brooklyn Bridge Park · The High Line · Times Square · Brooklyn Bridge · …"),
    ]
    sy = cs_top + Inches(0.28)
    bar_max = Inches(2.6)
    for label, count, color, sample in stages:
        add_rect(s, Inches(0.6), sy, Inches(12.1), Inches(0.5), fill=SURFACE, line=BORDER)
        # Stage name
        add_text(s, Inches(0.75), sy + Inches(0.06), Inches(3.4), Inches(0.22),
                 label, font=MONO, size=10.5, color=color, bold=True)
        # Bar
        bar_w = max(Inches(0.1), Emu(int(bar_max * count / 70)))
        add_rect(s, Inches(4.2), sy + Inches(0.15), bar_max, Inches(0.2),
                 fill=SURFACE2, line=False)
        add_rect(s, Inches(4.2), sy + Inches(0.15), bar_w, Inches(0.2),
                 fill=color, line=False)
        # Count
        add_text(s, Inches(6.9), sy + Inches(0.1), Inches(0.6), Inches(0.3),
                 str(count), font=MONO, size=14, color=color, bold=True)
        # Sample venues
        add_text(s, Inches(7.6), sy + Inches(0.15), Inches(5.0), Inches(0.32),
                 sample, font=MONO, size=8.5, color=MUTED, italic=True)
        sy += Inches(0.55)

    # ── Bottom: the A×A check, formula + verdict
    bt = sy + Inches(0.08)
    add_rect(s, Inches(0.6), bt, Inches(12.1), Inches(0.6),
             fill=RGBColor(0x12, 0x2A, 0x22), line_color=ACCENT3)
    add_text(s, Inches(0.8), bt + Inches(0.05), Inches(11.7), Inches(0.25),
             "A × A CHECK", font=MONO, size=10, color=ACCENT3, bold=True)
    add_text(s, Inches(0.8), bt + Inches(0.3), Inches(11.7), Inches(0.28),
             [[("intersection  |A1 ∩ A2|  ≥ 1", {"font": MONO, "size": 11, "color": TEXT, "bold": True}),
               ("   →   18 venues qualify ✓     Solvable for the universal-filter pair.",
                {"font": MONO, "size": 11, "color": ACCENT3})]])


def slide_bxb_detail(prs):
    """Slide 25 — B×B with NYC Type 3: 4 at_least's + count_distinct + per_day."""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(s, BG)
    add_label(s, Inches(0.6), Inches(0.3), "16  —  B × B Plan-Space Narrowing")
    add_title(s, Inches(0.6), Inches(0.55), Inches(12),
              "B × B  —  do counting + per-day constraints fit any schedule?", size=22)
    add_subtitle(s, Inches(0.6), Inches(1.05), Inches(12),
                 "Real example: NYC Type 3 — 4 at_least's + count_distinct + per_day park")

    # Task quote
    add_rect(s, Inches(0.6), Inches(1.55), Inches(12.1), Inches(0.6),
             fill=SURFACE2, line=False)
    add_text(s, Inches(0.8), Inches(1.63), Inches(11.7), Inches(0.5),
             "“2-day NYC trip Jul 4. Partner insists on the famous sites, I prefer hidden gems. As many different mid-priced cuisines as possible. One park visit every day.”",
             font=SERIF, size=12.5, color=TEXT, italic=True)

    # The four B-constraints
    cy = Inches(2.3)
    add_text(s, Inches(0.6), cy, Inches(12.1), Inches(0.25),
             "B-CONSTRAINTS  —  each narrows the SCHEDULE, not the venue pool",
             font=MONO, size=10, color=WARN, bold=True)
    bcs = [
        ("B1", "at_least: 4   condition: traffic_tier=high",            WARN,
         "≥ 4 famous-site visits across the 2 days"),
        ("B2", "at_least: 4   condition: traffic_tier=low",             ACCENT3,
         "≥ 4 hidden-gem visits across the 2 days"),
        ("B3", "at_least: 1   category=park   per_day",                 ACCENT,
         "park required EVERY day  (not just total)"),
        ("B4", "count_distinct: 3   field=cuisine   restaurants=mid",   PURPLE,
         "3 different mid-price cuisines across meal venues"),
    ]
    bt0 = cy + Inches(0.3)
    for i, (cid, expr, color, gloss) in enumerate(bcs):
        ty = bt0 + Inches(0.36) * i
        add_rect(s, Inches(0.6), ty, Inches(12.1), Inches(0.32), fill=SURFACE, line=BORDER)
        add_text(s, Inches(0.75), ty + Inches(0.06), Inches(0.5), Inches(0.22),
                 cid, font=MONO, size=11, color=color, bold=True)
        add_text(s, Inches(1.3), ty + Inches(0.06), Inches(5.7), Inches(0.22),
                 expr, font=MONO, size=10.5, color=TEXT)
        add_text(s, Inches(7.1), ty + Inches(0.06), Inches(5.6), Inches(0.22),
                 gloss, font=MONO, size=10, color=MUTED, italic=True)

    # Plan-space narrowing — baseline + hypergeometric per-constraint factors
    body_top = Inches(4.55)
    add_rect(s, Inches(0.6), body_top, Inches(12.1), Inches(2.0), fill=SURFACE, line=BORDER)
    add_text(s, Inches(0.8), body_top + Inches(0.1), Inches(11.7), Inches(0.25),
             "HYPERGEOMETRIC NARROWING  ·  baseline × ∏ fᵢ  ·  scipy.stats.hypergeom.sf in the validator",
             font=MONO, size=10, color=WARN, bold=True)

    # Cascade rows — show probability formula + computed factor + running plan count
    # Numbers computed from scipy.stats.hypergeom on the actual NYC pool
    # (35 food + 35 site venues, K_food=K_site=4 for the 2-day trip).
    stages = [
        # label                            factor expression                       computed   running count    bar frac  color
        ("baseline    K_food=4, K_site=4", "C(35,4) · C(35,4)",                    "",         "≈ 2.74 × 10⁹",   1.0,    MUTED),
        ("× B1  ≥ 4 high  in K=8",         "1 − F_hg(3; M=70, n=18, N=8)",         "= 0.111",  "≈ 3.0 × 10⁸",   0.111,  WARN),
        ("× B2  ≥ 4 low   in K=8",         "1 − F_hg(3; M=70, n=23, N=8)",         "= 0.238",  "≈ 7.2 × 10⁷",   0.0264, ACCENT3),
        ("× B3  ≥ 1 park each day",        "[1 − pmf(0; 35, 8, 2)] ²  per_day",    "= 0.168",  "≈ 1.2 × 10⁷",   0.00444, ACCENT),
        ("× B4  3 distinct cuisines",      "P_coupon · P(≥3 mid-meals in K=4)",    "= 0.020",  "≈ 2.4 × 10⁵",   0.0000871, PURPLE),
    ]
    sy = body_top + Inches(0.38)
    bar_max = Inches(2.4)
    for label, expr, factor, count, frac, color in stages:
        add_text(s, Inches(0.8), sy, Inches(2.9), Inches(0.22),
                 label, font=MONO, size=9.5, color=color, bold=True)
        add_text(s, Inches(3.7), sy, Inches(3.5), Inches(0.22),
                 expr, font=MONO, size=9, color=MUTED)
        add_text(s, Inches(7.3), sy, Inches(1.1), Inches(0.22),
                 factor, font=MONO, size=10, color=color, bold=True)
        # bar (log-scaled visual — small narrowings still visible)
        from math import log10
        log_frac = (log10(frac) + 5) / 5 if frac > 0 else 0  # maps 1.0 → 1.0, 1e-5 → 0
        log_frac = max(0.02, min(1.0, log_frac))
        bw = max(Inches(0.05), Emu(int(bar_max * log_frac)))
        add_rect(s, Inches(8.5), sy + Inches(0.05), bar_max, Inches(0.16),
                 fill=SURFACE2, line=False)
        add_rect(s, Inches(8.5), sy + Inches(0.05), bw, Inches(0.16),
                 fill=color, line=False)
        # running count (running cumulative)
        add_text(s, Inches(11.0), sy, Inches(1.7), Inches(0.22),
                 count, font=MONO, size=10, color=TEXT,
                 bold=(label.startswith("baseline") or "× B4" in label))
        sy += Inches(0.3)

    # Tiny footnote
    add_text(s, Inches(0.8), body_top + Inches(1.78), Inches(11.7), Inches(0.18),
             "F_hg = hypergeometric CDF.  Independence-product is an upper bound — correlated constraints narrow more.",
             font=MONO, size=8.5, color=MUTED, italic=True)

    # Bottom verdict
    bt = body_top + Inches(2.1)
    add_rect(s, Inches(0.6), bt, Inches(12.1), Inches(0.65),
             fill=RGBColor(0x2A, 0x21, 0x12), line_color=WARN)
    add_text(s, Inches(0.8), bt + Inches(0.08), Inches(11.7), Inches(0.25),
             "B × B CHECK  ·  combined narrowing", font=MONO, size=10, color=WARN, bold=True)
    add_text(s, Inches(0.8), bt + Inches(0.33), Inches(11.7), Inches(0.28),
             [[("f_joint ≈ 8.7 × 10⁻⁵   ", {"font": MONO, "size": 12, "color": WARN, "bold": True}),
               ("→  ≈ 2.4 × 10⁵ valid plans   (above the 2,000-plan lower bar).  ", {"font": MONO, "size": 11, "color": MUTED}),
               ("4 orders of magnitude narrower than baseline — tight but solvable.",
                {"font": MONO, "size": 11, "color": TEXT, "bold": True})]])


# ─────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────

def build():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    # ACT 1 — What is TravelBench
    slide_title(prs)                # 1
    slide_overview(prs)             # 2

    # ACT 2 — Venue / city world
    slide_dataset(prs)              # 3   city + venue resources overview
    slide_venue_polished(prs)       # 4   Corkbuzz hook
    slide_venue_sandbox(prs)        # 5-9 per-venue agent walkthrough (5 steps)
    slide_truth_carrier(prs)        # 10  mechanism explanation
    # slide_gt_vs_faulty(prs)       # REMOVED 2026-06-06 — pilot signal too confounded
    #                                 by venue-selection drift; trap mostly targets F-score
    #                                 fields not P-score fields. See scripts/_pilot_analysis.md.

    # ACT 3 — Task side
    slide_pscore_engine(prs)        # 12  P-score engine + real examples
    slide_task_types(prs)           # 13  6 best NYC tasks per type
    slide_paper_compare(prs)        # 14  vs DeepPlanning / TravelBench-Cheng / VitaBench
    slide_task_target(prs)          # 15  what the sandbox is about to build
    slide_sandbox(prs)              # 16-22 task-gen sandbox (7 steps, NYC type 4)

    # ACT 4 — Solvability
    slide_solvability_opener(prs)   # 22  the orientation question
    slide_axa_bxb_intro(prs)        # 23  A×A vs B×B explained
    slide_axa_detail(prs)           # 24  A×A worked example
    slide_bxb_detail(prs)           # 25  B×B worked example

    # ACT 5 — Evaluation
    slide_scoring(prs)                # 26  C/F/P mechanism
    slide_solving_task_target(prs)    # 27  task content shown before walkthrough
    slide_eval_comparison(prs)        # 28-33  Gemini-Pro walkthrough (6 phases)
    slide_eval_weak_divergence(prs)   # 34  GLM-4-7 divergence on same task
    slide_leaderboard(prs)            # 35  current results

    # ACT 6 — Where next
    slide_future(prs)               # 34  three new directions

    out_path = Path(__file__).resolve().parents[1] / "travelbench_overview.pptx"
    prs.save(out_path)
    print(f"Saved {out_path}  ·  {len(prs.slides)} slides")
    return out_path


if __name__ == "__main__":
    build()
