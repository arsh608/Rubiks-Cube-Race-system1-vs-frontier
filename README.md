# Rubik's Cube Race: Fast Decision Models vs. a Frontier LLM

**AI Engineering, Assignment 02** · Arsh Al Aman · Roll No 23K-0078

## 🎥 Demo screen recording

> **▶ [Watch the screen recording](VIDEO_LINK_HERE)**
>
> It shows the models playing (in progress), the completed games, and the statistical benchmark slide. My name and roll number are on screen the whole time.

---

## What this is

This app benchmarks fast "System-1" decision models against a frontier LLM on the same Rubik's Cube task.
- Every model starts from **the same cube, exactly 3 moves from solved**.
- Each turn, a model sees the cube state and picks one of 18 moves.
- The engine applies the move and checks whether the cube is solved.
- A game ends when the cube is solved or the model hits its move limit.

The app records speed (latency per move, total decision time) and thinking quality (solve rate, moves used, optimality gap against the exact BFS optimum). It also records API calls, cache hits and the actual cost.

| Model | Category | OpenRouter ID | Interface |
|---|---|---|---|
| Jev 1.13 | System-1 decision model | `typesafe/jev-1.13` | Decisions API `POST /api/alpha/decisions` |
| GPT-6 Luna Decisions | System-1 decision model | `openai/gpt-6-luna-decisions` | Decisions API |
| Cloudflare Clef | System-1 decision model | `cloudflare/clef` | Decisions API |
| Claude Opus 5.5 | Frontier LLM | `anthropic/claude-opus-5.5` | Chat completions `POST /api/v1/chat/completions` (strict JSON schema, medium reasoning) |

The experiment runs directly on OpenRouter with the course key, as **one budgeted run**. An optional local Ollama profile is included in the code but hidden by default (`ENABLE_OLLAMA=true` shows it); it was not used for the results below.

---

## Results

<!-- RESULTS:START -->
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

<!-- RESULTS:END -->

### Analysis and discussion

**Headline.** On a cube exactly 3 moves from solved (`F B D'`, optimal `D F' B'`), **only the frontier LLM solved it.** Claude Opus 5.5 found the *optimal* solution, `D F' B'`: 3 moves, gap 0, using 3 of its 5 allowed requests. None of the three System-1 decision models solved it within 20 moves.

**Speed.** The decision models answered in about 0.7–1.9 s per move. Claude took about 12.4 s per move, roughly 6–17× slower, because it reasons before answering.
- **Total decision time is almost the same.** Claude's 3 moves took 37 s; Clef spent 38 s and Jev 23 s on 20 moves each without solving.
- **The trade-off is cost per solve, not time.** Claude cost $0.049 for a correct, optimal solve. The three decision models together cost $0.003 and produced no solve.

**How the decision models failed**
- **Jev 1.13** made 20 valid moves, and **every one** turned the Up face (`U`, `U'`, `U2`). The Up face isn't in the scramble at all, so it never made progress.
- **Cloudflare Clef** explored more faces, but it repeatedly undid its own moves (`R' R`, `D D'`, `L' L`, `B B'`) and never moved closer to solved.
- **GPT-6 Luna Decisions** answered on only 3 of its 20 turns. On the other 17, OpenAI's provider **refused to answer** (`OpenAI refused to answer question "next_move"`). Each refusal was logged and counted as a wasted turn, never turned into a move, as the rules require. Its 733 ms average latency is therefore measured over just 3 moves. It is the "fastest" model by that average, but its main failure was refusals, not wrong moves.

**Answers to the five questions**
1. **Fastest:** GPT-6 Luna Decisions on average latency (733 ms over its 3 valid moves). Among decision models that answered every turn, Jev was fastest (1.16 s average, 0.94 s median). All decision models were an order of magnitude faster than Claude (12.4 s).
2. **Highest solve rate:** Claude Opus 5.5, 100% (1/1). Every decision model: 0%.
3. **Shortest solutions:** Claude Opus 5.5, 3 moves against an exact optimum of 3, so an optimality gap of **0**.
4. **Did the fastest model also give the best quality?** No. The fastest models never solved the cube. Speed per decision didn't translate into progress on a task that needs multi-step spatial planning.
5. **Which would I use?** A **hybrid**. Single-step System-1 models are cheap and fast, and they suit routing, classification and gating. But this task needs a short plan where each move depends on the cube's geometry, and only the reasoning model managed that. In a real application I'd use the frontier model, or a solver or verifier, for planning. A fast decision model would handle cheap, well-defined sub-decisions, such as "is this face solved?" or "which subsystem should handle this?", with escalation when its confidence is low. The evidence is the solve rates (100% vs 0%), the optimality gap (0 vs no solve), and the cost per solve ($0.049 vs no solve at any cost).

**Limitations.**
- This is **one** puzzle (n = 1 per model), as the course budget allows: about 65 requests for one good experiment. The results show a clear qualitative difference, but they aren't statistically strong.
- The decision models' results may depend on how the cube state is encoded for them. Every model received the same state and instructions.
- The course email suggested testing first with local Ollama models. That optional profile is built and unit-tested in this repo but was not run, by my choice; I went straight to the single OpenRouter experiment.

**Budget compliance.**
- **Requests:** 63 of 65 used (Jev 20/20, Luna 20/20, Clef 20/20, Claude 3/5), never near the key's 100-call hard limit.
- **Cost:** $0.052 in total.
- **Cache:** every valid response was cached. A replay of the same start state was served entirely from the cache, with zero requests to OpenRouter.

---

## How the course API rules are enforced (in the backend, not just the UI)

| Rule from the course email | Implementation |
|---|---|
| Start state exactly 3 moves from solved | Test set `exact3`. Each scramble is checked with an exact BFS and kept only if its optimum is **exactly 3**. A custom scramble at any other distance is rejected. |
| Stop at 20 moves and mark unsolved | Decision models have a move limit of 20. Reaching it gives status `limit` (unsolved). |
| Claude: at most 5 requests in total, including failures and retries | Claude's move limit is **5**. Separately, the persistent ledger caps `anthropic/claude-opus-5.5` at **5 requests for the lifetime of the key**. |
| About 65 requests in total (20 per decision model + 5 Claude); the key dies at 100 | Ledger caps: 20 / 20 / 20 / 5, **65 in total**. The cap can't be configured above 65, and any model not on the list is blocked. |
| No open loops | Every loop is bounded by the move limit, and retries are off by default (`MAX_RETRIES=0`). Each request must first get a slot from the ledger. A run is limited to 1 puzzle, and only one OpenRouter run can run at a time. The connection test is disabled for OpenRouter. |
| Cache all responses persistently and check before every request | `results/response_cache.json` (see below). |
| Don't reset counters or the cache on restart | Both live in files that the app never clears. Deleting a run from the UI deletes only that run's result file. If the ledger file is corrupt, the app refuses to start rather than reset to zero. |
| Log and track metrics before hitting OpenRouter | `results/api_log.jsonl` (see below). |
| Track live latency separately from cached response time | Each move records its `source` (`live` / `cache`), its live API latency, and `cached_ms`. |
| Record solve status, moves, latency, gap, API calls, cache hits and actual cost | Every trial in `results/session_*.json`, the dashboard, the CSV export and the report. Cost is OpenRouter's reported `usage.cost`. |
| One good OpenRouter experiment | The OpenRouter profile is locked to one exactly-3 puzzle. Before any budgeted request, a **free pre-flight** reads OpenRouter's public model catalog without the key. It checks that every model ID exists and which output format Opus's route supports, so no request is wasted on a 404 or a format fallback. An auth or credit error (401/402/403) stops the whole run immediately. |
| Never leak the key | The key is read only by the backend, from `.env` (git-ignored). It is never sent to the browser or written to any file; the ledger stores only a SHA-256 fingerprint. |

**Cache:**
- **Key:** a SHA-256 hash of provider + endpoint + the full request payload, so the same model with the same request always hits.
- **Lookup order:** the cache is checked before every request. A hit makes no network call and uses no budget.
- **What is stored:** only valid responses. Malformed outputs are logged and counted, never cached and never turned into a move.

**Request log:**
- Every `request` line is written *before* the HTTP call is sent.
- It includes the ledger counters at that moment, followed by the matching `response`, `cache_hit` or `blocked` line.

**Replays:** running the same experiment again is served entirely from the cache, with zero new requests. Tick **Replay cache at original speed** to replay it at the original pace. Replayed moves are always labelled **CACHE**.

---

## Setup

### 1. Python backend
```bash
python -m pip install -r backend/requirements.txt
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```
Edit `.env` and set `OPENROUTER_API_KEY`, `STUDENT_NAME` and `ROLL_NUMBER`. Never share the key or commit `.env`.

### 2. Run
```bash
python -m uvicorn app.main:app --app-dir backend --port 8000
```
Open http://localhost:8000.

### 3. Run the experiment (once)
1. Choose **Assignment models**. The settings lock to the course rules: exactly-3 cube, 1 puzzle, 20-move limit (5 for Claude).
2. Click **▶ Run benchmark** and confirm the budget dialog.
   - The pre-flight runs first, for free.
   - Then all four models race on the same cube.
   - Worst case is 65 requests; a good run uses far fewer.
3. Run `python backend/scripts/make_report.py` to fill in the Results section above and write `results/RESULTS.md`.

Re-running the same scramble is served from the cache and uses no requests.

---

## How it works

```
frontend (HTML/JS, no build)  ──REST + Server-Sent Events──►  FastAPI backend
  race lanes, 3D cubes, budget meter,                          ├─ cube.py     deterministic engine (54 facelets, 18 HTM moves)
  dashboard, results slide                                     ├─ solver.py   exact shortest path (bidirectional BFS)
                                                               ├─ prompts.py  ONE shared state + question contract for every model
                                                               ├─ adapters.py OpenRouter Decisions/Chat, Ollama systemone/chat, simulator
                                                               ├─ ledger.py   budget ledger + response cache + request log (persistent)
                                                               ├─ runner.py   puzzles → trials → turns, rule enforcement, live events
                                                               └─ stats.py    metrics + evidence-based answers
```

- **State** (only fields the question reads, per OpenRouter's Decisions guidance):
  - `cube_facelets`: the 54-letter string in URFDLB order.
  - `faces`: the same data as 3 rows per face.
  - `encoding`: how to read it.
  - `recent_moves`: the last 10 moves.
  - `moves_remaining`.
- **Actions:** 18 moves in the half-turn metric (`U U' U2 … B2`).
- **Decision models:** receive the state plus one `choice` question whose 18 criteria are the moves. The app records the chosen move, its confidence and the probabilities.
- **Chat models:** receive the same state and instructions. They must reply with exactly `{"move": "<move>"}`, using JSON-schema structured output.
- **Same rules for every model:** the same engine, state, instructions, history window and timeout.

### Metrics
| Metric | Definition |
|---|---|
| Avg latency per move | Mean **live** API latency of the call that returned each valid move. For a cached move, this is the latency of the original live call. |
| Cached response time | Time to serve a move from the cache (`cached_ms`), reported separately. |
| Total decision time | Sum of the live latencies of a model's moves in a game. Raw wall-clock time is also stored. |
| Total moves | Valid moves applied. Wasted turns (malformed output, provider refusal or API error) are counted separately. |
| Success rate | Games solved within the model's move limit ÷ games played. |
| Optimality gap | Model moves − exact shortest moves (BFS). 0 means optimal. |
| API calls / cache hits / cost | Real network requests, cache hits, and the `usage.cost` reported by OpenRouter. |

---

## Recording checklist
1. **Name and roll number on screen the whole time.** The fixed badge in the bottom-right corner is set from `.env`.
2. **In progress:** live lanes showing each model's current move, latency, confidence, LIVE/CACHE label, API calls and the budget meter.
3. **Completed:** each lane shows **✓ SOLVED in N moves · gap k**, **✗ MOVE LIMIT REACHED**, or **⛔ API BUDGET STOP**, plus the per-puzzle matrix.
4. **End on the slide:** click **📊 Results slide** for the full-screen statistical benchmark (all metrics from section 3 of the brief, plus answers to Q1–Q5).

---

## API (backend)
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config` | Profiles, models, suites, policy, budget (no secrets) |
| GET | `/api/budget` | OpenRouter ledger (used/cap per model, cost) and cache stats |
| GET | `/api/apilog?n=100` | Tail of the request log |
| POST | `/api/smoke` | One call per model (Ollama/simulator only) |
| POST | `/api/runs` | Start a run |
| GET | `/api/runs/{id}/events` | Live Server-Sent Events stream |
| GET | `/api/runs`, `/api/runs/{id}` | History and full run data |
| GET | `/api/stats`, `/api/export.csv` | Aggregated metrics, trial CSV |
| GET | `/api/contract` | Exact payloads sent to each interface |

## Tests
```bash
cd backend
python -m pytest -q
```
There are 78 tests. They cover:
- Cube engine against reference facelets, and the solver against brute-force BFS.
- Adapters, with OpenRouter and Ollama mocked.
- The ledger: Claude's cap surviving a restart, unknown models blocked, the total cap, and refusing to start on a corrupt file.
- The cache: a hit makes no HTTP call, the cache survives a restart, and malformed responses aren't cached.
- A full mocked OpenRouter run: a perfect run uses exactly 12 requests, re-running it uses 0, and a worst-case model that never progresses stays within 65 (Claude ≤5).
- Pre-flight: a missing model ID blocks the run with 0 requests spent, and Opus's output mode adapts to its route. A credit error stops the whole run after 1 request.

## Project layout
```
backend/app/        cube.py solver.py prompts.py adapters.py ledger.py runner.py stats.py config.py main.py
backend/scripts/    make_report.py
backend/tests/      pytest suite
frontend/           index.html styles.css cube3d.js app.js
results/            run files, response_cache.json, openrouter_budget.json, api_log.jsonl (evidence, no secrets)
results/sim/        simulator runs (git-ignored, not evidence)
.env.example        environment template (copy to .env, never commit .env)
```
