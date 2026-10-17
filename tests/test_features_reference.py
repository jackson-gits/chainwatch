"""FeatureEngine vs an independent P0 re-derivation (spec 9.3 / 9.6 / 9.7).

Two layers:

* fast tests build small synthetic streams by hand or with a seeded RNG and compare *every* P0
  feature of the streaming engine with :class:`tests._reference_features.ReferenceFeatures` at
  1e-9 tolerance;
* one ``@slow`` test replays the first 5000 events of the tiny world through both.

The reference lives in ``tests/_reference_features.py`` on purpose: it must not depend on
``chainwatch.features.engine`` or on a (non-existent) ``chainwatch.features.reference``.
"""

from __future__ import annotations

import random

import pytest

from chainwatch.data.io import iter_events
from chainwatch.features.engine import FeatureEngine
from chainwatch.features.registry import feature_names
from chainwatch.schema import Event
from tests._reference_features import P0_NAMES, ReferenceFeatures

TOL = 1e-9
TYPES = ("TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT")


def _ev(eid, ts, src, dst, amount, typ="TRANSFER", sbb=None, sba=None):
    return Event(eid, float(ts), src, dst, float(amount), typ, sbb, sba, None, None)


def _compare(cfg, events, max_events=None) -> int:
    """Feed the same events to engine and reference; return how many were checked."""
    eng = FeatureEngine(cfg, ("G2",))
    ref = ReferenceFeatures(cfg)
    n = 0
    for ev in events:
        got = eng.step(ev)
        want = ref.step(ev)
        assert set(want) == set(P0_NAMES), "reference must return exactly the P0 names"
        for name in P0_NAMES:
            g, w = got[name], want[name]
            assert abs(g - w) <= TOL, (
                f"event {n} (event_id={ev.event_id}) {name}: engine={g!r} reference={w!r}"
            )
        n += 1
        if max_events is not None and n >= max_events:
            break
    assert n > 0
    return n


def _huge_caps(cfg):
    """Remove the memory caps so the hand-built streams never hit saturation or eviction."""
    return cfg.with_overrides(
        {
            "features.deque_cap": 1_000_000,
            "features.payee_memory_n": 1_000_000,
            "features.state_ttl_s": 10_000_000_000,
        }
    )


def _random_stream(seed: int, n: int = 400, n_accts: int = 24) -> list[Event]:
    rnd = random.Random(seed)
    ids = [f"acct{i:03d}" for i in range(n_accts)]
    events = []
    ts = 0.0
    for i in range(1, n + 1):
        ts += rnd.choice([0.0, 1.0, 30.0, 300.0, 600.0, 3600.0, 7200.0])
        src = rnd.choice(ids)
        dst = rnd.choice([a for a in ids if a != src])
        amount = rnd.choice(
            [
                rnd.uniform(1.0, 500.0),
                100.0 * rnd.randint(1, 90),
                5000.0,
                42500.0,
                49999.0,
                rnd.lognormvariate(7.0, 1.4),
            ]
        )
        typ = rnd.choice(TYPES)
        sbb = rnd.choice([None, rnd.uniform(1.0, 200_000.0), 0.0])
        sba = rnd.choice([None, 0.0, rnd.uniform(0.0, 1000.0)])
        events.append(Event(i, ts, src, dst, amount, typ, sbb, sba, None, None))
    return events


def _chained_funnel_stream() -> list[Event]:
    """Fan-in, multi-credit FIFO, a suspicious relay and a suspicious neighbour."""
    events = []
    eid = 0
    for i, payer in enumerate(("payerA", "payerB", "payerC")):
        eid += 1
        events.append(_ev(eid, 10.0 + i, payer, "mule", 100.0 * (i + 1), "TRANSFER"))
    eid += 1
    events.append(_ev(eid, 20.0, "mule", "relay", 600.0, "TRANSFER"))  # fully matched, small dwell
    eid += 1
    events.append(_ev(eid, 30.0, "relay", "hop2", 500.0, "TRANSFER"))  # relay is fed by suspicious mule
    eid += 1
    events.append(_ev(eid, 40.0, "fresh", "mule", 700.0, "TRANSFER"))  # credits a suspicious account
    eid += 1
    events.append(_ev(eid, 50.0, "mule", "sink1", 250.0, "CASH_OUT", 300.0, 50.0))
    eid += 1
    events.append(_ev(eid, 60.0, "relay", "sink2", 200.0, "PAYMENT"))
    eid += 1
    events.append(_ev(eid, 3700.0, "hop2", "sink3", 50.0, "DEBIT"))  # TTL boundary on relay suspicion?
    for i in range(6):
        eid += 1
        events.append(_ev(eid, 4000.0 + 100.0 * i, f"hop{i % 3}", "mule", 150.0, "TRANSFER"))
    return events


def _equal_and_out_of_order_stream() -> list[Event]:
    events = [
        _ev(1, 1000.0, "S01", "D01", 100.0),
        _ev(2, 1000.0, "P02", "S01", 60.0),
        _ev(3, 1000.0, "S01", "D02", 40.0),
        _ev(4, 999.0, "S01", "D03", 10.0),  # late: clamped up to the clock
        _ev(5, 4600.0, "D01", "S01", 30.0),  # exactly ts - 3600 before the next event
        _ev(6, 4600.0, "S01", "D04", 90.0),
        _ev(7, 100.0, "Q01", "Q02", 5.0),  # very late
    ]
    return events


def test_p0_names_are_the_registry_p0_set():
    assert len(P0_NAMES) == 27
    assert set(P0_NAMES) == set(feature_names("G2", priority="P0"))
    assert list(P0_NAMES) == feature_names("G2", priority="P0")


def test_reference_matches_on_a_single_event(tiny_cfg_session):
    assert _compare(_huge_caps(tiny_cfg_session), [_ev(1, 5400.0, "S01", "D01", 49999.0)]) == 1


def test_reference_matches_random_streams(tiny_cfg_session):
    cfg = _huge_caps(tiny_cfg_session)
    for seed in (1, 2, 3):
        n = _compare(cfg, _random_stream(seed))
        assert n == 400


def test_reference_matches_chained_funnel(tiny_cfg_session):
    cfg = _huge_caps(tiny_cfg_session)
    _compare(cfg, _chained_funnel_stream())
    # the suspicion rule really fired in this stream (otherwise the comparison is vacuous)
    eng = FeatureEngine(cfg, ("G2",))
    flags = [eng.step(ev)["g_src_susp"] for ev in _chained_funnel_stream()]
    assert any(f == 1.0 for f in flags)


def test_reference_matches_equal_and_out_of_order_timestamps(tiny_cfg_session):
    cfg = _huge_caps(tiny_cfg_session)
    _compare(cfg, _equal_and_out_of_order_stream())


def test_reference_matches_with_default_caps_and_eviction(tiny_cfg_session):
    """Default caps (deque/payee memory) and short TTL are exercised on a long random stream."""
    cfg = tiny_cfg_session.with_overrides({"features.state_ttl_s": 90000})
    _compare(cfg, _random_stream(11, n=800))


@pytest.mark.slow
def test_reference_matches_tiny_world_first_5000(tiny_cfg_session, tiny_world):
    events = (le.event for le in iter_events(tiny_world / "events.csv.gz"))
    n = _compare(tiny_cfg_session, events, max_events=5000)
    assert n == 5000
