"""Injection tests (spec 13.2, ``tests/serving/test_inject.py``).

Pure-Python generator is deterministic per seed; each typology has the right structure; no numpy
import is used; and injection respects the rate cap.
"""

from __future__ import annotations

import ast
import importlib
import random
import time

import pytest
from fastapi.testclient import TestClient

from chainwatch.serving import inject
from chainwatch.serving.app import create_app
from tests.tiny import MINI_MODELS_DIR, tiny_cfg


def test_inject_module_is_pure_python_and_has_correct_exports():
    """``inject.py`` must only use stdlib imports and expose the required symbols."""
    source = importlib.util.find_spec("chainwatch.serving.inject")
    assert source is not None
    tree = ast.parse(open(source.origin, encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "numpy", "pandas", "sklearn", "lightgbm", "matplotlib",
        "scipy", "orjson", "uvloop", "httptools",
    }
    assert imported & forbidden == set(), f"heavy imports in serving.inject: {imported & forbidden}"
    # only stdlib + chainwatch.schema
    assert imported <= {"__future__", "math", "random", "collections", "chainwatch"}
    expected = ("funnel", "layered_chain", "split_merge", "round_trip", "structuring", "slow_roll")
    assert inject.TYPOLOGIES == expected
    assert callable(inject.make_campaign)


def test_make_campaign_is_deterministic_per_seed():
    victims = [f"V{i:04d}" for i in range(10)]

    def run() -> list:
        mules: list[str] = []

        def new_mule() -> str:
            m = f"M{len(mules)+1:03d}"
            mules.append(m)
            return m

        rng = random.Random(123)
        return inject.make_campaign(
            "funnel", rng, 1000.0, victims[:6], new_mule, "S0001",
            time_scale=0.5, ring_id="I1", first_event_id=50_000,
        )

    evs1 = run()
    evs2 = run()
    assert len(evs1) == len(evs2)
    for a, b in zip(evs1, evs2, strict=True):
        assert a.event.ts == pytest.approx(b.event.ts)
        assert a.event.amount == pytest.approx(b.event.amount)
        assert a.event.type == b.event.type
        assert a.is_fraud == b.is_fraud
        assert a.hop == b.hop
        assert a.src_role == b.src_role
        assert a.dst_role == b.dst_role
        assert a.ring_id == b.ring_id
        assert (a.event.src, a.event.dst) == (b.event.src, b.event.dst)


@pytest.mark.parametrize("typology", inject.TYPOLOGIES)
def test_each_typology_has_right_structure_and_balances_unknown(typology):
    victims = [f"V{i:04d}" for i in range(12)]
    counts = {"new_mule": 0}

    def new_mule() -> str:
        counts["new_mule"] += 1
        return f"M{counts['new_mule']:03d}"

    rng = random.Random(7)
    evs = inject.make_campaign(
        typology, rng, 2000.0, victims[:5], new_mule, "SINK",
        time_scale=0.5, ring_id="I2", first_event_id=10_000,
    )
    assert len(evs) > 0
    for le in evs:
        assert le.event.amount > 0
        # balances are unknown for injected events (the spec notes this)
        assert le.event.src_bal_before is None and le.event.src_bal_after is None
        assert le.event.dst_bal_before is None and le.event.dst_bal_after is None
        assert le.is_fraud == 1
        assert le.ring_id == "I2"
        assert le.event.event_id >= 10_000
    # time-sorted and hop non-negative
    ts = [e.event.ts for e in evs]
    assert ts == sorted(ts)
    assert all(e.hop >= 0 for e in evs)


def test_structuring_caps_amounts_when_needed():
    """Structuring typology should not produce unreasonably large transfers in the chunked form."""
    victims = [f"V{i:04d}" for i in range(8)]
    c = {"n": 0}

    def new_mule() -> str:
        c["n"] += 1
        return f"M{c['n']:03d}"

    rng = random.Random(42)
    evs = inject.make_campaign("structuring", rng, 5000.0, victims, new_mule, "SINK", time_scale=0.5)
    if evs:
        # most transfer amounts should be <= STRUCTURING_CAP in effect? the generator may chunk, but
        # intermediate chunks are <= 49999 + a small remainder possibility: capped to ~STRUCTURING_CAP
        assert max(e.event.amount for e in evs) <= 50000.0 + 0.01


def test_injection_respects_rate_cap_in_replay_app():
    cfg = tiny_cfg().with_overrides({"replay.inject_max_per_minute": 2})
    app = create_app(model_dir=MINI_MODELS_DIR, replay=True, cfg=cfg)
    with TestClient(app) as client:
        deadline = time.time() + 90.0
        while not client.get("/v1/replay/status").json().get("warmed"):
            assert time.time() < deadline
            time.sleep(0.25)
        client.post("/v1/replay/start", json={"speed": 2000})
        # first two injections
        r1 = client.post("/v1/inject/scenario", json={"typology": "funnel"})
        r2 = client.post("/v1/inject/scenario", json={"typology": "layered_chain"})
        assert r1.status_code == 200 and r2.status_code == 200
        # third should be rate limited
        r3 = client.post("/v1/inject/scenario", json={"typology": "funnel"})
        assert r3.status_code == 429
        body = r3.json()
        assert body["error"]["code"] == "rate_limited"
        # actual behaviour: the inject cap is a sliding 60s window; replay/reset does NOT
        # clear it (_init_state at replay.py:149 leaves _inject_times untouched), so the
        # limit still applies right after a reset
        client.post("/v1/replay/reset")
        deadline = time.time() + 90.0
        while not client.get("/v1/replay/status").json().get("warmed"):
            assert time.time() < deadline
            time.sleep(0.25)
        client.post("/v1/replay/start", json={"speed": 2000})
        assert client.post("/v1/inject/scenario", json={"typology": "funnel"}).status_code == 429


def test_inject_rejects_bad_typology_and_requires_running_or_paused():
    app = create_app(model_dir=MINI_MODELS_DIR, replay=True, cfg=tiny_cfg())
    with TestClient(app) as client:
        # idle: typology is validated before the replay state, so a bad typology is a 400
        r = client.post("/v1/inject/scenario", json={"typology": "nope"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_request"
        # idle with a valid typology: state error
        r = client.post("/v1/inject/scenario", json={"typology": "funnel"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "replay_state"
        # wait for warm-up and start
        deadline = time.time() + 90.0
        while not client.get("/v1/replay/status").json().get("warmed"):
            assert time.time() < deadline
            time.sleep(0.25)
        client.post("/v1/replay/start", json={"speed": 1000})
        r = client.post("/v1/inject/scenario", json={"typology": "nope"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_request"
        # unknown keys rejected
        r = client.post("/v1/inject/scenario", json={"typology": "funnel", "bad": 1})
        assert r.status_code == 422
