"""Aggregate benchmark statistics and the answers to the assignment's five questions."""

from __future__ import annotations

from statistics import mean, median
from typing import Iterable


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _r(x, n=2):
    return None if x is None else round(x, n)


def model_stats(trials: Iterable[dict]) -> list[dict]:
    """Per-model aggregates over finished trials (cancelled/running trials are excluded)."""
    by_model: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for t in trials:
        if t.get("status") not in ("solved", "limit", "error", "budget"):
            continue
        by_model.setdefault(t["model_key"], []).append(t)
        meta.setdefault(t["model_key"], {
            "model_key": t["model_key"], "model_name": t.get("model_name"),
            "model_id": t.get("model_id"), "category": t.get("category"),
            "interface": t.get("interface"), "mode": t.get("mode"), "provider": t.get("provider"),
            "move_limit": t.get("move_limit"),
        })

    out = []
    for key, ts in by_model.items():
        latencies = [m["latency_ms"] for t in ts for m in t.get("moves", [])
                     if m.get("valid") and m.get("latency_ms") is not None]
        solved = [t for t in ts if t.get("solved")]
        gaps = [t["optimal_gap"] for t in solved if t.get("optimal_gap") is not None]
        solved_moves = [t["move_count"] for t in solved]
        confs = [m["confidence"] for t in ts for m in t.get("moves", [])
                 if m.get("valid") and isinstance(m.get("confidence"), (int, float))]
        costs = [t.get("cost") or 0 for t in ts]
        # "Total time" = model decision time (sum of live API latencies of its moves). It is stable
        # across cached replays; raw wall-clock is reported separately as avg_wall_time_s.
        def _dt(t):
            v = t.get("decision_time_s")
            return v if v is not None else t.get("total_time_s")
        total_times = [_dt(t) for t in ts if _dt(t) is not None]
        solve_times = [_dt(t) for t in solved if _dt(t) is not None]
        wall_times = [t["total_time_s"] for t in ts if t.get("total_time_s") is not None]
        live_lat = [m["latency_ms"] for t in ts for m in t.get("moves", [])
                    if m.get("valid") and m.get("source") == "live" and m.get("latency_ms") is not None]
        cached = [m["cached_ms"] for t in ts for m in t.get("moves", [])
                  if m.get("source") == "cache" and m.get("cached_ms") is not None]
        out.append({
            **meta[key],
            "trials": len(ts),
            "solved": len(solved),
            "success_rate": round(100 * len(solved) / len(ts), 1),
            "avg_latency_ms": _r(mean(latencies), 1) if latencies else None,
            "median_latency_ms": _r(median(latencies), 1) if latencies else None,
            "p95_latency_ms": _r(_pct(latencies, 0.95), 1),
            "avg_total_time_s": _r(mean(total_times), 2) if total_times else None,
            "sum_total_time_s": _r(sum(total_times), 2),
            "avg_solve_time_s": _r(mean(solve_times), 2) if solve_times else None,
            "avg_wall_time_s": _r(mean(wall_times), 2) if wall_times else None,
            "avg_live_latency_ms": _r(mean(live_lat), 1) if live_lat else None,
            "avg_cached_ms": _r(mean(cached), 3) if cached else None,
            "live_calls": sum(t.get("live_calls", 0) for t in ts),
            "cache_hits": sum(t.get("cache_hits", 0) for t in ts),
            "budget_stops": sum(1 for t in ts if t.get("status") == "budget"),
            "total_moves": sum(t["move_count"] for t in ts),
            "avg_moves": _r(mean(t["move_count"] for t in ts), 2),
            "avg_moves_solved": _r(mean(solved_moves), 2) if solved_moves else None,
            "avg_optimal_gap": _r(mean(gaps), 2) if gaps else None,
            "zero_gap_solves": sum(1 for g in gaps if g == 0),
            "gap_samples": len(gaps),
            "invalid_outputs": sum(t.get("invalid_outputs", 0) for t in ts),
            "api_errors": sum(t.get("api_errors", 0) for t in ts),
            "retries": sum(t.get("retries", 0) for t in ts),
            "wasted_turns": sum(t.get("wasted_turns", 0) for t in ts),
            "errored_trials": sum(1 for t in ts if t.get("status") == "error"),
            "avg_confidence": _r(mean(confs), 3) if confs else None,
            "total_cost_usd": round(sum(costs), 8),
        })
    out.sort(key=lambda s: (s["avg_latency_ms"] is None, s["avg_latency_ms"] or 0))
    return out


def answers(stats: list[dict]) -> dict:
    """Evidence-based answers to the five questions in the brief."""
    if not stats:
        return {"ready": False}
    with_lat = [s for s in stats if s["avg_latency_ms"] is not None]
    fastest = min(with_lat, key=lambda s: s["avg_latency_ms"]) if with_lat else None
    best_rate = max(s["success_rate"] for s in stats)
    top_solvers = [s for s in stats if s["success_rate"] == best_rate]
    with_gap = [s for s in stats if s["avg_optimal_gap"] is not None]
    shortest = min(with_gap, key=lambda s: (s["avg_optimal_gap"], -s["success_rate"])) if with_gap else None

    def name(s):
        return s["model_name"] if s else "n/a"

    q1 = (f"{name(fastest)} — {fastest['avg_latency_ms']:.0f} ms per move on average "
          f"(median {fastest['median_latency_ms']:.0f} ms)." if fastest else "No valid moves recorded.")
    q2 = (", ".join(name(s) for s in top_solvers) +
          f" — {best_rate:.0f}% of puzzles solved within the move limit.")
    q3 = (f"{name(shortest)} — average optimality gap {shortest['avg_optimal_gap']:+.2f} moves over "
          f"{shortest['gap_samples']} solved puzzle(s) with a known optimum "
          f"({shortest['zero_gap_solves']} optimal solve(s))."
          if shortest else "No model solved a puzzle with a known optimum, so no gap could be measured.")
    if fastest is None:
        q4 = "Insufficient data."
    else:
        fast_is_top = fastest in top_solvers
        fast_is_short = shortest is not None and fastest["model_key"] == shortest["model_key"]
        if fast_is_top and fast_is_short:
            q4 = f"Yes — {name(fastest)} was both the fastest and the best on solve rate and optimality gap."
        elif fast_is_top or fast_is_short:
            q4 = (f"Partly — {name(fastest)} was fastest and "
                  f"{'tied/led on solve rate' if fast_is_top else 'had the smallest gap'}, "
                  f"but not on {'optimality gap' if fast_is_top else 'solve rate'}.")
        else:
            q4 = (f"No — {name(fastest)} was fastest ({fastest['avg_latency_ms']:.0f} ms/move) but solved "
                  f"{fastest['success_rate']:.0f}% vs {best_rate:.0f}% for {name(top_solvers[0])}.")
    best = top_solvers[0]
    best_is_frontier = "frontier" in (best.get("category") or "").lower()
    if best_rate == 0:
        q5 = ("No model solved puzzles reliably, so a pure LLM agent is not production-ready for this task; "
              "a hybrid that lets a fast model propose moves and verifies them with a search-based solver "
              "would be the practical choice.")
    elif best_is_frontier and fastest is not None and fastest["model_key"] != best["model_key"]:
        speedup = (best["avg_latency_ms"] or 0) / max(fastest["avg_latency_ms"] or 1, 1)
        q5 = (f"The frontier LLM ({name(best)}) delivered the higher solve rate ({best_rate:.0f}% vs "
              f"{fastest['success_rate']:.0f}%) but was ~{speedup:.0f}x slower per move. For correctness-"
              "critical planning I would use the frontier model, or a hybrid: a fast decision model for "
              "cheap/obvious steps with escalation to the frontier model (or a verifier) when confidence is low.")
    else:
        q5 = (f"{name(best)} gave the best solve rate at {best['avg_latency_ms'] or 0:.0f} ms/move; when the fast "
              "model is also accurate it is the better production choice, with a frontier model as fallback.")
    return {
        "ready": True,
        "q1_fastest": q1,
        "q2_highest_solve_rate": q2,
        "q3_shortest_solutions": q3,
        "q4_fastest_also_best": q4,
        "q5_recommendation": q5,
        "fastest_key": fastest["model_key"] if fastest else None,
        "top_solver_keys": [s["model_key"] for s in top_solvers],
        "shortest_key": shortest["model_key"] if shortest else None,
    }
