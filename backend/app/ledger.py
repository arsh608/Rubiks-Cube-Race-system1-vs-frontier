"""Persistent API budget ledger, response cache and request log.

* BudgetLedger  – counts every real OpenRouter request *before* it is sent (failures and
                  retries included), enforces per-model and total caps, and persists to disk so
                  counters survive restarts. Nothing in the app can reset it.
* ResponseCache – persistent JSON cache keyed by provider + endpoint + model + exact payload.
                  Checked before every request; a hit costs no budget.
* ApiLog        – append-only JSONL log of every lookup / request / response / block.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("cube_race.api")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # Refuse to silently start from zero if the ledger is corrupt — that could overspend.
        raise RuntimeError(f"cannot read {path}: {e}. Fix or restore the file before continuing.") from e


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else "no-key"


class BudgetExceeded(Exception):
    pass


class BudgetLedger:
    def __init__(self, path: Path, api_key: str, model_caps: dict[str, int], total_cap: int,
                 absolute_cap: int = 100):
        self.path = path
        self.fingerprint = key_fingerprint(api_key)
        self.model_caps = dict(model_caps)
        self.total_cap = min(total_cap, absolute_cap - 5)   # keep a safety margin under the hard stop
        self.absolute_cap = absolute_cap
        self._lock = asyncio.Lock()
        self.data = _read_json(path, {"version": 1, "keys": {}})
        self.entry = self.data["keys"].setdefault(self.fingerprint, {
            "created_at": _now(), "total_requests": 0, "per_model": {}, "cost_usd": 0.0,
            "blocked": 0, "last_request_at": None,
        })
        _atomic_write(self.path, self.data)

    def used(self, model_id: str | None = None) -> int:
        if model_id is None:
            return int(self.entry["total_requests"])
        return int(self.entry["per_model"].get(model_id, 0))

    def remaining(self, model_id: str) -> int:
        cap = self.model_caps.get(model_id, 0)
        return max(0, min(cap - self.used(model_id), self.total_cap - self.used()))

    async def reserve(self, model_id: str) -> int:
        """Count one request (persisted) before it is sent. Raises BudgetExceeded instead."""
        async with self._lock:
            cap = self.model_caps.get(model_id)
            reason = None
            if cap is None:
                reason = f"model {model_id!r} is not on the OpenRouter allow-list"
            elif self.used(model_id) >= cap:
                reason = f"{model_id} reached its cap of {cap} API requests"
            elif self.used() >= self.total_cap:
                reason = f"total OpenRouter budget of {self.total_cap} requests reached"
            if reason:
                self.entry["blocked"] = int(self.entry.get("blocked", 0)) + 1
                _atomic_write(self.path, self.data)
                raise BudgetExceeded(reason)
            self.entry["total_requests"] = self.used() + 1
            self.entry["per_model"][model_id] = self.used(model_id) + 1
            self.entry["last_request_at"] = _now()
            _atomic_write(self.path, self.data)   # persisted BEFORE the HTTP request goes out
            return int(self.entry["total_requests"])

    async def add_cost(self, cost: float | None) -> None:
        if not isinstance(cost, (int, float)) or cost <= 0:
            return
        async with self._lock:
            self.entry["cost_usd"] = round(float(self.entry.get("cost_usd", 0.0)) + float(cost), 8)
            _atomic_write(self.path, self.data)

    def snapshot(self) -> dict:
        return {
            "key_fingerprint": self.fingerprint,
            "total_used": self.used(),
            "total_cap": self.total_cap,
            "absolute_cap": self.absolute_cap,
            "cost_usd": round(float(self.entry.get("cost_usd", 0.0)), 6),
            "blocked": int(self.entry.get("blocked", 0)),
            "per_model": {m: {"used": self.used(m), "cap": c, "remaining": self.remaining(m)}
                          for m, c in self.model_caps.items()},
            "last_request_at": self.entry.get("last_request_at"),
        }


class ResponseCache:
    def __init__(self, path: Path):
        self.path = path
        self.data = _read_json(path, {"version": 1, "hits": 0, "misses": 0, "entries": {}})
        self.data.setdefault("entries", {})

    @staticmethod
    def make_key(provider: str, endpoint: str, payload: dict) -> str:
        canonical = json.dumps({"provider": provider, "endpoint": endpoint, "payload": payload},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        entry = self.data["entries"].get(key)
        if entry is None:
            self.data["misses"] = int(self.data.get("misses", 0)) + 1
        else:
            self.data["hits"] = int(self.data.get("hits", 0)) + 1
            entry["hits"] = int(entry.get("hits", 0)) + 1
            entry["last_hit_at"] = _now()
        _atomic_write(self.path, self.data)
        return entry

    def put(self, key: str, *, provider: str, endpoint: str, model: str, payload: dict, response: dict,
            live_latency_ms: float, cost: float | None) -> None:
        self.data["entries"][key] = {
            "provider": provider, "endpoint": endpoint, "model": model,
            "created_at": _now(), "live_latency_ms": round(live_latency_ms, 1), "cost_usd": cost,
            "hits": 0, "request": payload, "response": response,
        }
        _atomic_write(self.path, self.data)

    def snapshot(self) -> dict:
        per_model: dict[str, int] = {}
        for e in self.data["entries"].values():
            per_model[e["model"]] = per_model.get(e["model"], 0) + 1
        return {"entries": len(self.data["entries"]), "hits": int(self.data.get("hits", 0)),
                "misses": int(self.data.get("misses", 0)), "entries_per_model": per_model}


class ApiLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        rec = {"ts": _now(), "t": round(time.time(), 3), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.info("%s %s", event, {k: v for k, v in fields.items() if k not in ("payload",)})

    def tail(self, n: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out


class Services:
    """Bundle passed to adapters so every request path shares one ledger/cache/log."""

    def __init__(self, results_dir: Path, api_key: str, model_caps: dict[str, int], total_cap: int,
                 absolute_cap: int = 100):
        self.budget = BudgetLedger(results_dir / "openrouter_budget.json", api_key, model_caps,
                                   total_cap, absolute_cap)
        self.cache = ResponseCache(results_dir / "response_cache.json")
        self.log = ApiLog(results_dir / "api_log.jsonl")
