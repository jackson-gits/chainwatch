"""WebSocket hub tests (spec 13.3, ``tests/serving/test_ws.py``).

Snapshot on connect, live batches from ``POST /v1/score``, the bounded per-client queue with
``dropped`` accounting, heartbeat and API-key auth via ``?key=``. Timing-sensitive waits use
generous timeouts or pure in-process hub tests instead of wall-clock races.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import threading
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from chainwatch.serving.ws import QUEUE_BATCHES, WsHub

_IDS = itertools.count(2_000_000)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Baseline: the optional API key guard is off unless a test enables it."""
    monkeypatch.delenv("CHAINWATCH_API_KEY", raising=False)


def _recv_json(session, timeout=10.0):
    """Receive one message with a timeout (starlette's receive blocks forever on silence)."""
    box: list = []

    def _run():
        try:
            box.append(("ok", session.receive_json()))
        except Exception as exc:
            box.append(("err", exc))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if not box:
        raise AssertionError(f"no WebSocket message within {timeout:.0f}s")
    kind, value = box[0]
    if kind == "err":
        raise value
    return value


def _recv_until(session, predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "timed out waiting for the expected WebSocket message"
        msg = _recv_json(session, timeout=remaining)
        if predicate(msg):
            return msg


def _batch_with(event_id):
    """Predicate matching a ``batch`` message that carries the given event id."""
    return lambda m: m["type"] == "batch" and any(
        i["event"]["event_id"] == event_id for i in m["items"]
    )


def _score(client, **kw):
    clock = max(client.app.state.cw.engine.features.clock, 0.0)
    body = {
        "event_id": next(_IDS),
        "ts": clock + 5.0,
        "type": "TRANSFER",
        "src": "A00020001",
        "dst": "A00020002",
        "amount": 2000.0,
    }
    body.update(kw)
    r = client.post("/v1/score", json=body)
    assert r.status_code == 200, r.text
    return body


# --------------------------------------------------------------------------- snapshot and batches
def test_snapshot_on_connect(mini_client):
    with mini_client.websocket_connect("/v1/ws") as ws:
        snap = _recv_json(ws, timeout=10.0)
    assert snap["type"] == "snapshot"
    assert set(snap) >= {"recent", "info", "rings", "metrics"}
    assert isinstance(snap["recent"], list)
    assert snap["info"]["service"] == "chainwatch"
    assert "replay" not in snap  # replay is off for mini_client


def test_scored_events_arrive_as_a_batch(mini_client):
    with mini_client.websocket_connect("/v1/ws") as ws:
        assert _recv_json(ws)["type"] == "snapshot"
        payload = _score(mini_client)
        msg = _recv_until(
            ws,
            _batch_with(payload["event_id"]),
            timeout=10.0,
        )
    assert set(msg) >= {"items", "ring_events", "metrics", "dropped"}
    item = next(i for i in msg["items"] if i["event"]["event_id"] == payload["event_id"])
    assert item["event"]["src"] == payload["src"]
    assert item["decision"]["event_id"] == payload["event_id"]
    assert item["truth"] is None  # no labels in API mode
    assert isinstance(msg["ring_events"], list)
    assert set(msg["metrics"]) >= {"latency_us", "throughput", "decisions"}
    assert msg["dropped"] == 0


def test_client_input_is_ignored(mini_client):
    with mini_client.websocket_connect("/v1/ws") as ws:
        assert _recv_json(ws)["type"] == "snapshot"
        ws.send_text(json.dumps({"type": "ping"}))
        payload = _score(mini_client)
        msg = _recv_until(
            ws,
            _batch_with(payload["event_id"]),
            timeout=10.0,
        )
    assert msg["type"] == "batch"


# --------------------------------------------------------------------------- bounded queue
def test_bounded_queue_drops_oldest_and_reports_count():
    """105 enqueues to one silent client keep the newest 100 batches and report ``dropped: 5``."""

    class FakeWs:
        def __init__(self):
            self.sent: list[dict] = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

        async def receive_text(self):
            await asyncio.Event().wait()  # the client never speaks

    async def scenario():
        hub = WsHub(batch_ms=50)
        ws = FakeWs()
        serve_task = asyncio.get_running_loop().create_task(hub.serve(ws))
        while not hub.clients:
            await asyncio.sleep(0)
        for i in range(QUEUE_BATCHES + 5):
            hub.send_now({"type": "batch", "items": [i], "ring_events": []})
        for _ in range(500):
            await asyncio.sleep(0)
            if len(ws.sent) >= 1 + QUEUE_BATCHES:
                break
        serve_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serve_task
        return ws.sent

    sent = asyncio.run(scenario())
    assert sent[0]["type"] == "snapshot"
    batches = [m for m in sent if m["type"] == "batch"]
    assert len(batches) == QUEUE_BATCHES
    assert batches[0]["dropped"] == 5
    assert all(m["dropped"] == 0 for m in batches[1:])
    assert [m["items"][0] for m in batches] == list(range(5, QUEUE_BATCHES + 5))


# --------------------------------------------------------------------------- heartbeat
@pytest.mark.slow
def test_heartbeat_arrives(mini_client):
    with mini_client.websocket_connect("/v1/ws") as ws:
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            msg = _recv_json(ws, timeout=max(1.0, deadline - time.monotonic()))
            if msg["type"] == "heartbeat":
                assert isinstance(msg["ts"], (int, float))
                return
    raise AssertionError("no heartbeat message within 45s")


# --------------------------------------------------------------------------- auth
def test_auth_via_query_key(mini_client, monkeypatch):
    monkeypatch.setenv("CHAINWATCH_API_KEY", "sekret")
    with pytest.raises(WebSocketDisconnect) as exc:
        with mini_client.websocket_connect("/v1/ws"):
            pass
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with mini_client.websocket_connect("/v1/ws?key=wrong"):
            pass
    assert exc.value.code == 4401
    with mini_client.websocket_connect("/v1/ws?key=sekret") as ws:
        assert _recv_json(ws)["type"] == "snapshot"


def test_auth_via_header(mini_client, monkeypatch):
    monkeypatch.setenv("CHAINWATCH_API_KEY", "sekret")
    with pytest.raises(WebSocketDisconnect) as exc:
        with mini_client.websocket_connect("/v1/ws", headers={"x-api-key": "wrong"}):
            pass
    assert exc.value.code == 4401
    with mini_client.websocket_connect("/v1/ws", headers={"x-api-key": "sekret"}) as ws:
        assert _recv_json(ws)["type"] == "snapshot"
