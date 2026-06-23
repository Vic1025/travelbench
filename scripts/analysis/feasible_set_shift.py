"""Free feasibility-shift confirm: apply literature-grounded flaw LAYERS to a city
pool and measure how the per-task feasible venue set diverges between GT and the
agent-believed (faulty) view. No API. See VALID_DIFFICULTY_REDESIGN.md §14.
Layers: accessibility-flip (optimistic), price-shift (stale-cheaper), free/paid confusion.
Density default 0.30 (Klinkhardt visibility gradient lower band). Deterministic per (layer,venue) seed.
Usage: python scripts/analysis/feasible_set_shift.py --db <corpus.db> --density 0.30
"""
import sqlite3, json, glob, collections, hashlib, argparse, os
PRICE_ORDER=["budget","mid","upscale","fine-dining"]
def seeded(vid,layer,p):
    h=int(hashlib.blake2b(f"{layer}|{vid}".encode(),digest_size=8).hexdigest(),16)/2**64
    return h<p
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--db",required=True); ap.add_argument("--density",type=float,default=0.30)
    ap.add_argument("--tasks-glob",default=None); a=ap.parse_args()
    c=sqlite3.connect(a.db); c.row_factory=sqlite3.Row
    venues={r["venue_id"]:dict(r) for r in c.execute("SELECT * FROM venues")}
    tags=collections.defaultdict(set)
    for r in c.execute("SELECT venue_id,tag FROM tags"): tags[r["venue_id"]].add(r["tag"])
    city=os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(a.db)))) if "runs" in a.db else "New_York"
    tg=a.tasks_glob or f"data/cities/New_York/tasks/runs/**/*.json"
    def layers(v,vid,d):
        w=dict(v); vt=set(tags[vid])
        if w.get("wheelchair_accessible") in (0,None,False) and seeded(vid,"access",d): w["wheelchair_accessible"]=1; vt.add("step-free")
        if w.get("price_tier") in PRICE_ORDER and seeded(vid,"price",d):
            i=PRICE_ORDER.index(w["price_tier"]); 
            if i>0: w["price_tier"]=PRICE_ORDER[i-1]
        if (w.get("avg_cost_local") or 0)>0 and seeded(vid,"free",d): w["avg_cost_local"]=0; vt.add("free-entry")
        return w,vt
    def ok(v,vt,cond):
        if "has_tag" in cond: return cond["has_tag"] in vt
        f=cond.get("field"); 
        if not f: return None
        val=v.get(f); op=cond.get("operator"); tgt=cond.get("value")
        if f=="price_tier" and val in PRICE_ORDER and tgt in PRICE_ORDER:
            x,y=PRICE_ORDER.index(val),PRICE_ORDER.index(tgt); return {"<=":x<=y,">=":x>=y,"==":x==y,"<":x<y,">":x>y}.get(op)
        if op=="==": return val==tgt
        if op=="<=": return (val or 0)<=tgt
        if op==">=": return (val or 0)>=tgt
        return None
    moved=tot=0
    for f in glob.glob(tg,recursive=True):
        if "agent_log" in f: continue
        t=json.loads(open(f).read())
        pcs=(t.get("rubric",{}) or {}).get("personal_constraints",[])
        rel=[c.get("condition") or {} for c in pcs]
        rel=[b for b in rel if b.get("field") in ("price_tier","wheelchair_accessible","avg_cost_local") or b.get("has_tag") in ("free-entry","step-free")]
        if not rel: continue
        tot+=1; gt=set(); fa=set()
        for vid,v in venues.items():
            cv,cvt=layers(v,vid,a.density)
            if all(ok(v,tags[vid],b) for b in rel): gt.add(vid)
            if all(ok(cv,cvt,b) for b in rel): fa.add(vid)
        if gt!=fa:
            moved+=1
            print(f"  {t['task_id'][:34]:34} GT={len(gt):2} faulty={len(fa):2} +false={len(fa-gt)} -lost={len(gt-fa)}")
    print(f"TASKS shifted: {moved}/{tot} at density={a.density}")
if __name__=="__main__": main()
