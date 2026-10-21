"""Model adapters. Every adapter maps (state) -> one validated move.

* DecisionsAdapter        -> OpenRouter POST /api/alpha/decisions     (Jev, GPT-6 Luna Decisions, Clef)
* ChatAdapter             -> OpenRouter POST /api/v1/chat/completions (Claude Opus 5.5)
* OllamaDecisionsAdapter  -> Ollama POST /v1/systemone                (Clef Flash, local)
* OllamaChatAdapter       -> Ollama POST /api/chat                    (Qwen / DeepSeek-R1 / Gemma, local)
* MockAdapter             -> offline simulator (no network)

Every real request goes through ``_HTTPAdapter._request``:
  1. look up the persistent response cache (hit -> no network, no budget),
  2. OpenRouter only: reserve one request in the persistent budget ledger (refused -> stop),
  3. write a "request" line to the API log,
  4. send, measure live latency, log the response.
Valid responses are cached; malformed outputs are logged and counted, never coerced into a move.
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from .config import ModelSpec, Settings
from .cube import INVERSE, MOVES
from .ledger import BudgetExceeded, Services
from .prompts import MOVE_JSON_SCHEMA, QUESTION_ID, chat_messages, decisions_questions

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
MAX_REPLAY_SLEEP_S = 30.0


class FatalAPIError(Exception):
    """Configuration / auth / credit / budget problem — retrying will not help, abort the trial."""

    def __init__(self, message: str, status: int | None = None, budget: bool = False):
        super().__init__(message)
        self.status = status
        self.budget = budget


class RetryableAPIError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MalformedResponse(Exception):
    def __init__(self, message: str, raw: Any = None):
        super().__init__(message)
        self.raw = raw


@dataclass
class AttemptOutcome:
    move: str
    raw: Any = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    usage: dict | None = None
    reasoning: str | None = None
    served_model: str | None = None
    provider: str | None = None
    meta: dict = field(default_factory=dict)   # filled by _request: source, latency, cache key …
    raw_body: Any = None                       # full response body (what gets cached)


@dataclass
class TurnResult:
    move: str | None
    valid: bool
    latency_ms: float | None            # live API latency of the response that produced the move
    turn_ms: float                      # wall-clock for the whole turn incl. retries/backoff
    attempts: list[dict] = field(default_factory=list)
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    usage: dict | None = None
    reasoning: str | None = None
    malformed: int = 0
    api_errors: int = 0
    retries: int = 0
    fatal_error: str | None = None
    budget_stop: bool = False
    source: str | None = None           # "live" | "cache" | "sim"
    cached_ms: float | None = None      # time to serve from cache (cache hits only)
    cost: float = 0.0                   # actual USD spent this turn (live calls only)
    live_calls: int = 0                 # real network requests made this turn
    cache_hits: int = 0
    request_no: int | None = None       # ledger number of the OpenRouter request (if live)
    notes: list[str] = field(default_factory=list)


def _excerpt(obj: Any, limit: int = 600) -> str:
    try:
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(obj)
    return text if len(text) <= limit else text[:limit] + "…"


def _clean(text: str, limit: int = 300) -> str:
    """Strip HTML (e.g. upstream CDN error pages) and collapse whitespace for readable logs."""
    if "<" in text and ">" in text:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            msg = _clean(str(err.get("message") or ""))
            meta = err.get("metadata") or {}
            raw = meta.get("raw") if isinstance(meta, dict) else None
            if raw:
                raw_text = _clean(raw if isinstance(raw, str) else _excerpt(raw, 1000))
                if raw_text and raw_text not in msg:
                    msg = f"{msg} {raw_text}".strip()
            return msg
        if isinstance(err, str):
            return _clean(err)
        return _clean(_excerpt(body, 1000))
    except ValueError:
        return _clean(resp.text)


_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u2032": "'", "`": "'", "\u00b4": "'"})


def normalize_move(value: Any) -> Any:
    """Canonical spelling only (curly apostrophe -> ', stray spaces). Never changes WHICH move."""
    if not isinstance(value, str):
        return value
    return value.translate(_APOSTROPHES).replace(" ", "").strip()


def _cost_of(usage: Any) -> float | None:
    if isinstance(usage, dict) and isinstance(usage.get("cost"), (int, float)):
        return float(usage["cost"])
    return None


class BaseAdapter:
    def __init__(self, spec: ModelSpec, settings: Settings):
        self.spec = spec
        self.settings = settings

    async def call_once(self, state: dict, ctx: dict) -> AttemptOutcome:  # pragma: no cover
        raise NotImplementedError

    def on_valid(self, out: AttemptOutcome) -> None:
        """Hook: called once a response has produced a valid move (used for caching)."""

    async def warmup(self) -> None:
        """Hook: load the model before timing starts (local models)."""

    async def choose_move(self, state: dict, ctx: dict | None = None) -> TurnResult:
        """Same timeout + retry + validation policy for every model."""
        if ctx is None:
            ctx = {}
        ctx.setdefault("live_calls", 0)
        ctx.setdefault("cache_hits", 0)
        ctx.setdefault("cost", 0.0)
        attempts: list[dict] = []
        malformed = api_errors = 0
        notes: list[str] = ctx.setdefault("notes", [])
        turn_start = time.perf_counter()
        total_attempts = 1 + max(0, self.settings.max_retries)

        def result(**kw) -> TurnResult:
            return TurnResult(turn_ms=(time.perf_counter() - turn_start) * 1000, attempts=attempts,
                              malformed=malformed, api_errors=api_errors, notes=notes, cost=ctx["cost"],
                              live_calls=ctx["live_calls"], cache_hits=ctx["cache_hits"], **kw)

        for n in range(1, total_attempts + 1):
            t0 = time.perf_counter()
            try:
                out = await self.call_once(state, ctx)
                if out.move not in MOVES:  # final guard — never apply an unknown action
                    raise MalformedResponse(f"move {out.move!r} is not one of the 18 allowed moves", out.raw)
                meta = out.meta
                source = meta.get("source", "live")
                latency = meta.get("live_latency_ms")
                if latency is None:
                    latency = (time.perf_counter() - t0) * 1000
                attempts.append({"attempt": n, "status": "ok", "source": source,
                                 "latency_ms": round(latency, 1), "move": out.move,
                                 "cached_ms": meta.get("cached_ms"), "request_no": meta.get("request_no")})
                self.on_valid(out)
                return result(move=out.move, valid=True, latency_ms=latency, confidence=out.confidence,
                              probabilities=out.probabilities, usage=out.usage, reasoning=out.reasoning,
                              retries=n - 1, source=source, cached_ms=meta.get("cached_ms"),
                              request_no=meta.get("request_no"))
            except FatalAPIError as e:
                api_errors += 1
                attempts.append({"attempt": n, "status": "budget_blocked" if e.budget else "fatal",
                                 "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                                 "http_status": e.status, "error": str(e)})
                msg = str(e)
                if e.status and not msg.startswith("HTTP"):
                    msg = f"HTTP {e.status}: {msg}"
                return result(move=None, valid=False, latency_ms=None, retries=n - 1,
                              fatal_error=msg, budget_stop=e.budget)
            except MalformedResponse as e:
                malformed += 1
                attempts.append({"attempt": n, "status": "malformed",
                                 "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                                 "error": str(e), "raw": _excerpt(e.raw)})
            except (RetryableAPIError, httpx.TimeoutException, httpx.TransportError) as e:
                api_errors += 1
                kind = "timeout" if isinstance(e, httpx.TimeoutException) else "api_error"
                attempts.append({"attempt": n, "status": kind,
                                 "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                                 "http_status": getattr(e, "status", None),
                                 "error": str(e) or type(e).__name__})
            if n < total_attempts:
                await asyncio.sleep(self.settings.retry_backoff_s * n)

        # All attempts failed: this turn is a wasted turn (counts toward the move limit).
        return result(move=None, valid=False, latency_ms=None, retries=total_attempts - 1)


class _HTTPAdapter(BaseAdapter):
    provider = "openrouter"
    PATH = ""

    def __init__(self, spec: ModelSpec, settings: Settings, client: httpx.AsyncClient, services: Services):
        super().__init__(spec, settings)
        if services is None:
            raise ValueError("network adapters require Services (cache + budget + log)")
        self.client = client
        self.services = services

    # --- transport specifics (overridden for Ollama) ---
    def _url(self) -> str:
        return self.settings.base_url + self.PATH

    def _headers(self) -> dict:
        if not self.settings.has_key:
            raise FatalAPIError("OPENROUTER_API_KEY is not set in the backend environment (.env)")
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.settings.app_url,
            "X-Title": self.settings.app_title,
        }

    def _timeout(self) -> float:
        return self.settings.request_timeout_s

    def _map_http_error(self, status: int, msg: str) -> Exception:
        if status in RETRYABLE_STATUS:
            return RetryableAPIError(msg, status)
        return FatalAPIError(msg, status)

    def _transport_error(self, e: Exception) -> Exception:
        return e

    # --- the single choke point for every network request ---
    async def _request(self, payload: dict, ctx: dict) -> tuple[dict, dict]:
        svc = self.services
        key = svc.cache.make_key(self.provider, self.PATH, payload)
        t_lookup = time.perf_counter()
        hit = svc.cache.get(key)
        if hit is not None:
            cached_ms = (time.perf_counter() - t_lookup) * 1000
            ctx["cache_hits"] = ctx.get("cache_hits", 0) + 1
            svc.log.write("cache_hit", provider=self.provider, model=self.spec.model_id, request_hash=key[:16],
                          cached_ms=round(cached_ms, 2), original_live_latency_ms=hit.get("live_latency_ms"))
            if ctx.get("replay_speed") and hit.get("live_latency_ms"):
                await asyncio.sleep(min(hit["live_latency_ms"] / 1000, MAX_REPLAY_SLEEP_S))
            return hit["response"], {"source": "cache", "cache_key": key, "cached_ms": round(cached_ms, 2),
                                     "live_latency_ms": hit.get("live_latency_ms"), "payload": payload}

        headers = self._headers()  # fails fast (no budget used) if the key is missing
        request_no = None
        snap = None
        if self.provider == "openrouter":
            try:
                request_no = await svc.budget.reserve(self.spec.model_id)
            except BudgetExceeded as e:
                svc.log.write("blocked", provider=self.provider, model=self.spec.model_id,
                              request_hash=key[:16], reason=str(e), budget=svc.budget.snapshot())
                raise FatalAPIError(f"Budget stop: {e}", budget=True) from e
            snap = svc.budget.snapshot()
        svc.log.write("request", provider=self.provider, model=self.spec.model_id, endpoint=self.PATH,
                      request_hash=key[:16], request_no=request_no,
                      model_requests_used=(snap["per_model"].get(self.spec.model_id, {}).get("used")
                                           if snap else None),
                      total_requests_used=snap["total_used"] if snap else None)
        ctx["live_calls"] = ctx.get("live_calls", 0) + 1
        t0 = time.perf_counter()
        try:
            resp = await self.client.post(self._url(), json=payload, headers=headers, timeout=self._timeout())
        except (httpx.TimeoutException, httpx.TransportError) as e:
            svc.log.write("response", provider=self.provider, model=self.spec.model_id, request_hash=key[:16],
                          request_no=request_no, status="transport_error",
                          latency_ms=round((time.perf_counter() - t0) * 1000, 1), error=type(e).__name__)
            raise self._transport_error(e) from e
        latency = (time.perf_counter() - t0) * 1000
        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            pass
        cost = _cost_of(body.get("usage")) if isinstance(body, dict) else None
        if cost:
            ctx["cost"] = ctx.get("cost", 0.0) + cost
            if self.provider == "openrouter":
                await svc.budget.add_cost(cost)
        svc.log.write("response", provider=self.provider, model=self.spec.model_id, request_hash=key[:16],
                      request_no=request_no, status=resp.status_code, latency_ms=round(latency, 1), cost_usd=cost)
        if resp.status_code >= 400:
            raise self._map_http_error(resp.status_code, _error_message(resp))
        if not isinstance(body, dict):
            raise MalformedResponse("response body is not a JSON object", resp.text)
        if isinstance(body.get("error"), dict):
            # OpenRouter can return an error object with HTTP 200 (e.g. upstream failure).
            err = body["error"]
            code = err.get("code") if isinstance(err.get("code"), int) else None
            msg = _clean(str(err.get("message", "upstream error")))
            if code is None or code in RETRYABLE_STATUS:
                raise RetryableAPIError(msg, code)
            raise FatalAPIError(msg, code)
        return body, {"source": "live", "cache_key": key, "live_latency_ms": round(latency, 1),
                      "request_no": request_no, "cost": cost, "payload": payload}

    def on_valid(self, out: AttemptOutcome) -> None:
        m = out.meta
        if m.get("source") == "live" and m.get("cache_key") and isinstance(out.raw_body, dict):
            self.services.cache.put(m["cache_key"], provider=self.provider, endpoint=self.PATH,
                                    model=self.spec.model_id, payload=m["payload"], response=out.raw_body,
                                    live_latency_ms=m.get("live_latency_ms") or 0.0, cost=m.get("cost"))


class DecisionsAdapter(_HTTPAdapter):
    PATH = "/api/alpha/decisions"

    def build_payload(self, state: dict) -> dict:
        return {"model": self.spec.model_id, "state": state, "questions": decisions_questions()}

    async def call_once(self, state: dict, ctx: dict) -> AttemptOutcome:
        body, meta = await self._request(self.build_payload(state), ctx)
        out = self.parse(body)
        out.meta = meta
        out.raw_body = body
        return out

    @staticmethod
    def parse(body: dict) -> AttemptOutcome:
        answers = body.get("answers")
        if not isinstance(answers, dict) or QUESTION_ID not in answers:
            raise MalformedResponse(f"missing answers.{QUESTION_ID}", body)
        ans = answers[QUESTION_ID]
        if not isinstance(ans, dict):
            raise MalformedResponse("answer is not an object", body)
        choice = normalize_move(ans.get("choice"))
        if not isinstance(choice, str) or choice not in MOVES:
            raise MalformedResponse(f"invalid choice {choice!r}", body)
        conf = ans.get("confidence")
        probs = ans.get("probabilities")
        if isinstance(probs, dict):
            probs = {normalize_move(k): float(v) for k, v in probs.items() if isinstance(v, (int, float))}
        else:
            probs = None
        if not isinstance(conf, (int, float)):
            conf = probs.get(choice) if probs else None
        return AttemptOutcome(
            move=choice, raw={"answers": answers}, confidence=conf, probabilities=probs,
            usage=body.get("usage"), served_model=body.get("model"), provider=body.get("provider"),
        )


_STRUCTURED_FALLBACK = {"json_schema": "json_object", "json_object": "none"}


def parse_move_json(content: Any, finish: Any = None) -> str:
    """Strict: content must be exactly {"move": "<one of 18 moves>"}."""
    if isinstance(content, list):  # content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not isinstance(content, str) or not content.strip():
        raise MalformedResponse(f"empty content (finish_reason={finish})", content)
    text = content.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedResponse("content is not valid JSON", text) from e
    if not isinstance(data, dict) or set(data) != {"move"}:
        raise MalformedResponse('JSON must be exactly {"move": "<move>"}', text)
    move = normalize_move(data["move"])
    if not isinstance(move, str) or move not in MOVES:
        raise MalformedResponse(f"invalid move {data['move']!r}", text)
    return move


class ChatAdapter(_HTTPAdapter):
    PATH = "/api/v1/chat/completions"

    def __init__(self, spec: ModelSpec, settings: Settings, client: httpx.AsyncClient, services: Services):
        super().__init__(spec, settings, client, services)
        self.structured_mode = spec.structured_output

    def build_payload(self, state: dict) -> dict:
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": chat_messages(state),
            "max_tokens": self.settings.chat_max_tokens,
            "usage": {"include": True},
        }
        if self.structured_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "cube_move", "strict": True, "schema": MOVE_JSON_SCHEMA},
            }
            payload["provider"] = {"require_parameters": True}
        elif self.structured_mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if self.spec.reasoning_effort:
            payload["reasoning"] = {"effort": self.spec.reasoning_effort}
        return payload

    async def call_once(self, state: dict, ctx: dict) -> AttemptOutcome:
        try:
            body, meta = await self._request(self.build_payload(state), ctx)
        except FatalAPIError as e:
            # Capability negotiation: if the route rejects structured output, step down
            # json_schema -> json_object -> plain prompt (logged). Each step is a counted request.
            msg = str(e).lower()
            if (not e.budget and e.status in (400, 404) and self.structured_mode in _STRUCTURED_FALLBACK
                    and any(k in msg for k in ("response_format", "structured", "json_schema",
                                               "parameter", "no endpoints"))):
                old = self.structured_mode
                self.structured_mode = _STRUCTURED_FALLBACK[old]
                ctx.setdefault("notes", []).append(
                    f"{self.spec.model_id}: structured output '{old}' unsupported on this route, "
                    f"falling back to '{self.structured_mode}'")
                raise RetryableAPIError(f"structured output '{old}' rejected: {e}", e.status) from e
            raise
        out = self.parse(body)
        out.meta = meta
        out.raw_body = body
        return out

    @staticmethod
    def parse(body: dict) -> AttemptOutcome:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise MalformedResponse("no choices in response", body)
        msg = choices[0].get("message") or {}
        move = parse_move_json(msg.get("content"), choices[0].get("finish_reason"))
        reasoning = msg.get("reasoning")
        return AttemptOutcome(
            move=move, raw=msg.get("content"), usage=body.get("usage"),
            reasoning=(reasoning[:800] if isinstance(reasoning, str) and reasoning else None),
            served_model=body.get("model"), provider=body.get("provider"),
        )


class _OllamaMixin:
    provider = "ollama"

    def _url(self) -> str:
        return self.settings.ollama_url + self.PATH

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _timeout(self) -> float:
        return self.settings.ollama_timeout_s

    def _map_http_error(self, status: int, msg: str) -> Exception:
        if status == 404 or "not found" in msg.lower():
            return FatalAPIError(f"{msg} — pull it first: ollama pull {self.spec.model_id}", status)
        if status in RETRYABLE_STATUS:
            return RetryableAPIError(msg, status)
        return FatalAPIError(msg, status)

    def _transport_error(self, e: Exception) -> Exception:
        if isinstance(e, httpx.ConnectError):
            return FatalAPIError(f"Ollama is not reachable at {self.settings.ollama_url} — is it running?")
        return e

    async def warmup(self) -> None:
        """Load the model into memory so load time is not counted as decision latency."""
        try:
            await self.client.post(self.settings.ollama_url + "/api/generate",
                                   json={"model": self.spec.model_id, "keep_alive": "30m"},
                                   timeout=self.settings.ollama_timeout_s)
            self.services.log.write("warmup", provider="ollama", model=self.spec.model_id)
        except httpx.HTTPError as e:
            self.services.log.write("warmup_failed", provider="ollama", model=self.spec.model_id,
                                    error=type(e).__name__)


class OllamaDecisionsAdapter(_OllamaMixin, DecisionsAdapter):
    PATH = "/v1/systemone"

    def build_payload(self, state: dict) -> dict:
        return {"model": self.spec.model_id, "state": state, "questions": decisions_questions(),
                "keep_alive": "30m"}


class OllamaChatAdapter(_OllamaMixin, _HTTPAdapter):
    PATH = "/api/chat"

    def build_payload(self, state: dict) -> dict:
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": chat_messages(state),
            "stream": False,
            "format": MOVE_JSON_SCHEMA,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": self.settings.ollama_num_ctx},
        }
        if self.spec.think is not None:
            payload["think"] = self.spec.think
        return payload

    async def call_once(self, state: dict, ctx: dict) -> AttemptOutcome:
        try:
            body, meta = await self._request(self.build_payload(state), ctx)
        except FatalAPIError as e:
            # Some models reject the "think" flag entirely; drop it for the rest of the run.
            if not e.budget and "think" in str(e).lower() and self.spec.think is not None:
                self.spec = replace(self.spec, think=None)
                ctx.setdefault("notes", []).append(f"{self.spec.model_id}: 'think' unsupported, disabled")
                raise RetryableAPIError(str(e), e.status) from e
            raise
        msg = body.get("message") or {}
        move = parse_move_json(msg.get("content"), body.get("done_reason"))
        thinking = msg.get("thinking")
        return AttemptOutcome(
            move=move, raw=msg.get("content"),
            usage={"input_tokens": body.get("prompt_eval_count"), "output_tokens": body.get("eval_count")},
            reasoning=(thinking[:800] if isinstance(thinking, str) and thinking else None),
            served_model=body.get("model"), provider="ollama", meta=meta, raw_body=body,
        )


class MockAdapter(BaseAdapter):
    """Offline stand-in. Picks an optimal move with probability ``mock_skill``.

    Results produced by this adapter are tagged mode="mock" everywhere and are NOT
    benchmark evidence — it only exists so the UI/pipeline can be built before a key.
    """

    def __init__(self, spec: ModelSpec, settings: Settings, rng: random.Random | None = None):
        super().__init__(spec, settings)
        self.rng = rng or random.Random()

    async def call_once(self, state: dict, ctx: dict) -> AttemptOutcome:
        from .solver import best_next_moves  # local import keeps module import light

        lo, hi = self.spec.mock_latency_ms
        delay = self.rng.uniform(lo, hi) / 1000
        facelets = state["cube_facelets"]
        t0 = time.perf_counter()
        best, _ = await asyncio.gather(asyncio.to_thread(best_next_moves, facelets, 6),
                                       asyncio.sleep(delay))
        latency = (time.perf_counter() - t0) * 1000
        if self.rng.random() < 0.01:
            raise MalformedResponse("simulated malformed output", "{'move': ???}")
        _, good = best
        recent = state.get("recent_moves") or []
        if good and self.rng.random() < self.spec.mock_skill:
            move = self.rng.choice(good)
        else:
            avoid = {INVERSE[recent[-1]]} if recent else set()
            move = self.rng.choice([m for m in MOVES if m not in avoid])
        conf = round(self.rng.uniform(0.3, 0.95), 3)
        return AttemptOutcome(move=move, raw={"move": move}, confidence=conf,
                              meta={"source": "sim", "live_latency_ms": latency})


_ADAPTERS = {"decisions": DecisionsAdapter, "chat": ChatAdapter,
             "ollama_decisions": OllamaDecisionsAdapter, "ollama_chat": OllamaChatAdapter}


def make_adapter(spec: ModelSpec, settings: Settings, client: httpx.AsyncClient | None,
                 services: Services | None = None, rng: random.Random | None = None) -> BaseAdapter:
    if spec.interface == "mock":
        return MockAdapter(spec, settings, rng)
    cls = _ADAPTERS.get(spec.interface)
    if cls is None:
        raise ValueError(f"unknown interface {spec.interface!r}")
    if spec.provider != cls.provider:
        raise ValueError(f"{spec.model_id}: provider {spec.provider!r} does not match {spec.interface!r}")
    if client is None or services is None:
        raise ValueError("network adapters need an HTTP client and Services")
    return cls(spec, settings, client, services)
