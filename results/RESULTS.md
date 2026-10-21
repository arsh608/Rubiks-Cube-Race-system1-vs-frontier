# Rubik's Cube Race — Results

_Generated 2026-10-21 18:12 UTC from `results/` by `backend/scripts/make_report.py`. Simulator runs are excluded._

### A. OpenRouter experiment (assignment models)

**Run `20261021-230306-bd19c3`** — start state `F B D'` applied to a solved cube; exact shortest solution **3 moves** (`D F' B'`, bidirectional BFS). Move limits: decision models 20, Claude 5. Started 2026-10-21T18:03:06+00:00.

| Model | Result | Valid moves (limit) | Optimal | Gap | Avg latency | Decision time | Live calls | Cache hits | Wasted turns | Cost | Moves played |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Jev 1.13 | ❌ move limit | 20 (20) | 3 | — | 1.16 s | 23.12 s | 20 | 0 | 0 | $0.000987 | `U2 U U' U2 U2 U' U U2 U2 U2 U2 U' U U2 U2 U' U2 U2 U' U2` |
| GPT-6 Luna Decisions | ❌ move limit | 3 (20) | 3 | — | 733 ms | 2.20 s | 20 | 0 | 17 | $0.000225 | `U U U` |
| Cloudflare Clef | ❌ move limit | 20 (20) | 3 | — | 1.90 s | 38.02 s | 20 | 0 | 0 | $0.002040 | `U' R' R U' U U' U' U2 U2 D D' D L' L L B B' B' D B'` |
| Claude Opus 5.5 | ✅ solved | 3 (5) | 3 | 0 | 12.37 s | 37.12 s | 3 | 0 | 0 | $0.049152 | `D F' B'` |

**Statistical benchmark (OpenRouter)**

| Model | Interface | Avg latency / move | Median | Total decision time (avg) | Total moves | Success rate | Avg optimality gap | Live API calls | Cache hits | Invalid outputs | API errors / refusals | Actual cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **GPT-6 Luna Decisions** (`openai/gpt-6-luna-decisions`) | decisions | 733 ms | 748 ms | 2.20 s | 3 | 0% (0/1) | — | 20 | 0 | 0 | 17 | $0.000225 |
| **Jev 1.13** (`typesafe/jev-1.13`) | decisions | 1.16 s | 936 ms | 23.12 s | 20 | 0% (0/1) | — | 20 | 0 | 0 | 0 | $0.000987 |
| **Cloudflare Clef** (`cloudflare/clef`) | decisions | 1.90 s | 1.03 s | 38.02 s | 20 | 0% (0/1) | — | 20 | 0 | 0 | 0 | $0.002040 |
| **Claude Opus 5.5** (`anthropic/claude-opus-5.5`) | chat | 12.37 s | 10.34 s | 37.12 s | 3 | 100% (1/1) | 0 | 3 | 0 | 0 | 0 | $0.049152 |

**Answers (OpenRouter evidence)**

1. **Which model is fastest at making decisions?** GPT-6 Luna Decisions — 733 ms per move on average (median 748 ms).
2. **Which model achieved the highest solve rate?** Claude Opus 5.5 — 100% of puzzles solved within the move limit.
3. **Which model found the shortest solutions?** Claude Opus 5.5 — average optimality gap +0.00 moves over 1 solved puzzle(s) with a known optimum (1 optimal solve(s)).
4. **Did the fastest model also achieve the best solution quality?** No — GPT-6 Luna Decisions was fastest (733 ms/move) but solved 0% vs 100% for Claude Opus 5.5.
5. **Fastest model, frontier LLM, or hybrid?** The frontier LLM (Claude Opus 5.5) delivered the higher solve rate (100% vs 0%) but was ~17x slower per move. For correctness-critical planning I would use the frontier model, or a hybrid: a fast decision model for cheap/obvious steps with escalation to the frontier model (or a verifier) when confidence is low.

**Cache replay `20261021-231132-92ddc6`:** the same start state was re-run; 46 moves were served from the persistent cache with **0 new API requests** (excluded from the statistics above).

### API budget and cache

Key fingerprint `be9c4dcdd7135205` (SHA-256 prefix; the key itself is never stored): **63 / 65 requests used**, actual cost **$0.052404**, 0 request(s) blocked by the budget guard.

| OpenRouter model | Requests used | Cap |
|---|---|---|
| `typesafe/jev-1.13` | 20 | 20 |
| `openai/gpt-6-luna-decisions` | 20 | 20 |
| `cloudflare/clef` | 20 | 20 |
| `anthropic/claude-opus-5.5` | 3 | 5 |

46 cached responses, 46 cache hits, 80 misses.

| Model | Cached responses |
|---|---|
| `anthropic/claude-opus-5.5` | 3 |
| `cloudflare/clef` | 20 |
| `openai/gpt-6-luna-decisions` | 3 |
| `typesafe/jev-1.13` | 20 |

Every request is logged (before it is sent) in `results/api_log.jsonl`; live API latency and cache-serve time are recorded separately for every move in the run files.
