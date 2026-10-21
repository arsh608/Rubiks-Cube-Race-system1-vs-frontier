import dataclasses
import json
import time

from fastapi.testclient import TestClient

from app import config
from app.main import app

# Make the simulator fast for tests.
config.PROFILES["mock"]["models"] = [dataclasses.replace(m, mock_latency_ms=(1, 3))
                                     for m in config.PROFILES["mock"]["models"]]


def wait_done(client, rid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/runs/{rid}").json()
        if d["status"] in ("finished", "cancelled", "error"):
            return d
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_config_and_contract():
    with TestClient(app) as c:
        cfg = c.get("/api/config").json()
        assert set(cfg["profiles"]) == {"assignment", "mock"}   # Ollama hidden unless ENABLE_OLLAMA=true
        assert cfg["budget"]["total_cap"] == 65
        ids = [m["model_id"] for m in cfg["profiles"]["assignment"]["models"]]
        assert ids == ["typesafe/jev-1.13", "openai/gpt-6-luna-decisions", "cloudflare/clef",
                       "anthropic/claude-opus-5.5"]
        assert "test-key" not in json.dumps(cfg)
        con = c.get("/api/contract").json()
        assert con["decisions_request"]["questions"]["next_move"]["type"] == "choice"


def test_index_served():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Rubik" in r.text
        assert c.get("/app.js").status_code == 200


def test_mock_run_end_to_end_and_stats():
    with TestClient(app) as c:
        r = c.post("/api/runs", json={"profile": "mock", "suite": "primary", "puzzles": 2, "seed": 123})
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        with c.stream("GET", f"/api/runs/{rid}/events") as s:
            types = []
            for line in s.iter_lines():
                if line.startswith("data: ") and line != "data: {}":
                    types.append(json.loads(line[6:])["type"])
                if line.startswith("event: end"):
                    break
        assert types[0] == "session_started" and types[-1] == "session_finished"
        assert types.count("trial_finished") == 8
        d = wait_done(c, rid)
        assert d["status"] == "finished" and len(d["trials"]) == 8
        for t in d["trials"]:
            assert t["status"] in ("solved", "limit")
            assert t["turns_used"] <= t["move_limit"] <= 20
            assert t["move_limit"] == (5 if t["model_key"] == "opus" else 20)
            if t["solved"]:
                assert t["optimal_gap"] >= 0
        for p in d["puzzles"]:
            assert p["optimal_distance"] is not None and p["optimal_distance"] <= p["scramble_length"]
        st = c.get(f"/api/stats?ids={rid}&include_mock=true").json()
        assert len(st["stats"]) == 4 and st["answers"]["ready"]
        # simulator runs are excluded from aggregate stats by default
        assert c.get("/api/stats").json()["trials"] == 0
        csv = c.get(f"/api/export.csv?ids={rid}&include_mock=true").text
        assert len(csv.strip().splitlines()) == 9
        assert any(x["id"] == rid for x in c.get("/api/runs").json())
        assert c.delete(f"/api/runs/{rid}").status_code == 200


def test_same_puzzles_for_same_seed():
    with TestClient(app) as c:
        a = c.post("/api/runs", json={"profile": "mock", "puzzles": 3, "seed": 9, "models": ["jev"]}).json()["id"]
        b = c.post("/api/runs", json={"profile": "mock", "puzzles": 3, "seed": 9, "models": ["opus"]}).json()["id"]
        da, db = wait_done(c, a), wait_done(c, b)
        assert [p["state"] for p in da["puzzles"]] == [p["state"] for p in db["puzzles"]]


def test_custom_scramble_and_validation():
    with TestClient(app) as c:
        # the default "exactly 3 moves" set rejects a 2-move scramble
        r = c.post("/api/runs", json={"profile": "mock", "scramble": "R U", "models": ["opus"]})
        assert r.status_code == 400 and "exactly 3" in r.text
        r = c.post("/api/runs", json={"profile": "mock", "suite": "primary", "scramble": "R U",
                                      "models": ["opus"]})
        assert r.status_code == 200
        d = wait_done(c, r.json()["id"])
        assert d["puzzles"][0]["optimal_distance"] == 2
        assert c.post("/api/runs", json={"profile": "mock", "scramble": "R Z"}).status_code == 400
        assert c.post("/api/runs", json={"profile": "nope"}).status_code == 400


def test_cancel():
    with TestClient(app) as c:
        rid = c.post("/api/runs", json={"profile": "mock", "suite": "extension", "puzzles": 3,
                                        "seed": 1}).json()["id"]
        c.post(f"/api/runs/{rid}/cancel")
        d = wait_done(c, rid)
        assert d["status"] == "cancelled"


def test_smoke_endpoint_with_simulator():
    with TestClient(app) as c:
        d = c.post("/api/smoke", json={"profile": "mock"}).json()
        assert len(d["results"]) == 4
        assert all(r["ok"] or r["malformed"] for r in d["results"])
        assert c.post("/api/smoke", json={"profile": "nope"}).status_code == 400
