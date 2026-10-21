"""Benchmark orchestration: puzzles -> trials -> turns, with a replayable live event stream."""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .adapters import BaseAdapter, make_adapter
from .config import (OPENROUTER_ABSOLUTE_CAP, OPENROUTER_TOTAL_CAP, PROFILES, SUITES, ModelSpec, Settings,
                     openrouter_request_caps)
from .cube import SOLVED, apply_move, apply_moves, is_solved, random_scramble
from .ledger import Services
from .prompts import build_state
from .solver import solve_optimal
from .stats import answers, model_stats

FINISHED = ("solved", "limit", "error", "budget")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _puzzle(index: int, scramble: list[str], optimal_max_depth: int) -> dict:
    state = apply_moves(SOLVED, scramble)
    opt = solve_optimal(state, max_depth=min(optimal_max_depth, max(len(scramble), 1)))
    return {
        "index": index,
        "scramble": scramble,
        "scramble_length": len(scramble),
        "state": state,
        "optimal": opt.to_dict(),
        # exact unless the search was capped below the true distance
        "optimal_distance": opt.distance,
        "optimal_upper_bound": opt.distance if opt.distance is not None else len(scramble),
    }


def make_puzzles(suite: str, count: int, seed: int, optimal_max_depth: int,
                 custom_length: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    cfg = SUITES[suite]
    exact = cfg.get("exact_distance")
    puzzles = []
    for i in range(count):
        for _ in range(1000):
            length = custom_length or rng.randint(cfg["min_len"], cfg["max_len"])
            p = _puzzle(i, random_scramble(length, rng), optimal_max_depth)
            if exact is None or p["optimal_distance"] == exact:
                break
        else:  # pragma: no cover - a 3-move canonical scramble is essentially always distance 3
            raise RuntimeError(f"could not generate a puzzle exactly {exact} moves from solved")
        puzzles.append(p)
    return puzzles


@dataclass
class Session:
    id: str
    profile: str
    suite: str
    seed: int
    max_moves: int
    parallel: bool
    models: list[ModelSpec]
    puzzles: list[dict]
    settings_snapshot: dict
    replay_speed: bool = False
    created_at: str = field(default_factory=now_iso)
    status: str = "pending"          # pending | running | finished | cancelled | error
    finished_at: str | None = None
    trials: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    budget_before: dict | None = None
    budget_after: dict | None = None
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)
    _cancel: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def provider(self) -> str:
        return PROFILES[self.profile]["provider"]

    @property
    def mode(self) -> str:
        return {"mock": "mock", "ollama": "ollama"}.get(self.provider, "live")

    @property
    def done(self) -> bool:
        return self.status in ("finished", "cancelled", "error")

    def move_limit(self, spec: ModelSpec) -> int:
        return min(self.max_moves, spec.max_moves or self.max_moves)

    async def emit(self, kind: str, **data: Any) -> None:
        evt = {"seq": len(self.events), "type": kind, "t": time.time(), **data}
        self.events.append(evt)
        async with self._cond:
            self._cond.notify_all()

    async def wait_for_events(self, count: int, timeout: float = 15.0) -> bool:
        """Wait until more than ``count`` events exist (or the session ends)."""
        async with self._cond:
            if len(self.events) > count or self.done:
                return True
            try:
                await asyncio.wait_for(self._cond.wait(), timeout)
                return True
            except asyncio.TimeoutError:
                return False

    def summary(self) -> dict:
        return {
            "id": self.id, "profile": self.profile, "provider": self.provider, "mode": self.mode,
            "suite": self.suite, "suite_label": SUITES[self.suite]["label"], "seed": self.seed,
            "max_moves": self.max_moves, "parallel": self.parallel, "replay_speed": self.replay_speed,
            "created_at": self.created_at, "finished_at": self.finished_at,
            "status": self.status, "puzzle_count": len(self.puzzles),
            "trials_done": sum(1 for t in self.trials if t["status"] in FINISHED),
            "trials_total": len(self.puzzles) * len(self.models),
            "models": [{**m.public(), "move_limit": self.move_limit(m)} for m in self.models],
        }

    def to_json(self) -> dict:
        stats = model_stats(self.trials)
        return {
            **self.summary(),
            "settings": self.settings_snapshot,
            "budget_before": self.budget_before,
            "budget_after": self.budget_after,
            "puzzles": self.puzzles,
            "trials": self.trials,
            "notes": self.notes,
            "stats": stats,
            "answers": answers(stats),
        }


class BenchmarkRunner:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None, services: Services | None = None):
        self.settings = settings
        self.client = client
        self.sessions: dict[str, Session] = {}
        self.settings.results_dir.mkdir(parents=True, exist_ok=True)
        self.services = services or Services(settings.results_dir, settings.api_key, openrouter_request_caps(),
                                             OPENROUTER_TOTAL_CAP, OPENROUTER_ABSOLUTE_CAP)

    # ---------- persistence ----------
    def _dir(self, mode: str) -> Path:
        # Simulator runs live in results/sim/ (git-ignored) so they never mix with evidence.
        d = self.settings.results_dir / "sim" if mode == "mock" else self.settings.results_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _find(self, sid: str) -> Path | None:
        for d in (self.settings.results_dir, self.settings.results_dir / "sim"):
            p = d / f"session_{sid}.json"
            if p.exists():
                return p
        return None

    def save(self, s: Session) -> None:
        path = self._dir(s.mode) / f"session_{s.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.to_json(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def _read(self, p: Path) -> dict | None:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # A file still marked active but not owned by this process was interrupted by a restart.
        if d.get("status") in ("pending", "running") and d.get("id") not in self.sessions:
            d["status"] = "interrupted"
        return d

    def load_saved(self) -> list[dict]:
        out = []
        for d in (self.settings.results_dir, self.settings.results_dir / "sim"):
            for p in sorted(d.glob("session_*.json")):
                rec = self._read(p)
                if rec is not None:
                    out.append(rec)
        return out

    def get_saved(self, sid: str) -> dict | None:
        if sid in self.sessions:
            return self.sessions[sid].to_json()
        p = self._find(sid)
        return self._read(p) if p else None

    def delete_saved(self, sid: str) -> bool:
        """Deletes a run's result file only. The response cache and budget ledger are never touched."""
        s = self.sessions.get(sid)
        if s and not s.done:
            return False
        self.sessions.pop(sid, None)
        p = self._find(sid)
        if p:
            p.unlink()
            return True
        return s is not None

    # ---------- preflight ----------
    async def preflight_openrouter(self, models: list[ModelSpec]) -> tuple[list[ModelSpec], list[str]]:
        """Free check against OpenRouter's PUBLIC model catalog (no API key sent, no budget used).

        Verifies every model id exists and picks the chat output mode the route actually supports,
        so no budgeted request is wasted on a 404 or a structured-output fallback.
        """
        if self.client is None:
            raise ValueError("no HTTP client")
        base = self.settings.base_url
        try:
            dec = await self.client.get(base + "/api/v1/models", params={"output_modalities": "decisions"},
                                        timeout=30)
            allm = await self.client.get(base + "/api/v1/models", timeout=30)
            dec.raise_for_status()
            allm.raise_for_status()
            decision_ids = {m["id"] for m in dec.json().get("data", [])}
            catalog = {m["id"]: m for m in allm.json().get("data", [])}
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise ValueError(f"Pre-flight failed: cannot read the OpenRouter model catalog ({type(e).__name__}). "
                             "Check the internet connection; no API request was made.") from e
        notes, out, missing = [], [], []
        for m in models:
            if m.interface == "decisions":
                if m.model_id not in decision_ids:
                    missing.append(m.model_id)
                out.append(m)
                continue
            info = catalog.get(m.model_id)
            if info is None:
                missing.append(m.model_id)
                out.append(m)
                continue
            params = set(info.get("supported_parameters") or [])
            mode = m.structured_output
            if mode == "json_schema" and "structured_outputs" not in params:
                mode = "json_object" if "response_format" in params else "none"
            elif mode == "json_object" and "response_format" not in params:
                mode = "none"
            effort = m.reasoning_effort if ("reasoning" in params or "reasoning_effort" in params) else None
            if mode != m.structured_output or effort != m.reasoning_effort:
                notes.append(f"pre-flight: {m.model_id} output mode '{mode}', reasoning '{effort}' "
                             f"(route capabilities)")
            out.append(replace(m, structured_output=mode, reasoning_effort=effort))
        if missing:
            raise ValueError("Pre-flight: these model ids are not in OpenRouter's catalog right now: "
                             + ", ".join(missing) + ". No API request was made.")
        notes.append("pre-flight OK: all model ids found in the public OpenRouter catalog (no key used)")
        return out, notes

    async def _check_ollama(self, models: list[ModelSpec]) -> None:
        if self.client is None:
            raise ValueError("no HTTP client")
        try:
            r = await self.client.get(self.settings.ollama_url + "/api/tags", timeout=10)
            r.raise_for_status()
            have = {m.get("name") for m in r.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as e:
            raise ValueError(f"Ollama is not reachable at {self.settings.ollama_url} ({type(e).__name__}). "
                             "Install/start Ollama first (https://ollama.com/download).") from e
        missing = [m.model_id for m in models
                   if m.model_id not in have and f"{m.model_id}:latest" not in have]
        if missing:
            raise ValueError("Ollama models not downloaded yet. Run: " +
                             " ; ".join(f"ollama pull {m}" for m in missing))

    # ---------- lifecycle ----------
    async def create(self, profile: str, suite: str, puzzles: int, seed: int | None, max_moves: int,
                     parallel: bool, model_keys: list[str] | None, custom_length: int | None = None,
                     custom_scramble: list[str] | None = None, replay_speed: bool = False) -> Session:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}")
        if suite not in SUITES:
            raise ValueError(f"unknown suite {suite!r}")
        prof = PROFILES[profile]
        provider = prof["provider"]
        if prof["requires_key"] and not self.settings.has_key:
            raise PermissionError(
                "OPENROUTER_API_KEY is not configured. Add it to .env and restart the server, "
                "or use the Ollama / simulator profiles.")
        if any(s.provider == "openrouter" and not s.done for s in self.sessions.values()) \
                and provider == "openrouter":
            raise ValueError("an OpenRouter run is already in progress")
        models = [m for m in prof["models"] if not model_keys or m.key in model_keys]
        if not models:
            raise ValueError("no models selected")
        if provider == "openrouter":
            # Course budget rules: a single experiment, one cube exactly 3 moves from solved.
            if suite != "exact3":
                raise ValueError("OpenRouter runs must use the 'exactly 3 moves from solved' test set")
            if puzzles != 1:
                raise ValueError("OpenRouter runs are limited to 1 puzzle (one experiment within 65 requests)")
            if max_moves > 20:
                raise ValueError("move limit for OpenRouter runs is at most 20")
            models, preflight_notes = await self.preflight_openrouter(models)
        if provider == "ollama":
            await self._check_ollama(models)
            parallel = False   # one local model in memory at a time keeps latency fair on a laptop
        if seed is None:
            seed = random.randrange(1, 10**9)
        if custom_scramble:
            puzzle_list = [await asyncio.to_thread(_puzzle, 0, custom_scramble, self.settings.optimal_max_depth)]
            exact = SUITES[suite].get("exact_distance")
            if exact is not None and puzzle_list[0]["optimal_distance"] != exact:
                raise ValueError(f"custom scramble is {puzzle_list[0]['optimal_distance']} moves from solved; "
                                 f"this test set requires exactly {exact}")
        else:
            puzzle_list = await asyncio.to_thread(
                make_puzzles, suite, puzzles, seed, self.settings.optimal_max_depth, custom_length)
        s = Session(
            id=datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
            profile=profile, suite=suite, seed=seed, max_moves=max_moves, parallel=parallel,
            models=models, puzzles=puzzle_list, replay_speed=replay_speed,
            settings_snapshot={
                "request_timeout_s": (self.settings.ollama_timeout_s if provider == "ollama"
                                      else self.settings.request_timeout_s),
                "max_retries": self.settings.max_retries,
                "retry_backoff_s": self.settings.retry_backoff_s,
                "history_window": self.settings.history_window,
                "chat_max_tokens": self.settings.chat_max_tokens,
                "move_limits": {m.key: min(max_moves, m.max_moves or max_moves) for m in models},
                "move_metric": "HTM (18 moves: U,U',U2,...,B2)",
                "optimal_method": "bidirectional BFS (complete) up to depth "
                                  f"{self.settings.optimal_max_depth}",
                "cache": "persistent; checked before every request",
            },
        )
        if provider == "openrouter":
            s.budget_before = self.services.budget.snapshot()
            s.notes.extend(preflight_notes)
        self.sessions[s.id] = s
        s.task = asyncio.create_task(self._run(s))
        return s

    def cancel(self, sid: str) -> bool:
        s = self.sessions.get(sid)
        if not s or s.done:
            return False
        s._cancel.set()
        return True

    async def _run(self, s: Session) -> None:
        s.status = "running"
        await s.emit("session_started", session=s.summary(), puzzles=s.puzzles)
        final_status = "error"
        try:
            # One adapter per model for the whole session so any capability fallback
            # (e.g. structured-output downgrade) is applied consistently to every puzzle.
            adapters = [make_adapter(m, self.settings, self.client, self.services,
                                     random.Random(f"{s.seed}-{m.key}")) for m in s.models]
            for puzzle in s.puzzles:
                if s._cancel.is_set():
                    break
                await s.emit("puzzle_started", puzzle=puzzle)
                if s.parallel:
                    await asyncio.gather(*(self._trial(s, puzzle, a) for a in adapters))
                else:
                    for a in adapters:
                        if s._cancel.is_set():
                            break
                        await self._trial(s, puzzle, a)
                await s.emit("puzzle_finished", puzzle_index=puzzle["index"])
                self.save(s)
            final_status = "cancelled" if s._cancel.is_set() else "finished"
        except Exception as e:  # never leave a session hanging in "running"
            s.notes.append(f"runner crashed: {type(e).__name__}: {e}")
            await s.emit("log", level="error", message=s.notes[-1])
        finally:
            # Status flips to a terminal value right before the final event, with no await in
            # between, so stream consumers always receive "session_finished".
            for t in s.trials:
                if t["status"] == "running":
                    t["status"] = "cancelled"
            if s.provider == "openrouter":
                s.budget_after = self.services.budget.snapshot()
            s.status = final_status
            s.finished_at = now_iso()
            self.save(s)
            stats = model_stats(s.trials)
            await s.emit("session_finished", session=s.summary(), stats=stats, answers=answers(stats),
                         budget=s.budget_after)

    async def _trial(self, s: Session, puzzle: dict, adapter: BaseAdapter) -> None:
        spec = adapter.spec
        limit = s.move_limit(spec)
        trial = {
            "session_id": s.id, "puzzle_index": puzzle["index"], "model_key": spec.key,
            "model_name": spec.name, "model_id": spec.model_id, "category": spec.category,
            "interface": spec.interface, "provider": spec.provider, "mode": s.mode, "move_limit": limit,
            "scramble": puzzle["scramble"], "start_state": puzzle["state"],
            "optimal_distance": puzzle["optimal_distance"],
            "status": "running", "solved": False, "moves": [], "move_count": 0, "turns_used": 0,
            "wasted_turns": 0, "invalid_outputs": 0, "api_errors": 0, "retries": 0,
            "live_calls": 0, "cache_hits": 0, "total_time_s": None, "decision_time_s": None,
            "cache_time_ms": 0.0, "avg_latency_ms": None, "optimal_gap": None, "error": None,
            "cost": 0.0, "input_tokens": 0, "output_tokens": 0, "final_state": puzzle["state"],
            "started_at": now_iso(),
        }
        s.trials.append(trial)
        await s.emit("trial_started", puzzle_index=puzzle["index"], model_key=spec.key, move_limit=limit)
        if spec.provider == "ollama":
            await s.emit("log", level="info", message=f"Loading {spec.model_id} into memory (not timed)…")
            await adapter.warmup()

        state = puzzle["state"]
        history: list[str] = []
        t_start = time.perf_counter()
        # Bounded loop: at most `limit` turns, each turn at most 1 + MAX_RETRIES requests,
        # and every OpenRouter request must first pass the persistent budget ledger.
        while trial["turns_used"] < limit and not is_solved(state):
            if s._cancel.is_set():
                trial["status"] = "cancelled"
                break
            payload = build_state(state, history, trial["turns_used"], limit, self.settings.history_window)
            ctx: dict = {"replay_speed": s.replay_speed}
            turn = await adapter.choose_move(payload, ctx)
            trial["turns_used"] += 1
            trial["invalid_outputs"] += turn.malformed
            trial["api_errors"] += turn.api_errors
            trial["retries"] += turn.retries
            trial["live_calls"] += turn.live_calls
            trial["cache_hits"] += turn.cache_hits
            trial["cost"] = round(trial["cost"] + (turn.cost or 0.0), 8)
            if turn.cached_ms:
                trial["cache_time_ms"] = round(trial["cache_time_ms"] + turn.cached_ms, 2)
            usage = turn.usage or {}
            trial["input_tokens"] += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            trial["output_tokens"] += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            for n in turn.notes:
                if n not in s.notes:
                    s.notes.append(n)
                    await s.emit("log", level="warning", message=n)

            record = {
                "turn": trial["turns_used"], "move": turn.move, "valid": turn.valid,
                "source": turn.source,
                "latency_ms": None if turn.latency_ms is None else round(turn.latency_ms, 1),
                "cached_ms": turn.cached_ms, "turn_ms": round(turn.turn_ms, 1),
                "confidence": turn.confidence, "probabilities": turn.probabilities,
                "attempts": turn.attempts, "reasoning": turn.reasoning,
                "live_calls": turn.live_calls, "cache_hits": turn.cache_hits,
                "request_no": turn.request_no, "cost": turn.cost,
            }
            if turn.fatal_error:
                trial["status"] = "budget" if turn.budget_stop else "error"
                trial["error"] = turn.fatal_error
                record["error"] = turn.fatal_error
                status = (turn.attempts[-1].get("http_status") if turn.attempts else None)
                if spec.provider == "openrouter" and status in (401, 402, 403):
                    # Auth / credit problem affects every model: stop the whole run immediately so the
                    # other lanes don't each spend a request discovering the same error.
                    s._cancel.set()
                    msg = f"Stopping the run: {turn.fatal_error}"
                    if msg not in s.notes:
                        s.notes.append(msg)
                        await s.emit("log", level="error", message=msg)
                trial["moves"].append(record)
                await s.emit("move", puzzle_index=puzzle["index"], model_key=spec.key, record=record,
                             state=state, trial=self._trial_brief(trial))
                break
            if turn.valid and turn.move:
                state = apply_move(state, turn.move)
                history.append(turn.move)
                trial["move_count"] += 1
            else:
                trial["wasted_turns"] += 1
                record["error"] = "no valid move (wasted turn)"
            record["state_after"] = state
            trial["moves"].append(record)
            trial["final_state"] = state
            await s.emit("move", puzzle_index=puzzle["index"], model_key=spec.key, record=record,
                         state=state, trial=self._trial_brief(trial))

        trial["total_time_s"] = round(time.perf_counter() - t_start, 3)
        trial["final_state"] = state
        trial["solution"] = history
        lats = [m["latency_ms"] for m in trial["moves"] if m["valid"] and m["latency_ms"] is not None]
        trial["avg_latency_ms"] = round(sum(lats) / len(lats), 1) if lats else None
        trial["decision_time_s"] = round(sum(lats) / 1000, 3) if lats else 0.0
        if trial["status"] == "running":
            if is_solved(state):
                trial["status"] = "solved"
                trial["solved"] = True
                if puzzle["optimal_distance"] is not None:
                    trial["optimal_gap"] = trial["move_count"] - puzzle["optimal_distance"]
            else:
                trial["status"] = "limit"
        trial["finished_at"] = now_iso()
        await s.emit("trial_finished", puzzle_index=puzzle["index"], model_key=spec.key,
                     trial=self._trial_brief(trial))

    @staticmethod
    def _trial_brief(t: dict) -> dict:
        return {k: t[k] for k in (
            "puzzle_index", "model_key", "status", "solved", "move_count", "turns_used", "move_limit",
            "wasted_turns", "invalid_outputs", "api_errors", "retries", "live_calls", "cache_hits",
            "total_time_s", "decision_time_s", "avg_latency_ms", "optimal_gap", "optimal_distance",
            "error", "cost")}
