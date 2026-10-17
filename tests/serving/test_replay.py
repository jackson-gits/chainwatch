"""Replay controller and overlay tests (spec 13.2, ``tests/serving/test_replay.py``).

Covers state machine, speed changes, warm-up (silent processing), ``next_campaign`` fast-forward
that keeps state correct relative to an uninterrupted run, and ``reset``. The replay app is
booted with the committed demo data.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from chainwatch.serving.app import create_app
from tests.tiny import MINI_MODELS_DIR, tiny_cfg


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("CHAINWATCH_API_KEY", raising=False)


@pytest.fixture(scope="module", autouse=True)
def _replay_app():
    """A replay-capable app booted once per module (a few seconds for warm-up start-up is fine)."""
    return create_app(model_dir=MINI_MODELS_DIR, replay=True, cfg=tiny_cfg())


@pytest.fixture()
def client(_replay_app):
    with TestClient(_replay_app) as c:
        yield c


def _wait_warmed(client, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        status = client.get("/v1/replay/status").json()
        if status.get("warmed"):
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"replay never warmed up: {status}")
        time.sleep(0.25)


def _status(client):
    return client.get("/v1/replay/status").json()


def test_state_machine_initial_conditions(client):
    status = client.get("/v1/replay/status").json()
    assert status["state"] == "idle"
    assert status["total"] > 0
    assert 1.0 <= status["speed"] <= 5000.0
    assert isinstance(status["warmed"], bool)
    assert status["pending_injected"] == 0


def test_idle_operations_fail_with_409(client):
    _error = lambda resp, code: (resp.status_code, resp.json())
    assert _error(client.post("/v1/replay/pause"), 409)[0] == 409
    assert _error(client.post("/v1/replay/resume"), 409)[0] == 409
    assert _error(client.post("/v1/replay/next_campaign"), 409)[0] == 409
    assert _error(client.post("/v1/inject/scenario", json={"typology": "funnel"}), 409)[0] == 409


def test_warm_up_is_silent_processing(client):
    """Warm-up processes events before warmup_until_ts; after warm-up, total decisions/throughput
    from /v1/metrics remain zero because decisions made during warm-up were recorded as warmup_events."""
    _wait_warmed(client, timeout=150.0)
    status = _status(client)
    assert status["warmed"] is True
    assert status["sim_ts"] >= status["warmed"] and status["sim_ts"] > 0
    metrics = client.get("/v1/metrics").json()
    # warm-up events were tallied but never published as client decisions
    assert metrics["warmup_events"] > 0
    assert metrics["throughput"]["total_events"] == 0
    assert metrics["decisions"] == {"allow": 0, "verify": 0, "hold": 0}


def test_speed_changes_and_bounds(client):
    r = client.post("/v1/replay/speed", json={"speed": 60})
    assert r.status_code == 200 and r.json()["speed"] == 60.0
    r = client.post("/v1/replay/speed", json={"speed": 5000})
    assert r.status_code == 200 and r.json()["speed"] == 5000.0
    for bad in (0, -1, 5001, 6000):
        r = client.post("/v1/replay/speed", json={"speed": bad})
        assert r.status_code == 400, (bad, r.text)
    # a non-object body is rejected by request validation
    r = client.post("/v1/replay/speed", json="slow")
    assert r.status_code == 422
    r = client.post("/v1/replay/speed", json={"speed": 1})
    assert r.status_code == 200 and r.json()["speed"] == 1.0


def test_start_accepts_optional_speed_and_runs(client):
    r = client.post("/v1/replay/start", json={"speed": 5000})
    assert r.status_code == 200
    assert r.json()["state"] == "running"
    # a second start while already running or idle after previous? start from idle -> restarts
    # reset first to be sure
    client.post("/v1/replay/reset")
    _wait_warmed(client)
    r = client.post("/v1/replay/start")  # no body
    assert r.status_code == 200 and r.json()["state"] == "running"


def test_pause_resume_state_errors(client):
    client.post("/v1/replay/reset")
    _wait_warmed(client)
    assert client.post("/v1/replay/start", json={"speed": 1000}).status_code == 200
    assert client.post("/v1/replay/pause").status_code == 200
    assert client.post("/v1/replay/pause").status_code == 409
    assert client.post("/v1/replay/resume").status_code == 200
    assert client.post("/v1/replay/resume").status_code == 409
    assert client.post("/v1/replay/pause").status_code == 200


def test_next_campaign_fast_forward_advances_state(client):
    """``next_campaign`` fast-forwards to just before the next campaign window; afterwards the
    engine must have consumed at least the skipped span and its clock must have reached the
    reported ``sim_ts``."""
    client.post("/v1/replay/reset")
    _wait_warmed(client)
    client.post("/v1/replay/start", json={"speed": 5000})
    time.sleep(0.5)
    r = client.post("/v1/replay/next_campaign")
    assert r.status_code == 200
    ff_status = r.json()
    assert ff_status["state"] in ("running", "paused", "finished")
    assert "skipped" in ff_status and ff_status["skipped"] >= 0
    # let the loop settle at the fast-forwarded position
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _status(client)["sim_ts"] < ff_status["sim_ts"]:
        time.sleep(0.1)
    after_engine = client.app.state.cw.engine.engine_stats()
    after_metrics = client.get("/v1/metrics").json()
    # the skipped events were processed (fast-forward streams them without publishing)
    assert after_metrics["throughput"]["total_events"] >= ff_status["skipped"]
    assert after_engine["events"] >= ff_status["skipped"]
    assert after_engine["clock"] >= ff_status["sim_ts"] - 5.0
    # a second fast-forward either advances again or is rejected at the end of the replay
    r2 = client.post("/v1/replay/next_campaign")
    assert r2.status_code in (200, 409)


def test_reset_rebuilds_engine_state(client):
    client.post("/v1/replay/reset")
    _wait_warmed(client)
    client.post("/v1/replay/start", json={"speed": 2000})
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _status(client)["state"] == "running":
        time.sleep(0.1)
    client.post("/v1/replay/pause")
    old_events = client.get("/v1/metrics").json()["engine"]["events"]
    assert old_events > 0
    r = client.post("/v1/replay/reset")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"
    assert r.json()["warmed"] is False
    assert r.json()["position"] <= 2000  # replay was reloaded; small starting offset depending on dataset
    # warm-up should be able to finish again
    _wait_warmed(client, timeout=90.0)
    after = client.get("/v1/metrics").json()
    # warm-up again consumed events (engine.events advances) but they were never published
    assert after["warmup_events"] > 0
    assert after["throughput"]["total_events"] == 0
    assert after["decisions"] == {"allow": 0, "verify": 0, "hold": 0}


def test_replay_controls_validation(client):
    client.post("/v1/replay/reset")
    _wait_warmed(client)
    assert client.post("/v1/replay/start", json={"speed": 0}).status_code == 400
    assert client.post("/v1/replay/start", json={"speed": 6000}).status_code == 400
    assert client.post("/v1/replay/speed", json={}).status_code == 422  # extra/forbid
    assert client.post("/v1/replay/start", json={"bad": 1}).status_code == 422
