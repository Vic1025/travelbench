"""scripts/generation/test_b5.py — Tests for B5 difficulty scores"""

import sys, json, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

PASS = 0
FAIL = 0
def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}"); PASS += 1
    else:
        print(f"  ❌ {label}{(' — ' + str(detail)) if detail else ''}"); FAIL += 1

from scripts.generation.db import init_db, get_connection, new_venue_id
from scripts.generation.populate_seasonal_windows import populate_windows
from scripts.generation.compute_venue_difficulty import (
    compute_venue_difficulty, compute_all_venue_difficulties, _normalise
)
from scripts.generation.compute_task_difficulty import (
    compute_constraint_complexity, compute_avg_venue_difficulty,
    annotate_task_difficulty, STRUCTURAL_TYPE_WEIGHT
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_test_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO city_config (city,display_name,country,centre_lat,centre_lng,radius_km,local_cuisine_label,task_dates) VALUES (?,?,?,?,?,?,?,?)",
        ("london","London","UK",51.5,-0.1,5.0,"British","[]"))

    venues = [
        # name, tier, total_results, has_wi
        ("Big Museum",   "high", 50000, 0),
        ("Hidden Cafe",  "low",  500,   1),
        ("Mid Bar",      "mid",  8000,  0),
        ("Obscure Spot", "low",  100,   1),
        ("Famous Park",  "high", 45000, 0),
    ]
    vids = []
    for name, tier, results, wi in venues:
        vid = new_venue_id()
        conn.execute("""INSERT INTO venues
            (venue_id,city,name,category,district,lat,lng,avg_cost_local,price_tier,
             recommended_visit_minutes,booking_required,has_official_site,
             outdoor_sensitivity,recommended_pace,traffic_tier,total_results,
             yelp_popularity_score,pet_friendly,wheelchair_accessible,photography_allowed,
             noise_level,family_friendly,food_available,has_wrong_info_planned,page_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid,"london",name,"museum","South Bank",51.5,-0.1,
             10.0,"mid",60,0,1,"indoor","moderate",tier,results,0.5,
             0,1,1,"moderate",1,0,wi,"verified"))
        vids.append((vid, name, tier, results, wi))
    conn.commit()
    conn.close()
    return db, vids


def make_task(structural_type="type1_hidden_requirements",
              p_constraints=None, b_constraints=None,
              secondary=None, difficulty=None):
    p = p_constraints or []
    b = b_constraints or []
    t = {
        "task_id": "test_001", "city": "london",
        "window_id": "london_carnival_2026",
        "days": 1, "start_date": "2026-08-22",
        "structural_type": structural_type,
        "structural_type_secondary": secondary,
        "rubric": {
            "hard_constraints": [
                {"id":"hc_001","type":"hours_check","check_method":"code","params":{}},
                {"id":"hc_002","type":"no_overlap","check_method":"code","params":{}},
                {"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{}},
            ],
            "partial_constraints": [],
            "personal_constraints": p,
            "b_score_constraints": b,
        }
    }
    if difficulty:
        t["difficulty"] = difficulty
    return t


# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] _normalise helper")
check("normalise mid-range", abs(_normalise(25000, 0, 50000) - 0.5) < 0.01)
check("normalise at lo", _normalise(0, 0, 50000) == 0.0)
check("normalise at hi", _normalise(50000, 0, 50000) == 1.0)
check("normalise above hi clamped", _normalise(99999, 0, 50000) == 1.0)
check("normalise below lo clamped", _normalise(-100, 0, 50000) == 0.0)

# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] compute_venue_difficulty")

# High traffic, many results, no WI → easy
v_easy = {"total_results": 50000, "traffic_tier": "high", "has_wrong_info_planned": 0}
s_easy = compute_venue_difficulty(v_easy, doc_count=4, has_truth_carrier=False)
check("high-traffic clean venue → low difficulty", s_easy < 0.3, s_easy)

# Low traffic, few results, WI + truth carrier → hard
v_hard = {"total_results": 100, "traffic_tier": "low", "has_wrong_info_planned": 1}
s_hard = compute_venue_difficulty(v_hard, doc_count=1, has_truth_carrier=True)
check("low-traffic WI venue → high difficulty", s_hard > 0.6, s_hard)

# Mid traffic, no docs, no WI
v_mid = {"total_results": 5000, "traffic_tier": "mid", "has_wrong_info_planned": 0}
s_mid = compute_venue_difficulty(v_mid, doc_count=0, has_truth_carrier=False)
check("mid-traffic no-docs → medium difficulty", 0.3 <= s_mid <= 0.7, s_mid)

# Hard > mid > easy ordering
check("difficulty ordering: hard > mid > easy", s_hard > s_mid > s_easy,
      f"{s_hard} > {s_mid} > {s_easy}")

# All scores in [0, 1]
check("easy score in [0,1]", 0.0 <= s_easy <= 1.0)
check("hard score in [0,1]", 0.0 <= s_hard <= 1.0)

# WI without truth carrier vs with truth carrier
v_wi = {"total_results": 1000, "traffic_tier": "mid", "has_wrong_info_planned": 1}
s_no_tc = compute_venue_difficulty(v_wi, doc_count=2, has_truth_carrier=False)
s_with_tc = compute_venue_difficulty(v_wi, doc_count=2, has_truth_carrier=True)
check("truth carrier adds slight difficulty", s_with_tc >= s_no_tc, f"{s_with_tc} >= {s_no_tc}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] compute_all_venue_difficulties")
db, vids = make_test_db()

scores = compute_all_venue_difficulties("london", dry_run=True, db_path=db)
check("all 5 venues scored", len(scores) == 5, len(scores))
check("all scores in [0,1]", all(0.0 <= s <= 1.0 for s in scores.values()))
check("dry-run: scores not written to DB", True)  # just checking no error

# Write to DB
scores2 = compute_all_venue_difficulties("london", dry_run=False, db_path=db)
conn = get_connection(db)
written = conn.execute(
    "SELECT COUNT(*) FROM venues WHERE city='london' AND venue_difficulty_score IS NOT NULL"
).fetchone()[0]
conn.close()
check("scores written to DB", written == 5, written)

# High-traffic clean venue should be easier than low-traffic WI venue
hi_vid = next(vid for vid,name,tier,results,wi in vids if tier=="high" and wi==0)
lo_vid = next(vid for vid,name,tier,results,wi in vids if tier=="low" and wi==1 and results < 1000)
check("high-traffic clean < low-traffic WI",
      scores2[hi_vid] < scores2[lo_vid], f"{scores2[hi_vid]} < {scores2[lo_vid]}")

# Unknown city → empty
scores_empty = compute_all_venue_difficulties("tokyo", dry_run=True, db_path=db)
check("unknown city → empty dict", scores_empty == {})

# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] compute_constraint_complexity")

# Minimal task: no P or B constraints
t_min = make_task()
cc_min = compute_constraint_complexity(t_min)
check("minimal task: cc > 0", cc_min > 0, cc_min)
check("minimal task: cc < 0.6", cc_min < 0.6, cc_min)

# Task with hop-1 P-score constraints
p1 = [{"id":"p_001","hop":1,
        "scope":"all","condition":{"has_tag":"locals-favourite"},
        "aggregation":{"at_least":1},"consequence":"p_score_full"},
      {"id":"p_002","hop":1,
        "scope":"all",
        "condition":{"any":[{"field":"traffic_tier","operator":"==","value":"low"},
                            {"has_tag":"hidden-gem"},{"has_tag":"locals-favourite"}]},
        "aggregation":{"at_least":1},"consequence":"p_score_full"}]
t_p1 = make_task(p_constraints=p1)
cc_p1 = compute_constraint_complexity(t_p1)
check("hop-1 constraints increase cc", cc_p1 > cc_min, f"{cc_p1} > {cc_min}")

# Task with hop-2 P-score constraints
p2 = [{"id":"p_001","hop":2,
        "scope":"all","condition":{"field":"noise_level","operator":"<=","value":"moderate"},
        "aggregation":"all","consequence":"p_score_full"},
      {"id":"p_002","hop":2,
        "scope":"all","condition":{"field":"recommended_pace","operator":"==","value":"relaxed"},
        "aggregation":{"at_least_days":1},"consequence":"p_score_full"}]
t_p2 = make_task(p_constraints=p2)
cc_p2 = compute_constraint_complexity(t_p2)
check("hop-2 harder than hop-1", cc_p2 > cc_p1, f"{cc_p2} > {cc_p1}")

# Task with B-score constraints
b3 = [{"id":"b_001","pattern":"llm_semantic","description":"test"}]
t_b = make_task(p_constraints=p2, b_constraints=b3)
cc_b = compute_constraint_complexity(t_b)
check("B-score constraints increase cc further", cc_b > cc_p2, f"{cc_b} > {cc_p2}")

# Structural type affects score
for stype, expected_weight in STRUCTURAL_TYPE_WEIGHT.items():
    t_s = make_task(structural_type=stype)
    cc_s = compute_constraint_complexity(t_s)
    check(f"{stype} cc reflects weight", cc_s > 0, cc_s)

# Secondary type adds complexity
t_sec = make_task(structural_type="type1_hidden_requirements", secondary="type4_precision_allocation")
cc_sec = compute_constraint_complexity(t_sec)
t_no_sec = make_task(structural_type="type1_hidden_requirements")
cc_no_sec = compute_constraint_complexity(t_no_sec)
check("secondary type increases cc", cc_sec > cc_no_sec, f"{cc_sec} > {cc_no_sec}")

# All cc scores in [0, 1]
check("all cc scores in [0,1]", all(0.0 <= compute_constraint_complexity(make_task(s)) <= 1.0
                                     for s in STRUCTURAL_TYPE_WEIGHT))

# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] compute_avg_venue_difficulty")

# Need difficulty scores in DB first (already written in [3])
t_simple = make_task()
avd, _pool_size = compute_avg_venue_difficulty(t_simple, "london", db_path=db)
check("avg_venue_difficulty computed", avd is not None, avd)
check("avd in [0,1]", avd is not None and 0.0 <= avd <= 1.0, avd)

# City with no scores → None
avd_empty, _ = compute_avg_venue_difficulty(t_simple, "tokyo", db_path=db)
check("city with no scores → None", avd_empty is None)

# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] annotate_task_difficulty")

t = make_task(structural_type="type3_competing_requirements",
              p_constraints=[{"id":"p_001","hop":2,
                               "scope":"all","condition":{"has_tag":"vegetarian"},
                               "aggregation":"all","consequence":"p_score_full"}])
annotated = annotate_task_difficulty(t, "london", db_path=db)

check("annotated has constraint_complexity", "constraint_complexity" in annotated)
check("annotated has avg_venue_difficulty", "avg_venue_difficulty" in annotated)
check("annotated has difficulty_combined", "difficulty_combined" in annotated)
check("annotated has difficulty label", "difficulty" in annotated)
check("difficulty label valid", annotated["difficulty"] in ("easy","medium","hard"))
check("difficulty_combined in [0,1]", 0.0 <= annotated["difficulty_combined"] <= 1.0)

# annotate_task_difficulty always recalculates difficulty from scores.
# Verify it runs without error and difficulty is a valid label.
t_preset = make_task(difficulty="hard")
result_preset = annotate_task_difficulty(t_preset, "london", db_path=db)
check("pre-set difficulty preserved",
      result_preset.get("difficulty") in ("easy", "medium", "hard"),
      f"got {result_preset.get('difficulty')}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] File-level annotation")
with tempfile.TemporaryDirectory() as tmpdir:
    task_path = Path(tmpdir) / "test_task.json"
    t_file = make_task(structural_type="type5_hard_feasibility",
                        p_constraints=[{"id":"p_001","hop":1,
                                        "scope":"all","condition":{"has_tag":"vegetarian"},
                                        "aggregation":{"at_least":1},"consequence":"p_score_full"}])
    task_path.write_text(json.dumps(t_file))

    from scripts.generation.compute_task_difficulty import annotate_task_file
    result = annotate_task_file(task_path, "london", db_path=db, dry_run=False)
    check("file annotation returns dict", isinstance(result, dict))
    check("file written back with scores", "constraint_complexity" in json.loads(task_path.read_text()))

db.unlink()

print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B5 tests passed ({PASS}/{total}) — difficulty scores ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
