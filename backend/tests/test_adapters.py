import asyncio
import json

import httpx
import pytest

from app.adapters import ChatAdapter, DecisionsAdapter
from app.config import PROFILES, Settings, openrouter_request_caps
from app.ledger import Services
from app.cube import SOLVED, apply_move
from app.prompts import QUESTION_ID, build_state

SETTINGS = Settings(api_key="test-key", max_retries=2, retry_backoff_s=0, request_timeout_s=5)
SPECS = {m.key: m for m in PROFILES["assignment"]["models"]}
STATE = build_state(apply_move(SOLVED, "R"), ["R"], 1, 20, 10)
SERVICES = None


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_services(tmp_path):
    global SERVICES
    SERVICES = Services(tmp_path, "test-key", openrouter_request_caps(), 65)
    yield


def adapter_with(cls, spec, responses, seen=None):
    """responses: list of (status, json_body) returned in order."""
    queue = list(responses)

    def handler(request: httpx.Request):
        if seen is not None:
            seen.append({"url": str(request.url), "headers": dict(request.headers),
                         "json": json.loads(request.content)})
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return cls(spec, SETTINGS, client, SERVICES)


def decision_body(choice, conf=0.8):
    return {"id": "gen-1", "model": "typesafe/jev-1.13", "answers": {
        QUESTION_ID: {"type": "choice", "choice": choice, "confidence": conf,
                      "probabilities": {choice: conf, "U": 1 - conf}}},
        "usage": {"input_tokens": 100, "output_tokens": 1, "cost": 0.00001}}


def chat_body(content):
    return {"id": "gen-2", "model": "anthropic/claude-opus-5.5",
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 20}}


def test_decisions_request_shape_and_parse():
    seen = []
    a = adapter_with(DecisionsAdapter, SPECS["jev"], [(200, decision_body("R'"))], seen)
    r = run(a.choose_move(STATE))
    assert r.valid and r.move == "R'" and r.confidence == 0.8 and r.retries == 0
    req = seen[0]
    assert req["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert req["headers"]["authorization"] == "Bearer test-key"
    body = req["json"]
    assert body["model"] == "typesafe/jev-1.13"
    q = body["questions"][QUESTION_ID]
    assert q["type"] == "choice" and len(q["criteria"]) == 18 and "U'" in q["criteria"]
    assert body["state"]["cube_facelets"] == STATE["cube_facelets"]


def test_same_question_for_all_decision_models():
    payloads = [DecisionsAdapter(SPECS[k], SETTINGS, None, SERVICES).build_payload(STATE)
                for k in ("jev", "luna", "clef")]
    assert all(p["questions"] == payloads[0]["questions"] for p in payloads)
    assert all(p["state"] == payloads[0]["state"] for p in payloads)
    assert [p["model"] for p in payloads] == ["typesafe/jev-1.13", "openai/gpt-6-luna-decisions", "cloudflare/clef"]


def test_decisions_malformed_is_counted_then_retried():
    bad = {"answers": {QUESTION_ID: {"type": "choice", "choice": "X9"}}, "usage": {}}
    a = adapter_with(DecisionsAdapter, SPECS["clef"], [(200, bad), (200, decision_body("U2"))])
    r = run(a.choose_move(STATE))
    assert r.valid and r.move == "U2" and r.malformed == 1 and r.retries == 1


def test_all_attempts_malformed_gives_wasted_turn():
    bad = {"answers": {}, "usage": {}}
    a = adapter_with(DecisionsAdapter, SPECS["luna"], [(200, bad)] * 3)
    r = run(a.choose_move(STATE))
    assert not r.valid and r.move is None and r.malformed == 3 and r.fatal_error is None


def test_rate_limit_is_retried():
    a = adapter_with(DecisionsAdapter, SPECS["jev"], [(429, {"error": {"code": 429, "message": "slow down"}}),
                                                       (200, decision_body("F"))])
    r = run(a.choose_move(STATE))
    assert r.valid and r.api_errors == 1 and r.retries == 1


def test_auth_error_is_fatal():
    a = adapter_with(DecisionsAdapter, SPECS["jev"], [(401, {"error": {"code": 401, "message": "No auth"}})])
    r = run(a.choose_move(STATE))
    assert not r.valid and r.fatal_error and "401" in r.fatal_error


def test_error_object_with_200_is_retryable():
    a = adapter_with(DecisionsAdapter, SPECS["jev"], [(200, {"error": {"code": 502, "message": "upstream"}}),
                                                       (200, decision_body("B"))])
    r = run(a.choose_move(STATE))
    assert r.valid and r.move == "B" and r.api_errors == 1


def test_chat_request_shape_and_parse():
    seen = []
    a = adapter_with(ChatAdapter, SPECS["opus"], [(200, chat_body('{"move": "R\'"}'))], seen)
    r = run(a.choose_move(STATE))
    assert r.valid and r.move == "R'"
    body = seen[0]["json"]
    assert seen[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "anthropic/claude-opus-5.5"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"]["move"]["enum"][0] == "U"
    assert STATE["cube_facelets"] in body["messages"][1]["content"]


@pytest.mark.parametrize("content", [
    '{"move": "Q"}', "R'", '```json\n{"move":"R"}\n```', '{"move":"R","why":"x"}', "",
])
def test_chat_malformed_outputs_counted(content):
    a = adapter_with(ChatAdapter, SPECS["opus"], [(200, chat_body(content)), (200, chat_body('{"move":"U"}'))])
    r = run(a.choose_move(STATE))
    assert r.valid and r.move == "U" and r.malformed == 1


def test_chat_structured_output_fallback():
    seen = []
    a = adapter_with(ChatAdapter, SPECS["opus"], [
        (400, {"error": {"code": 400, "message": "response_format json_schema not supported"}}),
        (200, chat_body('{"move":"L2"}'))], seen)
    ctx = {}
    r = run(a.choose_move(STATE, ctx))
    assert r.valid and r.move == "L2"
    assert seen[1]["json"]["response_format"] == {"type": "json_object"}
    assert any("falling back" in n for n in ctx["notes"])


def test_missing_key_is_fatal():
    s = Settings(api_key="", max_retries=2, retry_backoff_s=0)
    a = DecisionsAdapter(SPECS["jev"], s, httpx.AsyncClient(), SERVICES)
    r = run(a.choose_move(STATE))
    assert not r.valid and "OPENROUTER_API_KEY" in r.fatal_error
    assert SERVICES.budget.used() == 0   # no budget consumed when the key is missing
