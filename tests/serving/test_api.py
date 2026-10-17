"""REST API tests (spec 13.1, ``tests/serving/test_api.py``).

Every endpoint of §13.1: success and error shapes, auth on and off, batch with one bad event,
ring and account endpoints, 404s, label fields rejected and the rate limit. Uses the session
``mini_client`` (replay off); event ids and timestamps are chosen per test so the shared engine
never serves a duplicate id or a late event.
"""

from __future__ import annotations

import itertools

import pytest

from tests.tiny import MINI_MODELS_DIR

_IDS = itertools.count(1_000_000)
_TIERS = ("allow", "verify", "hold")


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Baseline: the optional API key guard is off unless a test enables it."""
    monkeypatch.delenv("CHAINWATCH_API_KEY", raising=False)


def _clock(client):
    return max(client.app.state.cw.engine.features.clock, 0.0)


def _payload(client, *, src="A00010001", dst="A00010002", amount=1500.0, ts=None, **extra):
    body = {
        "event_id": next(_IDS),
        "ts": _clock(client) + 5.0 if ts is None else ts,
        "type": "TRANSFER",
        "src": src,
        "dst": dst,
        "amount": amount,
    }
    body.update(extra)
    return body


def _error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    return body["error"]["message"]


# --------------------------------------------------------------------------- liveness and info
def test_healthz_is_open_and_ready(mini_client, monkeypatch):
    for path in ("/healthz", "/health", "/ping", "/v1/health", "/v1/healthz"):
        r = mini_client.get(path)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["ready"] is True
        assert isinstance(body["version"], str)
        assert body["uptime_s"] >= 0
    # liveness stays open even when the API key is required
    monkeypatch.setenv("CHAINWATCH_API_KEY", "sekret")
    assert mini_client.get("/healthz").status_code == 200
    assert mini_client.get("/health").status_code == 200


def test_info_shape(mini_client):
    r = mini_client.get("/v1/info")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["service"] == "chainwatch"
    assert body["mode"] == "api"  # replay off for mini_client
    assert body["provenance"] == "ChainWorld (synthetic)"
    assert body["auth_required"] is False
    assert body["compare_mode"] is False
    assert body["model"]["production"] == "G2+R"
    assert body["model"]["detectors"] == ["B0", "B1", "G2"]
    assert set(body["platform"]) >= {"machine", "system", "python", "cpu_count", "byteorder"}
    assert set(body["engine"]) >= {"clock", "events", "accounts", "active_states"}
    assert isinstance(body["tz_offset_s"], float)


# --------------------------------------------------------------------------- /v1/score
def test_score_and_idempotent_replay(mini_client):
    payload = _payload(mini_client)
    r = mini_client.post("/v1/score", json=payload)
    assert r.status_code == 200
    assert "x-idempotent-replay" not in r.headers
    decision = r.json()
    assert decision["event_id"] == payload["event_id"]
    assert decision["decision"] in _TIERS
    assert decision["detector"] == "G2+R"
    assert decision["recommended_action"]
    assert isinstance(decision["score"], float)
    assert isinstance(decision["risk"], int)
    assert set(decision["latency_us"]) == {"features", "model", "decision", "total"}
    again = mini_client.post("/v1/score", json=payload)
    assert again.headers.get("x-idempotent-replay") == "true"
    assert again.json() == decision


def test_route_aliases(mini_client):
    assert mini_client.get("/info").status_code == 200
    assert mini_client.get("/metrics").status_code == 200
    assert mini_client.get("/rings").status_code == 200
    payload = _payload(mini_client)
    r1 = mini_client.post("/score", json=payload)
    assert r1.status_code == 200
    assert r1.json()["event_id"] == payload["event_id"]
    payload2 = _payload(mini_client)
    r2 = mini_client.post("/predict", json=payload2)
    assert r2.status_code == 200
    assert r2.json()["event_id"] == payload2["event_id"]


def test_score_late_event(mini_client):
    payload = _payload(mini_client, ts=_clock(mini_client) - 10.0)
    r = mini_client.post("/v1/score", json=payload)
    message = _error(r, 409, "late_event")
    assert "max_lateness_s" in message
    # the shared engine clock must not have moved backwards
    assert mini_client.get("/v1/info").json()["engine"]["clock"] >= _clock(mini_client)


@pytest.mark.parametrize(
    "override",
    [
        {"is_fraud": 1},
        {"ring_id": "7"},
        {"unknown_key": True},
        {"amount": 0.0},
        {"amount": -5.0},
        {"type": "WIRE"},
        {"src": ""},
    ],
    ids=[
        "label-is_fraud",
        "label-ring_id",
        "extra-key",
        "amount-zero",
        "amount-negative",
        "bad-type",
        "empty-src",
    ],
)
def test_score_rejects_bad_payloads(mini_client, override):
    payload = _payload(mini_client)
    payload.update(override)
    r = mini_client.post("/v1/score", json=payload)
    _error(r, 422, "bad_request")


# --------------------------------------------------------------------------- /v1/score/batch
def test_batch_one_bad_event_does_not_stop_the_others(mini_client):
    events = [
        _payload(mini_client),
        _payload(mini_client, amount=-1.0),
        _payload(mini_client),
        _payload(mini_client, is_fraud=1),
    ]
    r = mini_client.post("/v1/score/batch", json={"events": events})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 4
    assert results[0]["event_id"] == events[0]["event_id"]
    assert results[0]["decision"] in _TIERS
    assert results[1]["error"]["code"] == "bad_request"
    assert results[2]["event_id"] == events[2]["event_id"]
    assert results[3]["error"]["code"] == "bad_request"


def test_batch_replays_are_served_from_cache(mini_client):
    payload = _payload(mini_client)
    first = mini_client.post("/v1/score", json=payload).json()
    r = mini_client.post("/v1/score/batch", json={"events": [_payload(mini_client), payload]})
    results = r.json()["results"]
    assert results[0]["event_id"] != payload["event_id"]
    assert results[1] == first


@pytest.mark.parametrize(
    "body",
    [
        [1, 2],
        {"foo": 1},
        {"events": "nope"},
    ],
    ids=["list", "missing-events", "not-a-list"],
)
def test_batch_bad_bodies(mini_client, body):
    r = mini_client.post("/v1/score/batch", json=body)
    _error(r, 400, "bad_request")


def test_batch_not_json(mini_client):
    r = mini_client.post(
        "/v1/score/batch", content=b"{not json", headers={"content-type": "application/json"}
    )
    _error(r, 400, "bad_request")


def test_batch_over_max_rejected(mini_client):
    st = mini_client.app.state.cw
    payload = _payload(mini_client)
    r = mini_client.post("/v1/score/batch", json={"events": [payload] * (st.max_batch + 1)})
    message = _error(r, 400, "bad_request")
    assert str(st.max_batch) in message


def test_request_body_over_1mb_rejected(mini_client):
    r = mini_client.post("/v1/score", content=b"x" * 1_048_577)
    _error(r, 400, "bad_request")


# --------------------------------------------------------------------------- rings and accounts
def test_rings_list_and_errors(mini_client):
    r = mini_client.get("/v1/rings")
    assert r.status_code == 200
    assert isinstance(r.json()["rings"], list)
    assert mini_client.get("/v1/rings?limit=0").json() == {"rings": []}
    assert mini_client.get("/v1/rings?status=open,alert,closed").status_code == 200
    for item in mini_client.get("/v1/rings?detail=false").json()["rings"]:
        assert "edges" not in item
    for item in mini_client.get("/v1/rings?detail=true").json()["rings"]:
        assert "edges" in item
    _error(mini_client.get("/v1/rings?status=nope"), 400, "bad_request")
    _error(mini_client.get("/v1/rings?limit=-1"), 422, "bad_request")
    _error(mini_client.get("/v1/rings?limit=1001"), 422, "bad_request")
    _error(mini_client.get("/v1/rings/NOPE123"), 404, "not_found")


def test_account_endpoint(mini_client):
    payload = _payload(mini_client, src="A00010051", dst="A00010052")
    mini_client.post("/v1/score", json=payload)
    r = mini_client.get("/v1/accounts/A00010051")
    assert r.status_code == 200
    view = r.json()
    assert view["account_id"] == "A00010051"
    assert "ring_id" in view
    assert view["recent_decisions"]
    assert view["recent_decisions"][0]["event_id"] == payload["event_id"]
    assert len(view["recent_decisions"]) <= 20
    assert mini_client.get("/v1/accounts/A00010052").status_code == 200
    _error(mini_client.get("/v1/accounts/NEVERSEEN"), 404, "not_found")
    _error(mini_client.get("/v1/nope"), 404, "not_found")


# --------------------------------------------------------------------------- metrics and compare
def test_metrics_shape(mini_client):
    mini_client.post("/v1/score", json=_payload(mini_client))
    m = mini_client.get("/v1/metrics").json()
    assert set(m) >= {"latency_us", "throughput", "decisions", "rings", "memory", "engine", "warmup_events"}
    assert set(m["latency_us"]) == {"features", "model", "decision", "total"}
    for stage in m["latency_us"].values():
        assert set(stage) >= {"p50", "p95", "p99", "max", "n"}
        assert stage["n"] >= 1
    assert set(m["throughput"]) == {"events_per_s_10s", "events_per_s_60s", "total_events"}
    assert m["throughput"]["total_events"] >= 1
    assert set(m["decisions"]) == {"allow", "verify", "hold"}
    assert sum(m["decisions"].values()) == m["throughput"]["total_events"]
    assert m["latency_us"]["total"]["n"] == m["throughput"]["total_events"]
    assert set(m["rings"]) >= {"open", "alert", "closed"}
    assert m["memory"]["rss_mb"] is None or m["memory"]["rss_mb"] > 0
    assert set(m["engine"]) >= {"clock", "events", "accounts", "active_states"}
    assert m["warmup_events"] == 0
    assert isinstance(m["p95_series_us"], list)


def test_compare_mode_toggle(mini_client):
    try:
        r = mini_client.post("/v1/compare", json={"enabled": True})
        assert r.json() == {"compare_mode": True}
        assert mini_client.get("/v1/info").json()["compare_mode"] is True
        decision = mini_client.post("/v1/score", json=_payload(mini_client)).json()
        assert set(decision["shadow"]) == {"B0", "B1"}
        for name in ("B0", "B1"):
            assert isinstance(decision["shadow"][name]["score"], float)
            assert decision["shadow"][name]["decision"] in _TIERS
        assert mini_client.post("/v1/compare", json={"enabled": False}).json() == {"compare_mode": False}
        decision = mini_client.post("/v1/score", json=_payload(mini_client)).json()
        assert "shadow" not in decision
    finally:
        mini_client.post("/v1/compare", json={"enabled": False})


def test_compare_rejects_bad_bodies(mini_client):
    # pydantic coerces "yes" -> True in lax mode, so use a value it cannot coerce
    _error(mini_client.post("/v1/compare", json={"enabled": 2}), 422, "bad_request")
    _error(mini_client.post("/v1/compare", json={"enabled": "maybe"}), 422, "bad_request")
    _error(mini_client.post("/v1/compare", json={}), 422, "bad_request")
    _error(mini_client.post("/v1/compare", json={"enabled": True, "extra": 1}), 422, "bad_request")


# --------------------------------------------------------------------------- auth and rate limit
def test_auth_off_by_default(mini_client):
    assert mini_client.get("/v1/info").status_code == 200
    assert mini_client.post("/v1/score", json=_payload(mini_client)).status_code == 200


def test_auth_required_when_api_key_env_set(mini_client, monkeypatch):
    monkeypatch.setenv("CHAINWATCH_API_KEY", "sekret")
    _error(mini_client.get("/v1/info"), 401, "unauthorized")
    _error(mini_client.get("/v1/info", headers={"X-API-Key": "wrong"}), 401, "unauthorized")
    _error(mini_client.post("/v1/score", json=_payload(mini_client)), 401, "unauthorized")
    assert mini_client.get("/v1/info", headers={"X-API-Key": "sekret"}).status_code == 200
    assert mini_client.get("/v1/info", headers={"X-API-Key": "sekret"}).json()["auth_required"] is True
    # the dashboard and static assets stay open
    assert mini_client.get("/").status_code == 200
    assert mini_client.get("/static/app.js").status_code == 200


def test_rate_limit_per_client(mini_client):
    from fastapi.testclient import TestClient

    from chainwatch.serving.app import create_app

    cfg = mini_client.app.state.cw.cfg.with_overrides({"serving.rate_limit_rps": 2})
    app = create_app(model_dir=MINI_MODELS_DIR, replay=False, cfg=cfg)
    with TestClient(app) as limited:
        limited.get("/v1/info")  # spend the initial burst
        limited_seen = None
        for _ in range(60):
            r = limited.get("/v1/info")
            if r.status_code == 429:
                limited_seen = r
                break
        assert limited_seen is not None, "the token bucket never emptied after 60 rapid requests"
        _error(limited_seen, 429, "rate_limited")
        # /healthz bypasses the limiter entirely
        assert limited.get("/healthz").status_code == 200


# --------------------------------------------------------------------------- replay disabled
@pytest.mark.parametrize(
    "method, path, body",
    [
        ("get", "/v1/replay/status", None),
        ("post", "/v1/replay/start", None),
        ("post", "/v1/replay/pause", None),
        ("post", "/v1/replay/resume", None),
        ("post", "/v1/replay/next_campaign", None),
        ("post", "/v1/replay/reset", None),
        ("post", "/v1/inject/scenario", {"typology": "funnel"}),
    ],
)
def test_replay_endpoints_disabled_with_no_replay(mini_client, method, path, body):
    if method == "get":
        r = mini_client.get(path)
    else:
        r = mini_client.post(path, json=body)
    message = _error(r, 409, "replay_state")
    assert "replay is disabled" in message


# --------------------------------------------------------------------------- dashboard
def test_dashboard_and_static(mini_client):
    r = mini_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"
    assert mini_client.get("/static/style.css").status_code == 200
    _error(mini_client.get("/static/missing.css"), 404, "not_found")
