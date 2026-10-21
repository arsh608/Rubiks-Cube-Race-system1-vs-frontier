"""Runtime configuration. Secrets come only from environment variables (or a local .env)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


@dataclass(frozen=True)
class ModelSpec:
    key: str                 # stable short id used by the UI and result files
    name: str                # display name
    model_id: str            # provider model identifier
    interface: str           # "decisions" | "chat" | "ollama_decisions" | "ollama_chat" | "mock"
    category: str            # "System-1 decision model" | "Frontier LLM"
    provider: str = "openrouter"          # "openrouter" | "ollama" | "mock"
    max_moves: int | None = None          # per-model move cap (None = run limit)
    request_cap: int | None = None        # OpenRouter: max real API requests for this model, ever
    # chat only: "json_schema" | "json_object" | "none"
    structured_output: str = "json_schema"
    reasoning_effort: str | None = None   # OpenRouter chat: none|minimal|low|medium|high|xhigh
    think: bool | None = None             # Ollama chat: thinking on/off (None = don't send)
    # mock only: simulated behaviour
    mock_skill: float = 0.0
    mock_latency_ms: tuple[int, int] = (50, 150)

    def public(self) -> dict:
        d = asdict(self)
        d["mock_latency_ms"] = list(self.mock_latency_ms)
        return d


OPUS_EFFORT: str | None = _env_str("OPUS_REASONING_EFFORT", "medium") or None
if OPUS_EFFORT and OPUS_EFFORT.lower() in {"off", "none", "disabled"}:
    OPUS_EFFORT = None

# ----- Budget rules from the course email -----------------------------------------------
DECISION_MODEL_MAX_MOVES = _env_int("DECISION_MAX_MOVES", 20)   # stop & mark unsolved after 20
FRONTIER_MAX_MOVES = _env_int("FRONTIER_MAX_MOVES", 5)          # Claude: max 5 requests/moves
DECISION_REQUEST_CAP = 20                                        # per decision model, lifetime
CLAUDE_REQUEST_CAP = 5                                           # lifetime, incl. failures/retries
OPENROUTER_TOTAL_CAP = min(_env_int("OPENROUTER_TOTAL_CAP", 65), 65)   # never configurable above 65
OPENROUTER_ABSOLUTE_CAP = 100                                    # key is disabled at 100

# Ollama model tags (sized for an 8 GB RAM laptop; override in .env for bigger machines).
OLLAMA_DECISION_MODEL = _env_str("OLLAMA_DECISION_MODEL", "turintech/clef-flash-artemis:iq3_xxs")
OLLAMA_QWEN_MODEL = _env_str("OLLAMA_QWEN_MODEL", "qwen3.5:2b")
OLLAMA_DEEPSEEK_MODEL = _env_str("OLLAMA_DEEPSEEK_MODEL", "deepseek-r1:1.5b")
OLLAMA_GEMMA_MODEL = _env_str("OLLAMA_GEMMA_MODEL", "gemma3:4b")
OLLAMA_THINK = _env_str("OLLAMA_THINK", "false").lower() in {"1", "true", "yes", "on"}
ENABLE_OLLAMA = _env_str("ENABLE_OLLAMA", "false").lower() in {"1", "true", "yes", "on"}

PROFILES: dict[str, dict] = {
    "assignment": {
        "label": "Assignment models (OpenRouter, budgeted: 65 requests)",
        "provider": "openrouter",
        "requires_key": True,
        "models": [
            ModelSpec("jev", "Jev 1.13", "typesafe/jev-1.13", "decisions", "System-1 decision model",
                      max_moves=DECISION_MODEL_MAX_MOVES, request_cap=DECISION_REQUEST_CAP),
            ModelSpec("luna", "GPT-6 Luna Decisions", "openai/gpt-6-luna-decisions", "decisions",
                      "System-1 decision model", max_moves=DECISION_MODEL_MAX_MOVES,
                      request_cap=DECISION_REQUEST_CAP),
            ModelSpec("clef", "Cloudflare Clef", "cloudflare/clef", "decisions", "System-1 decision model",
                      max_moves=DECISION_MODEL_MAX_MOVES, request_cap=DECISION_REQUEST_CAP),
            ModelSpec("opus", "Claude Opus 5.5", "anthropic/claude-opus-5.5", "chat", "Frontier LLM",
                      max_moves=FRONTIER_MAX_MOVES, request_cap=CLAUDE_REQUEST_CAP,
                      structured_output="json_schema", reasoning_effort=OPUS_EFFORT),
        ],
    },
    "ollama": {
        "label": "Local open-source models (Ollama, free, unlimited)",
        "provider": "ollama",
        "requires_key": False,
        "hidden": not ENABLE_OLLAMA,
        "models": [
            ModelSpec("clef_flash", "Clef Flash (Ollama)", OLLAMA_DECISION_MODEL, "ollama_decisions",
                      "System-1 decision model", provider="ollama", max_moves=DECISION_MODEL_MAX_MOVES),
            ModelSpec("qwen", "Qwen 3.5 (Ollama)", OLLAMA_QWEN_MODEL, "ollama_chat", "Frontier-style LLM",
                      provider="ollama", max_moves=FRONTIER_MAX_MOVES, think=OLLAMA_THINK),
            ModelSpec("deepseek", "DeepSeek-R1 (Ollama)", OLLAMA_DEEPSEEK_MODEL, "ollama_chat",
                      "Frontier-style LLM", provider="ollama", max_moves=FRONTIER_MAX_MOVES, think=OLLAMA_THINK),
            ModelSpec("gemma", "Gemma 3 (Ollama)", OLLAMA_GEMMA_MODEL, "ollama_chat", "Frontier-style LLM",
                      provider="ollama", max_moves=FRONTIER_MAX_MOVES),
        ],
    },
    "mock": {
        "label": "Offline simulator (no API calls, NOT real results)",
        "provider": "mock",
        "requires_key": False,
        "models": [
            ModelSpec("jev", "Jev 1.13 [SIM]", "sim/jev-1.13", "mock", "System-1 decision model", provider="mock",
                      max_moves=DECISION_MODEL_MAX_MOVES, mock_skill=0.45, mock_latency_ms=(60, 140)),
            ModelSpec("luna", "GPT-6 Luna Decisions [SIM]", "sim/gpt-6-luna-decisions", "mock",
                      "System-1 decision model", provider="mock", max_moves=DECISION_MODEL_MAX_MOVES,
                      mock_skill=0.55, mock_latency_ms=(90, 220)),
            ModelSpec("clef", "Cloudflare Clef [SIM]", "sim/clef", "mock", "System-1 decision model", provider="mock",
                      max_moves=DECISION_MODEL_MAX_MOVES, mock_skill=0.35, mock_latency_ms=(40, 110)),
            ModelSpec("opus", "Claude Opus 5.5 [SIM]", "sim/claude-opus-5.5", "mock", "Frontier LLM", provider="mock",
                      max_moves=FRONTIER_MAX_MOVES, mock_skill=0.9, mock_latency_ms=(1200, 2600)),
        ],
    },
}


def openrouter_request_caps() -> dict[str, int]:
    """Lifetime request cap per OpenRouter model id. Any id not listed here is blocked."""
    return {m.model_id: m.request_cap for m in PROFILES["assignment"]["models"] if m.request_cap}


@dataclass(frozen=True)
class Settings:
    api_key: str = os.getenv("OPENROUTER_API_KEY", "").strip()
    base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai").rstrip("/")
    app_url: str = os.getenv("OPENROUTER_APP_URL", "http://localhost:8000")
    app_title: str = os.getenv("OPENROUTER_APP_TITLE", "Rubiks Cube Race")
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    ollama_num_ctx: int = _env_int("OLLAMA_NUM_CTX", 4096)
    max_moves: int = _env_int("MAX_MOVES", 20)
    # Generous: a timed-out Claude call still counts as 1 of its 5 requests.
    request_timeout_s: float = _env_float("REQUEST_TIMEOUT_S", 240.0)
    ollama_timeout_s: float = _env_float("OLLAMA_TIMEOUT_S", 300.0)
    # Extra attempts per turn. Default 0: every failed call is a wasted turn (the email forbids
    # open-ended retrying, and every retry would count against the 65-request budget).
    max_retries: int = max(0, _env_int("MAX_RETRIES", 0))
    retry_backoff_s: float = _env_float("RETRY_BACKOFF_S", 1.5)
    history_window: int = _env_int("HISTORY_WINDOW", 10)   # recent moves shown to every model
    chat_max_tokens: int = _env_int("CHAT_MAX_TOKENS", 8000)
    optimal_max_depth: int = _env_int("OPTIMAL_MAX_DEPTH", 10)
    student_name: str = os.getenv("STUDENT_NAME", "").strip()
    roll_number: str = os.getenv("ROLL_NUMBER", "").strip()
    results_dir: Path = Path(os.getenv("RESULTS_DIR", str(ROOT_DIR / "results")))

    @property
    def has_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-or-v1-xxxx")


settings = Settings()

SUITES = {
    "exact3": {"label": "Assignment: exactly 3 moves from solved", "min_len": 3, "max_len": 3,
               "exact_distance": 3,
               "purpose": "Course rule: start from a cube exactly 3 moves (verified by BFS) from solved."},
    "warmup": {"label": "Warm-up", "min_len": 3, "max_len": 4,
               "purpose": "Verify model adapters, move validation, and the cube engine."},
    "primary": {"label": "Primary benchmark", "min_len": 4, "max_len": 5,
                "purpose": "Compare models on small puzzles where exact shortest distances are computed."},
    "extension": {"label": "Extension", "min_len": 15, "max_len": 20,
                  "purpose": "Probe behaviour on more complex states and observe failure rates."},
}
