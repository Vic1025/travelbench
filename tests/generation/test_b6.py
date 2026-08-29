"""scripts/generation/test_b6.py — B6 constraint calibration tests"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.generation.calibrate_constraints import run_calibration, results

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}"); PASS += 1
    else:
        print(f"  ❌ {label}{(' — '+str(detail)) if detail else ''}"); FAIL += 1

print("\n[1] Running calibration probe pairs")
all_pass = run_calibration(verbose=False)
check("all 9 handlers calibrated (Δ ≥ 0.8)", all_pass)

print("\n[2] Individual handler checks")
for r in results:
    check(f"{r['name']} Δ≥0.8", r["delta"] >= 0.8, f"Δ={r['delta']:.2f}")

# [3] Paris regression — retired with the Paris-JSON pipeline.

print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B6 tests passed ({PASS}/{total}) — calibration ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
