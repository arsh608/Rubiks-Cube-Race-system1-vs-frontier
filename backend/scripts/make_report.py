"""Build the results report from saved experiment data (no API calls).

    python backend/scripts/make_report.py

Writes results/RESULTS.md and replaces the block between
<!-- RESULTS:START --> and <!-- RESULTS:END --> in README.md.
Simulator runs (results/sim/) are never included.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.stats import answers, model_stats  # noqa: E402

RESULTS = ROOT / "results"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def load_runs() -> list[dict]:
    runs = []
    for p in sorted(RESULTS.glob("session_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("mode") != "mock" and d.get("status") in ("finished", "cancelled"):
            runs.append(d)
    return runs


def ms(v):
    if v is None:
        return "—"
    return f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.2f} s"


def sec(v):
    return "—" if v is None else f"{v:.2f} s"


def gap(v):
    return "—" if v is None else (f"+{v}" if v > 0 else str(v))


def money(v):
    return f"${v:.6f}" if v else "$0"


STATUS = {"solved": "✅ solved", "limit": "❌ move limit", "budget": "⛔ budget stop", "error": "⚠️ error",
          "cancelled": "stopped"}


def stats_table(st: list[dict]) -> str:
    rows = ["| Model | Interface | Avg latency / move | Median | Total decision time (avg) | Total moves | "
            "Success rate | Avg optimality gap | Live API calls | Cache hits | Invalid outputs | API errors / refusals | "
            "Actual cost |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in st:
        rows.append(
            f"| **{s['model_name']}** (`{s['model_id']}`) | {s['interface']} | {ms(s['avg_latency_ms'])} | "
            f"{ms(s['median_latency_ms'])} | {sec(s['avg_total_time_s'])} | {s['total_moves']} | "
            f"{s['success_rate']:.0f}% ({s['solved']}/{s['trials']}) | {gap(s['avg_optimal_gap'])} | "
            f"{s['live_calls']} | {s['cache_hits']} | {s['invalid_outputs']} | {s['api_errors']} | "
            f"{money(s['total_cost_usd'])} |")
    return "\n".join(rows)


def trial_table(run: dict) -> str:
    rows = ["| Model | Result | Valid moves (limit) | Optimal | Gap | Avg latency | Decision time | Live calls | "
            "Cache hits | Wasted turns | Cost | Moves played |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in run["trials"]:
        rows.append(
            f"| {t['model_name']} | {STATUS.get(t['status'], t['status'])} | {t['move_count']} ({t.get('move_limit')}) | "
            f"{t.get('optimal_distance', '—')} | {gap(t.get('optimal_gap'))} | {ms(t.get('avg_latency_ms'))} | "
            f"{sec(t.get('decision_time_s'))} | {t.get('live_calls', 0)} | {t.get('cache_hits', 0)} | "
            f"{t.get('wasted_turns', 0)} | {money(t.get('cost', 0))} | `{' '.join(t.get('solution') or []) or '—'}` |")
    return "\n".join(rows)


def answers_md(a: dict) -> str:
    if not a.get("ready"):
        return "_No finished trials yet._"
    return "\n".join([
        f"1. **Which model is fastest at making decisions?** {a['q1_fastest']}",
        f"2. **Which model achieved the highest solve rate?** {a['q2_highest_solve_rate']}",
        f"3. **Which model found the shortest solutions?** {a['q3_shortest_solutions']}",
        f"4. **Did the fastest model also achieve the best solution quality?** {a['q4_fastest_also_best']}",
        f"5. **Fastest model, frontier LLM, or hybrid?** {a['q5_recommendation']}",
    ])


def budget_md() -> str:
    p = RESULTS / "openrouter_budget.json"
    if not p.exists():
        return "_No OpenRouter requests recorded._"
    data = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for fp, e in data.get("keys", {}).items():
        if fp == "no-key" or not e.get("total_requests"):
            continue
        out.append(f"Key fingerprint `{fp}` (SHA-256 prefix; the key itself is never stored): "
                   f"**{e['total_requests']} / 65 requests used**, actual cost **${e.get('cost_usd', 0):.6f}**, "
                   f"{e.get('blocked', 0)} request(s) blocked by the budget guard.\n")
        out.append("| OpenRouter model | Requests used | Cap |")
        out.append("|---|---|---|")
        caps = {"typesafe/jev-1.13": 20, "openai/gpt-6-luna-decisions": 20, "cloudflare/clef": 20,
                "anthropic/claude-opus-5.5": 5}
        for m, cap in caps.items():
            out.append(f"| `{m}` | {e.get('per_model', {}).get(m, 0)} | {cap} |")
    return "\n".join(out) or "_No OpenRouter requests recorded._"


def cache_md() -> str:
    p = RESULTS / "response_cache.json"
    if not p.exists():
        return "_Cache empty._"
    d = json.loads(p.read_text(encoding="utf-8"))
    per: dict[str, int] = {}
    for e in d.get("entries", {}).values():
        per[e["model"]] = per.get(e["model"], 0) + 1
    lines = [f"{len(d.get('entries', {}))} cached responses, {d.get('hits', 0)} cache hits, "
             f"{d.get('misses', 0)} misses.", "", "| Model | Cached responses |", "|---|---|"]
    lines += [f"| `{m}` | {n} |" for m, n in sorted(per.items())]
    return "\n".join(lines)


def build() -> str:
    runs = load_runs()
    openrouter = [r for r in runs if r.get("provider") == "openrouter"]
    # A run that made zero live requests is a cache replay: it is evidence for caching, not new data.
    live = [r for r in openrouter if not r.get("cache_replay")
            and sum(t.get("live_calls", 0) for t in r["trials"]) > 0]
    replays = [r for r in openrouter if r not in live]
    local = [r for r in runs if r.get("provider") == "ollama"]
    parts = [f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from `results/` "
             f"by `backend/scripts/make_report.py`. Simulator runs are excluded._", ""]

    parts += ["### A. OpenRouter experiment (assignment models)", ""]
    if not live:
        parts += ["_Not run yet._", ""]
    for r in live:
        p = r["puzzles"][0]
        parts += [f"**Run `{r['id']}`** — start state `{' '.join(p['scramble'])}` applied to a solved cube; "
                  f"exact shortest solution **{p['optimal_distance']} moves** "
                  f"(`{' '.join(p['optimal']['solution'])}`, bidirectional BFS). Move limits: decision models 20, "
                  f"Claude 5. Started {r['created_at']}.", "", trial_table(r), ""]
    if live:
        st = model_stats([t for r in live for t in r["trials"]])
        parts += ["**Statistical benchmark (OpenRouter)**", "", stats_table(st), "",
                  "**Answers (OpenRouter evidence)**", "", answers_md(answers(st)), ""]

    if local:
        parts += ["### B. Local open-source experiments (Ollama)", ""]
        st = model_stats([t for r in local for t in r["trials"]])
        n_puz = sum(len(r["puzzles"]) for r in local)
        parts += [f"{len(local)} run(s), {n_puz} puzzle(s), {sum(len(r['trials']) for r in local)} model-puzzle "
                  f"trials. Same engine, same state contract, same move limits (decision 20, frontier 5).", "",
                  stats_table(st), "", "**Answers (Ollama evidence)**", "", answers_md(answers(st)), ""]
        for r in local:
            parts += [f"<details><summary>Run <code>{r['id']}</code> — {r['suite_label']}, "
                      f"{len(r['puzzles'])} puzzle(s)</summary>", ""]
            for p in r["puzzles"]:
                sub = {**r, "trials": [t for t in r["trials"] if t["puzzle_index"] == p["index"]]}
                parts += [f"Puzzle {p['index'] + 1}: `{' '.join(p['scramble'])}` (optimal {p['optimal_distance']})",
                          "", trial_table(sub), ""]
            parts += ["</details>", ""]

    for r in replays:
        hits = sum(t.get("cache_hits", 0) for t in r["trials"])
        parts += [f"**Cache replay `{r['id']}`:** the same start state was re-run; {hits} moves were served from "
                  f"the persistent cache with **0 new API requests** (excluded from the statistics above).", ""]
    parts += ["### API budget and cache", "", budget_md(), "", cache_md(), "",
              "Every request is logged (before it is sent) in `results/api_log.jsonl`; live API latency and "
              "cache-serve time are recorded separately for every move in the run files.", ""]
    return "\n".join(parts)


def main() -> None:
    body = build()
    (RESULTS / "RESULTS.md").write_text("# Rubik's Cube Race — Results\n\n" + body, encoding="utf-8")
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    if START in text and END in text:
        pre, rest = text.split(START, 1)
        _, post = rest.split(END, 1)
        readme.write_text(pre + START + "\n" + body + "\n" + END + post, encoding="utf-8")
        print("README.md results section updated")
    else:
        print("README.md has no RESULTS markers; wrote results/RESULTS.md only")
    print("results/RESULTS.md written")


if __name__ == "__main__":
    main()
