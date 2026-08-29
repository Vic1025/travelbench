# External References

Sources consulted during TravelBench design work — papers, repos, blog posts, datasets. **Maintained for credit and citation.**

Format per entry: title + authors / arxiv ID or URL / date consulted / one-line idea / where applied in TravelBench.

**Policy:** ideas are paraphrased and reimplemented from understanding. No code is copied verbatim. If a verbatim quote or figure is ever included anywhere in TravelBench's artifacts, it must be attributed inline at that location AND logged here.

---

## Papers

| # | Title / Authors | arxiv ID | Consulted | Idea | Relevance to P-score widening |
|---|---|---|---|---|---|
| 1 | **DeepPlanning** — Zhang et al. (Alibaba Qwen) | 2601.18137v1 | 2026-05-25 | Long-horizon agentic planning benchmark; explicitly separates local (per-subtask) constraints from global (whole-plan) constraints | 🟢 High — domain overlap + constraint-decomposition lens may explain why models cluster on local checks |
| 2 | **TravelPlanner** — Xie et al. (Fudan/OSU/Penn State/Meta) | 2402.01622v4 | 2026-05-25 | First travel-planning benchmark; ~4M data records, 1,225 intents; GPT-4 at 60% success | 🟢 High — predecessor; failure-mode analysis (tool selection, constraint tracking, multi-constraint reasoning) directly comparable |
| 3 | **TravelBench (Cheng et al.)** — Renmin/NUS/Beihang/Alibaba AMAP | 2512.22673v3 | 2026-05-25 | **NAME COLLISION** with Vic's TravelBench. 1,100 instances, 10 real tools, multi-turn, unsolvable tasks, LLM-as-judge rubric | 🟢 High — same name, overlapping scope; design choices (unsolvable tasks, implicit preference elicitation) directly relevant to ceiling problem. Repo: `small-xiangcheng/TravelBench` |
| 4 | **τ²-Bench** — Barres et al. (Sierra/U Toronto) | 2506.07982v1 | 2026-05-25 | Dual-control conversational benchmark; compositional task generator with controlled complexity | 🟡 Medium — compositional task gen with parametric complexity tuning is adaptable for difficulty calibration |
| 5 | **τ-Bench** — Yao et al. (Sierra) | 2406.12045v1 | 2026-05-25 | Tool-agent-user benchmark; `pass^k` metric measures cross-trial consistency | 🟡 Medium — `pass^k` metric distinguishes consistent vs. occasional failures |
| 6 | **VitaBench** — Meituan LongCat Team | 2509.26490v2 | 2026-05-25 | Food delivery / retail / travel benchmark; rubric-based sliding-window evaluator; 66 tools | 🟡 Medium — rubric design pattern; 30% cross-scenario success suggests their tasks discriminate better |
| 7 | **OPeRA** — Wang et al. (Northeastern et al.) | 2506.05606v6 | 2026-05-25 | LLM evaluation on user online-shopping behavior simulation | 🔴 Low — orthogonal domain |
| 8 | **CL-Bench** — Dou et al. (Tencent Hunyuan/Fudan) | 2602.03587v1 | 2026-05-25 | Context-learning benchmark; 17.2% avg success across 10 frontier models | 🔴 Low — focuses on learning new context, not preference reasoning |
| 9 | **Multi-User LLM Agents** — Yang et al. (Stanford/KAUST/UT Austin/MIT) | 2604.08567v2 | 2026-05-25 | Multi-principal LLM agents with conflicting interests, privacy, coordination | 🔴 Low — orthogonal (multi-user, not single-user preference) |

**Priority for deep-dive:** 2601.18137 (DeepPlanning), 2402.01622 (TravelPlanner), 2512.22673 (TravelBench-Cheng).

---

## Self-evolve / long-horizon

➡️ **Moved into `docs/SELF_EVOLVE_DESIGN.md` §12** (consolidated 2026-06-19 so the design
+ its sources read as one doc). The full annotated list (19 self-evolve papers + the
long-horizon set + ACE / Claude memory tool / "Whose Facts Win?" / OWASP poisoning)
lives there. Local PDFs: `~/Documents/Books and Papers/Papers/self-evolve/literature.md`
and `~/Documents/Books and Papers/Papers/long-horizon-agents-reading-map.md`.

Upstream repo: `Vic1025/Evolve-on-exp-agent-framework` (private) — adapted into
`docs/SELF_EVOLVE_DESIGN.md` (deltas in §8).

## Repos

*(entries added as consulted)*

---

## Other

*(blog posts, datasets, talks, etc.)*
