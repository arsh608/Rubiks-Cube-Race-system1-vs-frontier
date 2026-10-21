"""FastAPI entry point. The OpenRouter key never leaves this process."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import PROFILES, ROOT_DIR, SUITES, settings
from .cube import MOVE_DESCRIPTIONS, MOVES, SOLVED, InvalidMoveError, apply_moves, parse_moves
from .prompts import INSTRUCTIONS, chat_messages, build_state, decisions_questions
from .adapters import make_adapter
from .runner import BenchmarkRunner
from .solver import solve_optimal
from .stats import answers, model_stats

FRONTEND_DIR = ROOT_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(limits=limits, timeout=settings.request_timeout_s) as client:
        app.state.runner = BenchmarkRunner(settings, client)
        yield
        for s in app.state.runner.sessions.values():
            s._cancel.set()


app = FastAPI(title="Rubik's Cube Race", version="1.0.0", lifespan=lifespan)


def runner(request: Request) -> BenchmarkRunner:
    return request.app.state.runner


class RunRequest(BaseModel):
    profile: str = "mock"
    suite: str = "exact3"
    puzzles: int = Field(1, ge=1, le=50)
    seed: int | None = None
    max_moves: int = Field(settings.max_moves, ge=1, le=200)
    parallel: bool = True
    models: list[str] | None = None
    scramble_length: int | None = Field(None, ge=1, le=30)
    scramble: str | None = None      # optional custom scramble, e.g. "R U F2"
    replay_speed: bool = False       # serve cache hits at their original live latency (demo replays)


@app.get("/api/config")
def get_config(request: Request):
    return {
        "has_key": settings.has_key,
        "profiles": {k: {"label": v["label"], "requires_key": v["requires_key"], "provider": v["provider"],
                         "models": [m.public() for m in v["models"]]}
                     for k, v in PROFILES.items() if not v.get("hidden")},
        "default_profile": "assignment" if settings.has_key else "mock",
        "budget": request.app.state.runner.services.budget.snapshot(),
        "suites": SUITES,
        "max_moves": settings.max_moves,
        "moves": MOVES,
        "move_descriptions": MOVE_DESCRIPTIONS,
        "solved": SOLVED,
        "student_name": settings.student_name,
        "roll_number": settings.roll_number,
        "policy": {
            "request_timeout_s": settings.request_timeout_s,
            "max_retries": settings.max_retries,
            "history_window": settings.history_window,
            "optimal_max_depth": settings.optimal_max_depth,
            "ollama_url": settings.ollama_url,
        },
    }


@app.get("/api/budget")
def budget(request: Request):
    svc = runner(request).services
    return {"openrouter": svc.budget.snapshot(), "cache": svc.cache.snapshot()}


@app.get("/api/apilog")
def api_log(request: Request, n: int = Query(100, ge=1, le=2000)):
    return runner(request).services.log.tail(n)


@app.get("/api/health")
def health():
    return {"ok": True, "has_key": settings.has_key}


@app.get("/api/contract")
def contract():
    """Shows the exact request bodies each interface receives (key excluded)."""
    sample = build_state(apply_moves(SOLVED, ["R", "U'", "F2"]), [], 0,
                         settings.max_moves, settings.history_window)
    return {
        "instructions": INSTRUCTIONS,
        "decisions_request": {"model": "<decision model id>", "state": sample,
                              "questions": decisions_questions()},
        "chat_messages": chat_messages(sample),
    }


class SmokeRequest(BaseModel):
    profile: str = "ollama"


@app.post("/api/smoke")
async def smoke_test(req: SmokeRequest, request: Request):
    """One real call per model on a 1-move scramble: verifies key, model ids and parsing."""
    if req.profile not in PROFILES:
        raise HTTPException(400, f"unknown profile {req.profile!r}")
    prof = PROFILES[req.profile]
    if prof["provider"] == "openrouter":
        raise HTTPException(403, "Connection test is disabled for OpenRouter: every request counts against the "
                                 "65-request course budget. Use the Ollama profile to test wiring.")
    r = runner(request)
    if prof["provider"] == "ollama":
        try:
            await r._check_ollama(prof["models"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    state = apply_moves(SOLVED, ["R"])
    payload = build_state(state, [], 0, settings.max_moves, settings.history_window)
    client = r.client

    async def one(spec):
        adapter = make_adapter(spec, settings, client, r.services)
        await adapter.warmup()
        ctx: dict = {}
        turn = await adapter.choose_move(payload, ctx)
        return {
            "model_key": spec.key, "model_id": spec.model_id, "interface": spec.interface,
            "ok": turn.valid, "move": turn.move, "solves_it": turn.move == "R'",
            "latency_ms": None if turn.latency_ms is None else round(turn.latency_ms, 1),
            "confidence": turn.confidence, "malformed": turn.malformed, "api_errors": turn.api_errors,
            "error": turn.fatal_error or (turn.attempts[-1].get("error") if not turn.valid and turn.attempts else None),
            "notes": ctx.get("notes", []), "source": turn.source,
        }

    results = [await one(m) for m in prof["models"]]   # sequential: one local model in RAM at a time
    return {"profile": req.profile, "scramble": ["R"], "results": results}


@app.post("/api/scramble/analyze")
def analyze(scramble: str):
    try:
        moves = parse_moves(scramble)
    except InvalidMoveError as e:
        raise HTTPException(400, str(e))
    state = apply_moves(SOLVED, moves)
    return {"state": state, "optimal": solve_optimal(state, settings.optimal_max_depth).to_dict()}


@app.post("/api/runs")
async def start_run(req: RunRequest, request: Request):
    custom = None
    if req.scramble:
        try:
            custom = parse_moves(req.scramble)
        except InvalidMoveError as e:
            raise HTTPException(400, str(e))
        if not custom:
            raise HTTPException(400, "custom scramble is empty")
    try:
        s = await runner(request).create(
            req.profile, req.suite, req.puzzles, req.seed, req.max_moves, req.parallel,
            req.models, req.scramble_length, custom, req.replay_speed)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return s.summary()


@app.get("/api/runs")
def list_runs(request: Request):
    r = runner(request)
    live = {sid: s.summary() for sid, s in r.sessions.items()}
    saved = []
    for d in r.load_saved():
        if d["id"] in live:
            continue
        saved.append({k: d.get(k) for k in (
            "id", "profile", "mode", "suite", "suite_label", "seed", "max_moves", "parallel", "created_at",
            "finished_at", "status", "puzzle_count", "trials_done", "trials_total", "models")})
    allruns = list(live.values()) + saved
    allruns.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return allruns


@app.get("/api/runs/{sid}")
def get_run(sid: str, request: Request):
    d = runner(request).get_saved(sid)
    if d is None:
        raise HTTPException(404, "run not found")
    return d


@app.delete("/api/runs/{sid}")
def delete_run(sid: str, request: Request):
    if not runner(request).delete_saved(sid):
        raise HTTPException(409, "run is still active or does not exist")
    return {"deleted": sid}


@app.post("/api/runs/{sid}/cancel")
def cancel_run(sid: str, request: Request):
    if not runner(request).cancel(sid):
        raise HTTPException(409, "run is not active")
    return {"cancelling": sid}


@app.get("/api/runs/{sid}/events")
async def run_events(sid: str, request: Request, after: int = -1):
    s = runner(request).sessions.get(sid)
    if s is None:
        raise HTTPException(404, "run is not active in this server process")

    last_id = request.headers.get("last-event-id")
    if last_id and last_id.isdigit():
        after = max(after, int(last_id))

    async def gen():
        i = after + 1
        yield "retry: 2000\n\n"
        while True:
            while i < len(s.events):
                yield f"id: {i}\ndata: {json.dumps(s.events[i])}\n\n"
                i += 1
            if s.done and i >= len(s.events):
                yield "event: end\ndata: {}\n\n"
                return
            if await request.is_disconnected():
                return
            got = await s.wait_for_events(i, timeout=10)
            if not got:
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _collect(request: Request, ids: str | None, include_mock: bool) -> tuple[list[dict], list[dict]]:
    r = runner(request)
    if ids:
        sessions = [d for sid in ids.split(",") if (d := r.get_saved(sid.strip()))]
    else:
        seen = set()
        sessions = []
        for sid, s in r.sessions.items():
            sessions.append(s.to_json())
            seen.add(sid)
        sessions += [d for d in r.load_saved() if d["id"] not in seen]
    if not include_mock:
        sessions = [d for d in sessions if d.get("mode") != "mock"]
    trials = [t for d in sessions for t in d.get("trials", [])]
    return sessions, trials


@app.get("/api/stats")
def stats(request: Request, ids: str | None = Query(None, description="comma separated run ids"),
          include_mock: bool = False, suite: str | None = None):
    sessions, trials = _collect(request, ids, include_mock)
    if suite:
        keep = {d["id"] for d in sessions if d.get("suite") == suite}
        trials = [t for t in trials if t["session_id"] in keep]
    st = model_stats(trials)
    return {"runs": [d["id"] for d in sessions], "trials": len(trials), "stats": st, "answers": answers(st)}


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv(request: Request, ids: str | None = None, include_mock: bool = False):
    _, trials = _collect(request, ids, include_mock)
    cols = ["session_id", "mode", "provider", "puzzle_index", "model_key", "model_name", "model_id",
            "interface", "status", "solved", "move_limit", "move_count", "turns_used", "optimal_distance",
            "optimal_gap", "avg_latency_ms", "decision_time_s", "total_time_s", "live_calls", "cache_hits",
            "cache_time_ms", "invalid_outputs", "api_errors", "retries", "wasted_turns", "cost",
            "scramble", "solution", "error"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for t in trials:
        row = dict(t)
        row["scramble"] = " ".join(t.get("scramble") or [])
        row["solution"] = " ".join(t.get("solution") or [])
        w.writerow(row)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=rubiks_race_trials.csv"})


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
