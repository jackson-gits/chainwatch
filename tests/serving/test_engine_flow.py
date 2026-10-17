"""ScoringEngine flow tests (spec 17.2, ``tests/serving/test_engine_flow.py``).

Idempotency, lateness, stage timings, the production detector, compare mode and the process lock.
Runs with only ``requirements-serve.txt`` plus pytest and httpx.
"""

from __future__ import annotations

import itertools
import threading

import pytest

from chainwatch.schema import Event, EventIn, LabeledEvent
from tests.tiny import MINI_MODELS_DIR

#: unique event-id space for this file so the session-scoped engine never sees a duplicate id
_IDS = itertools.count(3_000_000)


def _next_event(engine, *, src="A00030001", dst="A00030002", amount=1000.0, ts=None):
    """A fresh ``EventIn`` safely ahead of the engine clock (the clock starts at -inf)."""
    if ts is None:
        ts = max(engine.features.clock, 0.0) + 5.0
    return EventIn(
        event_id=next(_IDS),
        ts=ts,
        type="TRANSFER",
        src=src,
        dst=dst,
        amount=amount,
    )


def test_process_returns_decision_shape(mini_engine):
    ev = _next_event(mini_engine)
    decision, ring_events, replayed = mini_engine.process(ev.to_event())
    assert replayed is False
    assert isinstance(ring_events, list)
    assert decision["event_id"] == ev.event_id
    assert decision["decision"] in ("allow", "verify", "hold")
    assert decision["detector"] == "G2+R"
    assert decision["recommended_action"]
    assert isinstance(decision["score"], float)
    assert isinstance(decision["risk"], int)
    assert decision["ring_id"] is None or isinstance(decision["ring_id"], str)
    assert decision["model_version"] == mini_engine.info()["model"]["model_version"]
    assert isinstance(decision["reasons"], list)


def test_duplicate_event_id_is_idempotent(mini_engine):
    ev = _next_event(mini_engine)
    first, _, replayed = mini_engine.process(ev.to_event())
    assert replayed is False
    before = mini_engine.metrics_snapshot()
    second, ring_events, replayed = mini_engine.process(ev.to_event())
    assert replayed is True
    assert ring_events == []
    assert second == first
    after = mini_engine.metrics_snapshot()
    # a cached hit records no new latency sample, decision or throughput event
    assert after["throughput"]["total_events"] == before["throughput"]["total_events"]
    assert after["latency_us"]["total"]["n"] == before["latency_us"]["total"]["n"]
    assert after["decisions"] == before["decisions"]


def test_late_event_raises_and_boundary_is_exact(mini_engine):
    from chainwatch.serving.engine import LateEvent

    clock = mini_engine.features.clock
    # exactly max_lateness_s old is still accepted (the check is strict <)
    ok = _next_event(mini_engine, ts=clock - mini_engine.max_lateness)
    decision, _, replayed = mini_engine.process(ok.to_event())
    assert replayed is False and decision["event_id"] == ok.event_id
    # one second older is rejected with an explanatory message
    late = _next_event(mini_engine, ts=clock - mini_engine.max_lateness - 1.0)
    with pytest.raises(LateEvent) as exc:
        mini_engine.process(late.to_event())
    assert str(late.event_id) in str(exc.value)
    assert "max_lateness_s" in str(exc.value)


def test_stage_timings_present_and_non_negative(mini_engine):
    ev = _next_event(mini_engine)
    decision, _, _ = mini_engine.process(ev.to_event())
    lat = decision["latency_us"]
    assert set(lat) == {"features", "model", "decision", "total"}
    for value in lat.values():
        assert isinstance(value, int)
        assert value >= 0
    # the stage split never exceeds the recorded total (floor division per stage)
    assert lat["total"] >= lat["features"] + lat["model"] + lat["decision"]


def test_production_detector_is_g2r(mini_engine):
    info = mini_engine.info()
    assert info["model"]["production"] == "G2+R"
    assert info["model"]["detectors"] == ["B0", "B1", "G2"]
    assert info["model"]["registry_hash"].startswith("sha256:")
    assert info["model"]["config_hash"].startswith("sha256:")
    assert info["model"]["n_trees"] and set(info["model"]["n_trees"]) == {"B0", "B1", "G2"}


def test_compare_mode_returns_shadow_results(mini_cfg):
    from chainwatch.serving.engine import ScoringEngine

    eng = ScoringEngine(mini_cfg, MINI_MODELS_DIR, compare=True)
    ev = _next_event(eng)
    decision, _, _ = eng.process(ev.to_event())
    shadow = decision["shadow"]
    assert set(shadow) == {"B0", "B1"}
    for name in ("B0", "B1"):
        assert isinstance(shadow[name]["score"], float)
        assert shadow[name]["decision"] in ("allow", "verify", "hold")
    # and with compare off there is no shadow block
    plain = ScoringEngine(mini_cfg, MINI_MODELS_DIR, compare=False)
    decision, _, _ = plain.process(_next_event(plain).to_event())
    assert "shadow" not in decision
    assert plain.info()["compare_mode"] is False


def test_lock_prevents_interleaving(mini_engine):
    ev = _next_event(mini_engine)
    seen = []

    def worker():
        decision, _, _ = mini_engine.process(ev.to_event())
        seen.append(decision)

    assert mini_engine.lock.acquire(blocking=False)
    thread = threading.Thread(target=worker)
    try:
        thread.start()
        thread.join(0.3)
        # process() is guarded by the lock (rule E8): the worker cannot finish
        assert seen == []
    finally:
        mini_engine.lock.release()
    thread.join(5.0)
    assert not thread.is_alive()
    assert len(seen) == 1


def test_process_rejects_labeled_events(mini_engine):
    clock = max(mini_engine.features.clock, 0.0)
    ev = Event(next(_IDS), clock + 5.0, "A00030009", "A00030010", 10.0, "TRANSFER")
    labeled = LabeledEvent(ev, is_fraud=1)
    with pytest.raises(TypeError):
        mini_engine.process(labeled)


def test_warmup_events_do_not_pollute_metrics(mini_engine):
    ev = _next_event(mini_engine)
    before = mini_engine.metrics_snapshot()
    decision, _, replayed = mini_engine.process(ev.to_event(), warmup=True)
    assert replayed is False and decision["event_id"] == ev.event_id
    after = mini_engine.metrics_snapshot()
    assert after["warmup_events"] == before["warmup_events"] + 1
    assert after["throughput"]["total_events"] == before["throughput"]["total_events"]
    assert after["decisions"] == before["decisions"]


def test_account_view_and_unknown_account(mini_engine):
    assert mini_engine.account("A00099999") is None
    ev = _next_event(mini_engine, src="A00030021", dst="A00030022")
    mini_engine.process(ev.to_event())
    view = mini_engine.account("A00030021")
    assert view["account_id"] == "A00030021"
    assert view["ring_id"] is None
    assert view["recent_decisions"]
    assert view["recent_decisions"][0]["event_id"] == ev.event_id


def test_make_events_payloads_score_on_a_fresh_engine(mini_cfg, make_events):
    """The conftest ``make_events`` factory produces engine-ready payloads (unique-id space)."""
    from chainwatch.serving.engine import ScoringEngine

    eng = ScoringEngine(mini_cfg, MINI_MODELS_DIR)
    events = make_events(3)
    assert [e.event_id for e in events] == [0, 1, 2]
    for ev in events:
        decision, ring_events, replayed = eng.process(ev.to_event())
        assert replayed is False
        assert decision["event_id"] == ev.event_id
        assert isinstance(ring_events, list)
