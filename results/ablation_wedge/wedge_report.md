# Wedge pilot — load-bearing hours flaws (Arm C: faulty vs clean_equalvol)

Same test_lb corpus; only the served hours lie differs (volume held constant). Scored vs GT.
Δ = faulty − clean_equalvol; **negative ΔF = the lie made feasibility HARDER (wedge).**


## claude-sonnet-4-5
| task | C f/c | F faulty→clean (Δ) | P faulty→clean (Δ) | f_ded f→c |
|---|---|---|---|---|
| type1 | 0.96/0.96 | 0.21→0.56 (-0.35) | 1.00→1.00 (+0.00) | 9→6 |
| type2 | 0.88/0.96 | 0.68→0.45 (+0.23) | 0.75→0.92 (-0.17) | 2→4 |
| type3 | 0.96/0.96 | 0.51→0.83 (-0.32) | 0.47→0.40 (+0.07) | 7→4 |
| type4 | 0.96/0.72 | 0.36→0.96 (-0.60) | 1.00→0.50 (+0.50) | 6→2 |
| type5 | 0.96/0.96 | 0.65→0.30 (+0.35) | 0.62→0.62 (+0.00) | 3→6 |
| type6 | 0.96/0.92 | 0.00→0.34 (-0.34) | 0.50→0.50 (+0.00) | 14→9 |

**mean ΔF = -0.172, mean ΔP = +0.067 (n=6)**

## deepseek-chat
| task | C f/c | F faulty→clean (Δ) | P faulty→clean (Δ) | f_ded f→c |
|---|---|---|---|---|
| type1 | C 0.96/0.0 | F 0.0→None (n/a) | P 1.0→None | 14→0 |
| type2 | C 0.92/0.0 | F 0.7→None (n/a) | P 0.834→None | 1→0 |
| type3 | C 0.0/0.0 | F None→None (n/a) | P None→None | 0→0 |
| type4 | C 0.0/0.0 | F None→None (n/a) | P None→None | 0→0 |
| type5 | C 0.0/0.72 | F None→0.83 (n/a) | P None→0.75 | 0→2 |
| type6 | C 0.0/0.96 | F None→0.0 (n/a) | P None→0.75 | 0→10 |
