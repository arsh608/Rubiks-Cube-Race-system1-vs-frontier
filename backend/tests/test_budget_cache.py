"""The course email's budget rules, tested against worst-case model behaviour (no real network)."""

import asyncio
import json

import httpx
import pytest

from app.adapters import DecisionsAdapter, OllamaChatAdapter, OllamaDecisionsAdapter
from app.config import PROFILES, Settings, openrouter_request_caps
from app.cube import SOLVED, apply_move, apply_moves
from app.ledger import BudgetExceeded, BudgetLedger, Services
from app.prompts import QUESTION_ID, build_state
from app.runner import BenchmarkRunner, make_puzzles
from app.solver import best_next_moves

CAPS = openrouter_request_caps()
OR_SPECS = {m.key: m for m in PROFILES["assignment"]["models"]}
OL_SPECS = {m.key: m for m in PROFILES["ollama"]["models"]}
STATE = build_state(apply_move(SOLVED, "R"), [], 0, 20, 10)


def run(coro):
    return asyncio.run(coro)


def settings_for(tmp_path, **kw):
    base = dict(api_key="test-key", results_dir=tmp_path, max_retries=0, retry_backoff_s=0, request_timeout_s=5)
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------- ledger
def test_caps_match_the_email():
    assert CAPS == {"typesafe/jev-1.13": 20, "openai/gpt-6-luna-decisions": 20,
                    "cloudflare/clef": 20, "anthropic/claude-opus-5.5": 5}
    assert sum(CAPS.values()) == 65


def test_claude_cap_persists_across_restarts(tmp_path):
    led = BudgetLedger(tmp_path / "b.json", "k", CAPS, 65)
    for _ in range(5):
        run(led.reserve("anthropic/claude-opus-5.5"))
    with pytest.raises(BudgetExceeded):
        run(led.reserve("anthropic/claude-opus-5.5"))
    led2 = BudgetLedger(tmp_path / "b.json", "k", CAPS, 65)   # "restart"
    assert led2.used("anthropic/claude-opus-5.5") == 5 and led2.used() == 5
    with pytest.raises(BudgetExceeded):
        run(led2.reserve("anthropic/claude-opus-5.5"))
    assert led2.snapshot()["blocked"] == 2


def test_unknown_model_is_blocked(tmp_path):
    led = BudgetLedger(tmp_path / "b.json", "k", CAPS, 65)
    with pytest.raises(BudgetExceeded):
        run(led.reserve("openai/gpt-9-expensive"))
    assert led.used() == 0


def test_total_cap_never_above_95(tmp_path):
    led = BudgetLedger(tmp_path / "b.json", "k", {"m": 500}, total_cap=500)
    assert led.total_cap == 95
    led = BudgetLedger(tmp_path / "c.json", "k", {"m": 500}, total_cap=3)
    for _ in range(3):
        run(led.reserve("m"))
    with pytest.raises(BudgetExceeded):
        run(led.reserve("m"))


def test_corrupt_ledger_refuses_to_start(tmp_path):
    (tmp_path / "b.json").write_text("{not json")
    with pytest.raises(RuntimeError):
        BudgetLedger(tmp_path / "b.json", "k", CAPS, 65)


def test_counters_are_per_key(tmp_path):
    a = BudgetLedger(tmp_path / "b.json", "key-a", CAPS, 65)
    run(a.reserve("cloudflare/clef"))
    b = BudgetLedger(tmp_path / "b.json", "key-b", CAPS, 65)
    assert b.used() == 0
    assert "key-a" not in (tmp_path / "b.json").read_text()   # only a hash fingerprint is stored


# ---------------------------------------------------------------- cache through the adapter
CATALOG_DECISIONS = {"data": [{"id": "typesafe/jev-1.13"}, {"id": "openai/gpt-6-luna-decisions"},
                               {"id": "cloudflare/clef"}]}
CATALOG_ALL = {"data": [{"id": "anthropic/claude-opus-5.5",
                         "supported_parameters": ["response_format", "structured_outputs", "reasoning"]}]}


def counting_client(responder, catalog_all=None, catalog_dec=None, status=200):
    """Fake OpenRouter. GET = public catalog (free, not counted); POST = budgeted request (counted)."""
    calls = {"n": 0, "get": 0, "auth": []}

    def handler(request):
        if request.method == "GET":
            calls["get"] += 1
            assert "authorization" not in request.headers   # pre-flight never sends the key
            if request.url.params.get("output_modalities") == "decisions":
                return httpx.Response(200, json=catalog_dec or CATALOG_DECISIONS)
            return httpx.Response(200, json=catalog_all or CATALOG_ALL)
        calls["n"] += 1
        if status != 200:
            return httpx.Response(status, json={"error": {"code": status, "message": "Insufficient credits"}})
        return httpx.Response(200, json=responder(json.loads(request.content)))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def decision(choice):
    return {"model": "x", "answers": {QUESTION_ID: {"type": "choice", "choice": choice, "confidence": 0.9}},
            "usage": {"input_tokens": 10, "output_tokens": 1, "cost": 0.0001}}


def test_cache_hit_skips_network_and_budget(tmp_path):
    svc = Services(tmp_path, "test-key", CAPS, 65)
    client, calls = counting_client(lambda body: decision("R'"))
    a = DecisionsAdapter(OR_SPECS["jev"], settings_for(tmp_path), client, svc)
    first = run(a.choose_move(STATE))
    second = run(a.choose_move(STATE))
    assert calls["n"] == 1 and svc.budget.used() == 1
    assert first.source == "live" and second.source == "cache"
    assert second.latency_ms == pytest.approx(first.latency_ms, abs=0.2)   # original live latency kept
    assert second.cached_ms is not None and second.cost == 0
    assert svc.budget.snapshot()["cost_usd"] == pytest.approx(0.0001)
    # cache survives a restart
    svc2 = Services(tmp_path, "test-key", CAPS, 65)
    a2 = DecisionsAdapter(OR_SPECS["jev"], settings_for(tmp_path), client, svc2)
    assert run(a2.choose_move(STATE)).source == "cache" and calls["n"] == 1
    events = [e["event"] for e in svc2.log.tail(50)]
    assert events.count("request") == 1 and events.count("cache_hit") == 2


def test_malformed_response_is_not_cached(tmp_path):
    svc = Services(tmp_path, "test-key", CAPS, 65)
    client, calls = counting_client(lambda body: decision("NOPE"))
    a = DecisionsAdapter(OR_SPECS["clef"], settings_for(tmp_path), client, svc)
    r1 = run(a.choose_move(STATE))
    assert not r1.valid and r1.malformed == 1
    assert svc.cache.snapshot()["entries"] == 0


def test_budget_block_makes_no_http_call(tmp_path):
    svc = Services(tmp_path, "test-key", CAPS, 65)
    client, calls = counting_client(lambda body: decision("U"))
    a = DecisionsAdapter(OR_SPECS["luna"], settings_for(tmp_path), client, svc)
    for i in range(20):
        st = build_state(apply_moves(SOLVED, ["R"] * (i % 3 + 1) + ["U"] * (i // 3)), [], i, 20, 10)
        run(a.choose_move(st))
    n = calls["n"]
    r = run(a.choose_move(build_state(apply_moves(SOLVED, ["F", "B"]), [], 0, 20, 10)))
    assert r.budget_stop and calls["n"] == n and svc.budget.used("openai/gpt-6-luna-decisions") == 20


# ---------------------------------------------------------------- full OpenRouter run, network mocked
def _optimal_responder(body):
    """Fake OpenRouter: always answers with an optimal next move."""
    state = body["state"] if "state" in body else json.loads(body["messages"][1]["content"].split("\n", 1)[1])
    _, good = best_next_moves(state["cube_facelets"], 4)
    move = good[0] if good else "U"
    if "questions" in body:
        return decision(move)
    return {"choices": [{"message": {"content": json.dumps({"move": move})}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "cost": 0.01}}


def _bad_responder(body):
    """Fake OpenRouter: a model that never makes progress (worst case for the budget)."""
    if "questions" in body:
        return decision("U")
    return {"choices": [{"message": {"content": '{"move": "U"}'}, "finish_reason": "stop"}],
            "usage": {"cost": 0.01}}


async def _run_openrouter(tmp_path, responder, scramble=None):
    client, calls = counting_client(responder)
    r = BenchmarkRunner(settings_for(tmp_path), client)
    s = await r.create("assignment", "exact3", 1, 7, 20, True, None, custom_scramble=scramble)
    await s.task
    return r, s, calls


def test_openrouter_run_solves_then_replays_from_cache(tmp_path):
    r, s, calls = run(_run_openrouter(tmp_path, _optimal_responder))
    assert s.puzzles[0]["optimal_distance"] == 3
    assert all(t["status"] == "solved" and t["move_count"] == 3 and t["optimal_gap"] == 0 for t in s.trials)
    assert calls["n"] == 12 and r.services.budget.used() == 12
    # Second identical experiment: zero new requests, everything from the cache.
    scramble = s.puzzles[0]["scramble"]
    r2, s2, calls2 = run(_run_openrouter(tmp_path, _optimal_responder, scramble))
    assert calls2["n"] == 0 and r2.services.budget.used() == 12
    assert all(t["cache_hits"] == 3 and t["live_calls"] == 0 for t in s2.trials)


def test_openrouter_worst_case_stays_within_65(tmp_path):
    r, s, calls = run(_run_openrouter(tmp_path, _bad_responder))
    by = {t["model_key"]: t for t in s.trials}
    assert by["opus"]["turns_used"] == 5 and by["opus"]["status"] == "limit"
    for k in ("jev", "luna", "clef"):
        assert by[k]["turns_used"] == 20 and by[k]["status"] == "limit"
    snap = r.services.budget.snapshot()
    assert calls["n"] <= 65 and snap["total_used"] <= 65
    assert snap["per_model"]["anthropic/claude-opus-5.5"]["used"] <= 5


def test_openrouter_rules_enforced(tmp_path):
    client, _ = counting_client(_bad_responder)
    r = BenchmarkRunner(settings_for(tmp_path), client)
    for kwargs in ({"suite": "primary", "puzzles": 1}, {"suite": "exact3", "puzzles": 2}):
        with pytest.raises(ValueError):
            run(r.create("assignment", kwargs["suite"], kwargs["puzzles"], 1, 20, True, None))
    with pytest.raises(ValueError):
        run(r.create("assignment", "exact3", 1, 1, 30, True, None))


def test_exact3_puzzles_are_exactly_three_from_solved():
    for p in make_puzzles("exact3", 20, 5, 10):
        assert p["optimal_distance"] == 3 and p["scramble_length"] == 3


# ---------------------------------------------------------------- Ollama adapters
def test_ollama_payloads_and_parsing(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((str(request.url), body, dict(request.headers)))
        if request.url.path == "/v1/systemone":
            return httpx.Response(200, json=decision("R'"))
        return httpx.Response(200, json={"model": body["model"], "message": {"role": "assistant",
                                         "content": '{"move":"R\'"}', "thinking": "undo R"},
                                         "done": True, "prompt_eval_count": 300, "eval_count": 9})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = Services(tmp_path, "", CAPS, 65)
    st = settings_for(tmp_path, api_key="")
    d = run(OllamaDecisionsAdapter(OL_SPECS["clef_flash"], st, client, svc).choose_move(STATE))
    c = run(OllamaChatAdapter(OL_SPECS["qwen"], st, client, svc).choose_move(STATE))
    assert d.valid and d.move == "R'" and c.valid and c.move == "R'" and c.reasoning == "undo R"
    url, body, headers = seen[0]
    assert url == "http://localhost:11434/v1/systemone" and "authorization" not in headers
    assert body["questions"][QUESTION_ID]["type"] == "choice" and len(body["questions"][QUESTION_ID]["criteria"]) == 18
    url, body, _ = seen[1]
    assert url == "http://localhost:11434/api/chat" and body["stream"] is False
    assert body["format"]["properties"]["move"]["enum"][0] == "U"
    assert svc.budget.used() == 0   # Ollama never touches the OpenRouter budget


def test_ollama_preflight_lists_missing_models(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = BenchmarkRunner(settings_for(tmp_path), client)
    with pytest.raises(ValueError) as e:
        run(r.create("ollama", "exact3", 1, 1, 20, False, None))
    assert "ollama pull qwen3.5:2b" in str(e.value)


# ---------------------------------------------------------------- pre-flight, fail-fast, normalisation
def test_preflight_blocks_missing_model_without_spending(tmp_path):
    client, calls = counting_client(_bad_responder, catalog_dec={"data": [{"id": "typesafe/jev-1.13"}]})
    r = BenchmarkRunner(settings_for(tmp_path), client)
    with pytest.raises(ValueError) as e:
        run(r.create("assignment", "exact3", 1, 1, 20, True, None))
    assert "cloudflare/clef" in str(e.value) and calls["n"] == 0 and r.services.budget.used() == 0


def test_preflight_picks_supported_output_mode(tmp_path):
    cat = {"data": [{"id": "anthropic/claude-opus-5.5", "supported_parameters": ["response_format"]}]}
    client, calls = counting_client(_optimal_responder, catalog_all=cat)
    r = BenchmarkRunner(settings_for(tmp_path), client)

    async def go():
        sess = await r.create("assignment", "exact3", 1, 3, 20, True, ["opus"])
        await sess.task
        return sess
    s = run(go())
    opus = s.models[0]
    assert opus.structured_output == "json_object" and opus.reasoning_effort is None
    assert s.trials[0]["status"] == "solved" and s.trials[0]["live_calls"] == 3


def test_credit_error_stops_whole_run(tmp_path):
    client, calls = counting_client(_bad_responder, status=402)
    r = BenchmarkRunner(settings_for(tmp_path), client)

    async def go():
        sess = await r.create("assignment", "exact3", 1, 3, 20, False, None)   # sequential: first lane fails
        await sess.task
        return sess
    s = run(go())
    assert calls["n"] == 1 and r.services.budget.used() == 1
    assert s.status == "cancelled" and any("Stopping the run" in n for n in s.notes)


def test_curly_apostrophe_is_normalised(tmp_path):
    svc = Services(tmp_path, "test-key", CAPS, 65)
    client, _ = counting_client(lambda body: decision("R\u2019"))
    a = DecisionsAdapter(OR_SPECS["jev"], settings_for(tmp_path), client, svc)
    r = run(a.choose_move(STATE))
    assert r.valid and r.move == "R'"
