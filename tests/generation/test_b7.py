"""scripts/generation/test_b7.py — B7 validate_city full mode tests"""

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
        print(f"  ❌ {label}{(' — '+str(detail)) if detail else ''}"); FAIL += 1

from scripts.generation.db import init_db, get_connection, new_venue_id, new_doc_id
from scripts.generation.validate_city import validate_city
from scripts.generation.populate_seasonal_windows import populate_windows
from scripts.generation.compute_venue_difficulty import compute_all_venue_difficulties


def make_test_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    conn = get_connection(db)
    conn.execute("""INSERT INTO city_config
        (city,display_name,country,centre_lat,centre_lng,radius_km,local_cuisine_label,task_dates)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("london","London","UK",51.5,-0.1,5.0,"British","[]"))

    # Insert enough venues for full validate
    vids = []
    for i in range(5):
        vid = new_venue_id()
        conn.execute("""INSERT INTO venues
            (venue_id,city,name,category,district,lat,lng,avg_cost_local,price_tier,
             recommended_visit_minutes,booking_required,has_official_site,
             outdoor_sensitivity,recommended_pace,traffic_tier,total_results,
             yelp_popularity_score,pet_friendly,wheelchair_accessible,photography_allowed,
             noise_level,family_friendly,food_available,has_wrong_info_planned,page_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid,"london",f"Venue {i}","museum",f"District {i}",51.5+i*0.01,-0.1+i*0.01,
             10.0,"mid",60,0,1,"indoor","moderate","mid",5000,0.5,
             0,1,1,"moderate",1,0,0,"verified"))
        vids.append(vid)

        # Add source doc
        doc = new_doc_id()
        conn.execute("""INSERT INTO source_docs
            (doc_id,city,doc_type,title,author,source_name,date,body,page_status)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (doc,"london","blog",f"Blog {i}","Author","Blog","2026-01-01",
             "A" * 200, "verified"))
        conn.execute("INSERT INTO doc_venue_refs (doc_id,venue_id) VALUES (?,?)", (doc,vid))

    conn.commit()
    conn.close()
    populate_windows("london", db_path=db)
    return db, vids


# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] avg_venue_difficulty check — unscored warning")
db, vids = make_test_db()

result = validate_city("london", full=True, db_path=db)
warnings = result.get("warnings", [])
unscored_warned = any("venue_difficulty_score" in w or "unscored" in w.lower()
                       for w in warnings)
check("unscored venues trigger warning", unscored_warned, warnings)
check("unscored_venues in stats", result["stats"].get("unscored_venues", -1) == 5)

# After scoring, warning should clear
compute_all_venue_difficulties("london", dry_run=False, db_path=db)
result2 = validate_city("london", full=True, db_path=db)
warnings2 = result2.get("warnings", [])
unscored_warned2 = any("venue_difficulty_score" in w or "unscored" in w.lower()
                        for w in warnings2)
check("after scoring: no unscored warning", not unscored_warned2, warnings2)
check("unscored_venues=0 in stats", result2["stats"].get("unscored_venues", -1) == 0)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Task set checks — no tasks")
result3 = validate_city("london", full=True, db_path=db)
check("task_count=0 when no tasks", result3["stats"].get("task_count", -1) == 0)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Task set checks — with tasks in temp dir")
with tempfile.TemporaryDirectory() as tmpdir:
    task_dir = Path(tmpdir)

    tasks_to_write = [
        {"task_id":"lon_001","city":"london","window_id":"london_carnival_2026",
         "structural_type":"type1_hidden_requirements","difficulty":"easy",
         "avg_venue_difficulty":0.4,"days":1,"start_date":"2026-08-22",
         "public_input":{"query":"q","user_profile":"p"},
         "rubric":{"hard_constraints":[],"partial_constraints":[],
                   "personal_constraints":[],"b_score_constraints":[]}},
        {"task_id":"lon_002","city":"london","window_id":"london_carnival_2026",
         "structural_type":"type2_subset_selection","difficulty":"medium",
         "avg_venue_difficulty":0.5,"days":1,"start_date":"2026-08-22",
         "public_input":{"query":"q","user_profile":"p"},
         "rubric":{"hard_constraints":[],"partial_constraints":[],
                   "personal_constraints":[],"b_score_constraints":[]}},
        {"task_id":"lon_003","city":"london","window_id":"london_easter_2026",
         "structural_type":"type3_competing_requirements","difficulty":"hard",
         "avg_venue_difficulty":None,"days":2,"start_date":"2026-04-02",
         "public_input":{"query":"q","user_profile":"p"},
         "rubric":{"hard_constraints":[],"partial_constraints":[],
                   "personal_constraints":[],"b_score_constraints":[]}},
    ]
    for t in tasks_to_write:
        (task_dir / f"{t['task_id']}.json").write_text(json.dumps(t))

    from scripts.generation.validate_city import validate_city as vc
    result4 = vc("london", full=True, db_path=db, task_dir=task_dir)
    stats4   = result4.get("stats", {})
    warnings4 = result4.get("warnings", [])

    check("task_count=3", stats4.get("task_count", 0) == 3, stats4.get("task_count"))
    check("structural_types_covered has 3 types",
          len(stats4.get("structural_types_covered", [])) == 3,
          stats4.get("structural_types_covered"))

    window_warned = any("task" in w.lower() and ("window" in w.lower() or "only" in w.lower())
                         for w in warnings4)
    check("low window coverage triggers warning", window_warned, warnings4)

    avd_warned = any("avg_venue_difficulty" in w for w in warnings4)
    check("missing avd triggers warning", avd_warned, warnings4)

    wc = stats4.get("window_task_counts", {})
    check("carnival has 2 tasks", wc.get("london_carnival_2026", 0) == 2, wc)
    check("easter has 1 task", wc.get("london_easter_2026", 0) == 1, wc)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] Existing validate tests still pass")
import subprocess
from pathlib import Path as _P
_repo_root = _P(__file__).resolve().parent.parent.parent
result_existing = subprocess.run(
    ["python3","scripts/generation/test_validate.py"],
    capture_output=True, text=True,
    cwd=str(_repo_root),
)
check("existing validate tests pass", "All A6 tests passed" in result_existing.stdout,
      result_existing.stdout.split("\n")[-2])

db.unlink()

print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B7 tests passed ({PASS}/{total}) — validate_city complete")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
